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

import contextlib
import hashlib
import json
import math
import os
import queue
import shutil
import threading
import time
import traceback
import unicodedata
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import KW_ONLY, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, NamedTuple

import numpy as np

from . import export, hostload, library, locks, preview, storage, tiff
from .awake import KeepAwake
from .console import DeferredInterrupt
from .direct import (
    METER_EACH,
    METER_MODES,
    METER_ONCE,
    ColourPass,
    DirectScanner,
    library_named,
    locks_white_balance,
    raw_bytes_disagree,
    supports_infrared,
)
from .direction import FORWARD, REVERSED
from .framing import (
    FULL_FRAME,
    MAX_HOLD_MOVES,
    PRESCAN_COLUMNS,
    aim_units,
    frame_columns,
    reader_columns_to_hold_units,
    reversal_against,
    units_of,
)
from .library import FilmNotes
from .merge import merge_infrared
from .mono import MONO_CHANNEL, infrared_left_out, to_monochrome, wants_mono
from .protocol import (
    MM_PER_COMMAND,
    MM_PER_UNIT,
    DeviceSuspect,
    ScanReadError,
    StoppedBeforePass,
    film_process,
    units_for_param,
)
from .protocol import say as say_distance
from .usb_transport import UsbError

#: The infrared floor: an **untied** pass with infrared on holds the device this
#: long however few lines were asked for. Measured at 212-227 s across
#: resolutions, and the reason a short timeout once wedged the device. The
#: conservative end of the range, for the estimates; the read itself is held
#: to the top of it (`DirectScanner.UNTIED_INFRARED_IDLE_S`).
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
#: `protocol.units_for_param`, param + 1.84 units, for param 1 and the largest
#: correction, `DirectScanner.MAX_CORRECTION_PARAM` (87): 2.84 and 88.8 units
#: -- the cap was 8 once, and the demo's copy of that 8 is how its hold loop
#: came to fail where the scanner's held.
FINE_MIN_UNITS = units_for_param(1)
FINE_MAX_UNITS = units_for_param(DirectScanner.MAX_CORRECTION_PARAM)

#: The furthest one sub-frame move may go, chained commands and all: **one
#: frame width** (Stefan's "fine-limit" decision, 2026-10-13), 351.3 units on
#: the hold's scale -- the measured frame, `framing.FRAME_WIDTH_UNITS` (350.6
#: on the reader's scale), carried across through columns so the comparison
#: is on `plan_nudges`' own scale. Anything further is a whole-frame move,
#: which SLIDE_NEXT/SLIDE_PREV make properly. Four commands at param 87.
#: The window's own hand Move and the sheet's proposals stay at one command,
#: `FINE_MAX_UNITS`.
FINE_LIMIT_UNITS = reader_columns_to_hold_units(frame_columns(),
                                                int(PRESCAN_COLUMNS))

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


def rewind(scanner, frames: int, say=None,
           said: Callable[[int], str] | None = None) -> int | None:
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

    ``said`` says a place on the strip, 1-based, as `seek` is handed it: a
    seek on strip 2 numbered from 7 said "frame 11 (strip 2 · 5)" and then
    "rewinding 3 frame(s) from frame 5" of the same film, two numberings in
    two lines. Without it -- `tools/scan_roll.py`'s --rewind -- the lines
    say places, as they always did.
    """
    def tell(message):
        if say is not None:
            say(message)

    def at(position: int | None) -> str:
        if said is None or position is None or not plausible(position):
            return f"frame {_frame(position)}"
        return said(position + 1)

    def moved(before: int | None, after: int | None) -> str:
        if said is None:
            return f"frame {_frame(before)} -> {_frame(after)}"
        return f"{at(before)} -> {at(after)}"

    start = now = scanner.position()
    tell(f"rewinding {frames} frame(s) from {at(start)}")
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
            tell(f"  stopped after {done} of {frames}: still on "
                 f"{at(before)}")
            return None
        done += 1
        tell(f"  {done}/{frames}: {moved(before, now)}")
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

#: What one move of a hold costs beside the prescan that checks it: the SLIDE,
#: the driver's settle before it looks (`DirectScanner.HOLD_SETTLE_S`, taken
#: from there) and the host's measuring. Measured from the stored holds: the
#: gap between the end of the pass before a verification prescan and the start
#: of that prescan, 85 looks of 2026-10-03 to -05 at 300 dpi -- every one a
#: hold's first move, none a second or third -- median 2.6 s, 1.6 to 2.9 s, on
#: timestamps with one second's resolution. Those were made with a 0.4 s
#: settle; this is the rest of the 2.6. The look itself is a prescan, costed
#: as one (`estimate_seconds`).
HOLD_MOVE_S = DirectScanner.HOLD_SETTLE_S + 2.2

#: What one fine move costs, for the aim's question: a hold's move less the
#: driver's settle before its look -- the same 85 stored hold looks, 2.2 s.
#: An upper bound: it still holds the host's measuring between the move and
#: the look. A bare SLIDE has never been timed on its own. The window said
#: "about 1 s" from a 1.1 nothing measured.
MOVE_S = HOLD_MOVE_S - DirectScanner.HOLD_SETTLE_S

#: How many moves a held frame took, as the stored rolls ran them: 493 held
#: frames across rolls/*/roll.json, read 2026-10-12 -- 359 with no move, 130
#: with one, 4 with two -- 0.28 a frame, and 1.26 looks a frame counting its
#: own prescan. Each move is a SLIDE and the prescan that checks it. What the
#: window says a roll will cost, with the cap (`framing.MAX_HOLD_MOVES` a
#: frame) said beside it; `tools/scan_roll.py`'s 8-minute warning is costed
#: at the cap alone, which a wedge makes the safe end.
HOLD_MOVES_SEEN = 0.28


class FilmNotPlaced(RuntimeError):
    """The film could not be put where a roll starts, so nothing was scanned.

    Raised rather than returned so it takes the failed-job path: the window
    shows it where it shows a transport fault, and the job behind it is not
    mistaken for one that finished. Its text is the sentence the operator
    reads, so it says what to do.

    ``strip_ended_on`` is the frame the strip ended on, where that is why --
    the film would go no further while advancing to the roll's first frame
    -- and None for every other refusal: where the strip's end was found,
    which a roll to its end that stopped short of it never learned.
    """

    def __init__(self, message: str, strip_ended_on: int | None = None):
        super().__init__(message)
        self.strip_ended_on = strip_ended_on


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


def seek(scanner, target: int, say=None,
         said: Callable[[int], str] | None = None) -> int:
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
    unchanged -- the arrangement CLAUDE.md asks for. It was kept out of
    `scan_roll` when each backend had its own copy of that; the demo now runs
    the driver's, and the seek stays here, where the window and the tool
    share it.

    ``said`` says a place on the strip, 1-based, as the lines name it: the
    target and every reading of the counter. ``frame 3`` by default; a
    roll of strips passes `place_said` for its strip, so a seek on strip 2
    says ``frame 9 (strip 2 · 3)`` where the roll files that picture.
    """
    def tell(message):
        if say is not None:
            say(message)

    def at(place: int) -> str:
        return said(place) if said is not None else f"frame {place}"

    frame = target + 1
    if not plausible(target):
        raise FilmNotPlaced(
            f"{at(frame)} is past the {LAST_PLAUSIBLE_POSITION + 1} frames "
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
        # A counter no strip has is no place to say by the strip's numbers.
        raise FilmNotPlaced(
            f"the transport says the film is on frame {here + 1}, which no "
            "strip has -- a counter left over from before the strip went in "
            "reads like that. Nothing was scanned. Take the strip out and put "
            "it back, which reset the counter to frame 1 the one time this "
            "was seen, then start the roll again.")

    if here > target:
        tell(f"the film is on {at(here + 1)}; winding back "
             f"{here - target} to {at(frame)}")
        if rewind(scanner, here - target, say=say, said=said) is None:
            raise FilmNotPlaced(
                f"the film stopped short of {at(frame)} while winding back, "
                "so nothing was scanned -- the roll would have numbered "
                "frames it was not on")
    elif here < target:
        tell(f"the film is on {at(here + 1)}; advancing "
             f"{target - here} to {at(frame)}")
        on = here
        while on < target:
            landed = scanner.advance()
            if landed is None or landed <= on:
                raise FilmNotPlaced(
                    f"the strip ended on {at(on + 1)}, before {at(frame)}, "
                    "so nothing was scanned",
                    strip_ended_on=on + 1)
            tell(f"  {at(on + 1)} -> {at(landed + 1)}")
            on = landed

    arrived = _ask_position(scanner)
    if arrived != target:
        raise FilmNotPlaced(
            f"the film should be on {at(frame)} and the transport says "
            + (f"{at(arrived + 1)}" if arrived is not None
               and plausible(arrived) else f"frame {_frame(arrived)}")
            + ", so nothing was scanned")
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

    A frame whose prescan could not be written is left out, and said to
    ``say`` -- with its library entry where its pass was filed all the
    same (`RollManifest.prescan_told`). It dropped off a reopened sheet,
    and out of ``--approved``, without a word. So does one whose file the
    walk lists and the folder no longer holds: deleted by hand, or never
    copied with the rest.
    """
    folder = Path(folder)
    out: list[tuple[int, Path, dict]] = []
    for record in renumbered(manifest, say=say).get("frames") or ():
        name = record.get("prescan")
        try:
            number = int(record["number"])
        except (KeyError, TypeError, ValueError):
            continue
        error = record.get("prescan_error")
        if not name and error and say is not None:
            entry = record.get("prescan_entry")
            say(f"frame {number} is not on the sheet: its prescan was not "
                "written" + (f" ({str(error).removeprefix(NOT_WRITTEN)}); "
                             f"its pass is in the library as {entry}"
                             if entry else
                             f", and its pass was not filed either ({error})"))
        if not name:
            continue
        if not (folder / name).exists():
            if say is not None:
                say(f"frame {number} is not on the sheet: {name} is listed "
                    f"by the walk but is not in {folder}")
            continue
        out.append((number, folder / name, record))
    return out


def read_walk_prescan(manifest: dict, number: int, path: Path, record: dict,
                      library_root=None, say=None,
                      left: str = "left off the sheet",
                      stood_in: dict[int, str] | None = None
                      ) -> np.ndarray | None:
    """One walk prescan as a reference: in the film's own orientation, or
    None, said to ``say``, where it cannot be had.

    Read one at a time, each on its own: one file a power cut cut short
    refused the whole walk on the sheet, and stopped `--approved` with a
    traceback. A file is refused where it cannot be read (`tiff.read`) or
    no longer matches the checksum its walk recorded (`prescan_sha256`,
    `RollManifest.prescan_told`) -- the only way to know an uncompressed
    file whose strips came back as zeros, which reads as a black picture
    and was held to as one. A walk from before the checksum is read
    unchecked.

    A refused file is made again from the walk's **own** library entry for
    that prescan -- its record's `prescan_entry` -- corrected with today's
    code (`library.corrected`), and the log says so (Stefan's call,
    2026-10-09). Never from `approved.json`'s `reference_entry`, nor from a
    join on time: the join once gave a walk the roll's verification prescan
    (`walked_prescan_entries`). Only an entry that says it is a prescan of
    this frame, not the picture from before an aim, and that corrects --
    never a raw picture shown as a reference. Its folder is not compared:
    `roll_membership.folder` is where it was filed, and a roll renamed or
    duplicated since would never match. Otherwise the frame is left off,
    and the log says why.

    ``manifest`` is the walk as `walked_prescans` was given it, ``number``,
    ``path`` and ``record`` one of its answers; ``library_root`` is where
    the walk filed its prescans; ``left`` is what leaving the frame out
    means to the caller. ``stood_in``, where given, is told the entry a
    picture was made again from, by frame number: that entry is the
    frame's reference then, whatever else names one.
    """
    path = Path(path)
    try:
        image = tiff.read(str(path))
    except (OSError, ValueError) as exc:
        why = str(exc)
    else:
        said = record.get("prescan_sha256")
        why = None
        if said:
            try:
                if library._sha256(path) != said:
                    why = (f"{path.name} is not the file its walk wrote: it "
                           "does not match the checksum recorded for it")
            except OSError as exc:
                why = f"{path.name} cannot be read: {exc}"
        if why is None:
            return preview.unorient(image, *prescan_arrangement(manifest,
                                                                record))
    rebuilt, entry, refused = _rebuilt_prescan(number, record, library_root)
    if rebuilt is not None and entry is not None and stood_in is not None:
        stood_in[number] = entry
    if say is not None:
        if rebuilt is not None:
            say(f"frame {number}: {why}; shown from its library entry "
                f"{entry}, corrected with today's code")
        else:
            say(f"frame {number}: {why}; {left} -- {refused}")
    return rebuilt


def _rebuilt_prescan(number: int, record: dict, library_root
                     ) -> tuple[np.ndarray | None, str | None, str]:
    """`read_walk_prescan`'s stand-in for a refused file: the picture, the
    entry it came from, and why there is none where there is none."""
    named = record.get("prescan_entry")
    if not named:
        return None, None, "the walk names no library entry for it"
    if library_root is None:
        return None, None, "there is no library to make it again from"
    # By its last part on either separator: written on Windows, read
    # anywhere (`walked_prescan_entries` keeps the id as recorded).
    entry = Path(library_root) / PureWindowsPath(str(named)).name
    try:
        stored = json.loads((entry / "scan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None, f"its library entry {entry.name} cannot be read"
    stored = stored if isinstance(stored, dict) else {}
    member = (stored.get("extra") or {}).get("roll_membership")
    member = member if isinstance(member, dict) else {}
    if (member.get("kind") != "prescan"
            or str(member.get("number")) != str(number)):
        return None, None, (f"its library entry {entry.name} is not a "
                            f"prescan of frame {number}")
    if "before" in (stored.get("tags") or ()):
        return None, None, (f"its library entry {entry.name} is the picture "
                            "from before an aim")
    try:
        image, how = library.corrected(entry)
    except Exception as exc:                              # noqa: BLE001
        return None, None, (f"its library entry {entry.name} cannot be read "
                            f"({exc})")
    if how.get("corrected") not in ("applied", "already"):
        return None, None, (f"its library entry {entry.name} cannot be "
                            f"corrected ({how.get('corrected')})")
    # The picture as the walk's writer made the file: corrected, in every
    # channel it has, whatever the film. No walk prescan is ever written one
    # channel -- the window's `_file` and `tools/scan_roll.py` ask the
    # writer for no `mono` -- and a black and white frame made one channel
    # here was a 2-D picture among 3-D ones, which the edge reader passes
    # over (`frame_edges.propose`): no red line on the sheet, and held at
    # where the walk saw it. Already the film's own way up: the file was
    # turned for the screen, the entry never was.
    return image, entry.name, ""


#: How long after a walk's `finished` its last prescans may still be filed,
#: for joining a walk from before its records named their entries
#: (`walked_prescan_entries`). `finished` is written as the scanning ends and
#: the writer files what is queued behind it afterwards, a second or two each
#: for a 300 dpi prescan. Chosen, not measured: a roll into the folder cannot
#: file a prescan of its own sooner than a pass after it starts, and the roll's
#: `started` bounds it more closely where there is one.
WALK_FILED_WITHIN_S = 60.0


def _utc_time(text: Any) -> float | None:
    """A manifest's or an entry's time, as a timestamp; None if it has none."""
    if not isinstance(text, str) or not text:
        return None
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.timestamp()


def walked_prescan_entries(folder, records, library_root,
                           walk: dict | None = None,
                           roll: dict | None = None) -> dict[int, Path]:
    """The library entry each walked frame's prescan was filed as.

    ``records`` is ``(number, record)`` pairs of a walk's frames. A record's
    own `prescan_entry` -- the entry's id, which `RollManifest.amend` writes
    once the writer has filed it -- resolved against ``library_root``, which
    is where it was filed; the survey records no path, because one relative
    to wherever the window ran is no path the day after. A record that names
    one whose folder has since gone, or says it was not filed (`None`), has
    none: never another pass's, as `roll_entries` never gives a deleted
    frame another roll's. A `prescan_error` beside an entry it names is the
    copy's, not the filing's -- the file was not written, the pass was
    filed -- and the entry stands.

    Only a walk from before records said so is joined on the entries' own
    `roll_membership` -- a prescan of this folder and this number, not a
    picture from before an aim -- and only on entries filed while it ran.
    ``walk`` is its manifest: one that named any entry is not from before,
    and its `started` and `finished` bound the join. A roll after the walk
    files its own prescans into the folder with the same numbers, and a
    re-walk had filed its own before; taken newest first, the join gave a
    reopened walk the roll's verification prescan as the reference, and the
    next approval wrote it into `approved.json`. ``roll`` is the roll.json
    beside it, whose `started` bounds the join more closely. The oldest entry
    in that span wins: the walk's own was filed before anything that came
    after it.
    """
    root = Path(library_root)
    out: dict[int, Path] = {}
    missing = []
    for number, record in records:
        if "prescan_entry" in record or "prescan_error" in record:
            named = record.get("prescan_entry")
            if named and (root / str(named)).is_dir():
                out[number] = root / str(named)
        else:
            missing.append(number)
    walk = walk if isinstance(walk, dict) else {}
    if any(isinstance(r, dict) and ("prescan_entry" in r
                                    or "prescan_error" in r)
           for r in walk.get("frames") or ()):
        # Made since records named their entries: a frame that names none
        # was not filed, or its answer never came.
        return out
    if not missing or not root.is_dir():
        return out
    since = _utc_time(walk.get("started"))
    bounds = []
    finished = _utc_time(walk.get("finished"))
    if finished is not None:
        bounds.append(finished + WALK_FILED_WITHIN_S)
    rolled = _utc_time((roll or {}).get("started")
                       if isinstance(roll, dict) else None)
    if rolled is not None and (since is None or rolled > since):
        bounds.append(rolled)
    until = min(bounds) if bounds else None
    here = os.path.normcase(str(Path(folder).resolve()))
    joined: dict[int, tuple[float, Path]] = {}
    for record_path in root.glob("*/scan.json"):
        try:
            entry = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        member = (entry.get("extra") or {}).get("roll_membership") \
            if isinstance(entry, dict) else None
        if (not isinstance(member, dict) or member.get("kind") != "prescan"
                or "before" in (entry.get("tags") or ())
                or not member.get("folder")
                or os.path.normcase(str(Path(member["folder"]).resolve()))
                != here):
            continue
        created = _utc_time(entry.get("created"))
        if since is not None or until is not None:
            # Bounded, an entry that cannot say when it was filed cannot be
            # placed inside the walk, and is not taken to be.
            if (created is None or (since is not None and created < since)
                    or (until is not None and created > until)):
                continue
        try:
            number = int(member["number"])
        except (KeyError, TypeError, ValueError):
            continue
        # Ties by id, which starts with the time: the order the old join
        # sorted by.
        key = (created if created is not None else 0.0, record_path.parent)
        if number not in joined or key < joined[number]:
            joined[number] = key
    out.update({n: joined[n][1] for n in missing if n in joined})
    return out


def prescan_arrangement(manifest: dict, record: dict) -> tuple[int, bool]:
    """How a walk's `prescanNN.tif` was turned and mirrored when it was written.

    Its record's own pair where it has one, the manifest's otherwise -- a walk
    from before records carried one was turned by the file's one pair. One
    reading for the window's `read_survey` and the roll tool's `--approved`,
    which both have to un-turn the file into the film's own orientation before
    it is a reference; the tool used it as it lay on disk.
    """
    turn = record.get("prescan_rotation")
    turn = int(manifest.get("rotation") or 0) if turn is None else int(turn)
    mirrored = record.get("prescan_flipped")
    mirrored = (bool(manifest.get("flipped")) if mirrored is None
                else bool(mirrored))
    return turn % 360, mirrored


#: What a manifest's `settings` block calls a key, where the top level calls it
#: something else. Only `dpi` differs: `scan_roll` writes the scan resolution
#: under the name the driver uses, the window under the name it shows.
SETTING_ALIASES = {"resolution": "dpi"}


def manifest_settings(manifest: dict, progress: dict | None = None) -> dict:
    """One view of a roll's settings, whichever tool wrote it.

    The window writes the settings it restores at the **top level** of
    `survey.json` and again inside `settings`; `tools/scan_roll.py` writes them
    only inside `settings`, and calls the scan resolution `dpi`. So a walk made
    on the command line opened in the window with `prescan_resolution` reading
    `None` -- and that is not cosmetic. It becomes `_survey_predpi`, which is
    what pins a commissioned scan's prescan to the resolution its positions
    were decided at. Unpinned, the reference is resampled and
    `measure_shift` reads it at about half the confidence: 93.5 falls to
    47.4 against a floor of 55, so **every frame reads `unverified` and nothing
    moves**. A roll that costs hours, delivers no correction, and says nothing.

    Read side rather than write side deliberately. Fixing `scan_roll` would
    help folders that do not exist yet; the eleven already on disk --
    `registration-D` through `registration-M` -- are the evidence this whole
    feature was built on, and only the reader recovers them.

    Here rather than in the window since `tools/scan_roll.py --approved` pins
    its prescans with it too. It took `--prescan-dpi`, 300 unless told, and so
    held a walk prescanned at 600 dpi against 300 dpi passes -- the same
    unverified roll, from the other side.
    """
    out: dict = {}
    # Least specific first. A `settings` block is what the run was configured
    # with; the top level is what the window itself wrote and meant; a resumed
    # roll's progress file is more recent than the survey beside it.
    for layer in (manifest.get("settings"), manifest,
                  (progress or {}).get("settings"), progress or {}):
        for key, value in (layer or {}).items():
            if key != "settings" and value is not None:
                out[key] = value
    for name, alias in SETTING_ALIASES.items():
        if out.get(name) is None and out.get(alias) is not None:
            out[name] = out[alias]
    return out


def answering(receipt: Callable[[Any], None] | None,
              then: Callable[..., Any] | None = None
              ) -> Callable[..., Any] | None:
    """A writer job's `on_filed` that also answers a debug claim.

    ``receipt`` is what `DirectScanner.debug_claim` handed back: it is told
    the entry once `FrameWriter` has filed the pass, or None when it could
    not, and the spooled copy is let go or kept on that answer rather than on
    the claim. A pass in the library whose copy could not be written is
    filed: its spooled copy goes. ``then`` is the job's own `on_filed`,
    called after it with the same `Filing`.
    """
    if receipt is None:
        return then

    def told(filing: Filing) -> None:
        receipt(None if filing.failed else filing.entry)
        if then is not None:
            then(filing)

    return told


def queued(submit: Callable[..., Any], on_filed: Callable[..., Any] | None,
           /, **job: Any) -> None:
    """``submit(**job)``, with the promise ``on_filed`` makes kept with it --
    the job's own, before `answering` wraps it for a debug claim.

    A walk prescan's (`RollManifest.prescan_told`) tells its manifest that an
    amendment is coming, and the manifest is ahead of its file until it has.
    Promised when that answer was built -- before `_file` had checked, named
    or arranged anything -- a raise on the way left the promise standing for
    the rest of the session. So it is made here, as the job is queued, and
    taken back if queuing it raises.
    """
    manifest = getattr(on_filed, "manifest", None)
    if manifest is None:
        submit(**job)
        return
    manifest.expect()
    try:
        submit(**job)
    except BaseException:
        manifest.withdraw()
        raise


def bytes_are_another_pass(scanner: Any, raw_pixels: Any) -> bool:
    """Whether the bytes the scanner holds now belong to a later pass.

    `capture_record()` describes the scanner's **last** pass, and a picture
    carried on from before it -- a walk frame whose aim or hold failed after
    a verification prescan, filed with the prescan it arrived with -- is not
    that pass. `raw_bytes_disagree` cannot see it: both are prescans of the
    same frame at the same resolution, identically shaped. Identity can: the
    scanner's `last_pixels_raw` is the very array its last pass produced, so
    a picture whose raw pixels are some other array was not that pass.

    False where the scanner keeps no such array, which says nothing.
    """
    last = getattr(scanner, "last_pixels_raw", None)
    return raw_pixels is not None and last is not None and last is not raw_pixels


def roll_frame_label(roll: str, number: int) -> str:
    """The `film.frame` every roll entry carries: ``<roll>-<NN>``.

    One format for the window and `tools/scan_roll.py`, which wrote
    ``<roll>/<NN>`` and so could never be found from the window.
    """
    return f"{roll}-{int(number):02d}"


def roll_membership(roll: str, number: int, kind: str, folder: Any, *,
                    strip: int | None = None,
                    roll_number: int | None = None) -> dict[str, Any]:
    """What a roll entry records about its place: roll, frame, and which pass.

    ``kind`` is ``"frame"`` or ``"prescan"``. A walk's prescan and the frame
    later scanned there share a roll and a number, and joining on those alone
    let Export deliver a 300 dpi prescan as the frame at full resolution.
    A slide's RGBI pass, kept for its infrared beside the RGB pass that is
    the frame, is ``"infrared pass"`` for the same reason (`_file_pair`).

    ``number`` is the frame's place in ``folder`` -- on its strip -- which is
    what every reader compares with the folder's own records. For a strip of
    a roll of strips, ``strip`` and ``roll_number`` say which strip it is and
    the number the frame was given across the roll (`StripPlace.number`),
    which its label carries; neither is written for a roll from before
    strips, whose number is its place.
    """
    member = {"roll": str(roll), "number": int(number), "kind": kind,
              "folder": str(folder)}
    if strip is not None:
        member["strip"] = int(strip)
    if roll_number is not None:
        member["roll_number"] = int(roll_number)
    return member


#: The marks of a frame's `registration` that are readings of where it lies:
#: what the walk took of its prescan (`StripWalk.observe`), and the judgment
#: an aim made of it, with each second look its hold took (`_aim_frame`).
READING_MARKS = ("base", "correction")


def frame_read_by(registration: Any) -> dict[str, Any]:
    """``{"read_by": ...}`` for a roll or walk record of a frame whose marks
    hold a reading (`READING_MARKS`), and ``{}`` for one whose marks hold none.

    The code is named on the frame, as `approved.json` names it on each of
    its frames, and never once for the file. A roll resumed, a walk added
    to and a `tools/scan_roll.py` resume each carry the earlier runs' frame
    records forward unchanged under the settings the latest run writes: a
    name given once for the file said the latest run's code had made those
    readings, and named none where the latest run did not aim. A record is
    written by the run that read it, so the run's code is the reading's.
    Never raises: it is bookkeeping, and a frame is not lost to it.
    """
    try:
        if isinstance(registration, dict) and any(
                registration.get(key) is not None for key in READING_MARKS):
            return {"read_by": library.code_identity()}
    except Exception as exc:                                   # noqa: BLE001
        return {"read_by": {"record_error": f"{type(exc).__name__}: {exc}"}}
    return {}


def aiming_said(job: Any) -> bool | None:
    """What a roll's settings record for "aim each frame" (``correct``):
    the job's, or None -- not applicable -- for one that asked for no aim
    and held every frame it scans to a position (`Roll.approved`).

    A frame with a position is held to it and never aimed (`scan_roll`), so
    such a roll -- a commission from the contact sheet -- has nothing for
    the tick to say. Recorded as False, it was read back over its walk's
    True (`manifest_settings`): reopening a walk that had aimed each frame,
    once a commission from it had run, unticked "aim each frame". None is
    left out of that reading, and the walk's own answer stands (GUI2-10).
    """
    return aiming_of(job.correct, (a.number for a in job.approved or ()),
                     job.only)


def aiming_of(correct: Any, held: Iterable[int],
              scans: Iterable[int] | None) -> bool | None:
    """`aiming_said` from a roll's parts: ``correct`` as it was asked, the
    frames ``held`` to a position, and the frames it ``scans`` -- None for
    a roll to the end of the strip, whose frames only the strip knows. The
    one rule for the window's rolls and for `tools/scan_roll.py`, which
    builds no `Roll`: its ``--approved`` holds every walked frame, and
    recorded the tick False there for the window to read back over the
    walk's True, as the window's own commission once did."""
    held = {int(n) for n in held}
    scans = None if scans is None else {int(n) for n in scans}
    if correct or not held or not scans:
        return bool(correct)
    return None if scans <= held else False


def blanks_unreached(asked: Sequence[int] | None, blanks: Sequence[int],
                     ended_on: int | None) -> list[int]:
    """The frames ``asked`` for that blank places ended the roll before: past
    the blank that ended it, or the second of the two -- frames 11 and 12 of
    1-12 asked, where 9 and 10 had no picture. Nothing where blanks did not
    end it, or nothing was asked but the rest of the strip."""
    if not asked or ended_on is None:
        return []
    last = ended_on + 1 if ended_on + 1 in blanks else ended_on
    return [n for n in asked if n not in blanks and n > last]


def _ended_why(blanks: Sequence[int], ended_on: int) -> str:
    return (f"frames {ended_on} and {ended_on + 1} had no picture"
            if ended_on + 1 in blanks
            else f"frame {ended_on} had no picture and the film went no "
                 "further")


def _unreached_said(asked: Sequence[int] | None, blanks: Sequence[int],
                    ended_on: int | None) -> str:
    unreached = blanks_unreached(asked, blanks, ended_on)
    if not unreached:
        return ""
    return (f"; {'frames' if len(unreached) > 1 else 'frame'} "
            f"{number_spans(unreached)} not reached")


def ended_early(asked: Sequence[int] | None, blanks: Sequence[int],
                covered: int) -> bool:
    """Whether a roll the driver ended stopped short of the frames asked for:
    fewer taken than asked, the blank places it skipped aside. A run to the
    end of the strip that two blanks ended is no early end -- that is where
    the strip ends -- nor is one that skipped a blank and went on."""
    return bool(asked) and covered < len(asked or ()) - len(blanks)


def ended_early_said(covered: int, asked: Sequence[int],
                     blanks: Sequence[int], ended_on: int | None) -> str:
    """A roll's ``stopped`` where the driver ended it short of the frames
    asked for (`ended_early`): out of every frame the operator asked for,
    not that less the blank places -- "8 of the 10" hid that 11 and 12 of
    12 were never reached -- which are named apart (`blanks_unreached`).
    The one wording for the window's session and `tools/scan_roll.py`."""
    said = f"ended after {covered} of the {len(asked)} frames asked for"
    if ended_on is None:
        return said + " -- the log says why"
    said += f" -- {_ended_why(blanks, ended_on)}"
    if ended_on + 1 in blanks:
        said += ": the end of the film"
    return said + _unreached_said(asked, blanks, ended_on)


def blanks_said(blanks: Sequence[int], ended_on: int | None, covered: int,
                asked: Sequence[int] | None, dry: bool) -> str:
    """What a roll or walk says of the places it skipped as blank, once, as
    it ends: which, and whether they ended it -- "The roll ended at frame 9:
    frames 9 and 10 had no picture -- 8 of the 12 asked for were scanned;
    frames 11-12 not reached."."""
    did = "walked" if dry else "scanned"
    what = "walk" if dry else "roll"
    places = ", ".join(str(n) for n in blanks)
    skipped = (f"Frame{'s' if len(blanks) > 1 else ''} {places} had no "
               f"picture and {'were' if len(blanks) > 1 else 'was'} skipped, "
               "the prescan kept.")
    if ended_on is None:
        return skipped
    of = (f" -- {covered} of the {len(asked)} asked for were {did}"
          if asked else f" -- {covered} {did}")
    return (f"The {what} ended at frame {ended_on}: "
            f"{_ended_why(blanks, ended_on)}{of}"
            f"{_unreached_said(asked, blanks, ended_on)}.")


class StripAimed(NamedTuple):
    """How far, signed, the aims already sent left a strip, in hold units:
    a roll that aims it counts its own aims on from ``total``
    (`strip_aimed`)."""

    #: Its walk's aims, from survey.json.
    walk: float
    #: The aims of this roll's earlier runs, from roll.json.
    roll: float
    #: Aims that moved the film and kept no record of how far -- from
    #: before 2026-10 -- left out of both: the total is known only without
    #: them.
    unknown: int = 0

    @property
    def total(self) -> float:
        return self.walk + self.roll


def _aims_of(manifest: dict | None) -> tuple[float, int]:
    """The signed sum of a manifest's aims that can be told
    (`framing.aim_units`), and how many moved the film and cannot."""
    total, unknown = 0.0, 0
    for record in (manifest or {}).get("frames") or ():
        if not isinstance(record, dict):
            continue
        moved = aim_units((record.get("registration") or {}).get("correction"))
        if moved is None:
            unknown += 1
        else:
            total += moved
    return total, unknown


def walk_aimed_units(manifest: dict | None) -> float | None:
    """How far, signed, a walk's aims left its strip, in hold units: what a
    roll that aims the same strip counts its own aims on from
    (`framing.ROLL_NET_LIMIT_UNITS`, Stefan's "net-budget" decision: the
    roll's net, seeded from its walk). None where an aim moved the film and
    kept no record of how far: unknown, never 0.

    A sub-frame move does not touch the frame counter, and nothing undoes it
    -- it survives the whole-frame advances and the rewind -- so every aim
    the walk sent is still on the strip when a roll of it starts. Each
    frame's aim counts as `framing.aim_units` reads it, which is how the
    live walk counted it (`DirectScanner._aim_frame`): as its last look
    measured it (``final_units``) where one was believed, the signed sum of
    what it sent otherwise, the move in flight of a failed aim included --
    a failed aim whose only SLIDE raised mid-send is stored ``moved`` False,
    and moved the film. A dry run's would-send moved nothing. A record from
    before 2026-10-13 holds millimetres, read through `units_of`. 0 for a
    manifest that is none, or a walk that aimed nothing.
    """
    total, unknown = _aims_of(manifest)
    return None if unknown else total


def strip_aimed(walk: dict | None, roll: dict | None) -> StripAimed:
    """How far the aims already on a strip left it, for a run that aims it:
    its walk's (survey.json) and its roll's earlier runs' (roll.json,
    ``registration.correction``), each read as `walk_aimed_units` reads it.
    A roll resumed after a Stop or a failure counted on from the walk alone,
    and the first run's aims -- still on the strip -- let creep in one
    direction reach twice `framing.ROLL_NET_LIMIT_UNITS` before aiming
    stopped.

    The one seed for the window's session and `tools/scan_roll.py`, whose
    callers say which manifests are this strip's: a roll that aims gives
    both; a walk added to gives the walk it adds to and the roll beside it;
    a fresh walk gives neither. roll.json keeps only a frame's latest take,
    so a re-taken frame's earlier aim drops out of the count; and frames
    held to approved positions are not aims -- the held path has no budget.
    """
    walked, walk_unknown = _aims_of(walk)
    rolled, roll_unknown = _aims_of(roll)
    return StripAimed(walked, rolled, walk_unknown + roll_unknown)


def strip_aimed_said(aimed: StripAimed) -> str | None:
    """The one line a run that aims says of the aims already on its strip
    (`strip_aimed`), before the device opens; None where there are none.
    Where some cannot be told, the run counts on from the part that can --
    said, so a budget started low is not taken for a measured one."""
    parts = []
    if aimed.walk:
        parts.append(f"the walk of this strip moved it "
                     f"{say_distance(aimed.walk)}")
    if aimed.roll:
        parts.append(f"this roll's earlier runs {say_distance(aimed.roll)}")
    said = ""
    if parts:
        said = (" and ".join(parts) + " with their aims; this run counts on "
                "from there")
    if aimed.unknown:
        said += ("; " if said else "") + (
            f"{aimed.unknown} earlier aim{'s' if aimed.unknown > 1 else ''} "
            "on this strip moved it and kept no record of how far, so its "
            "aim total is unknown: this run counts on from the part that is "
            "known")
    return said or None


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

    The cap (`_cap`) governs making a folder, not finding one. A folder of
    exactly the name typed is found at any length: Duplicate's `<name>-2`
    and a restore beside a roll of the same name add two characters to a
    name already at the cap, and a folder made before the cap, renamed by
    hand or copied in can be longer still. Looked for under a cleaned and
    capped name, each was another folder, and finishing it from the window
    scanned every frame again into that one. A folder that cannot be looked
    at -- a name too long for Windows, or for POSIX, which raises -- is
    taken as no such folder, and the name made safe and capped.

    Nor is a dot name: the rolls' own `.trash` lives here (`library.TRASH`),
    and a roll typed ".trash" found it as a folder of that name, and would
    have scanned into the deleted rolls. Made safe, it is another name.

    A folder found by a name typed in another case -- ``gold200`` for
    ``Gold200``, on a disk that folds case -- is given in its own spelling
    (`as_on_disk`): the roll box, the label a new roll's frames carry and
    the key its sheet is filed under are the folder's, and two spellings of
    one folder were two rolls in the settings file (PLAT-06).
    """
    rolls = Path(rolls)
    name = (name or "").strip()
    as_typed = rolls / name
    if (name not in ("", ".", "..") and not name.startswith(".")
            and not any(c in name for c in "/\\:")
            and as_typed.parent == rolls and _is_dir(as_typed)):
        return as_on_disk(as_typed)
    cleaned = _safe(name, fallback="")
    return rolls / (cleaned or new_roll_name(rolls))


def as_on_disk(path: Any) -> Path:
    """``path`` in the spelling its folder has on disk, where a disk that
    folds case found it under another -- ``rolls/gold200`` for the folder
    ``Gold200`` -- and as given otherwise.

    By its parent's listing: the one name that is the same as ``path``'s
    once both are NFC and case-folded, and that `os.path.samestat` says is
    the same folder. Only where the disk gives both a file id: a volume
    that reports 0 for every one -- some network and FAT disks -- would
    have every name match. Never raises: anything that cannot be looked
    at leaves ``path`` as it is."""
    path = Path(path)
    name = path.name
    try:
        here = os.stat(path)
    except (OSError, ValueError):
        return path
    if not here.st_ino:
        return path
    wanted = unicodedata.normalize("NFC", name).casefold()
    try:
        with os.scandir(path.parent) as listing:
            for entry in listing:
                if entry.name == name:
                    return path
                if unicodedata.normalize("NFC", entry.name).casefold() != wanted:
                    continue
                try:
                    there = os.stat(entry.path)
                except OSError:
                    continue
                if there.st_ino and os.path.samestat(here, there):
                    return path.with_name(entry.name)
    except OSError:
        return path
    return path


def _resolved(path: Path) -> Path:
    """``path`` resolved, as the session keys its manifests; as given where
    it cannot be."""
    try:
        return path.resolve()
    except OSError:
        return path


def folder_in_use(folder: Any, survey: dict | None = None) -> list[str]:
    """What in ``folder`` a fresh walk would mix with or write over, each as
    a phrase for a question; empty where a walk may go in (P18-a).

    A walk wrote its survey.json over another strip's and left the rest:
    that strip's approved.json held the new strip's frames to its own
    positions, its roll.json put its done frames and holds on the new
    numbers, and a walk from before prescans were filed lost the only copy
    of each picture it walked again. So the folder is free only where
    everything in it belongs to a walk whose prescans are all in the
    library -- its survey.json, its roll.json and approved.json with no
    frames, each one's copy beside it, the prescans its records name with
    their entries, and dot names -- and is used for anything else:

    - a Duplicate cut short (`library.INCOMPLETE`);
    - frames in roll.json or approved.json;
    - a record that cannot be read, which is never taken for none;
    - a prescan the walk names with no entry, or with None for it;
    - any other file.

    ``survey`` is the walk as a session holds it, where that is ahead of
    the file. A directory listing and three small reads, no library glob:
    asked on the window's thread, and on the worker's before the seek.
    """
    folder = Path(folder)
    try:
        names = [p.name for p in folder.iterdir()]
    except FileNotFoundError:
        return []
    except OSError as exc:
        return [f"what cannot be looked at ({exc})"]
    said: list[str] = []
    if library.INCOMPLETE in names:
        said.append("a Duplicate cut short")
    allowed = {"survey.json", "roll.json", "approved.json",
               *(f"{n}{PREVIOUS}" for n in ("survey.json", "roll.json",
                                             "approved.json"))}
    walk = survey
    if walk is None:
        try:
            walk = read_manifest(folder / "survey.json")
        except ValueError:
            said.append("a survey.json that cannot be read")
            walk = {}
    for name in ("roll.json", "approved.json"):
        try:
            data = read_manifest(folder / name)
        except ValueError:
            said.append(f"a {name} that cannot be read")
            continue
        n = len([r for r in data.get("frames") or () if isinstance(r, dict)])
        if n:
            s = "" if n == 1 else "s"
            said.append(f"{n} scanned frame{s} (roll.json)"
                        if name == "roll.json" else
                        f"positions and turns for {n} frame{s} (approved.json)")
    only = 0
    for record in walk.get("frames") or ():
        if not isinstance(record, dict):
            continue
        for key in ("prescan", "prescan_before"):
            named = record.get(key)
            if not named:
                continue
            if record.get(f"{key}_entry"):
                allowed.add(str(named))
            else:
                only += 1
    if only:
        said.append(f"{only} prescan{'s' if only != 1 else ''} that "
                    f"exist{'s' if only == 1 else ''} nowhere else")
    others = [n for n in names if not n.startswith(".") and n not in allowed
              and n != library.INCOMPLETE]
    if others:
        said.append(f"{len(others)} other file{'s' if len(others) != 1 else ''}")
    return said


def walk_dir(rolls: Any, name: str) -> tuple[Path, list[str]]:
    """The folder a fresh walk named ``name`` goes into under ``rolls``:
    `roll_dir`'s, unless that holds what it would mix with or write over
    (`folder_in_use`) -- then the first ``<name>-2``, ``-3`` ... not there
    yet. Returns the folder, and what pushed it on (empty where nothing
    did). For `tools/scan_roll.py --dry-run`, which walks into a folder of
    its own, as a roll from before strips."""
    base = roll_dir(rolls, name)
    used = folder_in_use(base)
    if not used:
        return base, []
    for n in range(2, 1000):
        candidate = base.with_name(f"{base.name}-{n}")
        if not os.path.lexists(candidate):
            return candidate, used
    raise FileExistsError(f"no free name beside {base.name} in {rolls}")


def _is_dir(path: Path) -> bool:
    """Whether ``path`` is a folder, and False where that cannot be told."""
    try:
        return path.is_dir()
    except OSError:
        return False


def recorded_roll_name(folder) -> str | None:
    """The name a roll folder's own manifest gives its roll, if it has one.

    A frame's library label is ``<roll>-<NN>`` (`roll_frame_label`), and a
    roll is found from its entries by that roll -- so a roll added to must go
    on calling itself what it did, whatever its folder is called now. A
    duplicate or a renamed folder keeps the name it was scanned under.

    A roll of strips names itself in its index, and each of its strips by
    that: a new strip's folder has no records yet, and it fell back on its
    folder's name -- every frame of strip 2 would have been ``strip-02-NN``.
    """
    roll = roll_of(folder)
    if roll is not None:
        try:
            name = read_index(roll).get("roll")
        except ValueError:
            name = None
        if isinstance(name, str) and name.strip():
            return name
        if Path(folder) == roll:
            return None
    for manifest in ("survey.json", "roll.json"):
        try:
            name = read_manifest(Path(folder) / manifest).get("roll")
        except ValueError:
            continue
        if isinstance(name, str) and name.strip():
            return name
    return None


def recorded_typed_name(folder, roll: str) -> str | None:
    """The name a roll folder's manifests say ``roll`` was typed as, where
    that was another (`typed_name`), or None.

    For a run that names no roll -- a sheet's, a resume, a walk added to --
    which writes its manifest afresh, and so dropped the name as typed that
    an earlier run had recorded. Only from a manifest of the same roll, so a
    folder reused under another name does not lend its own. A roll of
    strips' index first, which keeps it for every strip.
    """
    owner = roll_of(folder)
    if owner is not None:
        try:
            index = read_index(owner)
        except ValueError:
            index = {}
        typed = index.get("typed_name")
        if (index.get("roll") == roll and isinstance(typed, str)
                and typed.strip() and typed != roll):
            return typed
    for manifest in ("survey.json", "roll.json"):
        try:
            data = read_manifest(Path(folder) / manifest)
        except ValueError:
            continue
        typed = data.get("typed_name")
        if (data.get("roll") == roll and isinstance(typed, str)
                and typed.strip() and typed != roll):
            return typed
    return None


# ---------------------------------------------------------------------------
# A roll of strips: one film, its strips walked in order, numbered across it
# ---------------------------------------------------------------------------
#
# Stefan's call, 2026-10-10. A 36-frame film is cut into strips of six,
# loaded one by one in order, and named once. Its folder holds one folder
# per strip (`library.STRIP_FOLDER`), each exactly what a roll folder was
# before -- its walk, its sheet's decisions, its frames, numbered inside it
# by their place on the strip -- and the roll's own index of the frame
# numbers each strip was given across the roll (`library.STRIPS_INDEX`):
# strip k's first is one past the frames the strips before it really had.
#
# Numbers never move once given. A strip is given its first number when it
# is claimed -- by the run that first walks or scans it, after the seek,
# under the roll's lock (`claim`) -- and its last when the next is claimed,
# closing it at the frames it really had. A frame found later past a closed
# strip's range is given the number after the roll's last (an extra). Each
# number is kept in three places: the index, each strip's records
# (``roll_number``), and each entry's label and delivered name. Nothing
# rewrites a label, a delivered name or a record's number.
#
# A folder without an index is a roll from before strips -- all 103 of them
# on 2026-10-10 -- and every path here reads it as it always was: its number
# is its place, and its label ``<folder>-NN``.

#: A film's frames, six strips of six: once this many numbers are given, the
#: window asks whether the next strip is from another film. A warning only.
FULL_ROLL = 36

#: What a roll of strips' index says it is.
STRIPS_KIND = "rps7200 roll of strips"


class StripMoved(ValueError):
    """The strip a walk was asked for is no longer the roll's next: another
    program claimed one, or the roll changed, between the question and the
    job. Raised before anything moves."""


class StripFolderUsed(ValueError):
    """The folder the next strip would have holds files its roll's index
    does not list -- copied in by hand, or left by a claim cut short."""


class NoSecondStrip(ValueError):
    """A roll from before strips takes no second strip: its records are at
    its top, numbered by place, and a strip beside them would mix two."""


class RollIsStrips(ValueError):
    """A roll of strips has no records of its own at its top: a walk or a
    roll goes into one of its strips."""


class RollFolderInUse(ValueError):
    """A fresh walk into a folder that holds what a walk would mix with or
    write over (`folder_in_use`), or a new strip into a folder that is no
    roll at all."""


class StripOutOfItsRoll(ValueError):
    """A strip's folder lying outside its roll -- copied, or carried, to
    where no index numbers it. Its records name the roll's label, so read
    as a roll from before strips its frames were labelled by their place:
    strip 2's second frame as the roll's frame 2."""


def strip_out_of_its_roll(folder: Any) -> str | None:
    """Why ``folder`` numbers nothing, where it is a strip outside its roll
    (`StripOutOfItsRoll`): its records carry the copy of a claim
    (`library.strip_block`) and it is no strip of a roll of strips here.
    None for any other folder."""
    folder = Path(folder)
    block = library.strip_block(folder)
    if block is None or library.lock_folder(folder) != folder:
        return None
    try:
        label = read_manifest(folder / "survey.json").get("roll")
    except ValueError:
        label = None
    if not isinstance(label, str) or not label:
        try:
            label = read_manifest(folder / "roll.json").get("roll")
        except ValueError:
            label = None
    of = f" of {label}" if isinstance(label, str) and label else ""
    return (f"{folder} is strip {block['number']}{of} outside its roll, where "
            "no roll's index numbers its frames: labelled here, they would "
            "take the numbers of the roll's first strip. Scan it in its "
            "roll. Nothing was moved.")


@dataclass(frozen=True)
class StripCount:
    """How many frames a strip had, by place, and how that is known.

    ``count`` is the highest place any record of it names -- a walk that
    started at frame 2 still counts frame 1, and a place a counter jump left
    unrecorded is counted -- raised to where a walk found the strip's end,
    or the place "Strip ends here" set (``ends_at``). ``complete`` is
    whether the strip's end was seen, or set: the next strip is offered
    then, and adding to this one otherwise. ``how`` says which, in words,
    its frame by its place: the record, kept in the roll's index as
    ``count_how``. A line for people says it with `said`.
    """

    strip: int
    first: int
    count: int
    complete: bool
    how: str
    #: ``how`` with its frame left out as ``{frame}``, for `said` to say it
    #: again; "" where ``how`` names no frame.
    form: str = ""

    @property
    def last(self) -> int:
        """The number its last frame has: ``first - 1`` for none."""
        return self.first + self.count - 1

    def said(self, strip: "StripPlace | None" = None) -> str:
        """``how`` for people, its frame said as every line says one
        (`place_said`): ``walk stopped after frame 9 (strip 2 · 3)`` on a
        strip numbered from 7, where ``how`` says "frame 3" -- a place,
        beside a roll that files that picture as frame 9. ``strip`` is its
        place in its roll, for an extra or the stub past its end; without
        it the count's own first number does, which is how an open strip
        numbers every frame."""
        if not self.form:
            return self.how
        frame = (place_said(strip, self.count) if strip is not None
                 else frame_said(self.first + self.count - 1,
                                 self.strip or None, self.count))
        return self.form.replace("{frame}", frame)

    def numbers(self) -> str:
        """Its numbers as said: ``13-18``, ``13``, or ``none``."""
        if self.count <= 0:
            return "none"
        if self.count == 1:
            return str(self.first)
        return f"{self.first}-{self.last}"


@dataclass(frozen=True)
class StripPlan:
    """The strip a walk or a roll would claim (`plan_next`): its roll, the
    label every frame carries, its number and folder, its first frame
    number, whether it is the roll's last strip taken again because nothing
    was ever recorded under it (``reuse``), the open strip the claim closes,
    and what to warn about first."""

    roll_folder: Path
    label: str
    number: int
    folder: Path
    first: int
    reuse: bool = False
    closes: StripCount | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class StripOffer:
    """What `plan_next` offers: the next strip, the last one where its walk
    did not reach its end (``again``, which the window offers to add to
    first), and one line saying both."""

    next: StripPlan
    again: StripCount | None
    said: str


@dataclass
class StripPlace:
    """A strip's place in its roll, for numbering its frames: its first
    number, its last once it is closed, where "Strip ends here" put its end,
    and each extra number a place past its range was given (by place).
    ``unsaved`` is why the last extra could not be written into the index,
    while it could not: the run stops after that frame.

    ``unindexed`` are the places among ``extra`` whose number only their
    record kept -- the index write refused as it was given (`place_of`):
    written in as the place is numbered again, and never given a second
    number. ``given_high`` is the highest number the roll had given, read
    under the roll's lock before the seek (`prime`): what an extra is
    numbered after where the roll's records cannot be read between passes."""

    roll_folder: Path
    label: str
    strip: int
    folder: Path
    first: int
    last: int | None = None
    ends_at: int | None = None
    extra: dict[int, int] = field(default_factory=dict)
    live: Callable[[Path], Any] | None = None
    unsaved: str | None = None
    unindexed: set[int] = field(default_factory=set)
    given_high: int | None = None

    def numbers(self) -> str:
        """Its range as said: ``13-18`` closed, ``from 19`` open."""
        if self.last is None:
            return f"from {self.first}"
        return (str(self.first) if self.last == self.first
                else f"{self.first}-{self.last}")

    def within(self, place: int) -> bool:
        """Whether ``place`` lies inside the numbers this strip was given."""
        return self.last is None or place <= self.last - self.first + 1

    def past_its_end(self, place: int) -> bool:
        """Whether ``place`` lies past where "Strip ends here" put this
        strip's end: the stub, no frame of the film."""
        return self.ends_at is not None and int(place) > self.ends_at

    def takes_extra(self, place: int, *, scan: bool = False) -> bool:
        """Whether ``place`` is numbered after the roll's last rather than
        by its place: past a closed range, and not the stub past "Strip ends
        here" -- unless a pass is scanned there (``scan``) on a closed strip,
        which says the stub was a frame of the film after all."""
        place = int(place)
        if self.within(place):
            return False
        if not self.past_its_end(place):
            return True
        return scan and self.last is not None

    def could_give(self, start_at: int, frames: int | None, only: Any, *,
                   scan: bool, rest_from: int | None = None) -> bool:
        """Whether a run from ``start_at`` -- ``frames`` of them, ``only``
        those, and the rest of the strip from ``rest_from`` after them --
        can reach a place that takes a number not given yet."""
        if self.last is None:
            return False
        asked = frames_asked(max(1, int(start_at)), frames, only)
        if asked is None or rest_from is not None:
            # To the end of the strip: past its range, wherever that is --
            # all of it the stub, for a walk of a strip that ends early.
            return self.ends_at is None or scan
        return any(self.takes_extra(p, scan=scan) and p not in self.extra
                   for p in asked)

    def prime(self) -> None:
        """Read the roll's highest number (`highest_given`) into
        ``given_high``: under the roll's lock and before the seek, where a
        refusal moves nothing. Raises ValueError where it cannot be read."""
        self.given_high = highest_given(self.roll_folder, live=self.live)

    def number(self, place: int, *, say: Callable[[str], Any] | None = None,
               scan: bool = False) -> int:
        """The number frame ``place`` of this strip has across its roll:
        ``first + place - 1`` inside its range, and past a closed range the
        number after the roll's last, given once and kept -- in the index,
        and in the record that carries it, before the frame is filed.

        Not for a walk of a place past "Strip ends here", open or closed:
        the stub is no frame of the film, and an extra given to it was a
        number used up by a picture nobody wants, the next strip numbered
        after it and a gap in the roll. Its walk prescan keeps the number its
        place has, which the next strip's first frame shares, as the prescans
        walked before the trim did. A scan of it is refused on an open strip
        (`past_strip_end`); on a closed one (``scan``) the end was set too
        early, and the frame takes an extra like any place past the range.

        Giving an extra closes the roll's open last strip, where that is
        another, at the frames it had -- or numbers it on past the extra,
        where it recorded none -- in the same index write: left open, it
        numbered its next frame by its place, which the extra already was.

        Called on the scanning thread between passes, with the roll's lock
        held since before the seek: one small JSON write, as each frame's
        record is. A write the disk refuses keeps the number all the same
        (``unsaved``), and it is never given again, because the records are
        counted too (`highest_given`). Records that cannot be read here are
        numbered after the highest read before the seek (``given_high``),
        exact while the lock is held, and the run stops after this frame;
        with none read, the ValueError is raised."""
        place = int(place)
        if place in self.extra and not self.within(place):
            # Given once, kept: a stub scanned by name and walked again
            # after keeps its extra.
            given = self.extra[place]
            if place in self.unindexed:
                # Given before, its index write refused: written in now,
                # never given again.
                self._index_extra(place, given, say=say, origin="record")
            return given
        if not self.takes_extra(place, scan=scan):
            return self.first + place - 1
        unread = None
        try:
            high = highest_given(self.roll_folder, live=self.live)
        except ValueError as exc:
            if self.given_high is None:
                raise
            high = self.given_high
            unread = (f"{library.STRIPS_INDEX} or a strip's record could not "
                      f"be read: {exc}")
        given = max(high, self.given_high or 0, *self.extra.values()) + 1
        self.extra[place] = given
        self._index_extra(place, given, say=say, unread=unread)
        return given

    def _index_extra(self, place: int, given: int, *,
                     say: Callable[[str], Any] | None,
                     origin: str | None = None,
                     unread: str | None = None) -> None:
        """Write the extra ``given`` to ``place`` into the roll's index, the
        open last strip closed or numbered on beside it (`_past_extra`), and
        say it; ``unsaved`` -- the run stops after this frame -- where the
        index would not take it, or ``unread`` says the records could not
        be read."""
        moved = None
        try:
            index = read_index(self.roll_folder)
            moved = _past_extra(index, self.roll_folder, self.live,
                                self.strip, given)
            if not any(isinstance(e, dict) and e.get("strip") == self.strip
                       and e.get("place") == place
                       for e in index.get("extra") or ()):
                index.setdefault("extra", []).append({
                    "strip": self.strip, "place": place, "number": given,
                    "given": _utc_now(),
                    **({"from": origin} if origin else {})})
            write_index(self.roll_folder, index)
            self.unindexed.discard(place)
            self.unsaved = unread
        except OSError as exc:
            moved = None
            self.unsaved = f"{library.STRIPS_INDEX} could not be written: {exc}"
        except ValueError as exc:
            moved = None
            self.unsaved = (f"{library.STRIPS_INDEX} could not be written, "
                            f"what it needs not being readable: {exc}")
        if say is None:
            return
        if origin == "record":
            said = (f"frame {place} of strip {self.strip} keeps {given}, the "
                    "number its record carries")
            said += (f"; it is written into {library.STRIPS_INDEX} now"
                     if self.unsaved is None else "")
        else:
            said = (f"frame {place} of strip {self.strip} lies past its "
                    f"numbers, {self.numbers()}, so it is numbered {given}")
        if self.unsaved:
            said += f" -- {self.unsaved}; the number is kept in its record"
        if moved:
            said += f"; {moved}"
        say(said)

    def settle(self, say: Callable[[str], Any] | None = None) -> None:
        """Before an open strip numbers any frame: where a number at or past
        its first was given elsewhere -- an extra whose index write was
        refused, which left this strip open -- close it at the frames it
        had, or number it on past that number where it recorded none, as an
        extra given now would have (`_past_extra`). Under the roll's lock
        and before the seek; raises ValueError where the roll cannot be read
        or the index would not take it, so nothing moves."""
        if self.last is not None:
            return
        others = highest_given(self.roll_folder, live=self.live,
                               leave_out=self.strip)
        if others < self.first:
            return
        index = read_index(self.roll_folder)
        listed = listed_strips(index)
        if not listed or listed[-1].get("strip") != self.strip:
            return
        moved = _past_extra(index, self.roll_folder, self.live, None, others)
        if moved is None:
            return
        try:
            write_index(self.roll_folder, index)
        except OSError as exc:
            raise ValueError(
                f"{self.roll_folder.name}'s number {others} lies inside strip "
                f"{self.strip}'s, {self.numbers()}, and "
                f"{library.STRIPS_INDEX} could not be written to move it "
                f"({exc})") from exc
        entry = listed[-1]
        if isinstance(entry.get("last"), int):
            self.last = int(entry["last"])
        self.first = int(entry.get("first") or self.first)
        if say is not None:
            say(f"{moved} -- {others} was given elsewhere while it was open")


class RollPlace(NamedTuple):
    """Where a roll or a walk files, derived before the seek
    (`ScanSession._roll_place`): its folder, the name its entries carry,
    its strip's place in a roll of strips -- None for a roll from before
    strips, and for a new strip, claimed after the seek -- and the plan of
    that new strip."""

    out: Path
    name: str
    strip: StripPlace | None = None
    plan: StripPlan | None = None


def read_index(roll: Any, say: Callable[[str], Any] | None = None) -> dict:
    """A roll of strips' own record (`library.STRIPS_INDEX`): ``{}`` where
    it has none. Raises ValueError, naming it, where neither it nor the copy
    kept beside it can be read (`read_manifest`) -- a roll whose numbers
    cannot be read numbers nothing, and never from its records alone."""
    path = Path(roll) / library.STRIPS_INDEX
    kept = path.with_name(path.name + PREVIOUS)
    if not path.exists() and kept.exists():
        # Only the copy left, a rename cut short: read rather than taken for
        # a roll with no strips, which would number strip 1 from 1 again.
        try:
            data = json.loads(kept.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"{path} is not there, and {kept.name} cannot "
                             f"be read: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{path} is not there, and {kept.name} is no "
                             "JSON object")
        if say is not None:
            say(f"{path} is not there; read {kept.name}, the version before "
                "it, instead")
        return data
    return read_manifest(path, say=say)


def write_index(roll: Any, data: dict) -> None:
    """Write a roll of strips' index, keeping the version before it beside
    it (`write_manifest`). Only under the roll's `locks.ROLL`: the session's
    claim and extras, held since before the seek; the window's "Strip ends
    here" and strip Delete; `tools/scan_roll.py`'s claim."""
    write_manifest(Path(roll) / library.STRIPS_INDEX, data, keep_previous=True)


def listed_strips(index: dict) -> list[dict]:
    """The strips an index lists, by number."""
    out = []
    for strip in index.get("strips") or ():
        if isinstance(strip, dict) and isinstance(strip.get("strip"), int):
            out.append(strip)
    return sorted(out, key=lambda s: int(s["strip"]))


def roll_of(folder: Any) -> Path | None:
    """The roll of strips ``folder`` is, or is a strip of; None for any
    other folder -- a roll from before strips among them."""
    folder = Path(folder)
    if library.roll_kind(folder) == "strips":
        return folder
    owner = library.lock_folder(folder)
    return owner if owner != folder else None


def roll_name_of(folder: Any) -> str:
    """The roll a folder belongs to, by its folder's name, as the roll box
    shows it: ``Gold200`` for its strip-02, the folder's own name for a
    roll from before strips."""
    owner = roll_of(folder)
    return (owner if owner is not None else Path(folder)).name


def roll_key(folder: Any) -> str:
    """A folder's name as the settings file keys what was decided on it:
    ``Gold200/strip-02`` for a strip, so strip-02 of two rolls never share
    one, and the bare name for a roll from before strips -- none of whose
    73 keys has a "/" in it, so each is found as it always was."""
    folder = Path(folder)
    owner = roll_of(folder)
    if owner is None or owner == folder:
        return folder.name
    return f"{owner.name}/{folder.name}"


def frame_said(roll_number: int, strip: int | None, place: int,
               of: str | None = None) -> str:
    """One frame as every line says it: ``frame 8`` where its number across
    the roll is its place -- strip 1, a roll from before strips -- and
    ``frame 8 (strip 2 · 2)`` otherwise, the number first and its place on
    the strip beside it. Two numberings are on screen: the transport and
    the files in a strip count places, labels and deliveries the roll.
    ``of`` names the roll: ``frame 8 of Gold200 (strip 2 · 2)``."""
    roll_number, place = int(roll_number), int(place)
    named = f"frame {roll_number}" + (f" of {of}" if of else "")
    if strip is None or roll_number == place:
        return named
    return f"{named} (strip {int(strip)} · {place})"


# -- places on a strip, said ------------------------------------------------
#
# Here rather than in the window, which had them alone: the session's own
# lines -- the seek, a roll's stop, the strip's end -- said places as "frame
# 3" beside a roll that files that picture as frame 9, and a copy of these
# that left out a closed strip's extras would number one wrongly. The window
# imports them from here.


def shown_number(strip: StripPlace | None, place: int, given: int = 0, *,
                 scan: bool = False) -> int | None:
    """The number frame ``place`` of a strip has across its roll, as the
    sheet shows it: the one its record carries (``given``), else its
    place's inside the strip's range or past "Strip ends here", else an
    extra it was given. None for a place past a closed range not numbered
    yet: it is given the number after the roll's last when it is scanned
    (`StripPlace.number`) -- as is one past "Strip ends here" on a closed
    strip, for a scan (``scan``). The place itself for a roll from before
    strips. Never gives a number: shown only."""
    place = int(place)
    if given:
        return int(given)
    if strip is None:
        return place
    if place in strip.extra and not strip.within(place):
        return strip.extra[place]
    if not strip.takes_extra(place, scan=scan):
        return strip.first + place - 1
    return None


def _and_joined(items) -> str:
    """``2``, ``2 and 4``, ``2, 3 and 4``."""
    items = [str(i) for i in items]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _stub_split(strip: StripPlace | None, places, scan: bool = False
                ) -> tuple[list[int], str]:
    """``places`` less the stub past "Strip ends here", and the stub said by
    its place: ``frame 6 of strip 3, past its end``. Numbered as the rest
    are, it took the number the next strip's first frame then had -- "frames
    10-15 ... on it already" of a strip that ends at 14 (the 2026-10-12
    tour). A scan of it is said as the scan numbers it (``scan``)."""
    places = [int(p) for p in places]
    if strip is None or scan:
        return places, ""
    stub = [p for p in places if strip.past_its_end(p)]
    if not stub:
        return places, ""
    return ([p for p in places if p not in stub],
            f"{'frame' if len(stub) == 1 else 'frames'} {_and_joined(stub)} "
            f"of strip {strip.strip}, past its end")


def places_said(strip: StripPlace | None, places, *, scan: bool = False
                ) -> str:
    """Frames of a strip as a question names them: by their numbers across
    the roll, their places on the strip beside them where the two differ --
    ``8, 10 (frames 2 and 4 of strip 2)`` -- and as they always were for a
    roll from before strips and for strip 1: ``2, 4``. Two numberings are on
    screen, and the transport and the files in a strip count places. For a
    scan (``scan``), as the scan will number them (`shown_number`)."""
    places, stub = _stub_split(strip, places, scan)
    if stub:
        return (f"{places_said(strip, places)} and {stub}" if places
                else stub)
    numbers = [shown_number(strip, p, scan=scan) for p in places]
    if strip is None or all(n == p for n, p in zip(numbers, places)):
        return ", ".join(str(p) for p in places)
    said = ", ".join("?" if n is None else str(n) for n in numbers)
    said += (f" ({'frame' if len(places) == 1 else 'frames'} "
             f"{_and_joined(places)} of strip {strip.strip})")
    if None in numbers:
        said += (" -- ? lies past the strip's numbers, and is numbered after "
                 "the roll's last as it is scanned")
    return said


def number_spans(numbers) -> str:
    """Frame numbers as runs, ``1-10, 12``, so a sheet of 36 fits a sentence."""
    runs: list[list[int]] = []
    for n in sorted({int(n) for n in numbers}):
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in runs)


def spans_said(strip: StripPlace | None, places) -> str:
    """Frames of a strip as runs, ``4-6, 8-9`` (`number_spans`), by their
    numbers across the roll with their places on the strip beside them
    where the two differ -- ``10-12 (frames 4-6 of strip 2)`` -- as
    `places_said` names them one by one, which it falls back to where a
    place has no number yet."""
    places, stub = _stub_split(strip, sorted({int(p) for p in places}))
    if stub:
        return (f"{spans_said(strip, places)} and {stub}" if places
                else stub)
    numbers = [shown_number(strip, p) for p in places]
    if strip is None or all(n == p for n, p in zip(numbers, places)):
        return number_spans(places)
    if None in numbers:
        return places_said(strip, places)
    return (f"{number_spans(numbers)} "
            f"({'frame' if len(places) == 1 else 'frames'} "
            f"{number_spans(places)} of strip {strip.strip})")


def place_said(strip: StripPlace | None, place: int) -> str:
    """One place on a strip as every line says a frame (`frame_said`):
    ``frame 10 (strip 2 · 4)``, ``frame 4`` where the two numbers agree,
    and ``frame ? (strip 2 · 10)`` where the place has no number yet --
    one past a closed strip's numbers, given the number after the roll's
    last as it is scanned. "First frame" and the transport count places;
    the browser, labels and files across the roll count numbers."""
    place = int(place)
    if strip is None:
        return f"frame {place}"
    if strip.past_its_end(place):
        return f"frame {place} of strip {strip.strip}, past its end"
    number = shown_number(strip, place)
    if number is None:
        return f"frame ? (strip {strip.strip} · {place})"
    return frame_said(number, strip.strip, place)


def planned_place(plan: StripPlan | None) -> StripPlace | None:
    """The strip ``plan`` claims, as a place to say its frames by before it
    is claimed: open, numbered from the first its plan gives it -- which
    the claim checks again under the roll's lock (`StripMoved`). None for
    no plan. For words only: nothing is numbered by it."""
    if plan is None:
        return None
    return StripPlace(plan.roll_folder, plan.label, plan.number, plan.folder,
                      plan.first)


def place_of(folder: Any, say: Callable[[str], Any] | None = None,
             strict: bool = True, live: Callable[[Path], Any] | None = None
             ) -> StripPlace | None:
    """The place of the strip in ``folder`` in its roll, for numbering its
    frames; None for a folder that is no strip of a roll of strips.

    Raises ValueError where the roll's index cannot be read or does not
    list the strip -- unless not ``strict``, for showing it only: then the
    copy of its claim its own records carry stands in (`library.
    strip_block`), and nothing may be numbered by it.

    Strict, a closed strip's extras are its records' as well as the
    index's: a number given past its range whose index write was refused
    is kept in the record alone (`StripPlace.number`), and read from the
    index only, the frame taken again was given a second number. Those
    places are ``unindexed``. Raises ValueError where its records cannot
    be read: nothing is numbered from a guess."""
    folder = Path(folder)
    number = library.strip_number(folder.name)
    if number is None:
        return None
    roll = folder.parent
    if library.roll_kind(roll) != "strips":
        return None
    try:
        index = read_index(roll, say=say)
        listed = next((s for s in listed_strips(index)
                       if str(s.get("folder") or "").casefold()
                       == folder.name.casefold()), None)
        if listed is None:
            raise ValueError(f"{roll / library.STRIPS_INDEX} does not list "
                             f"{folder.name}, so it numbers nothing")
    except ValueError:
        if strict:
            raise
        block = library.strip_block(folder) or {}
        first = block.get("first")
        return StripPlace(roll, roll.name, int(block.get("number") or number),
                          folder, first if isinstance(first, int) else 1,
                          live=live)
    label = index.get("roll")
    extra = {int(e["place"]): int(e["number"])
             for e in index.get("extra") or ()
             if isinstance(e, dict) and e.get("strip") == listed["strip"]
             and isinstance(e.get("place"), int)
             and isinstance(e.get("number"), int)}
    last, ends = listed.get("last"), listed.get("ends_at")
    place = StripPlace(
        roll, label if isinstance(label, str) and label else roll.name,
        int(listed["strip"]), folder, int(listed.get("first") or 1),
        last if isinstance(last, int) else None,
        ends if isinstance(ends, int) else None, extra, live=live)
    if strict and place.last is not None:
        for at, number in _record_extras(place, live).items():
            if at not in place.extra:
                place.extra[at] = number
                place.unindexed.add(at)
    return place


def _record_extras(strip: StripPlace, live: Callable[[Path], Any] | None
                   ) -> dict[int, int]:
    """The numbers a closed strip's records carry past its range, by place:
    roll.json's over survey.json's, as the window reads the two. Not the
    stub past "Strip ends here" walked under the number its place has. Raises
    ValueError, naming it, where a record cannot be read."""
    found: dict[int, int] = {}
    for name in ("survey.json", "roll.json"):
        data = _manifest_now(strip.folder / name, live)
        for record in data.get("frames") or ():
            if not isinstance(record, dict):
                continue
            given = record.get("roll_number")
            try:
                at = int(record.get("number"))
            except (TypeError, ValueError):
                continue
            if (not isinstance(given, int) or isinstance(given, bool)
                    or given <= 0 or strip.within(at)):
                continue
            if strip.past_its_end(at) and given == strip.first + at - 1:
                continue
            found[at] = given
    return found


def _manifest_now(path: Path, live: Callable[[Path], Any] | None
                  ) -> dict:
    """A strip's survey.json or roll.json as it stands: through the live
    manifest a session writes it with where that is ahead of the file,
    and read otherwise. Raises ValueError where it cannot be read."""
    manifest = live(path) if live is not None else None
    if manifest is not None:
        snapshot = manifest.snapshot()
        if snapshot is not None:
            return snapshot
    return read_manifest(path)


def _places(data: dict) -> list[int]:
    """The places a strip manifest's records name. Not a blank one's
    (`RollFrame.blank`): a place with no picture is no frame of the strip,
    and two of them in a row are where it ends."""
    return _numbers(r for r in data.get("frames") or ()
                    if isinstance(r, dict) and not r.get("blank"))


def blank_numbers(manifest: dict) -> set[int]:
    """The places a manifest recorded as blank (`RollFrame.blank`): settled
    unless chosen again -- not frames the roll wants, not done (Stefan's
    "blank-resume" answer adopted)."""
    return set(_numbers(r for r in manifest.get("frames") or ()
                        if isinstance(r, dict) and r.get("blank")))


def blanks_chosen(only: Sequence[int] | None,
                  *manifests: dict | None) -> set[int]:
    """The places in ``only`` recorded blank by the latest of ``manifests``
    that names them -- given latest first, a roll's then its walk's, as both
    callers pass them: chosen again knowing they had read blank, a tick on
    the sheet or ``--only``, and so taken whatever they read now
    (`DirectScanner.scan_roll`'s ``take_blank``; Stefan's "blank-resume"
    answer adopted, "settled unless chosen"). A place nobody found blank is
    left to the driver's reading.

    A roll's picture outranks its walk's blank, as in `roll_left`: a place
    the walk read blank and the roll then scanned as a picture is a picture,
    and is left to read as one. It was the union of every manifest's blanks,
    and such a place ticked again was taken as a chosen blank."""
    if not only:
        return set()
    found: set[int] = set()
    seen: set[int] = set()
    for manifest in manifests:          # latest first: the roll, then its walk
        if not manifest:
            continue
        blank = blank_numbers(manifest)
        found |= blank - seen
        seen |= blank | set(_places(manifest))
    return {n for n in only if n in found}


def strip_count(strip_folder: Any, live: Callable[[Path], Any] | None = None,
                listed: dict | None = None) -> StripCount:
    """How many frames the strip in ``strip_folder`` had (`StripCount`),
    from its records -- read through ``live`` where a session's manifest is
    ahead of the file -- and its entry in the index (``listed``, read when
    not given). A strip's end is where its walk found it (survey.json's
    ``strip_end``), or where a scanning run did, by the runs its roll.json
    records (`roll_left`).

    Raises ValueError, naming it, where a record cannot be read: a count
    taken without it could give a number twice."""
    folder = Path(strip_folder)
    if listed is None:
        place = place_of(folder)
        listed = ({} if place is None else
                  {"strip": place.strip, "first": place.first,
                   "ends_at": place.ends_at})
    strip = int(listed.get("strip") or library.strip_number(folder.name) or 0)
    first = int(listed.get("first") or 1)
    ends = listed.get("ends_at")
    if isinstance(ends, int) and ends >= 1:
        return _counted(strip, first, ends, True,
                        "ends at {frame}, as its sheet set")
    walk = _manifest_now(folder / "survey.json", live)
    roll = _manifest_now(folder / "roll.json", live)
    places = _places(walk) + _places(roll)
    count = max(places, default=0)
    # Where a walk found the strip's end, and where a scanning run did --
    # read from the runs its roll.json records, as every reader of how far
    # a roll got reads them (`roll_left`).
    walked_to = walk.get("strip_end")
    walked_to = walked_to if isinstance(walked_to, int) and walked_to >= 1 \
        else None
    scanned_to = (roll_left(walk if walk.get("frames") else roll, roll)
                  .strip_end if roll.get("frames") else None)
    ends_seen = [e for e in (walked_to, scanned_to) if e is not None]
    if ends_seen:
        count = max(count, max(ends_seen) - 1)
        how = ("walked to the end of the strip" if walked_to is not None
               else "scanned to the end of the strip")
        return StripCount(strip, first, count, True, how)
    if count == 0:
        return StripCount(strip, first, 0, False, "has nothing recorded")
    # The run that last wrote it says how it ended: the walk, or a roll
    # scanned without one -- its last run.
    if walk.get("frames"):
        last, what = walk, "walk"
    else:
        runs = roll_runs(roll)
        last, what = (runs[-1] if runs else roll), "roll"
    if last.get("cut_short"):
        form = f"{what} was cut short after {{frame}}"
    elif last.get("stopped"):
        form = f"{what} stopped after {{frame}}"
    else:
        form = f"{what} went as far as {{frame}}"
    return _counted(strip, first, count, False, form)


def _counted(strip: int, first: int, count: int, complete: bool,
             form: str) -> StripCount:
    """A `StripCount` whose ``how`` names its frame by its place, as the
    record has always kept it, and whose `said` can say it again."""
    return StripCount(strip, first, count, complete,
                      form.replace("{frame}", f"frame {count}"), form)


def highest_given(roll: Any, live: Callable[[Path], Any] | None = None,
                  index: dict | None = None, leave_out: int | None = None
                  ) -> int:
    """The highest frame number a roll of strips has given: every closed
    strip's last, the open strip's first plus its count less one, every
    extra -- and every ``roll_number`` its strips' records carry, which is
    the backstop: a number that reached a record and the library before an
    index write failed is never given twice. Records past "Strip ends here"
    that carry the number their place has are left out: they are the stub
    the strip trimmed, open or closed since. Closed, they were counted, and
    the next strip taken again was numbered one past the stub, a number no
    frame ever had. One carrying any other number is counted all the same.

    ``leave_out`` is a strip not counted -- the one `plan_next` would take
    again. Raises ValueError where the index or an open strip's records
    cannot be read: nothing is numbered from a guess."""
    roll = Path(roll)
    if index is None:
        index = read_index(roll)
    best = 0
    #: Each strip's trim, by its folder: where it ends, and its first.
    trimmed: dict[str, tuple[int, int]] = {}
    for listed in listed_strips(index):
        if listed["strip"] == leave_out:
            continue
        ends = listed.get("ends_at")
        if isinstance(ends, int):
            trimmed[str(listed.get("folder") or "").casefold()] = (
                ends, int(listed.get("first") or 1))
        last = listed.get("last")
        if isinstance(last, int):
            best = max(best, last)
            continue
        count = strip_count(roll / str(listed.get("folder") or ""), live,
                            listed)
        best = max(best, count.last)
    for extra in index.get("extra") or ():
        if (isinstance(extra, dict) and isinstance(extra.get("number"), int)
                and extra.get("strip") != leave_out):
            best = max(best, extra["number"])
    left_out = {str(s.get("folder") or "").casefold()
                for s in listed_strips(index) if s["strip"] == leave_out}
    for folder in library.strips_in(roll):
        if folder.name.casefold() in left_out:
            continue
        trim = trimmed.get(folder.name.casefold())
        for name in ("survey.json", "roll.json"):
            try:
                data = _manifest_now(folder / name, live)
            except ValueError:
                continue
            for record in data.get("frames") or ():
                if not isinstance(record, dict):
                    continue
                given = record.get("roll_number")
                try:
                    place = int(record.get("number"))
                except (TypeError, ValueError):
                    place = 0
                if (trim is not None and place > trim[0]
                        and given in (None, trim[1] + place - 1)):
                    continue
                if isinstance(given, int):
                    best = max(best, given)
    return best


def _past_extra(index: dict, roll: Path, live: Callable[[Path], Any] | None,
                strip: int | None, above: int) -> str | None:
    """Make way in ``index`` for the number ``above``, given past a closed
    strip's range: the roll's open last strip -- unless it is ``strip``,
    the one giving it -- closed at the frames it had, as the claim of the
    next strip closes it; or, where it recorded none, numbered from
    ``above + 1``, as `plan_next` would take it again. Left open, it counted
    only the frames it had, and numbered its next by its place: the number
    just given. An empty strip is never closed at ``first - 1``, which
    would leave a strip number no frame has, skipped for good.

    Returns what was done, said; None where nothing was. Raises ValueError
    where the open strip's records cannot be read."""
    listed = listed_strips(index)
    if not listed:
        return None
    last = listed[-1]
    if ((strip is not None and last.get("strip") == strip)
            or isinstance(last.get("last"), int)):
        return None
    count = strip_count(roll / str(last.get("folder") or ""), live, last)
    if count.count > 0:
        last.update(last=count.last, closed=_utc_now(), count_how=count.how)
        return (f"strip {last['strip']} is closed at {count.numbers()}: "
                f"{_how_said(count)}; a frame of it walked later is numbered "
                "after the roll's last")
    if int(last.get("first") or 1) <= above:
        last["first"] = above + 1
        return (f"strip {last['strip']} is numbered from {above + 1} now, "
                "nothing being recorded under it")
    return None


def open_last_strip(roll: Any, live: Callable[[Path], Any] | None = None,
                    leave_out: int | None = None) -> StripCount | None:
    """The roll's open last strip, counted: the one the first extra given
    past another strip's range closes, or numbers on where it recorded
    nothing (`_past_extra`). None where none is open, where it is
    ``leave_out``, or where the index or its records cannot be read -- for
    saying so before a run only."""
    try:
        listed = listed_strips(read_index(roll))
        if not listed:
            return None
        last = listed[-1]
        if (isinstance(last.get("last"), int)
                or (leave_out is not None and last.get("strip") == leave_out)):
            return None
        return strip_count(Path(roll) / str(last.get("folder") or ""), live,
                           last)
    except ValueError:
        return None


def strip_folder_used(plan: StripPlan, left: str = "Nothing was moved."
                      ) -> None:
    """Raise `StripFolderUsed` where the folder ``plan`` would claim holds
    files its roll's index does not list -- copied in by hand, or listed
    once and the index since read from its copy -- and nothing for a strip
    taken again (``reuse``), a folder not there, or an empty one.

    Read-only, and asked before the film moves: by the session before its
    seek and `tools/scan_roll.py` before the device opens. `claim` asks it
    again under the lock, where it says ``left`` of what came before.
    Never asked by `plan_next`, whose offer the window shows: a roll shown
    would raise."""
    if plan.reuse:
        return
    folder = plan.folder
    try:
        held = [p.name for p in folder.iterdir() if not p.name.startswith(".")]
    except FileNotFoundError:
        return
    except OSError as exc:
        raise StripFolderUsed(f"{folder} cannot be looked at ({exc}). "
                              f"{left}") from exc
    if held:
        raise StripFolderUsed(
            f"{folder} holds files {plan.roll_folder / library.STRIPS_INDEX} "
            f"does not list ({', '.join(sorted(held)[:3])}"
            + (" ..." if len(held) > 3 else "")
            + f"): strip {plan.number} cannot be claimed into it. Move them "
            f"out by hand. {left}")


def _refuse_as_no_strips(roll: Path) -> None:
    """Raise what a folder that takes no strip is refused with."""
    kind = library.roll_kind(roll)
    if kind == "legacy":
        raise NoSecondStrip(
            f"{roll.name} is one strip from before rolls held strips, and "
            "takes no second strip: a new walk starts a roll of its own. "
            "Nothing was moved.")
    if kind == "other":
        raise RollFolderInUse(
            f"{roll} holds files that are no roll's, so no strip goes into "
            "it: name another roll. Nothing was moved.")


def _refuse_open_in_trash(roll: Path, label: str, listed: dict,
                          folder: Path) -> None:
    """Raise where the open last strip, read as holding nothing, is in fact
    in the rolls' trash: taken again, its numbers went out a second time
    while its entries and delivered files still carried them, and its own
    restore was then refused, its place taken. A Delete closes it first
    (`close_strip`, and `library.trash` refuses it open); this is for one
    moved there some other way. A folder simply gone is a claim cut short
    before it was made, as far as anything here can tell, and is taken
    again as it always was."""
    try:
        if folder.is_dir():
            return
    except OSError:
        return
    # Another film's delete -- this roll's name given again since -- holds
    # none of this roll's strips (`library.strip_delete_is_of`).
    for batch in library.trash_batches(roll.parent):
        if (library.strip_delete_is_of(batch, roll)
                and any(n.casefold() == folder.name.casefold()
                        for n in batch.names)):
            raise ValueError(
                f"strip {listed['strip']} of {label} is in the rolls' trash "
                f"({batch.name}) with its numbers not fixed, so the next "
                "strip cannot be numbered: `tools/library.py restore "
                f"{roll.name}/{folder.name}` brings it back. Nothing was "
                "moved.")


def plan_next(roll: Any, live: Callable[[Path], Any] | None = None
              ) -> StripOffer:
    """The strip the next walk or roll of ``roll`` would claim, and what to
    say of it -- read-only: the window's question and Roll panel line,
    `tools/scan_roll.py --strip next` and the session's own check before
    the seek all ask this, so no two of them can plan two ways.

    - No roll yet, or one with no strips: strip 1, from 1.
    - The last strip claimed and nothing recorded under it -- a Stop before
      its first frame, a fault, a driver that found nothing: that strip
      again, its first worked out afresh. Nothing was given under it.
    - The last strip's end seen, or set: the next strip, numbered on from
      the roll's highest number.
    - Its walk stopped, cut short or limited: the next strip all the same,
      and ``again`` its count, which the window offers to add to first --
      closed now, the strip keeps the frames it had and a frame of it
      walked later is numbered after the roll's last.

    Raises `NoSecondStrip` for a roll from before strips, `RollFolderInUse`
    for a folder that is no roll -- a strip of a roll of strips among them
    -- and ValueError where the index cannot be read, or the open last
    strip is in the rolls' trash."""
    roll = Path(roll)
    owner = library.lock_folder(roll)
    if owner != roll:
        # A strip's folder, handed over as a roll: planned as one, the next
        # strip went into a roll of strips nested inside it, which nothing
        # that reads the rolls goes deep enough to find -- nor the guard on
        # `duplicates --delete` -- and its lock was the strip's, not the
        # roll's. An empty strip, a claim that walked nothing, read as new.
        raise RollFolderInUse(
            f"{roll.name} is a strip of {owner.name}: a new strip goes into "
            f"its roll, {owner.name}, never into one of its strips. Nothing "
            "was moved.")
    _refuse_as_no_strips(roll)
    if library.roll_kind(roll) == "new":
        plan = StripPlan(roll, roll.name, 1,
                         roll / library.STRIP_FOLDER.format(1), 1)
        return StripOffer(plan, None, "a new roll: frames from 1")
    index = read_index(roll)
    label = index.get("roll")
    label = label if isinstance(label, str) and label else roll.name
    strips = listed_strips(index)
    if not strips:
        plan = StripPlan(roll, label, 1,
                         roll / library.STRIP_FOLDER.format(1), 1)
        return StripOffer(plan, None, f"{label}: strip 1 next, from 1")
    last = strips[-1]
    folder = roll / str(last.get("folder") or "")
    count = strip_count(folder, live, last)
    if last.get("last") is None and count.count == 0:
        _refuse_open_in_trash(roll, label, last, folder)
        first = highest_given(roll, live, index, leave_out=last["strip"]) + 1
        plan = StripPlan(roll, label, int(last["strip"]), folder, first,
                         reuse=True)
        return StripOffer(plan, None, (
            f"{label}: strip {last['strip']} next again, numbered from "
            f"{first} -- nothing was recorded under it"))
    given = highest_given(roll, live, index)
    number = int(last["strip"]) + 1
    first = given + 1
    open_ = last.get("last") is None
    again = count if open_ and not count.complete else None
    warnings: tuple[str, ...] = ()
    if given >= FULL_ROLL:
        n = len(strips)
        warnings = (f"{label} has {given} frames in {n} "
                    f"strip{'' if n == 1 else 's'} already. If this strip is "
                    "from another film, Cancel and press New roll.",)
    else:
        warnings = _short_strip_said(strips, count)
    plan = StripPlan(roll, label, number,
                     roll / library.STRIP_FOLDER.format(number), first,
                     closes=count if open_ else None, warnings=warnings)
    if again is not None:
        said = (f"{label}: strip {last['strip']}'s {count.said()} -- the "
                f"next walk adds to it, or starts strip {number} from {first}")
    else:
        said = f"{label}: strip {number} next, numbered from {first}"
    return StripOffer(plan, again, said)


def _short_strip_said(strips: list[dict], count: StripCount
                      ) -> tuple[str, ...]:
    """The warning that the strip the next one follows was shorter than
    the one before it -- the film's last, as a rule, so the next strip is
    likely another film's -- or none. ``count`` is the last strip's own;
    only a count whose end was seen or set, or a closed range, is believed:
    a walk that stopped says nothing of how long the strip was. Short
    strips inside a roll happen too, so this warns and decides nothing.

    The full roll's warning (`FULL_ROLL`) covers a film that ends on a full
    strip; one of 24 exposures that ends on one is said only by the
    question naming the roll and its numbers."""
    if len(strips) < 2:
        return ()
    last, before = strips[-1], strips[-2]

    def frames_of(listed: dict) -> int | None:
        closed = listed.get("last")
        if not isinstance(closed, int):
            return None
        return closed - int(listed.get("first") or 1) + 1

    had = frames_of(last)
    if had is None and count.complete:
        had = count.count
    was = frames_of(before)
    if had is None or was is None or not 0 < had < was:
        return ()
    return (f"Strip {last['strip']} had {had} frame{'' if had == 1 else 's'}, "
            f"fewer than strip {before['strip']}'s {was}: a short strip is "
            "usually the film's last. If this strip is from another film, "
            "Cancel and press New roll.",)


def claim(plan: StripPlan, *, root: Any, typed: str = "", who: str = "",
          say: Callable[[str], Any] | None = None,
          live: Callable[[Path], Any] | None = None) -> StripPlace:
    """Claim the strip ``plan`` names: its roll folder made below ``root``
    (`storage.make_within`), and the index written -- the strip listed with
    its first number, the open strip before it closed at the frames it had
    (``count_how`` says how that is known). Returns its place.

    Called by the run that first walks or scans the strip, with the roll's
    lock held since before the seek, after the seek and its stop check --
    so a run with no film leaves no index and no folder -- and before the
    strip's folder is made or any pass is taken. Planned again first:
    `StripMoved` where the next strip is another than ``plan`` now,
    `StripFolderUsed` where its folder holds files the index does not list,
    ValueError where the index cannot be read. ``typed`` is the roll's name
    as typed, kept where its folder had to be called something else.
    """
    roll = plan.roll_folder
    offer = plan_next(roll, live)
    now = offer.next
    if (now.number, now.first) != (plan.number, plan.first):
        raise StripMoved(
            f"{roll.name}'s next strip is strip {now.number}, numbered from "
            f"{now.first}, not strip {plan.number} from {plan.first} as "
            "asked: the roll changed since. Nothing was moved; ask again.")
    folder = now.folder
    # Asked before the seek too (`strip_folder_used`); here a backstop, for
    # files that arrived since -- by now the film has moved.
    strip_folder_used(now, left="Nothing was scanned or written.")
    storage.make_within(root, roll)
    stamp = _utc_now()
    index: dict[str, Any] = (read_index(roll)
                             if library.roll_kind(roll) == "strips" else {})
    if not index:
        typed = (typed or "").strip()
        index = {"kind": STRIPS_KIND, "version": 1, "roll": now.label,
                 **({"typed_name": typed}
                    if typed and typed != now.label else {}),
                 "created": stamp, "strips": [], "extra": []}
    strips = [s for s in index.get("strips") or () if isinstance(s, dict)]
    if now.closes is not None:
        for listed in strips:
            if listed.get("strip") == now.closes.strip:
                listed.update(last=now.closes.last, closed=stamp,
                              count_how=now.closes.how)
    if now.reuse:
        for listed in strips:
            if listed.get("strip") == now.number:
                listed.update(first=now.first, claimed=stamp)
    else:
        strips.append({"strip": now.number, "folder": folder.name,
                       "first": now.first, "last": None, "claimed": stamp,
                       **({"by": who} if who else {}), "ends_at": None})
    index["strips"] = strips
    write_index(roll, index)
    if say is not None and now.closes is not None:
        say(f"strip {now.closes.strip} of {now.label} is closed at "
            f"{now.closes.numbers()}: {_how_said(now.closes)}")
    return StripPlace(roll, now.label, now.number, folder, now.first,
                      live=live)


def _how_said(count: StripCount, strip: StripPlace | None = None) -> str:
    """How a strip's count is known, after "strip 2 is closed at 7-12:":
    ``its walk stopped after frame 10 (strip 2 · 4)``, ``it was walked to
    the end of the strip``, ``it ends at frame 12 (strip 2 · 6), as its
    sheet set`` -- its frame said through `StripCount.said`."""
    how = count.said(strip)
    word = count.how.split(" ", 1)[0]
    if word in ("walk", "roll"):
        return f"its {how}"
    if word in ("walked", "scanned"):
        return f"it was {how}"
    return f"it {how}"


def _scanned_places(strip_folder: Path,
                    live: Callable[[Path], Any] | None) -> list[int]:
    """The places of a strip a pass was scanned at: each record of its
    roll.json that is no failed pass -- read through ``live`` where the
    session's manifest is ahead of the file. Filed, still being filed, its
    filing refused, its entry in the library's trash since: each one's
    number is on a label or a delivered file already, so it is given."""
    data = _manifest_now(strip_folder / "roll.json", live)
    out = set()
    for record in data.get("frames") or ():
        if not isinstance(record, dict):
            continue
        if record.get("error") and not frame_finished(record):
            continue
        try:
            out.add(int(record["number"]))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out)


def _index_refused(strip: StripPlace, exc: OSError, left: str) -> ValueError:
    """A lock or a write refused under `set_strip_end` or `close_strip`, as
    the ValueError their callers take for a refusal: a menu's handler
    written to that would otherwise have ended in a traceback."""
    if isinstance(exc, locks.Refused):
        return ValueError(f"{strip.roll_folder} is in use by {exc.who}; "
                          f"{left}")
    return ValueError(f"{library.STRIPS_INDEX} of {strip.label} could not be "
                      f"written ({exc}); {left}")


def close_strip(strip_folder: Any, *, who: str,
                live: Callable[[Path], Any] | None = None) -> str | None:
    """Close the open last strip of a roll at the frames it had, as the
    claim of the next strip would (`StripCount`), under the roll's lock.
    Returns the line to log, or None where the strip is closed already;
    raises ValueError with the refusal -- its index or its records cannot
    be read, the roll held by another program, the index not written.

    For a Delete of the strip, before it goes (`library.trash` refuses an
    open one): a strip deleted keeps its numbers (Stefan's "decided
    strict"), and one moved away open was read as holding nothing and
    taken again by the next walk, its numbers given a second time while
    its entries and delivered files still carried them. Called by the
    window in the lock it holds for the Delete, which counts this take
    (`locks`); ``live`` is the session's manifests, for frames of the strip
    still being filed.

    A strip closed already has the numbers its records alone keep past its
    range -- each given while the index would not take it -- written into
    the index first (`_index_unindexed`): once the strip is in the trash its
    records are no longer counted (`library.strips_in`), and those numbers
    were given again. A Delete never frees a number that was given."""
    folder = Path(strip_folder)
    strip = place_of(folder, live=live)
    if strip is None:
        return None
    if strip.last is not None:
        return _index_unindexed(folder, strip, who=who, live=live)
    left = f"strip {strip.strip} is left open"
    try:
        with locks.take(locks.ROLL, strip.roll_folder, exclusive=True,
                        what=who):
            index = read_index(strip.roll_folder)
            listed = next((s for s in index.get("strips") or ()
                           if isinstance(s, dict)
                           and s.get("strip") == strip.strip), None)
            if listed is None or isinstance(listed.get("last"), int):
                return None
            count = strip_count(folder, live, listed)
            listed.update(last=count.last, closed=_utc_now(),
                          count_how=count.how)
            write_index(strip.roll_folder, index)
    except OSError as exc:
        raise _index_refused(strip, exc, left) from exc
    if count.count <= 0:
        return (f"strip {strip.strip} of {strip.label} is closed with no "
                "frames: nothing was recorded under it")
    return (f"strip {strip.strip} of {strip.label} is closed at "
            f"{count.numbers()}: {_how_said(count, strip)}. Its numbers stay "
            "given; the next strip is numbered after the roll's last")


def _index_unindexed(folder: Path, strip: StripPlace, *, who: str,
                     live: Callable[[Path], Any] | None) -> str | None:
    """`close_strip` of a strip closed already: the numbers its records
    alone keep past its range written into the index, under the roll's
    lock, the open last strip made way for as an extra given then would
    have (`_past_extra`). Returns the line to log, or None where there are
    none; raises ValueError where a record or the index cannot be read, the
    roll is held by another program, or the index is not written."""
    if not strip.unindexed:
        return None
    left = (f"the numbers strip {strip.strip}'s records alone keep are not "
            "in the index")
    try:
        with locks.take(locks.ROLL, strip.roll_folder, exclusive=True,
                        what=who):
            # Again under the lock: the window's own Delete holds it, and
            # another program may have written the index since.
            strip = place_of(folder, live=live) or strip
            if not strip.unindexed:
                return None
            index = read_index(strip.roll_folder)
            kept = sorted(strip.unindexed)
            moved = _past_extra(index, strip.roll_folder, live, strip.strip,
                                max(strip.extra[p] for p in kept))
            stamp = _utc_now()
            index.setdefault("extra", []).extend(
                {"strip": strip.strip, "place": p, "number": strip.extra[p],
                 "given": stamp, "from": "record"} for p in kept)
            write_index(strip.roll_folder, index)
    except OSError as exc:
        raise _index_refused(strip, exc, left) from exc
    numbers = ", ".join(str(strip.extra[p]) for p in kept)
    return (f"strip {strip.strip} of {strip.label} keeps {numbers} past its "
            f"numbers, {strip.numbers()}, in {library.STRIPS_INDEX} now: "
            "given while it could not be written, they stay given once the "
            "strip goes" + (f"; {moved}" if moved else ""))


def set_strip_end(strip_folder: Any, place: int | None, *, who: str,
                  live: Callable[[Path], Any] | None = None) -> str:
    """Set where the open last strip of a roll ends -- "Strip ends here" on
    its sheet, at ``place`` -- or, with None, count it to its end again.
    Returns the line to log; raises ValueError with the refusal, a lock
    another program holds or an index the disk refused among them.

    For the stub at a strip's end that is no frame of the film: one of 66
    dated walks walked seven frames of a strip of six. Only on the roll's
    open last strip, before the next strip fixes its numbers; never past a
    place a pass was scanned at there (`_scanned_places`, through ``live``,
    the session's manifests): read as roll.json's ``done`` alone, a frame
    the writer was still filing as its roll ended was passed over, and the
    next strip's first frame was given the number its delivered file
    already had. Under the roll's lock. The window refuses it while the
    scanner works. The places past it keep the prescans and labels they
    were filed with, and a scan of one is refused (`past_strip_end`)."""
    folder = Path(strip_folder)
    strip = place_of(folder)
    if strip is None:
        raise ValueError(f"{folder.name} is no strip of a roll of strips")
    if strip.last is not None:
        raise ValueError(
            f"strip {strip.strip} of {strip.label} is closed at "
            f"{strip.numbers()}: its numbers are given, and where it ends "
            "cannot move now")
    left = "where it ends is left as it was"
    try:
        with locks.take(locks.ROLL, strip.roll_folder, exclusive=True,
                        what=who):
            index = read_index(strip.roll_folder)
            listed = listed_strips(index)
            if not listed or listed[-1].get("strip") != strip.strip:
                raise ValueError(f"strip {strip.strip} of {strip.label} is "
                                 "not the roll's last: the strip after it "
                                 "fixed its numbers")
            if place is not None:
                place = int(place)
                if place < 1:
                    raise ValueError("a strip ends at frame 1 or after it")
                try:
                    scanned = _scanned_places(folder, live)
                except ValueError as exc:
                    raise ValueError(f"{exc}; {left}") from exc
                past = [n for n in scanned if n > place]
                if past:
                    raise ValueError(
                        f"frame {past[0]} of strip {strip.strip} is scanned, "
                        f"so the strip cannot end at frame {place}")
            for entry in index.get("strips") or ():
                if (isinstance(entry, dict)
                        and entry.get("strip") == strip.strip):
                    entry["ends_at"] = place
            write_index(strip.roll_folder, index)
    except OSError as exc:
        raise _index_refused(strip, exc, left) from exc
    try:
        following = plan_next(strip.roll_folder, live).next.first
    except ValueError:
        following = None
    after = ("" if following is None
             else f"; the next strip is numbered from {following}")
    if place is None:
        return (f"strip {strip.strip} of {strip.label} counts to the end of "
                f"the strip again{after}")
    # Its last number beside its place, as every line says a frame: "ends at
    # frame 5: its numbers are 10-14" put a place before a range of roll
    # numbers. From its first and the place, not `place_said`: ``strip`` was
    # read before its end moved, and would call the new end past the old.
    last = strip.first + place - 1
    return (f"strip {strip.strip} of {strip.label} ends at "
            f"{frame_said(last, strip.strip, place)}: its numbers are "
            f"{strip.first}-{last}{after}")


def past_strip_end(strip: StripPlace | None, start_at: int,
                   frames: int | None, only: Any, what: str) -> None:
    """Refuse a scan of a place past where "Strip ends here" put its
    strip's end: a stub no frame of the film is, whose label the next
    strip's first frames share. A roll to the end of the strip from before
    that place goes past it too. Raises ValueError, saying ``what`` was
    refused, before anything moves: the session's pre-flight and
    `tools/scan_roll.py`'s before the device opens, so the two front ends
    cannot number one strip two ways. Not for a walk, whose prescans keep
    the labels they were filed with.

    On a closed strip a frame named past its end is let through: its end
    was set too early, and with the next strip's numbers fixed nothing can
    move it now, so the frame takes a number after the roll's last
    (`StripPlace.number`). Only a roll to the end of the strip is refused
    there, which would scan the stub too."""
    if strip is None or strip.ends_at is None:
        return
    ends = strip.ends_at
    asked = frames_asked(max(1, start_at), frames, only)
    if strip.last is not None and asked is not None:
        return
    past = (sorted(n for n in asked if n > ends) if asked is not None
            else [max(start_at, ends + 1)])
    if not past:
        return
    if asked is None:
        raise ValueError(
            f"{what}: a roll to the end of strip {strip.strip} goes past "
            f"where it ends ({ends}) -- name its frames"
            + (", or 'Count to the end of the strip' on its sheet undoes "
               "that" if strip.last is None else
               f"; its numbers, {strip.numbers()}, are fixed, and a frame "
               "past them scanned by name gets a number after the roll's "
               "last"))
    raise ValueError(
        f"{what}: frame {past[0]} of strip {strip.strip} is past where "
        f"strip {strip.strip} ends ({ends}); 'Count to the end of the strip' "
        "on its sheet undoes that")


#: What a manifest's previous version is kept as, beside it: the file as it
#: stood before this run or this commission first wrote over it.
PREVIOUS = ".bak"

#: How long `write_manifest` waits between attempts to rename a manifest
#: over one that someone has open. On Windows the rename fails outright
#: while any handle lacks FILE_SHARE_DELETE -- Python's own `open()` for
#: reading does, and the window reads these files, and Defender and the
#: indexer briefly hold every new file as a matter of course. Writing in
#: place had no such window; a rename once a frame, for hours, meets it.
REPLACE_RETRY_S = (0.05, 0.05, 0.1, 0.1, 0.1)


def _replace(temp: Path, path: Path) -> None:
    """`os.replace`, patient with a file that is only briefly held open.

    Raises the last PermissionError once the retries are spent, with the
    temporary file removed: a sharing violation that outlasts half a second
    is not the transient one this waits out, and the caller says so. The
    folder is synced once the new name is in (`library._sync_dir`), so a
    power cut cannot take the rename back on POSIX.
    """
    for wait in (*REPLACE_RETRY_S, None):
        try:
            os.replace(temp, path)
            break
        except PermissionError:
            if wait is None:
                temp.unlink(missing_ok=True)
                raise
            time.sleep(wait)
    library._sync_dir(path.parent)


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

    The rename waits out a reader that briefly holds the file (`_replace`),
    and raises only once that has failed too, leaving no ``.part`` behind.
    The name written beside is this writer's own (`library._beside`):
    the roll's writer thread, the scanning thread, the window and
    `tools/scan_roll.py` all write these, and two sharing one temporary name
    truncated and interleaved each other's text before either renamed it.

    Values JSON has no type for are written as an entry's record writes them
    (`library._plain`): an array as its list, a numpy number as the number,
    bytes as hex. `default=str` wrote their printed form -- a registration's
    score as the string "0.5", a long profile as "[0.1 0.2 ... 0.9]" -- and
    the roll read back something other than what it had measured.
    """
    path = Path(path)
    text = json.dumps(data, indent=2, default=library._plain)
    temp = library._beside(path)
    try:
        with open(temp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            library._fsync(fh.fileno())
    except BaseException:
        # A full disk, most likely: the file already there is untouched, and
        # the half-written one beside it is not left to be mistaken for it.
        temp.unlink(missing_ok=True)
        raise
    if keep_previous and path.exists():
        # Beside and renamed over, as the manifest is. Copied in place, a
        # copy cut short -- a full disk -- was left as the `.bak` that
        # `read_manifest` falls back on when the file itself is damaged.
        kept = path.with_name(path.name + PREVIOUS)
        spare = library._beside(kept)
        try:
            shutil.copyfile(path, spare)
            library._sync(spare)
            _replace(spare, kept)
        except OSError:
            # The copy is a courtesy; the write is not.
            spare.unlink(missing_ok=True)
    _replace(temp, path)


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
    try:
        aside = _unclaimed(path.with_name(path.name + ".unreadable"))
    except FileExistsError as exc:
        # An OSError still, so a roll that starts on this file fails as it
        # does on any manifest it cannot write, before it scans anything.
        raise FileExistsError(
            f"no free name to set {path} aside; it is left as it is and "
            "nothing was written over it") from exc
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
        # Beside and renamed over, as the manifest itself is: copied in place,
        # a copy cut short was kept for good -- this runs once, and only
        # while the file is not there.
        temp = path.with_name(f".{legacy.name}.part")
        try:
            shutil.copyfile(path, temp)
            # On the disk before it is named: renamed in unsynced, a power
            # cut could keep the name over a file of zeros, and this copy is
            # made once and never again.
            library._sync(temp)
        except BaseException:
            temp.unlink(missing_ok=True)        # not left for a sweep to find
            raise
        _replace(temp, legacy)


def approved_legacy(walk: dict, progress: dict) -> int:
    """How far an older `approved.json`'s numbers sit behind the strip's.

    It was written when a roll was commissioned, and that roll numbered its
    frames the way the file did -- so where the roll recorded positions, its
    shift is the file's, even when a later walk into the same folder has
    replaced the survey it was decided on; otherwise the walk's. Both as read
    from disk, before `renumbered`. One function for the reader and for the
    commission that merges into the file, so the two cannot key one file two
    ways.

    Here rather than in the window, as :func:`read_approved` is, because
    `tools/scan_roll.py --positions` and `tools/hold_check.py` read the file
    too, and a copy of either in a tool is how one file comes to be keyed
    two ways.
    """
    shift = legacy_shift(walk or progress) or 0
    decided = legacy_shift(progress) if progress else None
    return shift if decided is None else decided


def read_approved(folder, legacy: int = 0, say=None):
    """A roll's stored decisions: `(offsets, rotations, flips, entries, sources)`.

    `approved.json` holds what was decided by hand -- these positions and
    turns -- which nothing in the library holds; it is not the only such
    file in a roll folder (the walk's `survey.json`, a pass kept in
    `unfiled/`, a frame whose entry has gone), and the roll browser's Delete
    names each of them. Read on its own rather than only as part of loading
    a whole survey, because the tools read it by name too.

    ``legacy`` is how far the walk this file was written against numbered its
    frames from the strip's own, for a file from before frame numbers were
    places on the strip; see :func:`legacy_shift`. It keeps no positions of
    its own, so its numbers can only move with the walk's. A file that says it
    numbers by the strip is read as it stands, and so is every file when the
    caller leaves ``legacy`` at 0 -- the export path, whose library entries
    carry the same numbers the file does.

    ``say`` hears about a file that cannot be read. It used to come back as
    "no decisions" with nothing said, which reopened a roll with every turn
    and position gone and no sign that anything was missing.

    ``folder`` is a roll folder, whose `approved.json` is read, or the file
    itself: `tools/scan_roll.py --positions` and `tools/hold_check.py` are
    handed one by name, and it need not be called `approved.json` or sit in
    the folder it was decided in -- :func:`approved_shift` says what its
    ``legacy`` is then. A path that is not there is a folder unless it is
    named like a file (``*.json``); either way it holds no decisions.
    """
    path = Path(folder)
    if path.is_dir():
        approved_path = path / "approved.json"
    elif path.is_file() or path.suffix.lower() == ".json":
        approved_path = path
    else:
        approved_path = path / "approved.json"
    offsets: dict[int, float] = {}
    rotations: dict[int, int] = {}
    flips: dict[int, bool] = {}
    entries: dict[int, str] = {}
    # Who decided each position. Written since the sheet began proposing them,
    # and read back so a reopened roll does not relabel the ensemble's numbers
    # as his -- the same reason the sheet's own state carries them.
    sources: dict[int, str] = {}
    if not approved_path.exists():
        return offsets, rotations, flips, entries, sources
    try:
        stored = read_manifest(approved_path, say=say)
        records = stored.get("frames", [])
    except (OSError, ValueError, AttributeError) as exc:
        if say is not None:
            say(f"{exc} -- this roll's own positions and turns are not "
                "shown; the file is left as it is")
        return offsets, rotations, flips, entries, sources
    shift = 0 if stored.get("numbering") == NUMBERING else legacy
    for record in records:
        try:
            number = int(record["number"]) + shift
        except (KeyError, TypeError, ValueError):
            continue
        # A zero only where it says it was his: every ticked frame is written
        # with a position, and an untouched one's zero is no decision at all.
        # His "as surveyed" is, and read as absent the detector's number took
        # its place on reopen. Who decided goes with the position, so an
        # untouched frame's `source` is not read as his either.
        #
        # Per record, in either unit: `offset_units` since 2026-10-13,
        # `offset_mm` before it, converted and never rewritten -- so a file
        # the window has added to since holds both, frame by frame.
        #
        # Judged on the number read, not the one stored: `bool(nan)` is True
        # and json reads NaN, and a position that is no number is no
        # decision -- not the largest move there is. His "as surveyed" is
        # one whatever number is or is not stored beside it: a hold at 0,
        # the reading `_merge_kept` gives a source with no position, and
        # batch 5's for a missing key. Kept only where a number was read,
        # it left his `source` beside no position, and the CLI held the
        # walk's proposal where the window held 0.
        value = units_of(record, "offset")
        placed = bool(value) or bool(record.get("as_walked"))
        if placed:
            offsets[number] = value if value is not None else 0.0
        # `is not None` rather than truthiness: an explicit zero is a decision
        # here, and a file written before this existed has no key at all rather
        # than a zero.
        if record.get("rotation") is not None:
            rotations[number] = int(record["rotation"]) % 360
        if record.get("flipped") is not None:
            flips[number] = bool(record["flipped"])
        if record.get("reference_entry"):
            entries[number] = record["reference_entry"]
        if record.get("source") and placed:
            sources[number] = str(record["source"])
    return offsets, rotations, flips, entries, sources


def approved_shift(path, say=None) -> int | None:
    """What :func:`read_approved` is to be given as ``legacy`` for one file.

    0 for a file that says it numbers by the strip. For an older one, what the
    walk and the roll beside it say (:func:`approved_legacy`) -- the window's
    reading of the file in its own roll folder -- and None when neither can
    say, because the file keeps no positions of its own: a guess there holds
    each frame to another frame's position, and nothing downstream could tell
    that from a frame placed badly.

    ``path`` is the file, or a roll folder holding one. A file that cannot be
    read raises ValueError naming it (:func:`read_manifest`); a walk or roll
    beside it that cannot be read is no evidence either way.
    """
    path = Path(path)
    if path.is_dir():
        path = path / "approved.json"
    if read_manifest(path, say=say).get("numbering") == NUMBERING:
        return 0
    beside: list[dict] = []
    for name in ("survey.json", "roll.json"):
        try:
            beside.append(read_manifest(path.parent / name, say=say))
        except ValueError:
            beside.append({})
    walk, progress = beside
    if (legacy_shift(walk or progress) is None
            and (not progress or legacy_shift(progress) is None)):
        return None
    return approved_legacy(walk, progress)


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

    A frame's record and its filing never stop the roll by failing to reach
    the disk. The file is rewritten whole each time, so what one write could
    not say the next one does; until then it is kept here, said to ``say``,
    and named by `unsaved`. It used to raise out of the scanning loop, and a
    rename refused for half a second on Windows -- a reader, Defender --
    ended an hours-long roll part-way.
    """

    def __init__(self, path, data: dict, say=None,
                 replaces: RollManifest | None = None):
        self.path = Path(path)
        self.data = data
        self.say = say
        #: The manifest that wrote this file until now, retired at this
        #: one's first write and not before (`_write`): a walk that ends
        #: before its first frame writes nothing, and the walk it would have
        #: replaced goes on writing its late answers into its own file.
        self._replaces = replaces
        self._lock = threading.Lock()
        #: How many filings each frame number still waits for. A count and
        #: not a set, because a roll straight after another into the same
        #: folder carries this manifest on (`ScanSession._roll`), and may take
        #: again a frame whose first filing has not come back yet.
        self._awaiting: dict[int, int] = {}
        self._early: dict[int, tuple] = {}
        #: Amendments promised (`expect`) and not yet made (`amend`).
        self._amending = 0
        #: The records `record` replaced while an amendment was still
        #: promised, by identity: one of them may yet be amended (`amend`).
        self._replaced: list[dict] = []
        #: Set once another manifest owns the file (`retire`).
        self._retired = False
        self._written = False
        #: Why the last write did not reach the disk, while it has not.
        self.unsaved: str | None = None
        #: The file as this last wrote it, ``(st_mtime_ns, st_size)``; None
        #: before it has (`_catch_up`).
        self._stat: tuple[int, int] | None = None

    def _write(self) -> None:
        if self._retired:
            return
        if self._replaces is not None:
            # This file is this manifest's from now on: the one it replaces
            # stops writing it before this does.
            self._replaces.retire()
            self._replaces = None
        # The first write of a run keeps what was there before it.
        write_manifest(self.path, self.data, keep_previous=not self._written)
        self._written = True
        self.unsaved = None
        self._stat = self._stat_of_file()

    @property
    def written(self) -> bool:
        """Whether this run has written the file, or tried to: once it has,
        the file is this manifest's. False for a walk that recorded no
        frame -- whose file is the walk before it, untouched."""
        return self._written or self.unsaved is not None

    def _stat_of_file(self) -> tuple[int, int] | None:
        """The file's ``(st_mtime_ns, st_size)``, or None where it cannot
        be read."""
        try:
            found = self.path.stat()
        except OSError:
            return None
        return found.st_mtime_ns, found.st_size

    def _catch_up(self) -> None:
        """Adopt the file as it stands, where it was written by something
        else since this last wrote it and nothing here is owed to it. Called
        with the lock held.

        A session keeps the manifest of every roll it wrote, and a Delete
        marks a frame through it (`forget`). The folder may have been renamed
        away since, or taken into the rolls' trash, a frame marked there
        through the file (`mark_deleted_frames`), and the folder renamed or
        restored back: the next mark through this wrote what this held over
        the file, and the earlier frame's marker was gone -- that frame read
        as scanned, with its entry in the trash.

        Only where the file is not as this wrote it, so a roll in progress
        keeps the very records its scanning thread and writer hold. Never
        while this is ahead of the file (`ahead_of_disk`), or retired; nor
        before this has written it, since what it holds was read from it.
        """
        if (self._retired or self._awaiting or self._amending
                or self.unsaved is not None or self._stat is None):
            return
        now = self._stat_of_file()
        if now is None or now == self._stat:
            return       # gone, or as this wrote it: nothing to adopt
        try:
            data = json.loads(self.path.read_bytes().decode("utf-8"))
        except (OSError, ValueError):
            return       # damaged or held: a write from here mends it
        if isinstance(data, dict):
            self.data = data
            self._stat = now

    def _keep(self) -> None:
        """`_write`, and when the disk will not take it, keep it and say so."""
        try:
            self._write()
        except OSError as exc:
            self.unsaved = str(exc)
            if self.say is not None:
                self.say(f"could not write {self.path} ({exc}); what it "
                         "should say is kept, and the next write of it "
                         "carries it -- the next frame's, or one more once "
                         "the scanner is closed")

    def write(self) -> None:
        """Write it now, raising when that fails: for a caller that must know."""
        with self._lock:
            self._write()

    def save(self) -> bool:
        """Write it now, as a frame does; whether it reached the disk."""
        with self._lock:
            self._keep()
            return self.unsaved is None

    def ended(self, how: str | None, **run: Any) -> None:
        """Say when this run ended and, when it did not simply finish, why.

        As the roll tool's manifest does. Without it a roll.json read a year
        later -- frames 1 to 9 done, 10 to 38 missing -- could not say whether
        frame 10 was the end of the film, a Stop, a disk that filled or a
        device that went suspect, which decides whether resuming is safe.

        At the top, the last run's, as every reader has always read it; and
        in the file's own entry for this run, the last of its ``runs``, with
        ``run`` beside it -- how far it got (``reached``), and where it found
        the strip's end (``strip_end``) -- where it keeps them: the top is
        written afresh by the next run, and a run to the end of the strip
        that stopped after frame 9 read as a roll finished once another ran.
        """
        with self._lock:
            finished = _utc_now()
            self.data["finished"] = finished
            if how:
                self.data["stopped"] = how
            else:
                self.data.pop("stopped", None)
            runs = self.data.get("runs")
            if isinstance(runs, list) and runs and isinstance(runs[-1], dict):
                runs[-1]["finished"] = finished
                if how:
                    runs[-1]["stopped"] = how
                else:
                    runs[-1].pop("stopped", None)
                runs[-1].update(run)
            self._keep()

    def add_run(self, run: dict) -> None:
        """Add a run that took no frame to the file's ``runs`` -- one whose
        seek found the strip's end (`ScanSession._roll`) -- and write it. A
        file from before runs were kept is given its own first."""
        with self._lock:
            # The file as it stands, where something else wrote it since.
            self._catch_up()
            runs = roll_runs(self.data)
            self.data["runs"] = [*runs, dict(run)]
            self._keep()

    def named_by_others(self, number: int) -> set[str]:
        """The files this manifest's records of other frames name.

        A walk writes each frame's prescan beside the manifest, and a file
        another record names is that frame's picture: see `_roll`.
        """
        with self._lock:
            return {str(record[key])
                    for record in self.data.get("frames") or ()
                    if str(record.get("number")) != str(number)
                    for key in ("prescan", "prescan_before")
                    if record.get(key)}

    def only_copies(self, number: int) -> list[tuple[str, str, bool]]:
        """The prescans frame ``number``'s own record names that no library
        entry holds, as ``(kind, file, listed)``: its ``prescan`` and
        ``prescan_before`` whose entry the record does not name, or names as
        None -- a walk from before prescans were filed, or a pass the library
        refused -- and each earlier one kept that way, ``listed`` among its
        takes already (P18-b). Written over, each was gone for good; a walk
        added to keeps them."""
        out: list[tuple[str, str, bool]] = []
        with self._lock:
            for record in self.data.get("frames") or ():
                if str(record.get("number")) != str(number):
                    continue
                for key in ("prescan", "prescan_before"):
                    if record.get(key) and not record.get(f"{key}_entry"):
                        out.append((key, str(record[key]), False))
                for take in record.get("takes") or ():
                    if (isinstance(take, dict) and take.get("file")
                            and not take.get("entry")):
                        out.append((str(take.get("kind") or "prescan"),
                                    str(take["file"]), True))
        return out

    def waiting(self) -> int:
        """How many frame filings recorded here have not come back yet."""
        with self._lock:
            return sum(self._awaiting.values())

    def pending(self) -> bool:
        """Whether any frame recorded here still waits for its filing."""
        with self._lock:
            return bool(self._awaiting) or bool(self._amending)

    def ahead_of_disk(self) -> bool:
        """Whether this holds what the file does not: a filing still to come,
        or a write the disk refused and nothing has caught up since."""
        with self._lock:
            return (bool(self._awaiting) or bool(self._amending)
                    or self.unsaved is not None)

    def snapshot(self) -> dict | None:
        """What this holds, copied as far as its frames, where it is ahead
        of its file and still writes it; None where the file says as much.
        For counting a strip's frames while a run is filing them
        (`strip_count`): read from the file, a frame still to be written
        there was counted as none."""
        with self._lock:
            if self._retired or not (self._awaiting or self._amending
                                     or self.unsaved is not None
                                     or not self._written):
                return None
            data = dict(self.data)
            data["frames"] = [dict(r) for r in self.data.get("frames") or ()
                              if isinstance(r, dict)]
            return data

    def mark(self, **fields: Any) -> None:
        """Say ``fields`` at the top of the manifest, with its next write:
        how a run ended (`ScanSession._roll`), beside `ended`. Never a
        write of its own, so a walk that recorded nothing still writes
        nothing."""
        with self._lock:
            self.data.update(fields)

    def expect(self) -> None:
        """Say that the writer will `amend` a record: until it has, this
        manifest is ahead of its file, and a run into the same folder carries
        it on rather than reading the file (`ScanSession._roll`)."""
        with self._lock:
            self._amending += 1

    def withdraw(self) -> None:
        """Take back an `expect` whose job was never queued: nothing will
        `amend` for it."""
        with self._lock:
            self._amending = max(0, self._amending - 1)

    def retire(self) -> None:
        """Stop writing the file: another manifest owns it now.

        A walk into a folder whose last walk is not carried on replaces this
        one (`ScanSession._roll`), and the writer's late answers for the old
        walk's prescans still arrive here. Each rewrote survey.json whole,
        with the old walk's records over the new walk's file. They are kept
        in the dicts, which nothing reads any more, and not written.

        Called by the new walk's manifest at its own first write, never
        before (``replaces``): a walk that ended before its first frame
        replaced survey.json with an empty walk -- and the next attempt's
        first write copied that over survey.json.bak, the only list left of
        the walk's prescans' entries (FOUND-2).
        """
        with self._lock:
            self._retired = True

    def amend(self, record: dict, take: dict | None = None,
              **fields: Any) -> None:
        """Add to one record what only the writer can say, once it has.

        A walk's prescan: the file it wrote beside the manifest, and the
        library entry it filed. Named from the scanning thread as it was
        queued, the record pointed at a file nothing had written yet -- and
        after a full disk, a refused save or a kill, at one nothing ever would,
        or at the previous walk's picture of that place. ``take`` is the
        entry it filed, added to the record's ``takes`` (`record`).

        By the record itself rather than its number, since the writer can
        answer before the scanning thread has recorded it: the fields then
        wait in the dict, and `record` writes them. A record a later take has
        since replaced is amended and not written -- but its take is listed
        on the record that replaced it, as `filed` lists a roll frame's. A
        walk added to another walked a frame again while the first prescan
        of it still waited behind the writer's queue: the second record
        took the first's takes before it had any, and the first prescan's
        entry was named by nothing in the roll, nor kept by `duplicates
        --delete` for it.
        """
        with self._lock:
            self._amending = max(0, self._amending - 1)
            record.update(fields)
            if take is not None:
                record.setdefault("takes", []).append(take)
            if any(r is record for r in self.data.get("frames") or ()):
                self._keep()
            elif take is not None and any(r is record for r in self._replaced):
                for current in self.data.get("frames") or ():
                    if str(current.get("number")) == str(record.get("number")):
                        current.setdefault("takes", []).append(dict(take))
                self._keep()
            if not self._amending:
                # Nothing more is promised, so no replaced record is amended.
                self._replaced.clear()

    def carry_on(self, data: dict) -> None:
        """Take a new run's manifest, built on this one's `data`, as its own.

        For a roll into a folder whose last roll is still being filed: the
        filings still to come are this object's to apply, and they find the
        new run's records -- the same dicts, carried forward. The new run
        keeps a copy of the file as it stood, as every run does.
        """
        with self._lock:
            self.data = data
            self._written = False

    def record(self, record: dict, awaiting: bool = False) -> None:
        """This frame's record, replacing any earlier one of its number.

        ``awaiting`` says it was handed to the writer and is not done until
        `filed` says so.

        What it replaces is not forgotten. A frame scanned again, or walked
        again into a walk it adds to, kept only its latest entry: the earlier
        take's raw bytes stayed in the library and nothing in the roll named
        them. So every take the frame has been filed as is listed in
        ``takes``, earliest first -- ``{entry, filed_utc}`` with the file it
        was written to where the record names one, and for a walk which of
        its prescans it was -- carried from the record this replaces (one
        from before takes were listed gives the entries it names). The folder
        keeps only the latest picture, as it always has: Stefan's call,
        2026-10-09. Every take's entry stays in the library with its raw
        bytes, and Export makes the latest take -- nothing exports an earlier
        one yet (TODO.md, beside heavy work with the device open). A marker
        that its entry went to the trash (`forget`) is not carried: it is the
        replaced take's.
        """
        number = int(record["number"])
        with self._lock:
            earlier = next((f for f in self.data.get("frames") or ()
                            if str(f.get("number")) == str(number)
                            and f is not record), None)
            takes = _takes_of(earlier) + list(record.get("takes") or ())
            if takes:
                record["takes"] = takes
            if earlier is not None and self._amending:
                # Its prescan's answer may be still to come (`amend`).
                self._replaced.append(earlier)
            early = self._early.pop(number, None)
            if early is not None:
                self._apply(record, *early)
            elif awaiting:
                record["done"] = False
                self._awaiting[number] = self._awaiting.get(number, 0) + 1
            self.data["frames"] = [
                f for f in self.data.get("frames") or ()
                if str(f.get("number")) != str(number)] + [record]
            self._keep()

    def filed(self, number: int, entry, error: str | None,
              **extra: Any) -> None:
        """The writer's answer for one frame: its entry, or why there is none.

        ``extra`` is what the caller's answer adds -- `file`, the frame's
        ``frameNN.tif``, given only once the writer has written it, or
        `file_error`, why it was not (`Filing.copy_said`): the roll tool's
        named one before anything had, and the window's named none. Written
        whether or not the frame was filed, since a frame the library
        refused still has its delivered copy.
        """
        number = int(number)
        with self._lock:
            waiting = self._awaiting.get(number, 0)
            if not waiting:
                self._early[number] = (entry, error, extra)
                return
            if waiting > 1:
                # An earlier take's answer. The writer answers in the order
                # it was given frames, and the record here is the later
                # take's, which has its own answer still to come. Its entry
                # is a take all the same: the record that awaited it was
                # replaced before it could list it, and with nothing here it
                # was filed in the library and named by nothing in the roll.
                self._awaiting[number] = waiting - 1
                if error is None and entry:
                    take = {"entry": Path(entry).name, "filed_utc": _utc_now()}
                    if (extra or {}).get("file"):
                        take["file"] = extra["file"]
                    for record in self.data.get("frames") or ():
                        if str(record.get("number")) == str(number):
                            record.setdefault("takes", []).append(take)
                    self._keep()
                return
            del self._awaiting[number]
            for record in self.data.get("frames") or ():
                if str(record.get("number")) == str(number):
                    self._apply(record, entry, error, extra)
            self._keep()

    def prescan_told(self, record: dict, path: Path, key: str = "prescan",
                     entry_key: str | None = "prescan_entry"
                     ) -> Callable[..., Any]:
        """A walk prescan's `on_filed`: names ``path`` under ``key`` once the
        writer has written it, and the entry it was filed as under
        ``entry_key`` -- the entry's id, which is its folder in the library.

        A reopened walk had no way back to its prescans' entries: the frame
        record named only the file, so the approvals made from it named no
        reference entry. A copy that could not be written is named nowhere,
        and ``key``'s ``_error`` says why -- the filing's error for a pass
        not filed, and ``not written: <why>`` beside a filed entry for a
        copy alone. It said nothing there, and the frame dropped off a
        reopened sheet without a word. ``entry_key`` is written whatever the
        answer, None for a pass not filed: a record that names no entry at
        all is one from before they were named, which a reader joins on the
        library instead (`walked_prescan_entries`), and a pass kept out of
        the library is not that.

        A file it names gets its checksum too, ``key``'s ``_sha256``: the
        file is read back as a hold's reference, and a power cut can leave
        one whole in name and black in content.

        Promised only once it is queued (`queued`): taken as this was
        built, a `_file` that raised before handing the job over left the
        manifest ahead of its file for the rest of the session, and the
        window then refused to move or delete that roll as still being filed.
        """
        target = Path(path)

        def told(filing: Filing) -> None:
            fields: dict[str, Any] = {}
            if any(Path(p) == target for p in filing.written):
                fields[key] = target.name
                try:
                    fields[f"{key}_sha256"] = library._sha256(target)
                except OSError:
                    pass                  # named, and read unchecked
            take = None
            if entry_key:
                fields[entry_key] = (filing.entry.name
                                     if filing.entry is not None
                                     and not filing.failed else None)
                if fields[entry_key]:
                    take = {"entry": fields[entry_key],
                            "filed_utc": _utc_now(), "kind": key}
                    if key in fields:
                        take["file"] = fields[key]
            why = filing.why_unwritten(target)
            if filing.failed:
                fields[f"{key}_error"] = str(filing.error)
            elif why is not None:
                fields[f"{key}_error"] = NOT_WRITTEN + why
            self.amend(record, take=take, **fields)

        # Found there by `queued`, which makes the promise.
        setattr(told, "manifest", self)
        return told

    def awaits(self, number: int) -> bool:
        """Whether a filing for frame ``number`` is still to come here: its
        record does not yet name the entry it will end with."""
        with self._lock:
            return bool(self._awaiting.get(int(number))) \
                or int(number) in self._early

    def naming(self, entry_id: str) -> list[int]:
        """The frames whose record names the entry ``entry_id``: the same
        records `forget` marks, the file adopted where it moved on
        (`_catch_up`)."""
        with self._lock:
            self._catch_up()
            return _naming(self.data, entry_id)

    def forget(self, entry_id: str, marker: dict,
               number: int | None = None) -> tuple[str, list[int]]:
        """Mark each frame whose record names the entry ``entry_id`` as going
        to the trash, on the disk before it goes (`ScanSession.trash_entries`):
        ``entry_deleted``, ``marker`` -- ``{at, id, batch, by}`` -- beside the
        entry it names, which stays. The frame then counts as not scanned
        while that entry is out of the library, and as scanned again once it
        is back, with nothing more written (`scanned_frames`). The marker
        rides along in every later write of the file, which is rewritten
        whole; a rescan's record replaces it.

        Returns what came of it, with the frames: ``awaiting`` where frame
        ``number`` still waits for its filing, and nothing was written --
        the writer has told the window and not yet this record, and the
        record would be marked under the wrong entry; ``marked``, written,
        or OSError with nothing left marked; ``other`` where frame
        ``number``'s record names another take; ``none`` where no record
        names it.
        """
        with self._lock:
            if number is not None and (self._awaiting.get(int(number))
                                       or int(number) in self._early):
                return "awaiting", [int(number)]
            # The file as it stands, where it was marked through itself
            # since this wrote it: written from here, that mark was lost.
            self._catch_up()
            frames = self.data.get("frames") or ()
            named = [r for r in frames if _names_entry(r, entry_id)]
            if not named:
                other = number is not None and any(
                    str(r.get("number")) == str(number) for r in frames)
                return ("other" if other else "none"), []
            was = [(r, r.get("entry_deleted")) for r in named]
            for record in named:
                record["entry_deleted"] = dict(marker)
            try:
                self._write()
            except OSError:
                for record, before in was:
                    if before is None:
                        record.pop("entry_deleted", None)
                    else:
                        record["entry_deleted"] = before
                raise
            return "marked", _numbers(named)

    @staticmethod
    def _apply(record: dict, entry, error, extra=None) -> None:
        record.update(extra or {})
        if error is None:
            record["done"] = True
            record["entry"] = str(entry) if entry else None
            if entry:
                # This take, beside the ones before it (`record`).
                take = {"entry": Path(entry).name, "filed_utc": _utc_now()}
                if record.get("file"):
                    take["file"] = record["file"]
                record.setdefault("takes", []).append(take)
        else:
            record["done"] = False
            record["filing_error"] = str(error)


def _names_entry(record: Any, entry_id: str) -> bool:
    """Whether a frame record's own entry -- its latest take -- is
    ``entry_id``, however the path to it was written (backslashes from
    Windows included)."""
    return (isinstance(record, dict) and bool(record.get("entry"))
            and PureWindowsPath(str(record["entry"])).name == entry_id)


def _numbers(records: Iterable[dict]) -> list[int]:
    """The frame numbers of ``records``, unreadable ones left out."""
    out = []
    for record in records:
        try:
            out.append(int(record["number"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _naming(manifest: dict, entry_id: str) -> list[int]:
    """The frames of a manifest whose record names ``entry_id``."""
    return _numbers(r for r in manifest.get("frames") or ()
                    if _names_entry(r, entry_id))


def _takes_of(record: dict | None) -> list[dict]:
    """Every take a frame record names, earliest first, for the record that
    replaces it (`RollManifest.record`): its ``takes`` where it lists them,
    and for one from before they were listed, the entries it names -- a
    roll's filed frame, a walk's prescans."""
    if not isinstance(record, dict):
        return []
    if "takes" in record:
        return [dict(t) for t in record.get("takes") or ()
                if isinstance(t, dict)]
    out: list[dict] = []
    # Finished as `scanned_frames` reads it: a record from before `done`
    # existed -- `rolls/scan600*`, written by the roll tool -- names its
    # entry with no `done` at all, and its take was dropped (P21-r1).
    if frame_finished(record) and record.get("entry"):
        take = {"entry": PureWindowsPath(str(record["entry"])).name}
        if record.get("file"):
            take["file"] = record["file"]
        out.append(take)
    for key in ("prescan_before", "prescan"):
        if record.get(f"{key}_entry"):
            take = {"entry": str(record[f"{key}_entry"]), "kind": key}
            if record.get(key):
                take["file"] = record[key]
            out.append(take)
    return out


def scanned_frames(manifest: dict, library_root: Any = None) -> set[int]:
    """Which frames of a roll are finished, from its manifest.

    A frame that errored is **not** finished. That is the case a resume exists
    for, so it comes back offered again rather than counted as done.

    Manifests written before `done` existed have no key at all; there an image
    filed without an error is the best evidence available, which is the same
    test `done` records.

    Nor is one whose entry went to the trash (`RollManifest.forget`): its
    record says ``entry_deleted`` for the entry it names, and that entry is
    not in the library at ``library_root`` -- None counts it gone. Finishing
    the roll offers it again, as the Roll question, a reopened roll and
    `tools/scan_roll.py`'s advice all read it here; restored, it is
    scanned again, with nothing written. A record with no marker reads as it
    always has, so a library moved or missing never makes a frame not done.
    Moved here from the window, so the window, its roll browser and the roll
    tool cannot read one roll.json three ways.
    """
    out: set[int] = set()
    for record in manifest.get("frames") or ():
        try:
            number = int(record["number"])
        except (KeyError, TypeError, ValueError):
            continue
        if frame_finished(record) and not entry_trashed(record, library_root):
            out.add(number)
    return out


def frame_finished(record: dict) -> bool:
    """Whether a frame record says it was filed: its ``done``, and for one
    from before ``done`` existed, an image filed without an error -- not a
    walk's prescan. The one test `scanned_frames`, `_takes_of` and the
    window's roll readers make, so one record cannot read two ways."""
    finished = record.get("done")
    if finished is None:
        finished = not record.get("error") and "prescan" not in record
    return bool(finished)


def entry_trashed(record: dict, library_root: Any = None) -> bool:
    """Whether a frame record's entry went to the trash and is not back:
    marked for the entry it still names (`RollManifest.forget`), and no
    entry of that id in the library at ``library_root`` (None: not looked
    for, so gone)."""
    marker = record.get("entry_deleted")
    if not isinstance(marker, dict) or not record.get("entry"):
        return False
    name = PureWindowsPath(str(record["entry"])).name
    if marker.get("id") != name:
        return False
    if library_root is None:
        return True
    return not (Path(library_root) / name / "scan.json").is_file()


def mark_deleted_frames(roll_files: Iterable[Any], entry_id: str,
                        marker: dict, live: Callable[[Path], Any] | None = None,
                        number: int | None = None,
                        say: Callable[[str], Any] | None = None
                        ) -> list[tuple[Path, int, str]]:
    """Mark, in each roll.json of ``roll_files``, every frame whose record
    names ``entry_id`` (`RollManifest.forget`): ``(path, frame, outcome)``
    for each.

    Through the manifest a session is writing the file with, where ``live``
    hands one back -- it is ahead of the file while frames are being filed,
    and a write beside it would be overwritten by its next one -- and
    otherwise read, marked and written whole (`write_manifest`), the `.bak`
    a run kept left as it is: that is the roll from before the run. Raises
    `StillFiling` where that manifest still waits for frame ``number``'s
    filing, and OSError where a file cannot be read or written, with what
    was marked before it marked.

    Read as it is, never through `read_manifest`'s fallback: a roll.json
    that does not parse read as its `.bak`, and the write that kept "the
    file as it stood" then copied the damaged newer file over that good
    older one, and wrote the older one back, marked, as the roll -- every
    frame done since the `.bak` read as not done, and the damaged file was
    where the next run's write overwrites it. It is kept as unreadable
    instead (`_keep_unreadable`, said through ``say``), as `earlier_manifest`
    sets aside one neither reads, and the `.bak` -- what every reader was
    reading in its place -- is marked and written as the roll, and stays.
    """
    out: list[tuple[Path, int, str]] = []
    for given in roll_files:
        path = Path(given)
        manifest = live(path) if live is not None else None
        if manifest is not None:
            outcome, numbers = manifest.forget(entry_id, marker, number)
            if outcome == "awaiting":
                raise StillFiling(still_filing_said(numbers[0]))
            out += [(path, n, outcome) for n in numbers]
            continue
        data, damaged = _manifest_as_it_is(path)
        named = [r for r in data.get("frames") or ()
                 if _names_entry(r, entry_id)]
        if not named:
            continue
        for record in named:
            record["entry_deleted"] = dict(marker)
        if damaged is not None:
            aside = _keep_unreadable(path)
            if say is not None:
                say(f"{path} cannot be read ({damaged}); kept as {aside.name}, "
                    f"and {path.name} written again from "
                    f"{path.name + PREVIOUS}, the version before it, which "
                    "was being read in its place")
        # The `.bak` a run kept is the roll as it stood before that run, for
        # undoing a resume that merged wrongly by hand; a mark copied the
        # file over it, merge and all, and each roll.json a Delete of several
        # entries marks passed its own along. Kept only for a roll that has
        # none. The mark undoes itself: restored, the entry reads as scanned
        # again with nothing written (`scanned_frames`).
        kept = path.with_name(path.name + PREVIOUS)
        write_manifest(path, data,
                       keep_previous=damaged is None and not kept.exists())
        out += [(path, n, "marked") for n in _numbers(named)]
    return out


def _keep_unreadable(path: Path) -> Path:
    """Copy a manifest that does not parse to a name that says so, before
    it is written over: `set_aside`, by a copy beside and renamed in, so a
    write that then fails leaves the file where it was, and never no
    roll.json at all -- which reads as a roll with no frames."""
    aside = _unclaimed(path.with_name(path.name + ".unreadable"))
    spare = library._beside(aside)
    try:
        shutil.copyfile(path, spare)
        library._sync(spare)
        _replace(spare, aside)
    except BaseException:
        spare.unlink(missing_ok=True)
        raise
    return aside


def _manifest_as_it_is(path: Path) -> tuple[dict, str | None]:
    """A roll.json to be rewritten, and why the file itself does not parse
    (None where it does). Where it does not, its `.bak`, which every reader
    falls back on (`read_manifest`); ValueError where that does not either.
    A file that cannot be read at all -- held, refused -- raises OSError:
    that is no damage, and nothing is set aside for it. One gone since it
    was found names nothing."""
    if not path.exists():
        return {}, None
    raw = path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        return data, None
    except ValueError as exc:
        problem = str(exc)
    kept = path.with_name(path.name + PREVIOUS)
    try:
        data = json.loads(kept.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict):
        raise ValueError(f"{path} cannot be read: {problem}")
    return data, problem


class StillFiling(OSError):
    """An entry whose roll frame is still being written into its roll.json:
    marked now, the mark would be under the take before it."""


@dataclass
class TrashedEntries:
    """What `ScanSession.trash_entries` did: the move (`library.Trashed`),
    and each roll frame now counted as not scanned -- its roll.json and its
    number -- for an entry that went."""

    trashed: library.Trashed
    frames: list[tuple[Path, int]]


def still_filing_said(number: int) -> str:
    """What a Delete refused for `StillFiling` says."""
    return (f"frame {number} is still being written into its roll.json; "
            "try again in a moment")


def entries_named(rolls: Any) -> set[str]:
    """Every entry id the rolls under ``rolls`` name: each frame's entry and
    every take of it in a roll.json, each walk prescan's in a survey.json,
    the reference each approval in an approved.json was made on. What
    `tools/library.py duplicates --delete` never takes (`library.prunable`).
    A roll folder that cannot be read names nothing. The rolls' trash is
    read too: a roll restored from it reads its frames as done again, and
    found naming an entry `--delete` took, one of them was done with no
    entry and no mark. Each strip of a roll of strips, in the trash or
    not, as a roll from before strips (`library.roll_folders`): read one
    level deep, every strip's entries were named by nothing here."""
    out: set[str] = set()
    for folder in library.roll_folders(rolls):
        for name in ("roll.json", "survey.json", "approved.json"):
            try:
                data = read_manifest(folder / name)
            except (OSError, ValueError):
                continue
            for record in data.get("frames") or ():
                if not isinstance(record, dict):
                    continue
                for key in ("entry", "prescan_entry", "prescan_before_entry",
                            "reference_entry"):
                    if record.get(key):
                        out.add(PureWindowsPath(str(record[key])).name)
                out |= {PureWindowsPath(str(t["entry"])).name
                        for t in record.get("takes") or ()
                        if isinstance(t, dict) and t.get("entry")}
    return out


def recorded_numbers(manifest: dict) -> list[int]:
    """The frame numbers a manifest holds a record for, unreadable ones left out.

    What a roll that named no frames is taken to want -- so a place recorded
    blank is left out (`blank_numbers`): settled, unless chosen.
    """
    numbers = []
    for record in manifest.get("frames") or ():
        if isinstance(record, dict) and record.get("blank"):
            continue
        try:
            numbers.append(int(record["number"]))
        except (KeyError, TypeError, ValueError):
            continue
    return numbers


def wanted_after(earlier: dict, job: Any) -> list[int] | None:
    """`roll.json`'s `wanted` once this run has started: every frame the roll
    is meant to end up with. None is "every frame it records".

    A scanning run names its frames by `only` or by its range, and both are
    frames asked for. Only `only` used to count, so a range was left to the
    reader's fallback -- every frame *recorded* -- and a roll is recorded
    frame by frame as it goes. "1 to 4" stopped after frame 1 reopened as
    "nothing left to scan"; scanned into a roll a sheet had asked 2 and 4 of,
    it went on wanting 2 and 4 and reopened finished with frame 3 never
    taken. The frames earlier runs recorded join the union, as they always
    did beside `only`. A run to the end of the strip cannot be listed, and a
    walk asks for no scans; both leave the list as it was.
    """
    explicit = (earlier.get("wanted")
                or (earlier.get("settings") or {}).get("only")
                or earlier.get("only"))
    named = job.only
    if named is None and not job.dry_run and job.frames is not None:
        named = range(job.start_at, job.start_at + job.frames)
    before = explicit or (recorded_numbers(earlier) if named else ())
    return sorted({int(n) for n in before}
                  | {int(n) for n in (named or ())}) or None


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


def frames_asked(start_at: int, frames: int | None,
                 only: Iterable[int] | None) -> list[int] | None:
    """The frames a roll from ``start_at`` is asked to yield, in order.

    Numbered from 1, as a manifest numbers them. ``frames`` counts places on
    the strip from ``start_at`` -- where `DirectScanner.roll_ends` ends the
    roll -- and ``only`` is the frames chosen among them: one outside that
    range is never reached, and one nobody chose is advanced past without
    being yielded, so neither is here. None is a roll that runs to the end of
    the strip, and only the strip says when that is.

    One answer for both rolls that say whether they ended short, the window's
    and `tools/scan_roll.py`'s. The tool held what it got to ``frames`` alone,
    so a resume given `--only 2,5` beside its first run's `--frames 10`
    scanned both frames it chose and still failed, "after 2 of the 10"; the
    window held it to the smaller of ``frames`` and the frames chosen, which
    counts a chosen frame past the end of ``frames`` that the roll never
    reaches.
    """
    end = None if frames is None else start_at + frames
    if only is None:
        return None if end is None else list(range(start_at, end))
    return sorted(n for n in set(only)
                  if n >= start_at and (end is None or n < end))


def wanted_frames(manifest: dict, progress: dict) -> list[int]:
    """Every frame a roll is asked to end up with, in order, by what it was
    asked for: ``manifest`` is its walk's survey.json, or its roll.json
    where it has no walk, and ``progress`` its roll.json.

    The operator's own choice where he made one -- `only` is what a contact
    sheet's ticks become -- and otherwise every frame the walk found. Without
    this a resume cannot say "11 of 24": it would know what was done and not
    what was asked for. A run to the end of the strip adds what it reached,
    and what it did not (`roll_left`). Moved here from the window, so the
    window, its browser and `tools/scan_roll.py` read one roll alike.
    """
    # `wanted` first: a resumed run is told only what is *left*, so its `only`
    # is not the roll's set. The session writes the union under this key.
    for source in (progress, manifest):
        if source.get("wanted"):
            return sorted({int(n) for n in source["wanted"]})
    for source in (progress, manifest):
        only = source.get("only") or (source.get("settings") or {}).get("only")
        if only:
            return sorted({int(n) for n in only})
    return sorted(set(recorded_numbers(manifest)))


def _dry(manifest: dict) -> bool:
    """Whether a manifest is a walk's: the window says so at its top, the
    roll tool inside its settings."""
    return bool(manifest.get("dry_run")
                or (manifest.get("settings") or {}).get("dry_run"))


def roll_runs(progress: dict) -> list[dict]:
    """The scanning runs a roll.json records, earliest first: its ``runs``,
    and for a file from before they were kept, one run read off its top --
    the last run's, the only one such a file says anything of, with where
    the strip's end or a give-up was said there. Empty for a file with no
    frame recorded, and for a walk's survey.json: a walk scans nothing.

    Each run is ``{started, settings, output, finished, stopped, reached,
    strip_end, cut_short}`` as far as it says them: ``settings`` the run's
    own request, ``output`` where its copies went (`Output`), ``reached``
    the last frame it reached, ``strip_end`` the first place it found no
    picture -- the strip's end -- and ``cut_short`` why the driver gave up
    (`DirectScanner.roll_cut_short`), beside ``stopped``.
    """
    runs = progress.get("runs")
    if isinstance(runs, list):
        return [dict(run) for run in runs if isinstance(run, dict)]
    if not progress.get("frames") or _dry(progress):
        return []
    run: dict[str, Any] = {key: progress[key]
                           for key in ("started", "finished", "stopped")
                           if progress.get(key) is not None}
    settings = progress.get("settings")
    run["settings"] = dict(settings) if isinstance(settings, dict) else {}
    if isinstance(progress.get("strip_end"), int):
        run["strip_end"] = progress["strip_end"]
    if isinstance(progress.get("cut_short"), str) and progress["cut_short"]:
        run["cut_short"] = progress["cut_short"]
    return [run]


def open_run(run: dict) -> bool:
    """Whether a run asked for the end of the strip: no count of frames and
    none chosen. Only one that says so -- a file with no count is no
    evidence of one."""
    settings = run.get("settings") or {}
    return ("frames" in settings and settings.get("frames") is None
            and settings.get("only") is None and not settings.get("dry_run"))


def _run_start(run: dict) -> int:
    """The frame a run started at."""
    try:
        return max(1, int((run.get("settings") or {}).get("start_at") or 1))
    except (TypeError, ValueError):
        return 1


def _end_seen(run: dict, recorded: Iterable[int]) -> int | None:
    """Where a run found the strip's end -- the first place with no
    picture -- or None where it did not. Said by the run itself where it
    says how far it got; for a run read off the top of a file from before
    runs were kept, a run to the end of the strip that finished with
    nothing to stop it is taken to have reached it past its last frame."""
    end = run.get("strip_end")
    if isinstance(end, int) and not isinstance(end, bool) and end >= 1:
        return end
    if ("reached" in run or not open_run(run) or run.get("stopped")
            or run.get("cut_short") or not run.get("finished")):
        return None
    start = _run_start(run)
    later = [n for n in recorded if n >= start]
    return max(later) + 1 if later else None


class RollLeft(NamedTuple):
    """What is left of a roll (`roll_left`): the frames it is asked for,
    those done and those left, where the rest of the strip starts that no
    run reached (``rest_from``, None where it has none), and where a run
    found the strip's end (``strip_end``, the first place with no
    picture)."""

    wanted: list[int]
    done: list[int]
    remaining: list[int]
    rest_from: int | None
    strip_end: int | None


def roll_left(manifest: dict, progress: dict, library_root: Any = None,
              ends_at: int | None = None) -> RollLeft:
    """What is left of a roll: the one reading the window, its browser and
    `tools/scan_roll.py` take, so none says a roll is finished that another
    would finish. ``manifest`` and ``progress`` as `wanted_frames` takes
    them; ``library_root`` as `scanned_frames`; ``ends_at`` where "Strip
    ends here" put a strip's end.

    A run to the end of the strip cannot list what it wants, and one that
    stopped after frame 9 read as finished, its record saying only "after
    frame 9" until the next run wrote over that. So from the runs
    (`roll_runs`): where any asked for the end of the strip, from the
    lowest frame such a run started at, every frame recorded is wanted, and
    so is every place none recorded up to the highest any reached -- a
    frame never taken in between, a counter jump -- or to where the strip
    was found to end. ``rest_from`` is the place after the highest reached,
    unless the strip's end was found there or past it. With no run to the
    end of the strip, it is `wanted_frames` exactly, as it always was.

    The strip's end is where a run found it, and where its walk did --
    ``manifest`` being a walk's survey.json -- the further of the two, as
    `strip_count` reads the strip. From the runs alone, a strip walked to
    its end at 7, scanned to frame 4 by a run that was stopped and then 5
    and 6 from its sheet, had "the strip from 7" left for good: listed
    unfinished, Roll offered no next strip, and pressed, it wound the film
    to the strip's end to fail there, while `plan_next` already called the
    strip whole.
    """
    wanted = set(wanted_frames(manifest, progress))
    done = scanned_frames(progress, library_root)
    recorded = set(recorded_numbers(progress))
    #: Places with no picture, by either record: never wanted by a run to
    #: the end of the strip, only by a choice that names them. The walk's
    #: word yields to the roll's: a place it found blank that a run then
    #: took as a picture, chosen again, is a frame like any other.
    blank = (blank_numbers(progress)
             | (blank_numbers(manifest) - recorded))
    runs = roll_runs(progress)
    end = None
    for run in runs:
        # The latest evidence: a strip put in again may end elsewhere.
        seen = _end_seen(run, recorded)
        if seen is not None:
            end = seen
    walked = manifest.get("strip_end") if _dry(manifest) else None
    if (isinstance(walked, int) and not isinstance(walked, bool)
            and walked >= 1):
        end = walked if end is None else max(end, walked)
    trimmed = isinstance(ends_at, int) and ends_at >= 1
    if trimmed:
        end = int(ends_at) + 1
    rest_from = None
    opened = [run for run in runs if open_run(run)]
    if opened:
        start = min(_run_start(run) for run in opened)
        reached = [run["reached"] for run in runs
                   if isinstance(run.get("reached"), int)
                   and not isinstance(run.get("reached"), bool)]
        seen_to = max([*recorded, *reached], default=0)
        top = max(seen_to, start - 1)
        last = max(top, end - 1 if end is not None else 0)
        wanted |= {n for n in recorded if n >= start}
        wanted |= {n for n in range(start, last + 1)
                   if n not in recorded and n not in blank}
        # Against what a run took or reached, never where one only asked
        # to start: a run typed to start past the strip's end lifted the
        # rest past it too, and "the strip from 12" stayed unreached on a
        # strip known to end at 9.
        if end is None or end - 1 < seen_to:
            rest_from = top + 1
    if trimmed:
        # Past it is the stub, no frame of the film; a scan there is refused.
        wanted = {n for n in wanted if n <= ends_at}
        if rest_from is not None and rest_from > ends_at:
            rest_from = None
    # A place found blank is handled, apart from done: a resume does not
    # take it again, and the roll is not short of it (Stefan's "blank-resume"
    # answer adopted, settled unless chosen). Choosing it -- a tick on the
    # sheet, --only -- starts a run that names it, and that run takes it
    # (`blanks_chosen`); one stopped before it got there leaves it settled,
    # to be ticked again.
    wanted -= blank
    return RollLeft(sorted(wanted), sorted(done),
                    sorted(n for n in wanted if n not in done),
                    rest_from, end)


#: What a run's request is compared by with the frames its roll took, and
#: how each is said: what decides the pixels.
COMPARED = {"resolution": "dpi", "infrared": "infrared", "film": "film",
            "meter": "metering"}


def settings_differ(was: dict, now: dict) -> list[str]:
    """How a run's request differs from the one its roll's frames took,
    said: the resolution, the channels, the film metering reads and the
    metering, and between two infrared runs whether infrared was tied.

    ``was`` is the roll's, as either front end wrote it (`manifest_settings`
    of its roll.json); ``now`` the run's under the same names --
    ``resolution``, ``infrared``, ``film``, ``meter``, ``fast_infrared``.
    Moved here from `tools/scan_roll.py`, which refuses every difference,
    so the window compares the same things, said the same way."""
    asked = {key: (now[key], name) for key, name in COMPARED.items()
             if key in now}
    if now.get("infrared") and was.get("infrared"):
        # Only between two infrared runs: an RGB roll's record of the flag
        # governs nothing, and the window writes it whatever the channels.
        asked["fast_infrared"] = (bool(now.get("fast_infrared")),
                                  "tied infrared")
    return [f"{name} {was[key]} where this run asks for {value}"
            for key, (value, name) in asked.items()
            if was.get(key) is not None and was[key] != value]


def roll_differs(folder: Any, now: dict,
                 live: Callable[[Path], Any] | None = None
                 ) -> list[tuple[Path, dict, list[str]]]:
    """Where a run into ``folder`` asks for other settings than its roll's
    frames were taken at (`settings_differ`): each roll.json that records
    frames, with its settings and how they differ -- across the whole roll
    for a strip of a roll of strips, or the roll itself, every strip's
    (Stefan's call, 2026-10-10: one film's files come out alike), and the
    folder's own for a roll from before strips. Read through ``live`` where
    a session's manifest is ahead of its file; one that cannot be read
    holds a run to nothing."""
    folder = Path(folder)
    roll = roll_of(folder)
    out = []
    for each in (library.strips_in(roll) if roll is not None else [folder]):
        path = each / "roll.json"
        try:
            data = _manifest_now(path, live)
        except ValueError:
            continue
        if not data.get("frames"):
            continue
        was = manifest_settings(data)
        differs = settings_differ(was, now)
        if differs:
            out.append((path, was, differs))
    return out


# `MAX_FINE_STEPS`, eight commands, stood here until 2026-10-13: set when a
# command topped out at param 8, it allowed some 710 units -- two frames --
# at param 87. The limit is one frame width now, `FINE_LIMIT_UNITS`, in
# units rather than commands.


def plan_nudges(*, units: float) -> list[float]:
    """The sub-frame moves a request actually becomes, in units, in order and
    signed.

    The transport cannot travel an arbitrary distance. One `SLIDE` command
    delivers ``param + 1.84`` units (`protocol.units_for_param`) for an
    integer param in 1..``MAX_CORRECTION_PARAM`` (87), so the reachable set is
    a lattice starting at ``FINE_MIN_UNITS`` -- and
    **nothing in ``(0, FINE_MIN_UNITS)`` exists at all**. Asking for one unit
    does not get you one unit; it gets you nothing or a whole first command.

    This exists so the three places that need that truth share it rather than
    each modelling it: :meth:`ScanSession._move` executes the plan, the
    contact sheet's adjuster snaps what it shows to it, and a correction loop
    sizes its travel budget from it. A plan that predicts something the mover
    would not do is worse than no plan.

    Returns an empty list when the distance is below half the smallest
    deliverable move -- the same "leave it alone" the mover applies. Raises
    ``ValueError`` past one frame, :data:`FINE_LIMIT_UNITS`, deliberately
    rather than clamping: `DirectScanner.param_for_units` already clamps silently at the
    largest correction, and a caller that cannot see its request was
    truncated will keep issuing
    commands against a ceiling it does not know is there.
    """
    want = abs(float(units))
    # Counted on the scale the planner always counted in -- units times
    # `MM_PER_UNIT`, which the hold's scale is defined by -- so that a chain
    # of commands is split exactly as it was: a remainder that sits on a half
    # param falls the same side of it. 111.18 units went as params 87 and 21;
    # counted in units the remainder rounds to 20, and 261 typed values to
    # 800 units would move by one param that way. The tests hold this to the
    # millimetre planner on a 0.01-unit grid. Nothing here is shown or kept;
    # what comes back is in units.
    travel = want * MM_PER_UNIT
    smallest = MM_PER_UNIT + MM_PER_COMMAND
    largest = MM_PER_UNIT * DirectScanner.MAX_CORRECTION_PARAM + MM_PER_COMMAND
    if travel < smallest * 0.5:
        return []
    if want > FINE_LIMIT_UNITS:
        raise ValueError(
            f"{say_distance(want, signed=False)} is past one frame "
            f"({say_distance(FINE_LIMIT_UNITS, signed=False)}), the furthest "
            "a sub-frame move goes -- use the slide buttons")
    # In floats: it was a ceiling of the two in thousandths, which a
    # distance a hair over a multiple of the largest command could fall
    # either side of.
    steps = max(1, math.ceil(travel / largest))

    sign = 1.0 if units > 0 else -1.0
    out: list[float] = []
    moved = 0.0
    while travel - moved >= smallest * 0.5 and len(out) < steps:
        # Snapped through the scanner's own param arithmetic, not a copy of
        # it, so the plan cannot drift from what nudge_units() will really
        # send.
        asked = min(largest, travel - moved)
        param = DirectScanner.param_for_travel(asked)
        out.append(sign * units_for_param(param))
        moved += MM_PER_UNIT * param + MM_PER_COMMAND
    return out


def deliverable_units(units: float) -> float:
    """What the transport will actually travel for this request, in units.

    Signed, and never more precise than the hardware: a number finer than
    :data:`FINE_MIN_UNITS` is a lie, and showing one to an operator invites
    them to aim at a position that does not exist.
    """
    return float(sum(plan_nudges(units=units)))

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


#: Past this a run from a tool should be backgrounded: a harness that kills a
#: foreground command at 10 minutes abandons its read, which wedges the
#: scanner. CLAUDE.md's figure.
FOREGROUND_S = 8 * 60.0
#: How much longer than typical a pass can run, for the warning above. Scan
#: time tracks the exposure as well as the lines, and the 1800 dpi RGB entries
#: span 36-162 s around their 85 s median (CLAUDE.md, "budget above the
#: median, not at it"); `estimate_seconds` sits at or below the medians.
SLOW_PASS = 162 / 85
#: A calibration, 3-4 minutes, per the prompt a tool prints before one.
CALIBRATION_S = 210.0
#: Metering: up to three 300 dpi RGB probes, at CLAUDE.md's 22 s median.
METERING_S = 3 * 22.0
#: The lamp from cold, which `DirectScanner.wait_warm` waits out.
WARM_UP_S = 80.0


class RollSeconds(NamedTuple):
    """What a roll or a walk should cost (`roll_seconds`), in seconds: its
    passes at `estimate_seconds`, which `say_estimate` judges at their slow
    end; the rest -- film moves, metering, hold moves, a calibration; and
    what its holds would add past the moves the stored rolls took, were
    every one to take `MAX_HOLD_MOVES` (0 where they are costed at that cap
    already)."""

    passes: float
    other: float
    holds: float = 0.0

    @property
    def typical(self) -> float:
        """The two together, as a person reads the figure."""
        return self.passes + self.other


def roll_seconds(count: int, places: int, *, prescan_dpi: int, dpi: int,
                 infrared: bool, fast_ir: bool, film: str | None = None,
                 meter: str = METER_EACH, placing: int = 0,
                 dry_run: bool = False, calibrating: bool = False,
                 colour_pass: bool = False, holds: str = "cap"
                 ) -> RollSeconds:
    """What a roll of ``count`` frames over ``places`` places on the strip
    should cost, one home for the window, its sheet and
    `tools/scan_roll.py`, which each had their own sum: the window a walk
    frame at 23 s whatever it prescanned at, and 70 s of metering on every
    frame whatever the metering was.

    Every frame is prescanned at ``prescan_dpi`` and, but on a walk, scanned
    at ``dpi`` -- infrared tied or not -- and, where ``colour_pass`` and the
    film wants one (`DirectScanner.wants_colour_pass`), given the RGB pass
    a slide with infrared takes for its colour: the window's scans and
    rolls take it, the tools do not. Metered as ``meter`` says: each frame,
    once for the roll, or not. Every place is an advance forward. Each of
    ``placing`` frames held or aimed takes looks, each a move and a
    prescan: at the cap, ``holds="cap"`` -- what the tool's background
    warning is judged on, and what it always printed -- or as many as the
    stored rolls took, ``holds="seen"`` (`HOLD_MOVES_SEEN`), the rest of
    the cap then said apart (``RollSeconds.holds``). ``calibrating`` adds a
    calibration.

    Its sums keep the tool's order, so its printout is the same bytes
    (``tests/test_scan_roll_tool.py`` pins them).
    """
    prescan = estimate_seconds(prescan_dpi, False)
    scan = (0.0 if dry_run else estimate_seconds(
        dpi, infrared, bool(fast_ir and infrared)))
    colour = (estimate_seconds(dpi, False)
              if not dry_run and colour_pass and film is not None
              and DirectScanner.wants_colour_pass(film, infrared) else 0.0)
    metering = 0.0
    if not dry_run and meter == METER_EACH:
        metering = count * METERING_S
    elif not dry_run and meter == METER_ONCE:
        metering = METERING_S
    seen = holds == "seen"
    looks = placing * (HOLD_MOVES_SEEN if seen else MAX_HOLD_MOVES)
    more = (placing * (MAX_HOLD_MOVES - HOLD_MOVES_SEEN)
            * (prescan + HOLD_MOVE_S) if seen else 0.0)
    return RollSeconds(
        count * (prescan + scan + colour) + looks * prescan,
        places * FORWARD_FRAME_S + metering + looks * HOLD_MOVE_S
        + (CALIBRATION_S if calibrating else 0.0),
        more)


def scan_seconds(dpi: int, infrared: bool, fast_ir: bool,
                 film: str | None = None, metered: bool = True,
                 colour_pass: bool = False) -> float:
    """What a single Scan should cost: its metering, where it meters
    (`METERING_S`), the pass, and the RGB pass a slide with infrared takes
    where the caller takes one (``colour_pass``; the window does). Its own
    pass alone said "about 1m 51s" of a slide at 1800 dpi with infrared
    that takes about four minutes."""
    total = estimate_seconds(dpi, infrared, bool(fast_ir and infrared))
    if (colour_pass and film is not None
            and DirectScanner.wants_colour_pass(film, infrared)):
        total += estimate_seconds(dpi, False)
    return total + (METERING_S if metered else 0.0)


#: A job is refused where a disk it writes to would keep less than
#: `storage.SPACE_RESERVE` free beyond this many times what the job can put
#: there, and warned about below `SPACE_WARN` times it. Stefan's call,
#: 2026-10-09, "1x/2x plus a 2 GB reserve", for every job that writes: they
#: were chosen, not measured, when only a roll was checked. The need is an
#: upper bound already.
SPACE_REFUSE = 1.0
SPACE_WARN = 2.0

#: What a library entry holds besides its raw bytes and its decode, at most
#: a pass: `prescan.tif`, `shading.npz`, `ccd_mask.bin` and `scan.json`.
#: Twice the pixels alone was short: plain 3600 dpi entries measure 214.4 MB
#: at the median against 213.7 MB. An allowance, since no 7200 dpi entry
#: exists to measure.
ENTRY_EXTRA = 8 * 10**6
#: The passes a roll's or a walk's frame leaves in debug filing's spool until
#: close, each at a prescan's size: the ones nothing here files -- holds,
#: aims, metering probes. The last twelve rolls show 0.2-0.7 hold or aim
#: passes a frame, and metering takes up to three probes. An allowance, until
#: a debug-on roll measures it.
SPOOLED_A_FRAME = 4
#: What a calibration writes: its own lines, kept in the library, and the
#: reference, there and in its cache -- about 2 MB, twice over.
CALIBRATION_BYTES = 4 * 10**6


def frame_bytes(resolution: int, infrared: bool) -> int:
    """At most how many bytes one pass's pixels take, uncompressed.

    The transport's whole window (`FULL_FRAME`, in 7200 dpi units) at
    ``resolution``, 16 bits a sample: 570 MB for a 7200 dpi RGBI pass, as
    CLAUDE.md has it. A frame's window is smaller and gzip and deflate take
    more off, so this is the ceiling a disk has to have room for.
    """
    x0, y0, x1, y1 = FULL_FRAME
    width = -(-(x1 - x0 + 1) * resolution // 7200)       # rounded up
    lines = -(-(y1 - y0 + 1) * resolution // 7200)
    return width * lines * (4 if infrared else 3) * 2


#: A single scan's metering probes, which debug filing keeps until close:
#: three 300 dpi RGB passes, each spooled as pixels and raw bytes.
PROBE_ALLOWANCE = 3 * 2 * frame_bytes(300, False)


def pass_list(resolution: int, infrared: bool, film: str | None = None,
              colour_pass: bool = False) -> list[int]:
    """The passes one frame takes, each as `frame_bytes`: the pass asked for,
    and after it a slide's RGB pass, where the caller takes one
    (`DirectScanner.wants_colour_pass`). The window does, for its scans and
    its rolls; the tools take one pass. A roll counted one pass a frame, and
    a slide roll with infrared files two entries a frame (`_file_pair`):
    about 32 GB short over 38 frames at 7200 dpi."""
    passes = [frame_bytes(resolution, infrared)]
    if (colour_pass and film is not None
            and DirectScanner.wants_colour_pass(film, infrared)):
        passes.append(frame_bytes(resolution, False))
    return passes


def scan_space(passes: Sequence[int], *, library_root: Any = None,
               out_dir: Any = None, spool: Any = None
               ) -> list[tuple[Path, int]]:
    """Where one scan's passes go, and at most how much each place takes.

    The library each pass's decode and raw bytes -- about as much again,
    CLAUDE.md -- and `ENTRY_EXTRA`; the output folder one delivered copy,
    which for a slide is one picture the size of the first pass; and debug
    filing's spool each pass twice over until it is filed, and the metering
    probes until close. A place that is None takes nothing.
    """
    total = sum(passes)
    needs: list[tuple[Path, int]] = []
    if library_root is not None:
        needs.append((Path(library_root),
                      2 * total + ENTRY_EXTRA * len(passes)))
    if out_dir is not None and passes:
        needs.append((Path(out_dir), passes[0]))
    if spool is not None:
        needs.append((Path(spool), 2 * total + PROBE_ALLOWANCE))
    return needs


def roll_space(frames: int, resolution: int, infrared: bool,
               library_root: Any = None, roll_folder: Any = None,
               out_dir: Any = None, *, film: str | None = None,
               colour_pass: bool = False, spool: Any = None,
               prescan_resolution: int = 300) -> list[tuple[Path, int]]:
    """Where ``frames`` frames of a roll go, and at most how much each place
    takes.

    The library each frame's passes (`pass_list`), each its decode and raw
    bytes and `ENTRY_EXTRA`, and the frame's prescans -- its own and the one
    an aim replaced, each an entry of its own; the roll folder and the
    output folder one copy each. Debug filing's spool holds a frame until
    the writer has filed it -- so the one being scanned, those waiting for
    the writer and the one it is writing, never the whole roll -- and every
    frame's holds, aims and probes until close (`SPOOLED_A_FRAME`). A place
    that is None takes nothing.
    """
    passes = pass_list(resolution, infrared, film, colour_pass)
    prescan = frame_bytes(prescan_resolution, False)
    one = passes[0]
    needs: list[tuple[Path, int]] = []
    if library_root is not None:
        needs.append((Path(library_root), frames * (
            2 * sum(passes) + ENTRY_EXTRA * len(passes) + 2 * 2 * prescan)))
    for place in (roll_folder, out_dir):
        if place is not None:
            needs.append((Path(place), frames * one))
    if spool is not None:
        waiting = min(frames, 1 + FrameWriter.DEPTH + 1)
        needs.append((Path(spool), 2 * sum(passes) * waiting
                      + 2 * prescan * SPOOLED_A_FRAME * frames))
    return needs


def walk_space(frames: int, prescan_resolution: int, *,
               library_root: Any = None, roll_folder: Any = None,
               out_dir: Any = None, spool: Any = None
               ) -> list[tuple[Path, int]]:
    """Where a walk of ``frames`` frames goes, and at most how much each
    place takes: each frame's prescan and the one an aim replaced, filed in
    the library, written beside the walk's record and into the output
    folder's prescans; and in debug filing's spool, those two and the
    frame's holds and aims until close. A walk was checked for nothing, and
    it writes to all four. A place that is None takes nothing."""
    one = frame_bytes(prescan_resolution, False)
    needs: list[tuple[Path, int]] = []
    if library_root is not None:
        needs.append((Path(library_root), frames * 2 * 2 * one))
    for place in (roll_folder, out_dir):
        if place is not None:
            needs.append((Path(place), frames * 2 * one))
    if spool is not None:
        needs.append((Path(spool), frames * 2 * one * (2 + SPOOLED_A_FRAME)))
    return needs


def check_space(needs: Iterable[tuple[Any, int]],
                factor: float = SPACE_REFUSE,
                reserve: float | None = None) -> tuple[list[str], list[str]]:
    """Each disk too full for ``needs``, and each whose room cannot be read:
    `storage.check_space`, the one test, at `SPACE_REFUSE` unless told."""
    return storage.check_space(needs, factor, reserve)


def short_of_space(needs: Iterable[tuple[Any, int]],
                   factor: float = SPACE_REFUSE) -> list[str]:
    """Each disk `check_space` finds too full, said; for a caller that only
    refuses. A disk whose room cannot be read is in `check_space`'s second
    answer, which this leaves out."""
    return check_space(needs, factor)[0]


def say_leftovers(library_root: Any, rolls: Any = None, say=print) -> None:
    """Say in one line what earlier runs left behind (`library.leftovers`),
    as the window does when it opens: for a tool, before it opens the
    device. A spool of passes nobody filed, a pass the library refused and
    kept elsewhere, were found only by rereading the log that named them.
    Nothing where nothing is left; never raises, and files and compacts
    nothing -- those gzip, which is for a closed scanner.

    And what the trash holds, which a Delete no longer frees
    (`library.trash_said`): the room a run is refused for may be there.
    """
    if library_root is None:
        return
    for look in (lambda: library.leftovers_said(library.leftovers(
                     library_root, rolls=rolls, quick=True)),
                 lambda: library.trash_said(library_root, rolls)):
        try:
            said = look()
        except Exception as exc:                          # noqa: BLE001
            said = (f"could not look for what earlier runs left behind "
                    f"({type(exc).__name__}: {exc})")
        if said:
            say(said)


def say_estimate(passes_s: float, other_s: float, say=print,
                 warn=None) -> float:
    """Say how long a run should take, and when to background it.

    ``passes_s`` is its passes at `estimate_seconds`; ``other_s`` the rest --
    calibration, metering, film moves. The warning is judged on the slow end
    (`SLOW_PASS`, the lamp from cold) rather than the typical: a run warned
    about needlessly costs nothing, and one killed at 10 minutes is a wedge.
    Returns the slow end, in seconds.
    """
    typical = passes_s + other_s
    slow = passes_s * SLOW_PASS + other_s + WARM_UP_S
    say(f"estimated {typical / 60:.1f} min, up to {slow / 60:.1f} on a dense "
        "frame from cold (an estimate from the library's timings)")
    if slow > FOREGROUND_S:
        (warn or say)("  possibly longer than 8 minutes: run it in the "
                      "background. A foreground command killed mid-read "
                      "wedges the scanner.")
    return slow


# ---------------------------------------------------------------------------
# Jobs -- what the UI can ask for
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Approved:
    """Where the operator decided one frame should sit, and the picture he
    decided it from.

    `offset_units` is a signed displacement **relative to where the film sat
    when that frame was surveyed**, in units on the hold's scale, in the same
    sense `nudge_units` and `Move.units` use. It cannot be an absolute
    transport coordinate: the frame counter does not see a sub-frame move, so
    there is no such coordinate to name. Zero is a real answer -- "leave it
    exactly where I saw it" -- and is not the same as having no approval at
    all.

    Everything after `number` is keyword-only. The offset was `offset_mm`
    until 2026-10-13 and was passed by position, so a caller still doing that
    fails here, at construction, rather than asking for a move 9.46 times too
    small.

    `reference` is the prescan he actually looked at, carried by value. It is
    368 KB at 300 dpi and already in memory, and it is byte-identical to the
    pixels on his screen -- which is what "check it against the frame I
    approved" has to mean. `reference_entry` says which library entry that
    pass is. The driver never loads the picture back from it -- the array
    comes by value, and the hold records the name -- and every reader joins
    by the entry's id: the offline studies load the picture by it
    (`tools/roll_registration_study.py`'s remeasure, `tools/
    registration_margin.py`), as `read_survey` and `hold_check` find the
    entry. The window's are the bare id
    (`gui.approved_from_sheet`, since 2026-10-12; ``library\\<id>``
    before); `tools/scan_roll.py` keeps the entry's path where the library
    it was given has it, so `tools/hold_check.py` finds an entry outside the
    library it is pointed at.

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
    _: KW_ONLY
    offset_units: float = 0.0
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
    max_failures: int = 3
    name: str = ""
    out: str = ""
    #: The roll's name as the operator typed it, where the caller worked out
    #: the folder itself and hands over `out` -- the window does, so that it
    #: can say where a roll goes before it asks. Recorded, never a label: the
    #: frames go on calling themselves what the folder records. Without it a
    #: name cut to `NAME_CAP` reached the session as a folder alone, and the
    #: record kept nothing of what was typed.
    typed_name: str = ""
    notes: FilmNotes = field(default_factory=FilmNotes)
    tags: tuple[str, ...] = ()
    #: A walk or roll that starts the next strip of the roll of strips in
    #: `out` (or `name`'s folder): claimed by this run, after the seek
    #: (`claim`), and walked into its own folder. Never with `extend_walk`,
    #: which adds to a strip there is.
    new_strip: bool = False
    #: The strip and first number the question that asked for it named --
    #: ``(strip, first)``, from `plan_next` -- refused before anything moves
    #: (`StripMoved`) where the roll's next strip is another by now. None
    #: takes whatever is next.
    strip_plan: tuple[int, int] | None = None
    #: After the frames in `only`, go on from this frame to the end of the
    #: strip: a reopened roll finished with no last frame, whose frames
    #: left lie among done ones and whose rest no run reached (`roll_left`).
    #: One job, two runs -- the frames left, passing the done ones with the
    #: advance a sheet's unticked frames get, and then a seek on to here and
    #: the rest -- so the driver, the demo and the commands are as they
    #: were (Stefan's call, 2026-10-10). One Stop ends both; a roll metered
    #: once meters again for the second. None for any other roll.
    rest_from: int | None = None


@dataclass(frozen=True, kw_only=True)
class Move:
    """Move the film without scanning anything.

    Two different mechanisms, deliberately in one job so the UI cannot confuse
    them. `frames` steps whole pictures with SLIDE_NEXT/SLIDE_PREV, which the
    transport counts and `READ_STATE` confirms. `units` is a sub-frame
    nudge, which the frame counter does **not** see -- only a prescan can tell
    you it landed, and two to three steps are swallowed after a direction
    change, so a small move that reverses may not move the film at all.
    """

    frames: int = 0                          # +1 next picture, -1 previous
    units: float = 0.0                       # + towards the end of the film


Job = Calibrate | Prescan | Scan | Roll | Move


@dataclass(frozen=True)
class Output:
    """Where a job writes its copies, and as what: fixed as the job starts.

    The window sets the session's `out_dir`, `out_format` and `jpeg_quality`
    from its own thread whenever the operator changes them, and the session
    read them live as each picture was queued. So a folder chosen during a
    roll took the roll's later frames there, past the checks made before the
    film moved -- that the folder is there, and the paths fit -- which had
    covered only the folder the roll began with. Stefan's call, 2026-10-10:
    the three are taken once as each job starts (`ScanSession._run`), and a
    change applies from the next job. A turn and a mirror are not among
    them: those reach the later frames of a roll from the Roll button, as
    they always have. A roll commissioned from the contact sheet keeps each
    approved frame's own (`ScanSession._orientation_for`).
    """

    folder: Path | None
    format: str
    quality: int


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
    #: Its number across its roll of strips -- what its label and delivered
    #: name carry: frame 8 is place 2 of strip 2 (`StripPlace.number`) --
    #: for every frame of a strip, strip 1's included, where it equals the
    #: place. 0 for a pass of a roll from before strips, whose number is its
    #: place (``number``), and of no roll.
    roll_number: int = 0


#: What became of one writer job: see `Filing`.
FILED = "filed"                  # in the library, and every copy written
WRITTEN = "written"              # no entry asked for, and every copy written
COPY_FAILED = "copy not written"  # its pass is safe; a delivered copy is not
NOT_FILED = "not filed"          # its raw bytes are not in the library
#: What a walk prescan's record says before the reason its file is missing.
NOT_WRITTEN = "not written: "


@dataclass(frozen=True)
class Filing:
    """What became of one `FrameWriter` job: exactly one per job.

    The window and `tools/scan_roll.py` used to learn of a failure from the
    words of a log line -- the window by two regular expressions over the
    session's wording, guessing the pass by its frame number, and the tool
    by counting every line it was given -- so a copy that could not be
    written was counted as a pass that could not be filed. This is what
    they read now; the words are for people only (`trouble`).

    ``seq`` is the job's own; ``shown`` the result the window shows it
    under, or None where no result stands for it. ``owner`` is the ordinal
    of the session job that queued it (`ScanSession`), so a filing that
    lands after its job has ended counts against that job and no other.
    ``part`` names which of a frame's writes this was, where it was not the
    frame itself: "infrared pass", "picture" or "prescan before its aim".
    ``called`` is the picture as its result is labelled, the part beside it
    -- "300 dpi RGB", "frame 8 (strip 2 · 2) · 600 dpi RGB + infrared ·
    infrared pass" -- which the words name it by (`named`).

    ``entry`` is set whenever the pass is in the library -- FILED, or
    COPY_FAILED with its own entry. ``partner`` is the entry a slide's
    picture is made from with, when the picture has none of its own and
    its copy could not be written. ``kept`` is where `keep_unfiled` put a
    pass the library refused. ``written`` are the copies on disk, and
    ``unwritten`` each copy that is not, as ``(path, why)``.

    ``sheet`` is a walk prescan's own ``prescanNN.tif``, the one file of
    its copies a reopened contact sheet reads (`walked_prescans`). Only
    that one missing takes the frame off the sheet: said of any copy, the
    output folder's included, an unplugged output drive told the operator
    to walk every frame of a strip again whose sheet was whole.
    """

    seq: int
    number: int
    kind: str
    outcome: str
    shown: int | None = None
    owner: int = 0
    part: str = ""
    entry: Path | None = None
    partner: Path | None = None
    kept: Path | None = None
    written: tuple[Path, ...] = ()
    unwritten: tuple[tuple[str, str], ...] = ()
    error: str | None = None
    sheet: Path | None = None
    called: str = ""

    @property
    def named(self) -> str:
        """The picture as the words name it: as its result is labelled, or
        ``picture N`` where the job said nothing -- `tools/scan_roll.py`'s
        writer. Not ``said``: that is the success line, which the session
        says before the "filed" the 1:1 view and Save As read.

        A single Scan's "Not filed" notice called it "picture 0", a number
        no single pass has, beside a result the window labelled "300 dpi
        RGB" (the batch 3 tour)."""
        return self.called or f"picture {self.number}"

    @property
    def failed(self) -> bool:
        """Whether its raw bytes are not in the library."""
        return self.outcome == NOT_FILED

    def why_unwritten(self, path: str | Path) -> str | None:
        """Why the copy at ``path`` was not written, or None if it was, or
        was never asked for."""
        for unwritten, why in self.unwritten:
            if Path(unwritten) == Path(path):
                return why
        return None

    def copy_said(self, path: str | Path) -> dict[str, str]:
        """What a frame's record says of its copy at ``path``, which this
        job was asked to write: ``file``, its name, once it is written, and
        otherwise ``file_error``, why not.

        Only for a path that was asked for, so it always answers. A filing
        that crashed -- `FrameWriter._run` turning what `_write` did not
        foresee into one with nothing written and nothing said unwritten --
        answered nothing, and every reader then took a ``frameNN.tif`` on
        the disk, an earlier take's, for this one's."""
        target = Path(path)
        if any(Path(p) == target for p in self.written):
            return {"file": target.name}
        why = self.why_unwritten(target)
        if why is None:
            why = str(self.error) if self.error else "not written"
        return {"file_error": why}

    def trouble(self) -> list[str]:
        """What went wrong, for people: empty unless NOT_FILED or COPY_FAILED.

        Never the success line (`said`), so a list of these -- the writer's
        `errors`, a tool's stderr -- holds only what needs reading.
        """
        if self.outcome == NOT_FILED:
            return [f"{self.named} could not be filed: {self.error}"]
        if self.outcome != COPY_FAILED:
            return []
        # One line per reason: a one-channel copy that could not be made
        # costs every copy of the pass for the one reason.
        reasons: dict[str, list[str]] = {}
        for path, why in self.unwritten:
            reasons.setdefault(why, []).append(path)
        return [f"{self.named}: could not write {', '.join(paths)} "
                f"({why}); {self._safe(paths)}"
                for why, paths in reasons.items()]

    def _safe(self, paths: list[str]) -> str:
        """What still holds a pass whose copies at ``paths`` are missing,
        and what puts them back."""
        if self.entry is None:
            # A slide's picture, which is no pass and has no entry: it is
            # made again from its two. Nothing is said of the RGB pass,
            # which is filed after this and speaks for itself.
            return ("the picture is not kept itself -- it is made from the "
                    "frame's two passes, and its infrared pass is in the "
                    f"library as {self.partner.name if self.partner else '?'}")
        if self.sheet is not None and any(Path(p) == self.sheet
                                          for p in paths):
            return (f"its library entry {self.entry.name} is safe, but a "
                    "reopened contact sheet will not show this frame -- "
                    "walk it again from the window, keeping the sheet")
        if self.part:
            # The picture from before an aim: kept to compare against, and
            # in the library whole. No result stands for it to save again.
            return f"its library entry {self.entry.name} is safe"
        return (f"its library entry {self.entry.name} is safe -- Save As "
                "or Export in the window writes it again")

    def said(self) -> str | None:
        """The success line, where there is an entry to name."""
        if self.entry is None or self.failed:
            return None
        return f"filed: {self.entry.name}"


@dataclass(frozen=True)
class Event:
    kind: str                                # see KINDS
    text: str = ""
    done: int = 0
    total: int = 0
    result: Result | None = None
    busy: bool = False
    filing: Filing | None = None
    job: Any = None
    detail: str = ""                         # a failure's traceback

#: "failed" carries its traceback in `detail`, for the window's kept log
#: alone: its pane says the one line in `text`. Every failure has one,
#: refusal or bug -- both are ValueError, OSError and RuntimeError here, so
#: no type tells them apart -- and an overnight roll that died of a bug is
#: diagnosed from it.
#: "state" with ``busy`` carries the job's ordinal in `done`: 1 for the
#: session's first job, counting up. Each `Filing` names the job that queued
#: it by that number (`Filing.owner`). And the job itself in `job`, as it was
#: handed over: which roll is running is the job's to say -- its folder, its
#: approvals -- not the window's guess from what it handed over last.
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
#: "filing" is sent once for every writer job, whatever became of it: the
#: `Filing` in `filing`, the result it is shown under in `done` (0 for
#: none), its frame number in `total` and its outcome in `text`. It is what
#: a window marks a pass by -- never the words of a log line.
#: "reference_moved" is a scan or a roll refused before it moved anything,
#: because the scanner's gain or offsets are no longer the ones the shading
#: reference was taken at; `text` says which. The window offers a
#: calibration, or `accept_reference` to scan with it anyway.
#: "dropped" is a job handed over and let go of without being started -- by
#: a stop, Quit's halt or the session closing -- with the job in `job` and
#: its description in `text`. It is the only word such a job gets: every
#: other job is said by a busy "state" and then "finished" or "failed".
KINDS = ("state", "log", "progress", "result", "filed", "filing",
         "transport", "calibrated", "reference_moved", "finished", "failed",
         "dropped", "closed", "blanks")
#: "blanks" is a roll or walk that skipped places with no picture
#: (`RollFrame.blank`), sent once as it ends: `text` says which and whether
#: they ended it, `done` is 1 where they ended it short of the frames asked
#: for (`ended_early`) and 0 otherwise -- a run to the end of the strip that
#: ends on two blanks included, which found the strip's end. The window says
#: an early end prominently (Stefan's "blank-frame" decision, 2026-10-13),
#: and the rest in its log.


# ---------------------------------------------------------------------------
# The writer thread
# ---------------------------------------------------------------------------


#: The folder a picture the library refused is kept in instead, beside the
#: delivered copies it was written with; see :func:`keep_unfiled`. And where
#: it goes when there is no such folder, or it refuses as well. Named in the
#: library, whose `leftovers` looks for them.
UNFILED = library.UNFILED
UNFILED_TEMP = library.UNFILED_TEMP


def keep_unfiled(image: np.ndarray, meta: dict[str, Any], near=(),
                 compress: bool = False, refused_by: Any = None,
                 why: str = "",
                 **save: Any) -> tuple[Path | None, list[str]]:
    """File a picture the library refused somewhere else, whole.

    The pixels and raw bytes in hand are the only copy of the pass: nothing
    re-reads them from the scanner, and with RPS7200_DEBUG off nothing spooled
    them either. When `library.save` raised -- a full library disk, an
    unplugged drive, a folder that will not take a file -- they used to be
    dropped with the job, so the pass was lost although the disk the roll's
    own TIFF had just been written to still had room for it.

    Kept as an ordinary entry, raw bytes and reference and mask beside the
    pixels, so moving the folder into the library files it: tried in an
    `unfiled` folder beside each of ``near`` -- the folders a delivered copy
    was just written to, which are known to take a file -- and then in the
    system's temporary directory. Written plain unless ``compress``, like
    everything filed with the scanner open, and then `library.compact` is
    the caller's once it has closed: the window's close does it, and
    `tools/scan_roll.py` after its writer finishes. A caller filing with the
    device already closed -- `tools/scan.py` -- compresses it at once; plain,
    and nothing compacting it, it stayed raw.bin and uncompressed TIFFs for
    good. ``save`` is what `library.save` takes besides the pixels, meta and
    root.

    ``refused_by`` is the library that refused it, and ``why`` its reason.
    The entry records the library (`extra.unfiled_from`, a key added to a
    copy of the scan's meta, as `roll_membership` reaches `extra`), and a
    pointer to where it went is written into that library's
    `unfiled-elsewhere/` -- or the temporary directory's, where the library
    will not take even that. `library.leftovers` finds a kept pass by
    either, and `make verify` fails until it is moved in: before, a pass
    kept beside an output folder was found by nothing but the log line
    that named it.

    Returns the entry, or None, and why each place refused it.
    """
    import tempfile

    places: list[Path] = []
    for folder in [*(Path(p) / UNFILED for p in near),
                   Path(tempfile.gettempdir()) / UNFILED_TEMP]:
        if folder not in places:
            places.append(folder)
    save = dict(save, compress=compress,
                tags=sorted({*(save.get("tags") or ()), UNFILED}))
    named = library_named(refused_by) if refused_by is not None else None
    if named is not None:
        meta = dict(meta, unfiled_from=named)
    refused = []
    for root in places:
        try:
            kept = library.save(image, meta, root=root, **save)
        except Exception as exc:                         # noqa: BLE001
            refused.append(f"{root}: {exc}")
            continue
        if named is not None:
            _point_to_unfiled(Path(refused_by), named, kept, why)
        return kept, refused
    return None, refused


def _point_to_unfiled(library_root: Path, named: str, kept: Path,
                      why: str) -> None:
    """Write down in the library that refused a pass where it was kept
    (`keep_unfiled`); in the temporary directory where the library will
    not take that either. Never raises: the pass is kept, and this only
    helps find it."""
    import tempfile

    pointer = json.dumps({
        "library": named, "kept": library_named(kept), "why": str(why),
        "utc": _utc_now()}, indent=2)
    name = f"{Path(kept).name}.json"
    for folder in (library_root / library.UNFILED_ELSEWHERE,
                   Path(tempfile.gettempdir()) / library.UNFILED_POINTERS_TEMP):
        try:
            # The library's own folder at most, never the ones above it: a
            # library on a drive that has gone has its pointer kept in the
            # temporary directory instead.
            if folder.parent == library_root:
                storage.make_within(library_root, folder)
            else:
                folder.mkdir(parents=True, exist_ok=True)
            library._write_atomic(folder / name, pointer)
            return
        except Exception:                                 # noqa: BLE001
            continue


def kept_said(kept: Path | str) -> str:
    """What to do with a pass `keep_unfiled` kept, said after where it is.

    The same words for a folder beside a delivered copy and one in the
    system's temporary directory hid the difference that matters: Windows'
    Disk Cleanup and Storage Sense empty %TEMP%, and a slide's passes and a
    real roll's prescans, which have no copy to sit beside, always land
    there.
    """
    import tempfile

    try:
        Path(kept).resolve().relative_to(Path(tempfile.gettempdir()).resolve())
    except (OSError, ValueError):
        return "move that folder into the library to file it"
    return ("it is in the system's temporary folder, which Windows may clear "
            "-- move it into the library now")


def _named(job: dict) -> str:
    """A writer job's picture as its lines name it (`Filing.named`): as its
    result is labelled, where the job says (``called``), else by its
    number."""
    called = job.get("called")
    if isinstance(called, str) and called:
        return called
    return f"picture {job.get('number')}"


class FrameWriter:
    """Writes finished frames to disk on a thread, off the scanning loop.

    Filing a frame gzips its raw bytes -- seconds at 1800 dpi and several times
    that at 3600 -- and doing it inline leaves the scanner **open and idle** for
    exactly that long, once per frame. That is the state that preceded a wedge
    (see CLAUDE.md). On this thread the write instead overlaps the next frame's
    scan, so the device is busy rather than idle throughout -- where there is
    a next frame. A roll's last one has none, and is written plain (`idle`).

    What waits is bounded (`DEPTH`). A scan costs far longer than a write, so
    the writer is normally idle waiting; a bound only matters if that stops
    being true, and then blocking is right -- an unbounded queue would hold
    whole frames in memory, and at 3600 dpi one frame is over a hundred
    megabytes.

    Failures are collected, not raised: a roll runs for hours, and a frame that
    cannot be filed should cost that frame, not the thirty after it. Every
    job ends in exactly one `Filing`, kept in `filings` and reported once:
    to ``on_done``, then to the job's own ``on_filed``. `errors` is their
    words for people, drained by the caller once the roll ends; nothing
    counts it.

    A job's optional keys beside what it writes: ``seq`` and ``shown``,
    ``owner``, ``part``, ``sheet`` (see `Filing`); ``pair``, the seq of the
    job whose entry a picture with none of its own is made from;
    ``claims``, the output names `export.claim` reserved for it, released
    once the job is over; ``unwritten``, copies refused before the job was
    queued; ``roll_folder``, the roll folder its own copy goes in, which is
    never made again once it has gone (`_folder_for`).
    """

    #: Two frames waiting, besides the one being written. Frames, not jobs:
    #: a roll's prescans are jobs of their own now, filed raw in their own
    #: entries, one or two ahead of each frame and a few hundred kilobytes
    #: each, and they wait against a bound of their own (`PRESCANS`).
    #: Counted as jobs, two stopped the scanning thread after one frame, and
    #: the four that made room for the prescans was four frames wherever
    #: none is queued -- `tools/scan_roll.py --no-library` on a real roll --
    #: where a 7200 dpi RGBI frame job holds about 1.7 GB.
    DEPTH = 2
    #: Two frames' prescans: the picture each was framed on, and the one a
    #: hold or an aim replaced.
    PRESCANS = 4

    def __init__(self, depth: int = DEPTH, on_done: Any = None,
                 idle: Callable[[], bool] | None = None):
        #: Asked as each job starts, where a device stays open between jobs:
        #: True once nothing is scanning. A job queued to compress is then
        #: written plain instead, and compacted once the device has closed.
        #: A roll's frames compress on this thread because the next frame is
        #: scanning meanwhile; one still queued when the roll has ended has
        #: no next frame, and gzipped then it is the open-and-idle state
        #: again. None where the device closes before the writer finishes.
        #: Asked once more before the delivered copies: a roll that ended
        #: while the entry was being written leaves them nothing to overlap.
        self.idle = idle
        # One queue, so jobs are written in the order they came -- a walk's
        # record names the last prescan filed under its number -- and bounded
        # by what waits in it rather than by its length (`submit`).
        self.queue: queue.Queue = queue.Queue()
        self._frames = threading.BoundedSemaphore(depth)
        self._prescans = threading.BoundedSemaphore(self.PRESCANS)
        #: Every job's `Filing`, in the order they were written.
        self.filings: list[Filing] = []
        #: What the filings said went wrong, for people (`Filing.trouble`),
        #: and the reports that could not be made (`problems`).
        self.errors: list[str] = []
        #: A report that raised -- ``on_done`` or a job's ``on_filed`` --
        #: with the outcome it was reporting. The filing itself stands.
        self.problems: list[str] = []
        # Not failures: things the chosen format could not carry, like the
        # infrared plane in a JPEG. Drained per frame by `_filed` so they are
        # read while the roll is running, not at the end of it.
        self.notes: list[str] = []
        self.done: list[tuple[int, Path | None]] = []
        # Called with each job's `Filing` as it lands, so a UI can show where
        # a frame went without polling `done`.
        self.on_done = on_done
        #: Entries filed with ``compress=False``, for `library.compact` once
        #: the scanner is closed.
        self.uncompressed: list[Path] = []
        #: What each job not yet written will put where, by the job: see
        #: `queued_needs`. Added on the scanning thread, taken off on this one.
        self._queued: dict[int, list[tuple[Path, int]]] = {}
        self._queued_lock = threading.Lock()
        #: Why the thread last ended other than at `finish`'s None, and how
        #: often it has been started again since (`_revive`).
        self.died: str | None = None
        self.restarts = 0
        #: Whether the thread has taken `finish`'s None: once it has, a
        #: thread no longer running is one that is done, not one that died.
        self._ended = False
        self._revive_lock = threading.Lock()
        # A daemon on purpose. Every way out joins it through `finish` --
        # `tools/scan_roll.py` from a `finally`, under a Ctrl-C deferral of
        # its own -- so the only exit that leaves it behind is the operator
        # insisting on Ctrl-C while it files, and an interpreter that waited
        # for it would make that insisting wait for every queued frame
        # anyway. Windows ending the process as its console closes ends
        # every thread, daemon or not.
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="filing")
        self._thread.start()

    def _room(self, job: dict) -> threading.BoundedSemaphore:
        """The bound a job waits against: see `DEPTH`."""
        return self._prescans if job.get("kind") == "prescan" else self._frames

    def _run(self) -> None:
        while True:
            job = self.queue.get()
            if job is not None:
                # No longer waiting once taken: the bound is on what waits,
                # as the queue's length was, the job being written on top.
                self._room(job).release()
            #: Whether this job's `Filing` has gone to `_report`, which
            #: reports each one once, however its reports then fare.
            reported = False
            try:
                if job is None:
                    self._ended = True
                    return
                try:
                    # Stamped, so the pass this overlaps can say so
                    # (`hostload`): the scanning thread's next prescan, a
                    # hold, the window's next Scan.
                    with hostload.stamp("filing"):
                        filing = self._write(job)
                except Exception as exc:                 # noqa: BLE001
                    # What `_write` did not foresee. Still one record, and a
                    # failure: nothing says the pass reached the library.
                    filing = self._fallback(job, exc)
                reported = True
                try:
                    self._report(job, filing)
                except Exception as exc:                 # noqa: BLE001
                    # Raised from here, it would end this thread, and every
                    # filing after this one: the next `submit` would block
                    # the scanner for good.
                    said = (f"{_named(job)}: its filing could not be "
                            f"kept ({type(exc).__name__}: {exc})")
                    self.problems.append(said)
                    self.errors.append(said)
            except BaseException as exc:
                # What still ends this thread: no `Exception`, so nothing
                # above could take it as a filing. Its job is reported as
                # not filed if it had no report yet -- once, as every job is
                # -- and the thread is let end, its traceback to
                # `threading.excepthook`. The next `submit` or `finish`
                # starts another on the same queue (`_revive`).
                self.died = f"{type(exc).__name__}: {exc}"
                if job is not None and not reported:
                    try:
                        self._report(job, self._fallback(job, exc))
                    except BaseException:                # noqa: BLE001
                        pass
                raise
            finally:
                # Each on its own, and `task_done` always: one that raised
                # here ended the thread with the queue's count left owing,
                # and a reader waiting for it to reach nothing -- the tour's
                # `still_going` -- waited for good.
                if job is not None:
                    try:
                        # Whatever came of it, the names it reserved are free
                        # again: a job that failed before its copies held
                        # them for the session, and a retake took `-2` for
                        # nothing.
                        export.release(job.get("claims") or ())
                    except Exception as exc:             # noqa: BLE001
                        self._kept_going(job, "its reserved names", exc)
                    try:
                        # And its bytes are on the disk now, or never will be.
                        with self._queued_lock:
                            self._queued.pop(id(job), None)
                    except Exception as exc:             # noqa: BLE001
                        self._kept_going(job, "what it was to write", exc)
                try:
                    self.queue.task_done()
                except ValueError:
                    pass

    def _fallback(self, job: dict, exc: BaseException) -> Filing:
        """The `Filing` of a job whose writing raised: not filed.

        Built by hand where the job's own fields will not make one -- a
        number that is no number, a sheet that is no path. That raised from
        inside the handler meant to catch the first failure, and ended this
        thread with the job never reported.
        """
        try:
            return self._filing(job, NOT_FILED, error=str(exc))
        except Exception as also:                         # noqa: BLE001
            def whole(key: str) -> int:
                try:
                    return int(job.get(key) or 0)
                except (TypeError, ValueError):
                    return 0
            called = job.get("called")
            return Filing(seq=whole("seq"), number=whole("number"),
                          kind="scan", outcome=NOT_FILED,
                          owner=whole("owner"),
                          called=called if isinstance(called, str) else "",
                          error=f"{exc}; and its record could not be made "
                                f"({type(also).__name__}: {also})")

    def _kept_going(self, job: dict, what: str, exc: Exception) -> None:
        """A tidy-up after a job that raised, said and passed over."""
        said = (f"{_named(job)}: {what} could not be let go "
                f"({type(exc).__name__}: {exc})")
        self.problems.append(said)
        self.errors.append(said)

    def _revive(self) -> None:
        """Start the thread again where it died, on the same queue.

        It is guarded so that nothing a job does should end it, but it is a
        thread, and one that ended left `submit` waiting for room nothing
        would ever make -- the scanner held between passes, the window busy
        for good -- and `finish` returning at once with every queued frame,
        raw bytes and all, never written. Started again on the queue it
        left, every job behind it is written as it would have been, in the
        order it came and with its own compression. Said once each time, in
        `errors` and in `notes`, which the window reads as the roll runs.
        """
        with self._revive_lock:
            if self._thread.is_alive() or self._ended:
                return
            self.restarts += 1
            why = self.died or "no reason was given"
            said = (f"the filing thread stopped ({why}) and was started "
                    "again; nothing queued was lost")
            self.errors.append(said)
            self.notes.append(said)
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name="filing")
            self._thread.start()

    @staticmethod
    def _needs(job: dict) -> list[tuple[Path, int]]:
        """What one job will put where, at most: the library its pixels and
        raw bytes, about as much again where it carries no raw pixels, and
        each copy's folder the pixels once."""
        image = job.get("image")
        size = int(getattr(image, "nbytes", 0) or 0)
        needs: list[tuple[Path, int]] = []
        if job.get("library"):
            raw = job.get("raw_image")
            needs.append((Path(job["library"]), size + (
                int(getattr(raw, "nbytes", 0) or 0) if raw is not None
                else size)))
        for path in job.get("paths") or ():
            needs.append((Path(path).parent, size))
        return needs

    def queued_needs(self) -> list[tuple[Path, int]]:
        """What the jobs queued and not yet written will put on disk, and
        where, for a free-space check (`storage.check_space`).

        A Scan returns once its pass is queued, so the next job's check read
        a disk that did not have it on yet; two Scans queued behind one
        another were each let through where only one fitted.
        """
        with self._queued_lock:
            return [need for needs in self._queued.values() for need in needs]

    @staticmethod
    def _filing(job: dict, outcome: str, **fields: Any) -> Filing:
        """This job's `Filing`, with what the job itself says of it."""
        seq = int(job.get("seq", 0) or 0)
        shown = job.get("shown")
        sheet = job.get("sheet")
        return Filing(seq=seq, number=int(job.get("number") or 0),
                      kind=str(job.get("kind") or "scan"), outcome=outcome,
                      shown=seq if shown is None else shown,
                      owner=int(job.get("owner") or 0),
                      part=str(job.get("part") or ""),
                      sheet=Path(sheet) if sheet is not None else None,
                      called=str(job.get("called") or ""),
                      **fields)

    def _report(self, job: dict, filing: Filing) -> None:
        """Keep one job's `Filing`, and report it once to each who asked.

        ``on_done`` first -- the session's "filed" -- and then the job's own
        ``on_filed`` -- its manifest -- as it always was. Each guarded on its
        own: raised from here, a report ended this thread and every filing
        behind it, and a report that raised on success was taken for the
        frame's failure -- told twice, recorded not done, and its spooled
        copy filed a second time by debug filing although the entry was on
        disk throughout.
        """
        self.filings.append(filing)
        self.errors.extend(filing.trouble())
        if not filing.failed:
            self.done.append((filing.number, filing.entry))
        for who, report in (("on_done", self.on_done),
                            ("on_filed", job.get("on_filed"))):
            if report is None:
                continue
            try:
                report(filing)
            except Exception as exc:                     # noqa: BLE001
                said = (f"{filing.named}: {filing.outcome}, but it "
                        f"could not be reported ({who}: {exc})")
                self.problems.append(said)
                self.errors.append(said)

    def _write(self, job: dict) -> Filing:
        # The delivered files carry the orientation that was asked for and the
        # shading correction; the library entry below carries neither. Its
        # pixels have to stay exactly what the scanner sent, or they stop
        # matching the raw bytes beside them and `library.reconstruct` is right
        # to call it a changed decode. `raw_image` is that; `image` is what the
        # operator asked to be given.
        turned = preview.orient(job["image"], job.get("rotate") or 0,
                                bool(job.get("flip")))
        # The library entry first, the operator's copies after. A copy can be
        # written again from the entry at any time; the entry cannot be written
        # again from anything, because it holds the only raw bytes. Written the
        # other way round, an unplugged drive or a full output folder raised
        # before `library.save` ran, and the scan's raw bytes were lost with it.
        entry = None
        #: Why the library would not take this picture, if it would not.
        refused: Exception | None = None
        #: What it would have been filed with, kept for `keep_unfiled`.
        pixels: np.ndarray = job["image"]
        filing: dict[str, Any] = {}
        #: Whether this job compresses -- its entry and its copies alike.
        compress = job.get("compress", True)
        if compress and self.idle is not None and self.idle():
            compress = False                  # see `idle`
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
                    f"{_named(job)}: no raw pixels came with this pass; "
                    "filed its corrected pixels, labelled as corrected")
            if raw_image is not None:
                pixels = raw_image
            filing = dict(
                film=job["film"],
                tags=job["tags"],
                prescan=job["prescan"],
                prescan_meta=job.get("prescan_meta"),
                inquiry=job["inquiry"],
                corrections=corrections,
                **job["capture"],
            )
            try:
                entry = library.save(pixels, job["meta"], root=job["library"],
                                     compress=compress, **filing)
            except Exception as exc:                     # noqa: BLE001
                # Not raised yet. The copies below are still written -- the
                # corrected picture can reach a drive the library is not on --
                # and the raw data is kept somewhere, before the job is
                # reported as the failure it is. Raised here, both went with
                # it: a full library disk lost the frame entirely, and the
                # frame after it the same way.
                refused = exc
            else:
                if not compress:
                    self.uncompressed.append(entry)
                # Bytes that cannot be this picture's are filed all the same,
                # and `library.save` warns -- on stderr, which the window's
                # log pane never shows. Said here too, where it is read.
                try:
                    said = library.raw_disagreement(json.loads(
                        (Path(entry) / "scan.json").read_text(encoding="utf-8")))
                except (OSError, TypeError, ValueError, AttributeError):
                    said = None
                if said:
                    self.notes.append(f"{_named(job)}: {said}")
                # And a marker it could not take away afterwards, which it
                # warns of the same way, on stderr alone. One stat.
                try:
                    left = (Path(entry) / library.INCOMPLETE).exists()
                except OSError:
                    left = False
                if left:
                    self.notes.append(
                        f"{_named(job)}: {Path(entry).name} is "
                        f"filed, but its {library.INCOMPLETE} marker could "
                        "not be removed; `make verify` names it, and it can "
                        "be deleted once verify finds the entry otherwise "
                        "whole")
        #: Each copy that is not on disk, and why: refused a name before the
        #: job was queued (`ScanSession._file`), or refused here.
        unwritten: list[tuple[str, str]] = [
            (str(p), str(why)) for p, why in job.get("unwritten") or ()]
        written = []
        paths = list(job.get("paths") or ())
        # One channel on the way out, three in the library. A consumer cannot
        # tell black and white from a slide by looking at the pixels -- see
        # rps7200/mono.py -- so the file it reads has to say so by its shape.
        # Made after the entry, as part of the copies: a channel
        # `to_monochrome` refuses -- one a roll manifest or a hand-edited
        # preset put back -- raised above `library.save`, and the pass's raw
        # bytes went with it.
        delivered = turned
        #: Whether the copies compress: as the entry did, unless the roll
        #: has ended since it was begun (P13-a). The entry's gzip, already
        #: under way, is not stopped part way -- `uncompressed` could not
        #: say what such an entry was -- but the copies' deflate, 1.6-4.5 s
        #: a 3600 dpi frame, is a choice still open, and they are written
        #: plain as a roll's last frame's are. They stay so: a delivered
        #: copy is never compacted.
        copy_compress = compress
        if copy_compress and paths and self.idle is not None and self.idle():
            copy_compress = False
        if paths and job.get("mono"):
            try:
                delivered = to_monochrome(
                    turned, job.get("mono_channel") or MONO_CHANNEL)
            except ValueError as exc:
                unwritten += [(str(p), f"its one-channel copy could not be "
                                       f"made: {exc}") for p in paths]
                paths = []
        for path in paths:
            # Each copy on its own: one that cannot be written -- a missing
            # drive, a full disk -- says so and does not stop the others.
            try:
                self._folder_for(Path(path), job.get("roll_folder"))
                # The format comes from the name, so one call covers both: a
                # roll's own `rolls/...tif` and an output folder set to JPEG are
                # written correctly side by side without this having to know
                # the setting.
                # Plain when the entry is, for the same reason: a single pass
                # is written with the scanner open and idle, and the deflate
                # of a full-resolution copy is the work that must not happen
                # then. Its library entry is compacted after close; a
                # delivered copy stays as written, larger and lossless.
                # Written beside and renamed over, whatever the format, so a
                # frame retaken or walked again is the old file or the new
                # one, never a truncated one (`export._whole`).
                note = export.write(str(path), delivered, resolution=job["dpi"],
                                    quality=job.get("quality")
                                    or export.DEFAULT_QUALITY,
                                    compress=copy_compress)
            except Exception as exc:                     # noqa: BLE001
                unwritten.append((str(path), str(exc)))
                continue
            written.append(Path(path))
            if job.get("mono"):
                note = "; ".join(filter(None, (note,
                                               infrared_left_out(turned))))
            if note:
                self.notes.append(f"{Path(path).name}: {note}")
        problems = [f"could not write {p} ({why})" for p, why in unwritten]
        copies = dict(written=tuple(written), unwritten=tuple(unwritten))
        if refused is not None:
            kept, elsewhere = keep_unfiled(
                pixels, job["meta"], near=[p.parent for p in written],
                refused_by=job["library"], why=str(refused), **filing)
            said = [f"could not be filed in the library ({refused})"]
            if kept is not None:
                self.uncompressed.append(kept)
                said.append(f"its raw data is kept in {kept} -- "
                            + kept_said(kept))
            else:
                said.append("and could not be kept anywhere else either ("
                            + "; ".join(elsewhere) + ")")
            if written:
                said.append("written to " + ", ".join(str(p) for p in written))
            # Still a failure: the picture is not in the library, and a roll
            # stops on it (`ScanSession._filed`).
            return self._filing(job, NOT_FILED, kept=kept,
                                error="; ".join(said + problems), **copies)
        if entry is not None:
            return self._filing(job, COPY_FAILED if unwritten else FILED,
                                entry=entry, **copies)
        if not unwritten:
            return self._filing(job, WRITTEN, **copies)
        # No entry asked for, and a copy that could not be written. Not a
        # failure when the picture is a slide's, made from two passes whose
        # infrared one is in the library (`ScanSession._file_pair`): it can
        # be made again from them. A failure otherwise -- the copies were
        # the picture's only home -- and one that still names the copies
        # written, which a bare OSError lost.
        partner = next((f for f in reversed(self.filings)
                        if job.get("pair") is not None
                        and f.seq == job["pair"]), None)
        if partner is not None and partner.entry is not None \
                and not partner.failed:
            return self._filing(job, COPY_FAILED, partner=partner.entry,
                                **copies)
        return self._filing(job, NOT_FILED, error="; ".join(problems),
                            **copies)

    @staticmethod
    def _folder_for(path: Path, roll_folder: Any = None) -> None:
        """See that a copy's folder is there, making only an output folder's
        `PRESCAN_SUBDIR` below an output folder that is.

        Every copy's folder used to be made with its parents, so an output
        folder iCloud had taken away, or a stick pulled out mid-roll, was
        made again as a plain folder on C: and filled there. A folder that
        has gone stays gone now: the copy is not written and says why, and
        its pass is filed all the same. ``roll_folder`` is the job's roll
        folder, said as what it is when it is the one that went: renamed or
        deleted by hand, while the roll's lock kept every program off it.
        """
        parent = path.parent
        if parent.name == PRESCAN_SUBDIR and _is_dir(parent.parent):
            parent.mkdir(exist_ok=True)
            return
        if (roll_folder is not None and parent == Path(roll_folder)
                and not _is_dir(parent)):
            raise storage.DriveMissing(
                f"{parent} is gone -- moved or deleted while its frames were "
                "filed -- and is not made again")
        storage.require_dir(parent, remedy=(
            "A folder that has gone is not made again here, so nothing is "
            "written on another disk in its place"))

    def submit(self, **job) -> None:
        """Queue one job, waiting while its kind's bound is full. ``kind``
        "prescan" waits against `PRESCANS`; anything else is a frame.

        Waited for in slices, the thread looked at between them: a thread
        that had died left this waiting for good (`_revive`)."""
        room = self._room(job)
        while not room.acquire(timeout=0.5):
            self._revive()
        self._revive()
        with self._queued_lock:
            self._queued[id(job)] = self._needs(job)
        self.queue.put(job)

    def finish(self) -> None:
        """Wait for every queued frame. Call after the session has closed.

        Until the thread has taken the None put here, not only until it has
        ended: one that died first is started again, and each start writes
        at least the job it takes, so this ends."""
        self.queue.put(None)
        while True:
            self._thread.join()
            if self._ended:
                return
            self._revive()


# ---------------------------------------------------------------------------
# The session
# ---------------------------------------------------------------------------


def compact_after_close(entries: Iterable[Any],
                        say: Callable[[str], None]) -> None:
    """Gzip what was filed plain while the device was open, now that it has
    closed (`library.compact`): the window's and the roll tool's last frames,
    and passes kept beside a roll's copies.

    One that has gone since is said, and passed over: Delete moves an entry
    into the trash at once, and a roll's Delete takes its `unfiled/` with
    it. Compacted, its FileNotFoundError said "could not compress ... it
    stays uncompressed and complete" of an entry that was not there.
    """
    for entry in entries:
        if not Path(entry).is_dir():
            say(f"{entry} is no longer there -- moved to the trash, or its "
                "roll folder was -- and stays uncompressed; `tools/library.py "
                "compact --write` gzips it if it is restored into the library")
            continue
        try:
            library.compact(entry)
        except (OSError, ValueError) as exc:
            say(f"could not compress {entry}: {exc}; it stays uncompressed "
                "and complete")


def close_device(scanner: Any) -> None:
    """Close the scanner's transport, and do nothing else.

    `DirectScanner.close()` closes the transport and then files what debug
    filing spooled. A caller that files its own passes wants those two apart:
    the device closed before any filing starts -- gzipping with it open and
    idle preceded a wedge -- and debug filing after its own filing has landed,
    because a pass is claimed from it only once it is filed. `close()` is
    still called afterwards, and closing a closed transport is nothing.

    A transport the scanner was handed is not its own to close, and is left.
    """
    if not getattr(scanner, "_own_transport", True):
        return
    transport = getattr(scanner, "t", None)
    if transport is not None:
        transport.close()


def debug_filing_into(scanner: Any, root: Any) -> None:
    """Point debug filing (RPS7200_DEBUG=1) at ``root``, the caller's library.

    It filed into RPS7200_DEBUG_ROOT, or `./library`, whatever library the
    run itself files into: a tool run with `--library /mnt/ext/library` put
    its frames there and every probe, hold and framing prescan behind them
    in the laptop's ./library, where nothing looking at that roll would find
    them. Set as the scanner opens, so the spool that holds them until they
    are filed is beside that library too (`DirectScanner.debug_root`). An
    RPS7200_DEBUG_ROOT the operator set still wins.

    And the session's calibrations are kept there, debug or not
    (`DirectScanner.library_root`): their lines explain every reference the
    library's entries carry, and RPS7200_DEBUG_ROOT does not move them.
    """
    if root and hasattr(scanner, "library_root"):
        scanner.library_root = root
    if (not root or os.environ.get(DirectScanner.DEBUG_ROOT_ENV)
            or not hasattr(scanner, "debug_root")):
        return
    scanner.debug_root = root


def reference_refused(path: Any, calibrating: bool = True) -> str | None:
    """Why a tool's ``--reference`` must not be ``path``, or None.

    It names the cache file, never its folder: given the shared
    ``calibration`` folder -- the folder a default-folder refusal names
    -- a reused reference cannot be read, and a calibration ran its
    minutes and then could not be saved over it. Refused whether or not
    this run calibrates.

    Calibrating replaces the file it is given. Given a library entry's
    shading.npz -- a command line from history, less its --reuse -- it
    replaced that entry's own reference, the one its checksum names (and,
    while the calibration's bytes were written beside the cache, put a
    folder inside the entry). A kept calibration's own shading.npz is the
    same: the reference its lines reduced to, which `tools/library.py
    calibrations` compares today's reduction with.
    """
    if Path(path).is_dir():
        return (f"--reference {path} is a folder; it names the reference "
                f"file in one, such as {Path(path) / 'shading.npz'}.")
    if not calibrating:
        return None
    folder = Path(path).parent
    if (folder / "scan.json").exists():
        return (f"--reference {path} is inside the library entry {folder}; "
                "calibrating would replace that entry's own reference. Give "
                "a --reference outside the library.")
    if (folder / "calibration.json").exists():
        return (f"--reference {path} is inside the kept calibration {folder}; "
                "calibrating would replace the reference its lines reduced "
                "to. Give a --reference outside the library.")
    return None


def say_reused(path: Any, say=print) -> None:
    """Say how old a cached reference is, before a run reuses it.

    It describes the sensor at the lamp, exposure and gain of the pass that
    measured it, and becomes the reference of every entry the run files --
    and `--reuse` took one of any age, from any power-on, without a word.
    """
    try:
        hours = (time.time() - Path(path).stat().st_mtime) / 3600.0
    except OSError:
        return
    age = (f"{hours * 60:.0f} minutes" if hours < 1
           else f"{hours:.1f} hours" if hours < 48 else f"{hours / 24:.0f} days")
    say(f"reusing the reference cached {age} ago in {path}; it describes the "
        "sensor as it was then -- after a power cycle or a lamp change, "
        "calibrate instead")


class HeldOpen:
    """A scanner open for a ``with`` block, for a tool that files its own passes.

    Leaving the block closes the device and nothing else (`close_device`), so
    the tool's own filing -- gzip and all -- runs with the device closed.
    :meth:`release` is the scanner's own exit, deferred until that filing is
    done: it files what debug filing spooled and nobody claimed. Exited with
    the block instead, it ran first, and deleted every claimed pass from the
    spool before the tool had filed it -- so a pass whose filing then failed
    was nowhere. A claimed pass is now kept until its caller answers for it
    (`DirectScanner.debug_claim`), and `debug_settle`, after release, files
    one that was never answered for.

    Entered and exited once each, as the scanner would be by ``with``.
    """

    def __init__(self, scanner: Any):
        self.scanner = scanner
        self._entered: Any = None

    def __enter__(self) -> Any:
        self._entered = self.scanner.__enter__()
        return self._entered

    def __exit__(self, *exc: object) -> None:
        if self._entered is not None:
            close_device(self._entered)

    def release(self) -> None:
        """The scanner's own exit: debug filing. Call once the tool's is done."""
        entered, self._entered = self._entered, None
        if entered is not None:
            entered.__exit__(None, None, None)


def filing_interrupt(say: Callable[[str], None]) -> DeferredInterrupt:
    """Ctrl-C deferred while a tool files what it scanned, as during a pass.

    The scanner's own exit -- debug filing -- ran inside the tool's
    `DeferredInterrupt` while the ``with`` that owned the scanner ended it.
    `HeldOpen` moved it after the tool's filing, which runs once that block
    is over, and so out of the deferral: a Ctrl-C there raised in the middle
    of `_debug_flush`, which has already taken the spooled passes off the
    scanner, so those still to be filed were left in a temporary directory
    nothing names, and the entry being written stayed INCOMPLETE. A tool
    holds this over its own filing and `HeldOpen.release` both. A fresh one:
    the Ctrl-C that stopped the scanning is not counted, so the next one is
    asked again rather than taken at its word.
    """
    return DeferredInterrupt(say=say, message=(
        "\nstopping once what was scanned is filed -- cut short, a library "
        "entry is left half written and the passes debug filing kept go "
        "nowhere. Press Ctrl-C again to abort anyway."))


class _Stopped(Exception):
    """Raised on the worker when a cooperative stop was asked for."""


class ScanSession:
    """A scanner, a worker thread that owns it, and a queue of jobs.

    Nothing on this class touches the device except from the worker thread.
    `submit`, `request_stop`, `halt`, `force_abort`, `outstanding` and `poll`
    are the UI's side and are safe to call from a main loop -- `halt` from
    any thread.
    """

    def __init__(
        self,
        root: str | Path | None = "library",
        reference: str | Path = "calibration/shading.npz",
        rolls: str | Path = "rolls",
        open_scanner: Any = None,
        verbose: bool = True,
        out_dir: str | Path | None = None,
        keep_awake: Callable[..., contextlib.AbstractContextManager]
        | None = None,
    ):
        self.root = str(root) if root else None
        #: What keeps the host awake through a job (`_awake`): `KeepAwake`,
        #: unless a test hands in its own.
        self._keep_awake = keep_awake or KeepAwake
        #: The job the worker is running, for the writer thread to ask about;
        #: None between jobs.
        self._current: Job | None = None
        #: Which job queued each picture still being filed, by its result's
        #: seq: a filing that fails stops the job that queued it, not
        #: whichever runs by the time the writer gets there.
        self._owners: dict[int, Job | None] = {}
        #: The ordinal of the job the worker took last: 1 for the first,
        #: counting up. Said with "state" and put on every writer job it
        #: queues, so each `Filing` names the job it belongs to however late
        #: it lands (`Filing.owner`).
        self._job_no = 0
        #: Each pass queued to be filed and not yet answered for, and where
        #: each that was filed went, by seq: see `filing`. Under one lock,
        #: and changed before the event that says so is emitted, so a caller
        #: never sees "filed" with nothing behind it.
        self._filing_lock = threading.Lock()
        self._owed: set[int] = set()
        self._filed_as: dict[int, Path] = {}
        #: The owed whose results are shown and whose passes are not queued
        #: yet (`_deliver`); let go at the end of a job that never queued
        #: them, which would otherwise be owed for good.
        self._unqueued: set[int] = set()
        self.reference = str(reference)
        self.rolls = Path(rolls)
        #: Makes the reader a roll's in-walk correction asks about each frame's
        #: edges: ``edge_reader(film)`` -> a fresh reader, or None. The window
        #: sets it to `tools/frame_edges.walk_reader`; left None, the driver's
        #: own strip-level detector decides, as it always has.
        self.edge_reader: Callable[[str], Any] | None = None
        #: Where the last roll or walk wrote its manifest. The folder is
        #: derived here (`roll_dir`, or the job's own `out`), and a walk with
        #: no name is given a new one, so a caller that wants to file something
        #: beside that manifest -- the contact sheet's decisions, which are
        #: made after the walk finishes -- can ask rather than recompute a
        #: name it never saw.
        self.last_roll_dir: Path | None = None
        self.verbose = verbose
        # A second copy of each scan, written where the operator asked for it as
        # the scan lands rather than afterwards. The library entry is the record;
        # this is the file they actually wanted.
        self.out_dir = Path(out_dir) if out_dir else None
        #: The turn and the mirror every file written from here on gets: see
        #: `rotation`, `flip` and `arrange`. One tuple, so the scanner thread
        #: reads both in one look -- written as two attributes from the
        #: window's thread, a frame could be filed with the new turn and the
        #: old mirror.
        self._arrangement: tuple[int, bool] = (0, False)
        #: What the running job writes its copies to, and as what, taken as
        #: it started; None between jobs. See `Output`.
        self._output: Output | None = None
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
        #: Every job handed over and not yet over, oldest first: queued,
        #: taken, or running. Exact, because it is changed where the jobs
        #: are -- `submit`, `request_stop`, the worker -- under one lock, and
        #: emptied when the worker ends (`_close_out`), after which nothing
        #: is taken. The window asks it (`outstanding`); its own copy, kept
        #: from the events, lagged the worker by a tick of its pump.
        self._outstanding: list[Job] = []
        self._outstanding_lock = threading.Lock()
        self._closed = False
        #: The jobs a stop was asked of that the worker had already taken:
        #: one that has not started yet keeps its stop as it starts, where
        #: clearing it for the job's own sake used to lose it.
        self._stop_meant: list[Job] = []
        self._stop = threading.Event()
        #: Quit's stop, a Ctrl-C's, SIGTERM's or a closed console's: never
        #: cleared, so no job starts after it (`halt`). And whether it was
        #: the console, which a slide's colour pass is not started after.
        self._halted = threading.Event()
        self._console_closed = threading.Event()
        #: Why the running job was stopped, when nobody pressed Stop: set by
        #: `_filed` just before it asks, cleared as each job starts.
        self._stop_reason: str | None = None
        self._thread: threading.Thread | None = None
        self._scanner: Any = None
        self._writer: FrameWriter | None = None
        #: Each roll folder's manifest as the last run into it left it, by
        #: file: its filings can still be coming in when the next run into
        #: that folder starts (`_roll`), and one it could not write is
        #: written again once the scanner is closed.
        self._manifests: dict[Path, RollManifest] = {}
        self._seq = 0
        #: Whether a roll's frames are still coming: the device is busy
        #: scanning the next one while the writer files the last. Read by
        #: the writer (`FrameWriter.idle`), set on the scanner thread.
        self._rolling = False
        self.dead = False                    # set by force_abort
        self.inquiry_text = ""
        #: Whether a `Calibrate` job has left this session a shading reference.
        #: Written on the worker, read by the window through the "calibrated"
        #: event rather than directly, so it never reads one job behind.
        self.calibrated = False
        #: The gain/offset mismatch the last refused scan found, and the one
        #: the operator chose to scan through (`_reference_moved`).
        self.reference_moved: dict[str, Any] | None = None
        self.accepted_mismatch: dict[str, Any] | None = None
        #: The disks a running roll could not read the room of, said once
        #: each (`_room_for`); emptied as each roll starts.
        self._unknown_said: set[str] = set()
        #: How a refusal in another program names this session's holder: the
        #: window says "the window", a tool its own name (`locks.program`).
        self.who = locks.program()
        #: What the driver's log lines are prefixed with while a strip past
        #: a roll's first runs: ``strip 2 · ``. Set and cleared on the
        #: scanner thread (`_roll`).
        self._log_prefix = ""
        #: The roll folders this session has written into, each held against
        #: every other process from before its first seek until the session
        #: closes (`_hold_roll`): its last frames are still being filed
        #: after a roll has ended. By `locks.key`, so one folder is one hold.
        self._roll_locks: dict[str, locks.Held] = {}
        #: Called once the device has closed, every filing is in and the
        #: debug spool is settled, before the plain entries are compacted:
        #: the window waits there for its own threads that read or write
        #: entries -- Save all, Export, Save As, the full-resolution view --
        #: whose open files on Windows made a compaction fail part way.
        #: Never with the device open: it would be waiting with the scanner
        #: open and idle. None waits for nothing.
        self.before_compact: Callable[[], None] | None = None

    # -- the UI's side -----------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("session already started")
        # A daemon on purpose. Quit, Ctrl-C and SIGTERM all wait for it (the
        # window's `_wait_to_quit`); what does not is a second Ctrl-C, the
        # operator choosing to abandon the read, and a worker the
        # interpreter waited for would keep the process -- and the device --
        # through that, then wait for a job that never comes. Windows ending
        # the process as its console closes ends every thread, daemon or not
        # (`ConsoleClose`, `tasks.start_apart`).
        self._thread = threading.Thread(target=self._run, daemon=True, name="scanner")
        self._thread.start()

    def submit(self, job: Job) -> None:
        """Hand a job to the worker. Any job: what may be handed over when
        is the window's to decide, and its refusals say so in its words.

        Nothing is queued once the worker has ended (`_close_out`): nothing
        would take it, and counted as outstanding it kept the window
        "working" for good.
        """
        # Not clearing the stop flag here. The worker clears it as each job
        # starts, which is what keeps a stop meant for the previous job from
        # killing the next one; clearing it on submit as well cancelled a Stop
        # the operator had just pressed on the job still running, whenever
        # anything else was queued behind it -- an aim-click, a key.
        with self._outstanding_lock:
            if self._closed:
                return
            self._outstanding.append(job)
            self._jobs.put(job)

    def outstanding(self) -> tuple[Job, ...]:
        """Every job handed over and not yet over, oldest first: queued,
        taken or running. Empty once the worker has ended. Safe from any
        thread."""
        with self._outstanding_lock:
            return tuple(self._outstanding)

    def _let_go(self, job: Job) -> None:
        """Take one job off `outstanding`, by identity: two Scans of the
        same settings are equal, and are two jobs. Under the lock."""
        for at, held in enumerate(self._outstanding):
            if held is job:
                del self._outstanding[at]
                return

    def request_stop(self) -> None:
        """Stop at the next safe point, and drop what is queued behind it.

        Never risks the device: the running job stops where it can, and a job
        that has not started is simply not started. A Stop that left the
        queue alone let a double-pressed Scan, or a roll queued behind a
        walk, run anyway once the first one had stopped.

        A job the worker has taken and not yet started is stopped as well:
        it keeps this stop as it starts (`_stop_meant`). Asked in that
        instant, the stop used to be cleared by the very job it was for,
        which then ran to its end.

        Each job dropped is said by a "dropped" event (`_said_dropped`).
        """
        dropped: list[Job] = []
        shutdown = False
        with self._outstanding_lock:
            while True:
                try:
                    job = self._jobs.get_nowait()
                except queue.Empty:
                    break
                if job is None:
                    shutdown = True          # the close request is not a job
                else:
                    dropped.append(job)
                    self._let_go(job)
            if shutdown:
                self._jobs.put(None)
            # What is left is the job the worker holds: running, or taken
            # and about to start.
            self._stop_meant = list(self._outstanding)
            self._stop.set()
        if dropped:
            self._emit("log", text=f"stopped: {len(dropped)} queued job"
                       f"{'' if len(dropped) == 1 else 's'} not started")
        self._said_dropped(dropped)

    def _said_dropped(self, jobs: list[Job]) -> None:
        """A "dropped" event for each job let go of without being started.

        Such a job reports nothing else: no "state", no "finished" or
        "failed". The window sets some of what a job will change as it hands
        the job over, ahead of its start -- a calibration counted as done, a
        walk as walking, a roll's estimate on screen -- and undoes it when the
        job ends; for a job dropped at launch, while the scanner was still
        being opened, that end never came. Outside the jobs' lock, as every
        event is.
        """
        for job in jobs:
            self._emit("dropped", text=_describe(job), job=job)

    def halt(self, *, console_closed: bool = False) -> None:
        """Quit's stop: a roll stops after the frame in flight, a single scan
        after the pass or probe in flight, and no job starts again in this
        session.

        For Quit's "stop after what is in flight", a Ctrl-C, SIGTERM and the
        console closing. `request_stop` is meant for the job running, and
        the worker clears it as the next one starts -- so a job handed over a
        moment before the quit could be taken, cleared of it and run whole:
        a scan, its colour pass, a roll. This is never cleared. Each job
        that starts from here is stopped before its first command, a single
        scan's metering before its next probe, and with ``console_closed``
        a slide's colour pass is not started at all (`_scan`). Safe from
        any thread, `ConsoleClose`'s included: it takes the jobs' lock, as
        `request_stop` does.
        """
        if console_closed:
            self._console_closed.set()
        # Before the stop: whatever the worker is between, taking a job or
        # clearing the last one's stop, it sees one or the other set.
        self._halted.set()
        self.request_stop()

    @property
    def halted(self) -> bool:
        """Whether `halt` has been asked: no job starts again."""
        return self._halted.is_set()

    # -- what files are written as -----------------------------------------

    @property
    def rotation(self) -> int:
        """A quarter turn applied to every file written from here on -- the
        output folder's copy and a roll's own TIFF. Never the library entry.
        Set from the UI, so a picture rotated on screen is rotated in the
        files that follow it. A turn and a mirror set together go through
        `arrange`."""
        return self._arrangement[0]

    @rotation.setter
    def rotation(self, value: int) -> None:
        self._arrangement = (value, self._arrangement[1])

    @property
    def flip(self) -> bool:
        """Whether those files also read left to right. A strip loaded the
        other way up comes off this scanner backwards, and turning it does
        not fix that -- so it is its own answer rather than a fourth angle."""
        return self._arrangement[1]

    @flip.setter
    def flip(self, value: bool) -> None:
        self._arrangement = (self._arrangement[0], value)

    def arrange(self, rotation: int, flip: bool) -> None:
        """Set the turn and the mirror in one assignment.

        The scanner thread reads them while a roll runs, and a turn made in
        the window reaches the frames filed after it, by design. Set one and
        then the other, a frame filed between the two got the new turn and
        the old mirror: an arrangement nobody chose.
        """
        self._arrangement = (rotation, bool(flip))

    def output_now(self) -> Output:
        """The output folder, format and JPEG quality as they are set now,
        which the next job to start takes (`Output`)."""
        return Output(self.out_dir, self.out_format, self.jpeg_quality)

    def job_output(self) -> Output | None:
        """What the running job writes its copies to, and as what -- taken
        as it started -- or None between jobs. For the window, which says
        when a change it makes waits for the next job."""
        return self._output

    def _out(self) -> Output:
        """The running job's `Output`; the one in force now outside a job."""
        return self._output or self.output_now()

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

    def filing(self, seq: int) -> tuple[str, Path | None]:
        """Where the pass a result shows went: ``("owed", None)`` while it is
        still being filed, ``("filed", entry)`` once it is in the library,
        and ``("none", None)`` for one never queued for the library or that
        the library refused.

        For the window, which learns of an entry from the "filed" event a
        tick or two after the writer has it, and so could not tell a pass
        still being filed from one never to be: a Delete pressed in between
        offered only to drop the pass from the session, and the entry was
        filed afterwards with nothing said. Safe from any thread.

        Owed from before its result is shown (`_deliver`), not from when it
        is queued: a slide's frame is queued behind its infrared pass and
        its picture, each of which can wait seconds for the writer, and
        said "none" all that time.
        """
        with self._filing_lock:
            if seq in self._owed:
                return "owed", None
            entry = self._filed_as.get(seq)
        return ("filed", entry) if entry is not None else ("none", None)

    def _owe(self, seq: int) -> None:
        """Owe the pass a result about to be shown a filing: see `filing`.

        Only where it will be filed -- a writer, and a library -- so a
        session with none says "none" from the start.
        """
        if self._writer is None or self.root is None:
            return
        with self._filing_lock:
            self._owed.add(seq)
            self._unqueued.add(seq)

    def _owe_no_more(self) -> None:
        """Let go of what a job showed and never queued, once it is over:
        a job that failed between the two left its result owed for good."""
        with self._filing_lock:
            self._owed -= self._unqueued
            self._unqueued.clear()

    @property
    def debug_filing(self) -> bool:
        """Whether the scanner keeps a spooled copy of every pass for debug
        filing (RPS7200_DEBUG): a pass the library refused may still be
        filed from it as the scanner closes. False for a stand-in that keeps
        none."""
        return bool(getattr(self._scanner, "debug", False))

    def live_manifest(self, path: Any) -> RollManifest | None:
        """The manifest this session writes the file ``path`` through, while
        it has one: ahead of the file while frames are still being filed,
        and the only thing that may write it meanwhile. Safe from any
        thread."""
        try:
            key = Path(path).resolve()
        except OSError:
            return None
        return self._manifests.get(key)

    def _roll_files(self, entries: list[Path]) -> list[Path]:
        """Every roll.json a frame record naming one of ``entries`` may be
        in: each roll folder under the rolls folder and in its trash, the
        folder each entry says it was filed for, and every roll this session
        wrote. By what names the entry, never by the folder alone: a
        duplicate and a renamed folder carry the record with them, and most
        entries record their folder relative to wherever the window ran.

        The rolls' trash because a roll comes back from it: a frame whose
        entry went while its roll was there was left unmarked, and read as
        done once the roll was restored. Marked there, it reads as it would
        have. A roll folder elsewhere -- `tools/scan_roll.py --out` -- is
        found only by the folder its entry recorded. Each strip of a roll
        of strips, wherever it is, as a roll from before strips
        (`library.roll_folders`)."""
        found: dict[str, Path] = {}

        def add(path: Path) -> None:
            try:
                if path.is_file():
                    found.setdefault(os.path.normcase(str(path.resolve())),
                                     path)
            except OSError:
                pass

        for folder in library.roll_folders(self.rolls):
            add(folder / "roll.json")
        for entry in entries:
            record = library._read_record(entry) or {}
            member = (record.get("extra") or {}).get("roll_membership") or {}
            if isinstance(member, dict) and member.get("folder"):
                add(Path(str(member["folder"])) / "roll.json")
        for path in list(self._manifests):
            if path.name == "roll.json":
                add(path)
        return list(found.values())

    def still_filing(self, entries: Iterable[Any]) -> str | None:
        """Why these entries cannot go to the trash yet, or None: a frame
        one of them was filed as is still waiting for its filing in the
        roll.json this session writes, and a mark made now would land on
        the take before it (`RollManifest.forget`). The window asks before
        its questions; `trash_entries` asks again."""
        for entry in entries:
            record = library._read_record(Path(entry)) or {}
            member = (record.get("extra") or {}).get("roll_membership") or {}
            if not isinstance(member, dict) or member.get("kind") != "frame":
                continue
            number = member.get("number")
            folder = member.get("folder")
            manifest = (self.live_manifest(Path(str(folder)) / "roll.json")
                        if folder else None)
            if (manifest is not None and isinstance(number, int)
                    and manifest.awaits(number)):
                return still_filing_said(number)
        return None

    def trash_entries(self, entries: Iterable[Any], *, by: str,
                      why: str = "") -> TrashedEntries:
        """Move these entries of this session's library into its trash
        (`library.trash`), every roll frame each was filed as marked first.

        Each roll.json whose records name one of them (`_roll_files`) is
        held as written into (`locks.ROLL`) -- through this session's own
        manifest where it is writing that roll, which is ahead of the file
        -- and the frames that name it are marked going to the trash
        (`mark_deleted_frames`), on the disk before anything moves. A mark
        over an entry whose rename was then refused reads as scanned, since
        its entry is still there (`scanned_frames`); a rename over an
        unmarked frame would have read as scanned with nothing left.

        A rename, never a copy or a delete: metadata on the disk the entry
        is on, so it is done at once from the window's thread, even while
        the scanner works -- the writer files other entries beside it, and
        each writes the index under a name of its own. Raises `StillFiling`
        where a frame of one of them is still being filed, `locks.Refused`
        where another program writes a roll that names one, ValueError for
        a path that is no entry of this library, and OSError where a mark
        cannot be written; nothing has moved then.
        """
        if self.root is None:
            raise ValueError("this session files into no library")
        root = Path(self.root)
        entries = [Path(e) for e in entries]
        waiting = self.still_filing(entries)
        if waiting is not None:
            raise StillFiling(waiting)
        frame_of = {entry.name: self._frame_of(entry) for entry in entries}
        naming = [(path, entry_id, frame_of[entry_id])
                  for path, entry_id, _frames in self.frames_naming(entries)]
        marked: list[tuple[Path, int, str]] = []
        with contextlib.ExitStack() as held:
            # A strip's roll.json is held by its roll's folder, as every
            # writer of a roll of strips holds it (`library.lock_folder`).
            for folder in sorted({library.lock_folder(p.parent)
                                  for p, _i, _n in naming
                                  if self.live_manifest(p) is None}):
                held.enter_context(locks.take(
                    locks.ROLL, folder, exclusive=True, what=self.who))

            def before(batch: Path) -> None:
                at = _utc_now()
                for path, entry_id, number in naming:
                    marked.extend(
                        (where, frame, entry_id) for where, frame, outcome
                        in mark_deleted_frames(
                            [path], entry_id,
                            {"at": at, "id": entry_id, "batch": batch.name,
                             "by": by},
                            live=self.live_manifest, number=number,
                            say=lambda m: self._emit("log", text=m))
                        if outcome == "marked")

            trashed = library.trash(root, entries, by=by, why=why,
                                    before=before)
        # Only the frames whose entry went: one whose rename was refused
        # keeps its mark over an entry still there, and reads as scanned.
        gone = set(trashed.moved)
        return TrashedEntries(trashed, [(path, frame) for path, frame, entry_id
                                        in marked if entry_id in gone])

    def frames_naming(self, entries: Iterable[Any]
                      ) -> list[tuple[Path, str, list[int]]]:
        """Each roll.json whose frame records name one of ``entries`` as
        that frame's entry (`_roll_files`), with the entry's id and the
        frames: what `trash_entries` marks, and so what a Delete may say
        will count as not scanned. Through this session's own manifest
        where it writes the file, which is ahead of it. An entry listed only
        among a frame's earlier `takes` is in none: the frame keeps its
        later take, and still counts as scanned."""
        entries = [Path(e) for e in entries]
        out = []
        for path in self._roll_files(entries):
            manifest = self.live_manifest(path)
            for entry in entries:
                numbers = (manifest.naming(entry.name) if manifest is not None
                           else self._named_in(path, entry.name))
                if numbers:
                    out.append((path, entry.name, numbers))
        return out

    @staticmethod
    def _named_in(path: Path, entry_id: str) -> list[int]:
        """The frames of the roll.json at ``path`` naming ``entry_id``; none
        where it cannot be read."""
        try:
            return _naming(read_manifest(path), entry_id)
        except (OSError, ValueError):
            return []

    @staticmethod
    def _frame_of(entry: Path) -> int | None:
        """The frame number a roll frame's entry says it is; None for any
        other entry."""
        record = library._read_record(entry) or {}
        member = (record.get("extra") or {}).get("roll_membership") or {}
        number = member.get("number") if isinstance(member, dict) else None
        return (number if isinstance(number, int)
                and member.get("kind") == "frame" else None)

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
            # With the strip a roll of strips' run is on, past its first
            # (`_roll`): the driver says places, and frame 2 of strip 2 is
            # frame 8 of the roll.
            scanner.log_hook = lambda m: self._emit(
                "log", text=self._log_prefix + m)
        # Debug filing into this session's library, beside the frames it
        # files: it went to `./library` whatever `--library` said.
        debug_filing_into(scanner, self.root)
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
        opened = False
        try:
            self._scanner = self._open_scanner()
            self._listen(self._scanner)
            self._scanner.open()
            opened = True
            info = self._scanner.inquiry()
            self.inquiry_text = info.describe() if hasattr(info, "describe") else str(info)
            self._emit("state", text=self.inquiry_text)
        except Exception as exc:                         # noqa: BLE001
            self._emit("failed", text=f"could not open the scanner: {exc}",
                       detail=traceback.format_exc())
            if opened:
                # Opened, and then INQUIRY failed. Left open, the interface
                # stayed claimed until the window quit: no other session,
                # and no other program -- `tools/check_scanner.py`, to find
                # out what is wrong -- could reach the scanner meanwhile.
                try:
                    self._scanner.close()
                except Exception as also:                # noqa: BLE001
                    self._emit("log", text=f"close: {also}")
            # Nothing handed over will run, and nothing more is taken.
            self._close_out()
            self._emit("closed")
            return
        # Where the film is before anything has moved it. The window used to
        # learn the position only from its own frame buttons, so at launch --
        # and after anyone used the scanner's keys -- it had nothing to say
        # about where a roll would start from.
        self._report_position()

        self._writer = FrameWriter(on_done=self._filed,
                                   idle=lambda: not self._rolling)
        try:
            while True:
                job = self._jobs.get()
                if job is None:
                    break
                self._current = job
                self._job_no += 1
                self._stop_reason = None
                with self._outstanding_lock:
                    # Cleared of the last job's stop -- unless the stop was
                    # asked of this one as it was taken (`request_stop`), or
                    # is Quit's, which no job starts past (`halt`). Under the
                    # jobs' lock, so a stop asked meanwhile lands on one side
                    # of this or the other, never between.
                    meant = any(held is job for held in self._stop_meant)
                    self._stop_meant = []
                    if meant or self._halted.is_set():
                        self._stop.set()
                    else:
                        self._stop.clear()
                # Where its copies go and as what, fixed for the whole job;
                # taken before the preflight, so the folder its checks look
                # at is the one it writes to (`Output`).
                self._output = self.output_now()
                self._emit("state", text=_describe(job), busy=True,
                           done=self._job_no, job=job)
                try:
                    try:
                        with self._awake(job):
                            note = self._dispatch(job)
                    finally:
                        # Before the job is said to be over, so nothing it
                        # showed and never queued is still owed by then.
                        self._owe_no_more()
                    # A move reports every frame it makes; anything else says
                    # once where it left the film. Only after a job that ended
                    # normally: after a failure the device is left alone.
                    if not isinstance(job, Move):
                        self._report_position()
                    self._emit("finished", text=note or _describe(job))
                except (_Stopped, StoppedBeforePass) as exc:
                    # Stopped as asked is not a failure: as one, the window
                    # turned red and spoke of what was held and walked as it
                    # does of a job that broke. A scan stopped during or
                    # after its metering says so in its own words.
                    self._emit("finished", text=str(exc) or "stopped")
                except Exception as exc:                 # noqa: BLE001
                    self._emit("failed", text=f"{type(exc).__name__}: {exc}",
                               detail=traceback.format_exc())
                # Over, whatever its last filings still have to say: see
                # `_filed`, which stops only a job still running.
                self._current = None
                self._output = None
                with self._outstanding_lock:
                    self._let_go(job)
                self._emit("state", text="idle", busy=False)
        finally:
            # Nothing more is taken from here, and nothing handed over now
            # will run: the window asks `outstanding` to know.
            self._close_out()
            # Order matters: the device closes first, and only then does the
            # writer get to spend time gzipping. The other way round is the
            # open-and-idle state that preceded a wedge. The device alone:
            # `close()` also files what debug filing caught, and that waits
            # for the writer below -- see there.
            try:
                if not self.dead:
                    close_device(self._scanner)
            except Exception as exc:                     # noqa: BLE001
                self._emit("log", text=f"close: {exc}")
            if self._writer is not None:
                self._emit("log", text="filing what is still queued ...")
                self._writer.finish()
                for problem in self._writer.errors:
                    self._emit("log", text=problem)
                # Every filing is in now, so a manifest whose last write was
                # refused -- the last frame's, with no frame after it to
                # carry it -- is written once more, and says so again if it
                # still cannot be.
                for manifest in self._manifests.values():
                    if manifest.unsaved is not None:
                        manifest.save()
            # Nothing more is written into a roll folder: each is free for
            # another program -- the window's Rename, a scan_roll -- again.
            self._let_go_of_rolls()
            # Debug filing (RPS7200_DEBUG=1), after every filing is in. A pass
            # filed here was claimed as it was handed to the writer, and its
            # spooled copy let go only once the writer answered for it
            # (`answering`); what is left is what nothing here kept --
            # metering probes, holds -- and a pass whose filing failed, whose
            # spooled copy is then the one there is. Run before the writer
            # finished, it deleted the spool of a pass still to be filed, and
            # of one that went on to fail.
            #
            # After a force abort too. `close()` sends the device nothing --
            # it closes a transport the abort already closed, which is
            # harmless, and files the spool -- and skipping it left every
            # spooled pass in a temporary directory nothing would ever name,
            # at the one time the probes and holds matter most.
            try:
                self._scanner.close()
            except Exception as exc:                     # noqa: BLE001
                self._emit("log", text=f"close: {exc}")
            # And what close() leaves: a claimed pass whose answer never came
            # back, taken as one nobody filed (`DirectScanner.debug_settle`).
            settle = getattr(self._scanner, "debug_settle", None)
            if callable(settle):
                try:
                    settle()
                except Exception as exc:                 # noqa: BLE001
                    self._emit("log", text=f"debug filing: {exc}")
            # The window's own readers and writers of entries finished first,
            # with the device already closed (`before_compact`).
            if self.before_compact is not None and self._writer is not None \
                    and self._writer.uncompressed:
                try:
                    self.before_compact()
                except Exception as exc:                 # noqa: BLE001
                    self._emit("log", text=f"before compacting: {exc}")
            if self._writer is not None:
                # Now, with the device closed: see `_file`'s `compress`.
                compact_after_close(self._writer.uncompressed,
                                    lambda m: self._emit("log", text=m))
            self._emit("closed")

    def _close_out(self) -> None:
        """The worker is ending: take nothing more, and let go of every job
        handed over, which nothing will now run.

        On both ways out, the scanner that never opened as well. A job left
        counted there kept the window "working" until it quit, refusing
        Open, Rename and Delete of rolls -- ordinary use with the scanner
        off -- which is the lock-up `ScannerGui._scannerless` was written
        about. Each is said as dropped (`_said_dropped`).
        """
        with self._outstanding_lock:
            self._closed = True
            while True:
                try:
                    self._jobs.get_nowait()
                except queue.Empty:
                    break
            dropped = list(self._outstanding)
            self._outstanding.clear()
            self._stop_meant = []
        self._said_dropped(dropped)

    def _awake(self, job: Job) -> contextlib.AbstractContextManager:
        """The host kept out of idle sleep for a job that reads passes.

        A roll runs for hours unattended, and a laptop that sleeps part-way
        suspends the bus in the middle of a READ -- an abandoned read, the
        wedge -- with the rest of the roll never scanned (`awake`). Per job
        rather than for the window's life: the scanner held open and idle
        between jobs is no reason to keep the machine up. Not for a move,
        which takes seconds.
        """
        if isinstance(job, Move):
            return contextlib.nullcontext()
        return self._keep_awake(say=lambda m: self._emit("log", text=m))

    def _filed(self, filing: Filing) -> None:
        """Called on the writer thread as each job lands, with its `Filing`.

        Guarded as a whole: raised from here, the writer kept the filing and
        said the raise only in the list it repeats at close, after a roll
        that could have been told hours earlier.
        """
        try:
            self._say_filed(filing)
        except Exception as exc:                         # noqa: BLE001
            self._emit("log", text=(
                f"{filing.named}: {filing.outcome}, but the session could "
                f"not say so ({type(exc).__name__}: {exc})"))

    def _say_filed(self, filing: Filing) -> None:
        if self._writer is not None:
            while self._writer.notes:
                self._emit("log", text=self._writer.notes.pop(0))
        #: The job that queued this picture, as it was then.
        owner = self._owners.pop(filing.seq, None)
        filed = filing.entry if not filing.failed else None
        with self._filing_lock:
            # Before anything is said of it: see `filing`.
            self._owed.discard(filing.seq)
            if filed is not None:
                self._filed_as[filing.seq] = filed
        # Written with no entry on purpose -- a walk's picture from before
        # its aim, or a session with no library -- says nothing here: it
        # said "could not be filed: None", beside the line that reports real
        # failures, until nobody read either.
        for line in filing.trouble():
            self._emit("log", text=line)
        # The roll that queued it, and only while it is still running. The
        # last frames of a roll file after it has ended, and a late failure
        # there stopped whatever ran next -- another roll, a walk -- and
        # dropped the jobs queued behind it, for a roll already over.
        #
        # Only a pass that is not in the library. A copy that could not be
        # written -- a slide's picture included, whose two passes are filed
        # -- costs that copy, as a negative's always has: the frames after it
        # are safe in the library, and `_room_for` catches a full output
        # disk before every frame.
        if filing.failed and isinstance(owner, Roll) and owner is self._current:
            # A disk that is full, or a library that has gone, fails every
            # frame after this one the same way. Scanning on spent the rest
            # of the roll's film and time on pictures that were then thrown
            # away, one log line each.
            self._emit("log", text=(
                f"stopping the roll after the frame in flight: "
                f"{filing.named} could not be filed, and the frames after "
                "it would be lost the same way"))
            # Said as the reason, before the stop it explains: the roll
            # ended "as asked", in roll.json as in the log, the same words
            # as the operator's Stop.
            self._stop_reason = (f"{filing.named} could not be filed "
                                 f"({filing.error})")
            self.request_stop()
        # What the window marks a pass by: one for every job, whatever came
        # of it, and the result it is shown under.
        self._emit("filing", done=filing.shown or 0, total=filing.number,
                   text=filing.outcome, filing=filing)
        if filed is not None:
            self._emit("log", text=filing.said() or "")
            # The UI needs the path to read full-resolution pixels back for
            # a 1:1 look; the working copy it already has is decimated and
            # cannot show grain or shadow noise.
            self._emit("filed", done=filing.seq, text=str(filed))

    def _dispatch(self, job: Job) -> str | None:
        """Run one job. Returns a note when the outcome needs explaining."""
        self._check_stop()
        # Every job here drives the device. One an earlier pass was abandoned
        # in is refused before the job starts, rather than part way in by the
        # driver: a roll had already made its folder and manifest, and the
        # window went on queueing Scans that each got that far. `getattr`,
        # because a stand-in need not carry the flag.
        suspect = getattr(self._scanner, "suspect", None)
        if suspect is not None:
            raise DeviceSuspect(
                f"not {_describe(job)}: {suspect}. The scanner may "
                "still be mid-scan; power-cycle it and open a new session.")
        # The folders, the path lengths and the disks, before anything is
        # sent: a roll's folder and name come back from it, derived once.
        place = self._preflight(job)
        # Asked again here and below, immediately before each job's first
        # command that drives the device. The one above came before all of
        # that, and a stop -- or Quit's halt -- asked meanwhile was let
        # through to a whole pass.
        self._check_stop()
        if isinstance(job, (Scan, Roll)) and not getattr(job, "dry_run", False):
            refused = self._reference_moved(job)
            if refused is not None:
                return refused
            self._check_stop()
        if isinstance(job, Calibrate):
            self._calibrate(job)
        elif isinstance(job, Prescan):
            self._prescan(job)
        elif isinstance(job, Scan):
            return self._scan(job)
        elif isinstance(job, Roll):
            note = self._roll(job, place)
            if job.rest_from is None or note is not None:
                # Stopped, short, refused or failed: the rest is not begun.
                return note
            return self._rest_of_strip(job, place)
        elif isinstance(job, Move):
            return self._move(job)
        else:
            raise TypeError(f"unknown job {job!r}")
        return None

    def _preflight(self, job: Job) -> RollPlace | None:
        """Refuse a job its folders, its paths or its disks cannot take,
        before it sends anything.

        Every job that writes -- a calibration, a prescan, a scan, a roll and
        a walk -- is asked:

        - whether each folder it writes in can be had: a configured root may
          have its last folder made and no more, and the output folder must
          be there already (`storage`). A drive not plugged in used to fail
          after the pass, and a vanished output folder was made again on C:.
          A remembered output folder that has gone refuses every job that
          writes a copy, until it is back, chosen again or cleared;
        - on Windows, whether its longest path fits in 259 characters
          (`storage.longest_too_long`), from the names it will really use;
        - whether each disk has room for what it can write, and for what the
          writer still has queued, with `storage.SPACE_RESERVE` kept free
          (`check_space`). Warned about below twice that, and said where a
          disk's room cannot be read. Only a roll was checked, and a walk
          not at all.

        Raised, so the job fails through the failed-job path -- which the
        demo runs too; nothing is greyed -- with nothing sent to the scanner
        and no folder made. Asked between jobs with the device open and idle,
        so it does nothing heavy: `stat` and free-space calls only, and never
        during a read.

        Returns a roll's folder and the name its entries carry, derived here
        before the seek, for `_roll`; None for anything else.
        """
        if isinstance(job, Move):
            return None
        what = _refusal(job)
        refused: list[str] = []
        if self.root is not None:
            refused.append(storage.missing_root(self.root) or "")
        if isinstance(job, Calibrate):
            needs: list[tuple[Path, int]] = []
            if self.root is not None:
                needs.append((Path(self.root), CALIBRATION_BYTES))
            if job.mode != "off":
                folder = Path(job.reference).parent
                refused.append(storage.missing_root(folder) or "")
                needs.append((folder, CALIBRATION_BYTES))
            refused = [said for said in refused if said]
            if refused:
                raise storage.DriveMissing(f"{what}: " + "; ".join(refused))
            self._room(job, what, needs)
            return None
        place = None
        if isinstance(job, Roll):
            place = self._roll_place(job)
            # A new strip's folder is made below its roll's, which may not
            # be there yet either: the roll folder's root is what is asked.
            refused.append(storage.missing_root(self._roll_root(
                place.plan.roll_folder if place.plan is not None
                else place.out)) or "")
            if not job.dry_run:
                past_strip_end(place.strip, job.start_at, job.frames,
                               job.only, what)
                if job.rest_from is not None:
                    # And the rest of the strip after the frames left.
                    past_strip_end(place.strip, job.rest_from, None, None,
                                   what)
        # The folder this job writes to, fixed as it started (`Output`).
        out_dir = self._out().folder
        if out_dir is not None:
            try:
                storage.require_dir(out_dir)
            except OSError as exc:
                refused.append(str(exc))
        refused = [said for said in refused if said]
        if refused:
            raise storage.DriveMissing(f"{what}: " + "; ".join(refused))
        too_long = storage.longest_too_long(self._longest_paths(job, place))
        if too_long:
            raise OSError(f"{what}: " + "; ".join(too_long))
        if isinstance(job, Roll):
            assert place is not None
            asked = frames_asked(max(1, job.start_at), job.frames, job.only)
            # A roll to the end of the strip is refused only when not even
            # its first frame fits; each frame is checked again before the
            # next is scanned (`_room_for`), and warned about for them all.
            # The frames left before the rest of the strip, and its first.
            refuse = len(asked) if asked is not None else 1
            count = (len(asked) if asked is not None
                     else max(1, LAST_PLAUSIBLE_POSITION + 2 - job.start_at))
            if job.rest_from is not None and not job.dry_run:
                refuse += 1
                count += max(1, LAST_PLAUSIBLE_POSITION + 2 - job.rest_from)
            self._room(job, what, self._roll_needs(job, refuse, place[0]),
                       self._roll_needs(job, count, place[0]))
            return place
        if isinstance(job, Prescan):
            passes = [frame_bytes(job.resolution, False)]
            copies = (out_dir / PRESCAN_SUBDIR
                      if out_dir is not None else None)
        else:
            # A slide scanned with infrared takes an RGB pass after it where
            # the scanner offers one, as `_scan` asks.
            passes = pass_list(job.resolution, job.infrared, job.film,
                               colour_pass=hasattr(self._scanner,
                                                   "colour_pass"))
            copies = out_dir
        self._room(job, what, scan_space(passes, library_root=self.root,
                                         out_dir=copies,
                                         spool=self._spool_place()))
        return None

    def queued_needs(self) -> list[tuple[Path, int]]:
        """What the writer has queued and not yet written, and where
        (`FrameWriter.queued_needs`); nothing where no writer has started.

        Counted on top of a job's own need by every check for room
        (`_room`), and by the window's Duplicate: a roll's last frames are
        still being filed for a while after it ends, and a check that read
        the disk without them let a copy fill it part way."""
        writer = self._writer
        return writer.queued_needs() if writer is not None else []

    def _room(self, job: Job, what: str, needs: list[tuple[Path, int]],
              warn: list[tuple[Path, int]] | None = None) -> None:
        """Refuse ``needs``, with what the writer has queued, where a disk
        cannot take them; warn below twice ``warn`` (``needs`` unless
        given), and say each disk whose room cannot be read."""
        queued = self.queued_needs()
        short, unknown = check_space([*needs, *queued], SPACE_REFUSE)
        if short:
            raise OSError(f"{what}: " + "; ".join(short) + self._trash_room())
        tight, _ = check_space([*(needs if warn is None else warn), *queued],
                               SPACE_WARN)
        noun = ("the walk" if getattr(job, "dry_run", False)
                else "the roll" if isinstance(job, Roll)
                else "the calibration" if isinstance(job, Calibrate)
                else "this prescan" if isinstance(job, Prescan)
                else "this scan")
        if tight:
            self._emit("log", text=(
                f"{noun} may fill the disk: " + "; ".join(tight)
                + (" -- it stops before a frame there is no room for"
                   if isinstance(job, Roll) and not job.dry_run else "")
                + self._trash_room()))
        for line in unknown:
            self._emit("log", text=f"before {noun}: {line}; going ahead")

    def _trash_room(self) -> str:
        """What the trashes hold, for a refusal or a warning for room: a
        Delete moves things there and frees nothing, so the room may be in
        them (`library.trash_said`). Empty where they hold nothing."""
        try:
            said = library.trash_said(self.root, self.rolls)
        except OSError:
            return ""
        return f"; {said}" if said else ""

    def _spool_place(self) -> Path | None:
        """Where the scanner's debug filing spools, for a free-space check:
        none with it off, and none for a stand-in that has no spool."""
        place = getattr(self._scanner, "spool_place", None)
        if not callable(place):
            return None
        try:
            found = place()
        except Exception:                                # noqa: BLE001
            return None
        return Path(found) if found is not None else None

    def _roll_place(self, job: Roll) -> RollPlace:
        """The folder a roll or walk files into, and the name its entries
        carry: before the seek, so it is known before anything moves.

        The folder by `roll_dir`, the one derivation everything that files
        beside a roll uses; the label the roll's entries carry is the name
        its folder already records, so a roll added to keeps calling itself
        what it did. `job.out` is a folder the caller already has -- the
        sheet's own, a reopened roll's -- and a name given with it wins.

        A roll of strips: a new strip (`Roll.new_strip`) is planned here,
        read-only -- its folder, the roll's label and its first number
        (`plan_next`) -- and claimed after the seek; a walk or a roll into
        one of its strips is numbered by the strip's place in the roll
        (`place_of`), and labelled with the roll's name, never the strip
        folder's. Each refusal is raised here, on the failed-job path, before
        anything moves: a new strip into a roll from before strips
        (`NoSecondStrip`) or a folder that is no roll, one of its strips
        among them (`RollFolderInUse`), a plan the roll has moved on from
        (`StripMoved`), an index that cannot be read, a walk or a roll into
        a roll of strips' own folder (`RollIsStrips`), and one into a strip
        outside its roll (`StripOutOfItsRoll`). And the rest of a strip
        (`Roll.rest_from`) asked of a walk, or of a strip not claimed yet,
        which has none.
        """
        if job.rest_from is not None and (job.dry_run or job.new_strip):
            raise ValueError(
                "the rest of a strip is finished in a roll there is, after "
                "its frames left; a walk or a new strip was asked to. "
                "Nothing was moved.")
        if job.new_strip:
            if job.extend_walk:
                raise ValueError(
                    "a new strip is walked afresh, and 'add to it' adds to a "
                    "strip there is: both were asked. Nothing was moved.")
            roll = Path(job.out) if job.out else roll_dir(self.rolls, job.name)
            plan = plan_next(roll, live=self.live_manifest).next
            if (job.strip_plan is not None
                    and tuple(job.strip_plan) != (plan.number, plan.first)):
                asked, first = job.strip_plan
                raise StripMoved(
                    f"{plan.label}'s next strip is strip {plan.number}, "
                    f"numbered from {plan.first}, not strip {asked} from "
                    f"{first} as the question said: the roll changed since. "
                    "Nothing was moved; ask again.")
            # Its folder holding files the index does not list, said in the
            # pre-flight: the claim that also asks is after the seek.
            strip_folder_used(plan)
            return RollPlace(plan.folder, plan.label, None, plan)
        if job.out:
            out = Path(job.out)
            name = job.name or recorded_roll_name(out) or out.name
        else:
            out = roll_dir(self.rolls, job.name)
            name = recorded_roll_name(out) or out.name
        if library.roll_kind(out) == "strips":
            raise RollIsStrips(
                f"{out.name} is a roll of strips: a "
                f"{'walk' if job.dry_run else 'roll'} goes into one of its "
                "strips, or starts its next one. Nothing was moved.")
        strip = place_of(out, live=self.live_manifest)
        if strip is not None:
            name = strip.label
        else:
            # A strip outside its roll -- `--open-roll` of one, carried into
            # the rolls folder, or a copy -- read as a roll from before
            # strips: its second frame labelled as its roll's frame 2, for
            # good, since nothing rewrites a label.
            outside = strip_out_of_its_roll(out)
            if outside is not None:
                raise StripOutOfItsRoll(outside)
        return RollPlace(out, name, strip, None)

    def _roll_root(self, out: Path) -> Path:
        """The configured folder a roll's folder is made below: this
        session's rolls where it lies inside them -- a sheet's roll always
        does (the window's `_roll_folder`) -- and otherwise the roll's own,
        a folder named as `job.out`. Each may have its last folder made and
        never the ones above it (`storage`). Inside as `storage.inside`
        says, links followed, which is how the window decided it."""
        return self.rolls if storage.inside(out, self.rolls) else out

    def _roll_needs(self, job: Roll, frames: int, out: Path
                    ) -> list[tuple[Path, int]]:
        """Where ``frames`` of this roll's or walk's frames go, and how much
        each place takes at most (`roll_space`, `walk_space`)."""
        spool = self._spool_place()
        out_dir = self._out().folder
        if job.dry_run:
            return walk_space(
                frames, job.prescan_resolution, library_root=self.root,
                roll_folder=out,
                out_dir=(out_dir / PRESCAN_SUBDIR
                         if out_dir is not None else None),
                spool=spool)
        # The window's rolls take a slide's colour pass (`_file_pair`).
        return roll_space(frames, job.resolution, job.infrared,
                          library_root=self.root, roll_folder=out,
                          out_dir=out_dir, film=job.film,
                          colour_pass=True, spool=spool,
                          prescan_resolution=job.prescan_resolution)

    def _longest_paths(self, job: Prescan | Scan | Roll,
                       place: RollPlace | None) -> list[Path]:
        """The longest path each place this job writes in can be given.

        From the names it will really use and their real bounds: its library
        entry's id, made longer by `_reserve` and holding its record's
        temporary name; each delivered copy at its last free name and its
        writer's temporary name; a roll's own records; and debug filing's
        spool. Not the entry `keep_unfiled` makes beside a copy when the
        library refuses a pass: one too long there is kept in the system's
        temporary folder instead, and refusing a roll for it refused one
        whose every frame the library would take.
        """
        number, roll, notes = 0, "", job.notes
        resolution, infrared = job.resolution, False
        walk = isinstance(job, Roll) and job.dry_run
        paths: list[Path] = []
        copies: list[Path] = []
        #: The number its label and delivered names carry: its place, or
        #: for a strip its number across the roll, which can run to three
        #: digits past the roll's highest.
        labelled = 0
        if isinstance(job, Roll):
            assert place is not None
            out, roll = place.out, place.name
            number = labelled = LAST_PLAUSIBLE_POSITION + 1
            held = place.strip or place.plan
            if held is not None:
                try:
                    given = highest_given(held.roll_folder,
                                          live=self.live_manifest)
                except ValueError:
                    given = 0
                labelled = max(held.first - 1, given) + number
                paths.append(written_beside(
                    held.roll_folder / (library.STRIPS_INDEX + PREVIOUS)))
            notes = replace(job.notes, frame=roll_frame_label(roll, labelled))
            if walk:
                resolution = job.prescan_resolution
            paths.append(manifest_bound(out))
            copies.append(out / (f"prescan{number:02d}-before-{number}.tif"
                                 if walk else f"frame{number:02d}.tif"))
        if isinstance(job, (Scan, Roll)) and not walk:
            infrared = job.infrared
        meta = {"resolution_dpi": resolution, "film": job.film,
                "channels": 4 if infrared else 3}
        if self.root is not None:
            paths.append(entry_bound(self.root, library.entry_id(
                meta, notes, datetime.now(timezone.utc))))
        out_dir = self._out().folder
        if out_dir is not None:
            prescan = isinstance(job, Prescan) or walk
            where = out_dir / PRESCAN_SUBDIR if prescan else out_dir
            copies.append(copy_bound(where / self._out_name(
                labelled or number, meta, roll, "-before" if walk else "")))
        paths += [export.temp_name(copy) for copy in copies]
        spool = self._spool_place()
        if spool is not None:
            paths.append(spool_bound(spool))
        return paths

    def _reference_moved(self, job: Job) -> str | None:
        """Refuse a scan whose shading reference the scanner has moved off.

        The device sets red's gain itself during a calibration and has been
        seen to change it again with none from here: 96 of 122 scans after
        the 2026-10-04 00:06Z reference ran at red gain 37 against the 39 it
        was taken at, corrected red ~4% off. So before a scan or a roll the
        gain and offsets are read -- one READ GAIN/OFFSET, a status query --
        and held against the reference's (`DirectScanner.reference_mismatch`).

        Stefan's choice, 2026-10-04: say so and offer a calibration. The job
        ends before it moves anything, with this as its note, and
        "reference_moved" tells the window; `accept_reference` lets the next
        one go on with this very mismatch. A job that wants no correction, a
        stand-in without the check, and a read that fails are let through --
        the last raises again at the pass's own first command if the device
        has really gone.
        """
        if not getattr(job, "shading", True):
            return None
        check = getattr(self._scanner, "reference_mismatch", None)
        if not callable(check):
            return None
        # Nothing recorded to hold the scan to -- a reference whose archive
        # cannot be read, a stand-in's -- and no command is spent asking.
        if getattr(self._scanner, "reference_settings", None) is None:
            return None
        # Only the read is let off: a device that will not answer is found
        # again at the pass's own first command. An error in the check itself
        # is not -- caught with the read, one waved every scan through.
        try:
            now = self._scanner.get_gain_offset()
        except (UsbError, ScanReadError, TimeoutError, OSError):
            return None
        moved = check(now)
        if moved is None or _same_gains(moved, self.accepted_mismatch):
            return None
        now, ref = moved["now"], moved["reference"]
        text = (f"the scanner's gain is {'/'.join(map(str, now['gain']))} "
                f"(offsets {'/'.join(map(str, now['offset']))}), but the "
                "calibration in use was taken at "
                f"{'/'.join(map(str, ref['gain']))} (offsets "
                f"{'/'.join(map(str, ref['offset']))}) -- corrected colour "
                "would be a few percent off. Calibrate again, or scan with "
                "it anyway")
        self.reference_moved = moved
        self._emit("reference_moved", text=text)
        return f"not {_describe(job)}: {text}"

    def accept_reference(self) -> None:
        """Scan with the reference in force despite the mismatch last found.

        This one pairing only: a gain that moves again, or a new reference,
        is asked about afresh.
        """
        self.accepted_mismatch = self.reference_moved

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
        # every attribute the real one publishes. A pass that came with none
        # is still shown and written where it goes, and kept out of the
        # library, as a roll's prescan is (`_unpublished`). A thinner meta
        # made up here from the job was the fallback, and filed an entry
        # that described itself wrongly -- the failure the paragraph above
        # is about, kept alive for any scanner but the real one. Its copies
        # are named by the resolution alone, as the roll's are.
        published = getattr(self._scanner, "last_scan_meta", None)
        meta = dict(published or {"resolution_dpi": job.resolution})
        raw_image = getattr(self._scanner, "last_pixels_raw", None)
        label = f"prescan {job.resolution} dpi"
        # Kept so the scan taken next has something to be judged against. One
        # pass, ~370 KB at 300 dpi, replaced each time -- not a history.
        self._last_prescan = (image, self._position(), meta)
        seq = self._deliver(
            "prescan", label, image, {
                "resolution_dpi": job.resolution,
                # So the window can say a pass was read bottom-up and turned.
                "read_direction": meta.get("read_direction")},
            # Filed below exactly when it published a record of itself.
            owed=bool(published),
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
            file_entry=self._unpublished(published, label),
            called=label,
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
            # Every stop -- Stop, Escape, Quit, Ctrl-C, SIGTERM, a closed
            # console -- is asked before each metering probe and before the
            # pass, between commands, so nothing is abandoned: a stop during
            # metering used to be followed by the rest of the probes and the
            # whole pass, five minutes at 7200 dpi, where the button says
            # "finishes this pass". It raises `StoppedBeforePass`, which
            # `_run` reports as stopped.
            should_stop=self._stop.is_set,
        )
        # `scan()` hands back the corrected pixels and keeps the ones the
        # scanner sent in `last_pixels_raw`. Read now, before anything else
        # runs a pass: it describes the pass that just ran. Without it this job
        # filed the corrected pixels as the entry's raw `scan.tif`, and every
        # view and export of it was then corrected a second time.
        raw_image = getattr(self._scanner, "last_pixels_raw", None)
        # A slide scanned with infrared takes an RGB pass straight after, for
        # its colour (`DirectScanner.wants_colour_pass`). The RGBI pass's own
        # record is read first: the RGB pass rebinds it. `getattr`, because a
        # stand-in need not carry it; the demo does.
        colour = capture = None
        take = getattr(self._scanner, "colour_pass", None)
        if take is not None and DirectScanner.wants_colour_pass(job.film,
                                                                job.infrared):
            if self._console_closed.is_set():
                return self._without_colour(job, image, meta, raw_image)
            capture = self._scanner.capture_record()
            colour = take(meta, resolution=job.resolution, frame=job.frame,
                          film=job.film, keep_raw=True, shading=job.shading)
        label = f"{job.resolution} dpi {'RGBI' if job.infrared else 'RGB'}"
        here = self._prescan_here()
        meta = self._note_reversal(meta, image, *(here or (None, None)))
        if colour is not None:
            colour_meta = self._note_reversal(colour.meta, colour.image,
                                              *(here or (None, None)))
            paired = self._pair(0, image, meta, colour, colour_meta)
            label = f"{job.resolution} dpi RGB + infrared"
            seq = self._deliver("scan", label, paired[0], paired[1],
                                owed=True)
            self._file_pair(seq, 0, paired, image=image, raw_image=raw_image,
                            capture=capture, colour=colour, notes=job.notes,
                            tags=tuple(job.tags) + ("gui",),
                            mono=wants_mono(job.mono, job.film),
                            mono_channel=job.mono_channel, called=label)
            return None
        seq = self._deliver("scan", label, image, meta, owed=True)
        self._file(seq, 0, image, meta, job.notes, tuple(job.tags) + ("gui",),
                   raw_image=raw_image,
                   mono=wants_mono(job.mono, job.film),
                   mono_channel=job.mono_channel, called=label)
        # A pass that wanted correction and could not get one no longer comes
        # back here at all -- scan() raises ShadingUnavailable instead, which
        # this job's dispatcher already turns into a surfaced error. There is
        # nothing left worth flagging on a normal return: the only remaining
        # reason meta["shading_skipped"] is set is job.shading=False, and that
        # is the job's own choice, not a shortfall.
        return None

    #: What a slide's RGBI pass filed without its colour pass says of it.
    NO_COLOUR_PASS = "colour pass not taken: the console was closed"

    def _without_colour(self, job: Scan, image: np.ndarray,
                        meta: dict[str, Any],
                        raw_image: np.ndarray | None) -> str:
        """A slide's RGBI pass, filed alone: its console has closed.

        Windows ends the process about 5 s after its console closes, so a
        colour pass started now -- 85 s at 1800 dpi -- is certain to be cut
        off part way: the abandoned read, and a power cycle. So it is not
        started. The RGBI pass is shown and filed with its raw bytes, tagged
        as what it is, an infrared pass, with no copy in the output folder:
        on a slide its blue clips and draws lines, which is why the colour
        pass exists. Only for a closed console. Every other stop still takes
        the colour pass, which has no deadline and finishes a frame half
        scanned (`DirectScanner.colour_pass`).
        """
        self._emit("log", text=(
            "this slide's RGB pass is not taken: the console was closed, and "
            "Windows ends the process about 5 s later, part way through any "
            "pass begun. Its RGBI pass is filed as an infrared pass, and no "
            "copy of it is written"))
        here = self._prescan_here()
        meta = self._note_reversal(meta, image, *(here or (None, None)))
        label = f"{job.resolution} dpi RGBI, no colour pass"
        seq = self._deliver("scan", label, image, meta, owed=True)
        said = "; ".join(filter(None, (job.notes.notes, self.NO_COLOUR_PASS)))
        self._file(seq, 0, image, meta, replace(job.notes, notes=said),
                   tuple(job.tags) + ("gui", "infrared pass"),
                   raw_image=raw_image, copies=False, called=label)
        return f"filed the RGBI pass alone -- {self.NO_COLOUR_PASS}"

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
        # A position the transport would not say is unknown, never "the same
        # place": two of them compared equal, and a scan was judged against a
        # prescan of whatever the film held when the counter last said nothing.
        if where is None:
            return None
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

    def _pair(self, number: int, image: np.ndarray, meta: dict[str, Any],
              colour: ColourPass, colour_meta: dict[str, Any],
              what: str | None = None
              ) -> tuple[np.ndarray, dict[str, Any], dict[str, Any],
                         dict[str, Any]]:
        """What a slide's two passes deliver, and each one's record of the other.

        Returns ``(picture, its meta, the RGBI pass's meta, the RGB pass's
        meta)``. The picture is the RGB pass with the RGBI pass's infrared
        registered onto it (`rps7200.merge`): the colour of the pass whose
        blue held, and the dust record of the one that has it.

        Where the two will not register, the picture is the RGB pass alone and
        the log says why. Not the RGBI pass: its blue is what the second pass
        exists to replace, and its clipped sky and the lines through it are
        what an operator would get back. The colour is the RGB pass's either
        way, and it is what `library.corrected` makes of the frame's entry.

        Both passes carry one ``infrared_pair`` id, and the RGB pass the
        registration, which is how `library.corrected` finds one entry from
        the other and makes the same picture again.

        ``what`` is the frame as its lines say it (`frame_said`): on strip 2
        a roll's frame 3 is frame 9 of the roll, which its files carry.
        """
        pair = uuid.uuid4().hex[:12]
        if what is None:
            what = f"frame {number}" if number else "this scan"
        merged: np.ndarray | None = None
        record: dict[str, Any] = {}
        if (meta.get("reversal") or None) != (colour_meta.get("reversal") or None):
            record.update(ok=False,
                          reason="the two passes came back turned differently")
        else:
            merged, record = merge_infrared(colour.image, image)
        ir_meta = dict(meta, infrared_pair={"id": pair, "role": "infrared"})
        colour_meta = dict(colour_meta, infrared_pair={
            "id": pair, "role": "colour", "registration": record})
        if merged is None:
            self._emit("log", text=(
                f"{what}: the infrared could not be laid on the RGB pass "
                f"({record.get('reason')}); delivering the RGB pass without "
                "infrared -- both passes are filed"))
            return colour.image, colour_meta, ir_meta, colour_meta
        shift = record["shift_px"]
        self._emit("log", text=(
            f"{what}: colour from the RGB pass, infrared from the RGBI pass, "
            f"registered on {record['tiles']}/{record['of']} tiles -- "
            f"dy {shift['dy'][0]:+.2f}..{shift['dy'][1]:+.2f} px, "
            f"dx {shift['dx'][0]:+.2f}..{shift['dx'][1]:+.2f} px, "
            f"{record['after_px']['median_px']:.2f} px left"))
        delivered = dict(colour_meta, channels=int(merged.shape[2]),
                         channel_order=list(meta.get("channel_order") or "RGBI"))
        return merged, delivered, ir_meta, colour_meta

    def _file_pair(
        self,
        seq: int,
        number: int,
        paired: tuple[np.ndarray, dict[str, Any], dict[str, Any],
                      dict[str, Any]],
        *,
        image: np.ndarray,
        raw_image: np.ndarray | None,
        capture: dict[str, Any] | None,
        colour: ColourPass,
        notes: FilmNotes,
        tags: tuple[str, ...],
        path: Path | None = None,
        membership: Callable[[str], dict[str, Any]] | None = None,
        prescan: np.ndarray | None = None,
        prescan_meta: dict[str, Any] | None = None,
        on_filed: Callable[..., Any] | None = None,
        on_picture: Callable[..., Any] | None = None,
        called: str = "",
        **common: Any,
    ) -> tuple[int, bool]:
        """File a slide's two passes and deliver the picture made of them.

        Three writes, in this order, on the one writer:

        - the **RGBI pass**, an entry of its own for its infrared, and no
          copy anywhere else;
        - the **picture** (`_pair`), to ``path`` and the output folder, and
          into no entry: the library holds raw passes, and this one is made
          again from the two of them whenever it is asked for;
        - the **RGB pass** last, as the frame: under ``seq``, so the window's
          full view and Save As read it, and with ``on_filed``, so a manifest
          is done when the frame's entry is. Last because Export takes the
          newest ``"frame"`` entry of a number.

        ``membership(kind)`` is the roll's `roll_membership` for a pass of
        that kind; ``common`` goes to every `_file`. ``on_picture`` is the
        picture's own `on_filed`: whether ``path`` was written is its
        filing's to say, and it lands before the RGB pass's on the one
        writer, which the frame's record waits for. Returns the delivered
        picture's arrangement. ``called`` is the frame as its result is
        labelled; the first two are named by it with their part beside it.

        The first two are filed under seqs no result carries, and shown under
        ``seq`` (`Filing.shown`), so the window marks the frame for either.
        The picture names the RGBI pass as its ``pair``: a copy of it that
        cannot be written is then a copy not written, as a negative's is,
        when that pass is in the library -- the picture is made again from
        it and the RGB pass -- and a failure only when it is not. It was a
        failure always, and stopped a slide roll whose passes were all filed.
        """
        picture, picture_meta, ir_meta, colour_meta = paired

        def member(meta: dict[str, Any], kind: str) -> dict[str, Any]:
            return (dict(meta, roll_membership=membership(kind))
                    if membership is not None else meta)

        infrared = self._unannounced_seq()
        self._file(infrared, number, image,
                   member(ir_meta, "infrared pass"), notes,
                   tuple(tags) + ("infrared pass",), raw_image=raw_image,
                   capture=capture if capture is not None else {},
                   copies=False, shown=seq, part="infrared pass",
                   called=f"{called} · infrared pass" if called else "",
                   **common)
        arranged = self._file(self._unannounced_seq(), number, picture,
                              picture_meta, notes, tags, path=path,
                              file_entry=False, shown=seq, part="picture",
                              pair=infrared if self.root is not None else None,
                              on_filed=on_picture,
                              called=f"{called} · picture" if called else "",
                              **common)
        self._file(seq, number, colour.image, member(colour_meta, "frame"),
                   notes, tuple(tags) + ("colour pass",),
                   raw_image=colour.raw_image, capture=colour.capture,
                   prescan=prescan, prescan_meta=prescan_meta, copies=False,
                   on_filed=on_filed, called=called, **common)
        return arranged

    def _move(self, job: Move) -> str | None:
        """Whole frames, or a sub-frame nudge. Never both in one job."""
        # The last prescan is of where the film was. A nudge moves it without
        # the counter seeing, so the position would still vouch for it.
        self._last_prescan = None
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

        if job.units:
            # One SLIDE command tops out at the largest correction, param 87
            # (88.8 units), so anything further is several of them. Doing
            # that here rather than making the caller loop is what stops a
            # long request quietly becoming a single command's worth --
            # which is exactly what it used to do, when that was param 8, so
            # every click on the prescan moved the film the same distance.
            #
            # The planning itself lives in `plan_nudges` so the adjuster and
            # any correction loop can ask what a distance becomes without
            # driving the transport to find out.
            try:
                plan = plan_nudges(units=job.units)
            except ValueError as exc:
                # Says "use the slide buttons" itself, once.
                return str(exc)
            sign = 1.0 if job.units > 0 else -1.0
            moved = 0.0
            done = 0
            for step in plan:
                if self._stop.is_set():
                    break
                # `moved` accumulates what the scanner says it did, not what
                # the plan predicted: the plan is a forecast and this line is
                # the report.
                out = self._scanner.nudge_units(step)
                moved += abs(out["asked_units"])
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
            return (f"moved {say_distance(sign * moved)}{how} -- the frame counter "
                    "does not see this; prescan to check it landed")
        return "nothing to move"

    def _roll(self, job: Roll, place: RollPlace | None = None
              ) -> str | None:
        # Where it files, derived before anything moves (`_preflight`), so
        # the roll's folder is known before the seek.
        out, name, strip, plan = (place if place is not None
                                  else self._roll_place(job))
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
        # And before the seek, what the driver refuses for certain. It says so
        # only at the roll's first frame -- once the seek has wound the film
        # and the folder exists -- and a 7200 dpi roll only frame by frame,
        # after each was prescanned and metered: three frames and minutes of
        # transport, then "giving up", reported as a roll that finished.
        # `tools/scan_roll.py` refuses all of these before it opens anything.
        if not job.dry_run and not DirectScanner.correctable_at(job.resolution):
            raise DirectScanner.uncorrectable(job.resolution)
        if job.meter not in METER_MODES:
            raise ValueError(f"unknown meter mode {job.meter!r}; expected one "
                             f"of {METER_MODES}")
        locks_white_balance(job.film)          # raises for a film it does not know
        if job.infrared and not supports_infrared(job.film):
            raise ValueError(
                f"infrared is blind to {job.film}: every frame would spend "
                "its infrared pass and hand back the picture rather than the "
                "dust. Scan it RGB.")
        # A disk that cannot hold what the roll will file was refused before
        # this (`_preflight`): the first sign used to be a failed filing,
        # after hours of film and scanner time. A disk whose room could not
        # be read is said once a roll, frame by frame (`_room_for`).
        self._unknown_said = set()
        # The folder held against every other program before the film moves:
        # a `tools/scan_roll.py` or another window writing into it, a Rename
        # or Delete of it, refuse this roll here, with nothing moved. A strip
        # is held by its roll's folder (`library.lock_folder`): a Rename of
        # the roll moves it, and its claim writes the roll's index.
        lock = plan.roll_folder if plan is not None else library.lock_folder(out)
        had = locks.key(lock) in self._roll_locks
        self._hold_roll(lock)
        try:
            # And a fresh walk where one would mix with or write over what
            # is there (P18-a): asked again under the lock, the folder as it
            # stands now -- the window and the tool chose a free one, and
            # something may have filled it since. A strip is walked again by
            # adding to it: its records never shrink, so no number it gave is
            # given twice.
            self._walk_refused(job, out, strip, plan)
            # And the numbers its frames can take, read now: under the lock,
            # nothing else gives one until the run ends, and a read that
            # failed between passes lost the frame just scanned.
            strip = self._numbers_ready(job, out, strip)
        except BaseException:
            if not had:
                self.let_go_of_roll(lock)
            raise

        def at(place: int) -> str:
            """A place on this run's strip as its lines say it
            (`place_said`): a new strip's, before its claim, as its plan
            numbers it."""
            return place_said(strip if strip is not None
                              else planned_place(plan), place)

        try:
            seek(self._scanner, first,
                 say=lambda m: self._emit("log", text=m), said=at)
        except FilmNotPlaced as exc:
            # Where it stopped, so the readout does not go on showing where it
            # started. Only for a refusal: after a transport fault the device
            # is left alone, as it is after every other failed job.
            self._report_position()
            if (exc.strip_ended_on is not None and not job.dry_run
                    and self._strip_ended(job, out, exc, said=at)
                    and job.frames is None and job.only is None):
                # A roll to the end of the strip whose strip ends on the
                # frame before it, as its records agree: nothing of it was
                # left, and the roll is finished -- the rest of a strip a
                # roll stopped on its last frame, from the Roll button or
                # as the second part of finishing one (`_rest_of_strip`).
                # Only that second part used to: a reopened roll with
                # nothing left but its rest goes as a plain roll from it,
                # and that failed at the strip's real end.
                return (f"the strip ends at {at(exc.strip_ended_on)}; the "
                        "roll is finished")
            raise
        # Read and checked by the seek, so the readout has it without another
        # question to the device.
        self._emit("transport", done=first)
        if self._stop.is_set():
            return (f"stopped with the film on {at(first + 1)}, before "
                    "anything was scanned")
        # The name as typed: the window's beside the folder it worked out
        # (`Roll.typed_name`), or the name a caller gave in its place.
        typed = (job.typed_name or job.name or "").strip()
        if plan is not None:
            # A new strip is claimed now, and not before: after the seek and
            # its stop check, so a run with no film leaves no index and no
            # folder; before its folder is made and any pass taken. Planned
            # again under the roll's lock, held since before the seek.
            strip = claim(plan, root=self._roll_root(plan.roll_folder),
                          typed=typed, who=self.who,
                          say=lambda m: self._emit("log", text=m),
                          live=self.live_manifest)
            self._emit("log", text=(
                f"strip {strip.strip} of {strip.label} is {out}, numbered "
                f"{strip.numbers()}"))
        if strip is not None:
            strip.live = self.live_manifest
        # The folder `_roll_place` derived. Its own folder at most, and never
        # the ones above it: a rolls folder on a drive that has gone was
        # refused before the seek, and is not made again on C: if it went
        # since (`storage`).
        storage.make_within(self._roll_root(out), out)
        # Said where the folder is the name typed cut, whoever worked the
        # folder out -- the window always does, and was never told so. A
        # roll of strips' folder is the roll's, not the strip's.
        named = strip.roll_folder if strip is not None else out
        if typed and name_cut(typed) and named.name == _safe(typed):
            self._emit("log", text=(
                f"the roll's name is {len(typed)} characters, longer than a "
                f"folder's name may be here; its folder and files are called "
                f"{named.name}, and its record keeps the name as typed"))
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
        # The last run into this file, while its frames are still being filed:
        # each says it is done in this same manifest as it lands, so the file
        # on disk is behind, and read from there this run would carry those
        # frames forward as not done and write over the writer's answer. It
        # used to wait for the writer instead -- with the film moved and the
        # device open and idle while the last frames gzipped, the state
        # FrameWriter exists to avoid. So the run carries on the manifest
        # still in hand, whose filings then land in the records it carries.
        # The same when the last run's final write was refused: the file is
        # stale by exactly that frame, and read from there it would be
        # carried forward as not done -- and the manifest that knew better
        # dropped, with nothing left to catch the file up.
        key = manifest_path.resolve()
        live = self._manifests.get(key)
        if carried and live is not None and live.ahead_of_disk():
            earlier = live.data
        elif carried and manifest_path.exists():
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
        # The name as it was typed, where the folder's is another: made safe
        # to be a folder, or cut to `NAME_CAP`. A run that typed none -- a
        # sheet's roll, a resume, a walk added to -- keeps what this roll's
        # records say: the manifest is written afresh, and carried only its
        # frames, so the second run dropped what the first had recorded.
        # Nor does one that typed only the roll's label or its folder's name:
        # the window puts a reopened roll's folder in the roll box and sends
        # it back as typed, and `tools/scan_roll.py` advises `--roll
        # <folder>`; each replaced the long name the roll was typed as.
        as_typed = typed if typed not in (name, out.name, named.name) else ""
        if not as_typed:
            kept = earlier.get("typed_name")
            as_typed = (kept if isinstance(kept, str) and kept.strip()
                        and kept != name and earlier.get("roll") == name
                        else recorded_typed_name(out, name) or "")
        # The session's turn and mirror as the roll starts, read in one look
        # (`arrange`), for both places the manifest records them.
        rotation, flipped = self._arrangement
        # Which walk this is, for a walk: written once as it starts and never
        # again, so what the sheet decided on it is found by it alone (the
        # window's `walk_stamp`). survey.json's time was the stamp, and the
        # writer's late answers rewrite the file after the walk has ended --
        # decisions filed between the two were keyed to a time no file had
        # any more. A walk added to is the same walk, and keeps its id; one
        # from before ids is given one.
        walk_id = None
        if job.dry_run:
            kept_id = earlier.get("walk_id") if job.extend_walk else None
            walk_id = (kept_id if isinstance(kept_id, str) and kept_id
                       else uuid.uuid4().hex)
        started = _utc_now()
        #: This run's own request, as its `settings` say it.
        asked_for = self._run_settings(job, start_at, count, rotation, flipped)
        # A run of frames the roll has done only -- a deliberate retake,
        # which the window names before it starts -- leaves the roll's own
        # settings where every reader looks: reopened, the roll puts its
        # settings back, and `tools/scan_roll.py` holds a resume to them.
        # Written as the retake's, a one-frame retake at 3600 dpi RGBI in a
        # roll of 1800 dpi RGB put 3600 RGBI back for the frames left, and
        # the tool then refused to finish the roll at its own 1800 (Stefan's
        # call, 2026-10-10). The retake's own go into its run (`runs`).
        retake = (not job.dry_run and bool(earlier.get("frames"))
                  and isinstance(earlier.get("settings"), dict)
                  and self._retakes_only(job, earlier))
        roll_was = manifest_settings(earlier) if retake else {}

        def roll_said(key: str, value: Any) -> Any:
            """The roll's own where a retake keeps it, this run's else."""
            kept_value = roll_was.get(key)
            return value if kept_value is None else kept_value

        manifest: dict[str, Any] = {
            "roll": name,
            **({"typed_name": as_typed} if as_typed else {}),
            **({"walk_id": walk_id} if walk_id is not None else {}),
            # When this run began; `RollManifest.ended` says when and how it
            # ended, as the roll tool's manifest does.
            "started": started,
            # Says the numbers below are places on the strip, so a reader can
            # tell this file from one written before they were.
            "numbering": NUMBERING,
            "dpi": roll_said("resolution", job.resolution),
            "infrared": roll_said("infrared", job.infrared),
            "meter": roll_said("meter", job.meter),
            "film": roll_said("film", job.film),
            "dry_run": job.dry_run,
            # Which strip of its roll this is, and its first number: a copy
            # of the claim, so a strip can be read on its own, and its roll's
            # browser still list it where the index cannot be read.
            **({"strip": {"number": strip.strip, "first": strip.first}}
               if strip is not None else {}),
            # Where an earlier walk found the strip's end, kept: the place
            # with no picture, which says the strip is whole
            # (`strip_count`). A scanning run's is in its run (`runs`).
            **({"strip_end": earlier["strip_end"]}
               if job.dry_run and isinstance(earlier.get("strip_end"), int)
               else {}),
            "start_at": roll_said("start_at", start_at),
            # Both recorded so the survey can be opened again rather than
            # walked again. The prescan resolution because an approved
            # position is checked against a reference at that resolution and
            # a mismatch costs half the correlation confidence; the
            # orientation because prescanNN.tif is written arranged the way the
            # screen had it, and a reference has to be the film's own.
            "prescan_resolution": roll_said("prescan_resolution",
                                            job.prescan_resolution),
            "rotation": rotation,
            "flipped": flipped,
            "only": (earlier.get("only") if retake
                     else list(job.only) if job.only is not None else None),
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
            #
            # The roll's own where this run is a retake that keeps them.
            "settings": (dict(earlier["settings"]) if retake
                         else dict(asked_for)),
            #: Every frame this roll is meant to end up with, across however
            #: many sessions it takes. The union, because a resumed run is told
            #: only what is *left* -- taking its `only` as the answer is what
            #: made a four-frame roll report itself as two.
            #:
            #: A roll that named no frames wrote none here, and is read as
            #: wanting every frame it recorded (the window's `wanted_frames`).
            #: So that is its side of the union when this run names some: the
            #: window resumes such a roll with just the frames left, and the
            #: union from nothing made the roll want those few -- read as
            #: finished with a frame still to do, a failed frame outside the
            #: range no longer left. A run that names none still writes none,
            #: so the reader's fallback goes on counting its frames too.
            "wanted": wanted_after(earlier, job),
            # Earlier attempts' frames, kept. This run's records replace the
            # ones for the frames it scans and leave the rest alone.
            "frames": list(earlier.get("frames") or []),
        }
        if not job.dry_run:
            # Every scanning run, earlier ones carried as the frames are: its
            # own request, where its copies went, and -- once it ends
            # (`RollManifest.ended`) -- how far it got and why it stopped.
            # The top of the file is the last run's alone, so a run to the
            # end of the strip stopped after frame 9 read as finished once
            # another ran, and nothing could say the rest was never reached
            # (`roll_left`). A file from before runs were kept gives its own
            # top as its first.
            output = self._out()
            manifest["runs"] = [*roll_runs(earlier), {
                "started": started,
                "settings": dict(asked_for),
                "output": {"folder": (str(output.folder)
                                      if output.folder is not None else None),
                           "format": output.format,
                           "quality": output.quality},
                **({"retake": True} if retake else {}),
            }]
        # The one writer of this file from here on, from both threads; see
        # `RollManifest`. The last run's, when its filings are still to come
        # and this run was built on what it holds.
        if live is not None and earlier is live.data:
            live.carry_on(manifest)
            record_of = live
        else:
            # Not carried on -- a walk that does not add to the last one --
            # and perhaps still waiting for the writer's answers about it,
            # which would otherwise rewrite this file with that walk: retired
            # as this one first writes (`RollManifest.retire`).
            record_of = RollManifest(
                manifest_path, manifest,
                say=lambda m: self._emit("log", text=m), replaces=live)
        self._manifests[key] = record_of

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

        # The window counts frames from 1 and the transport from 0.
        only = None if job.only is None else tuple(n - 1 for n in job.only)
        # Places this strip's walk or roll found blank and the operator
        # ticked again: taken this time whatever they read (`blanks_chosen`).
        survey = None
        if not job.dry_run and (out / "survey.json").exists():
            try:
                survey = _manifest_now(out / "survey.json", self.live_manifest)
            except ValueError:
                survey = None
        take_blank = blanks_chosen(job.only, earlier, survey)
        if take_blank:
            self._emit("log", text=(
                f"{', '.join(place_said(strip, n) for n in sorted(take_blank))}"
                " read blank before and "
                f"{'was' if len(take_blank) == 1 else 'were'} chosen again: "
                "taken whatever they read now"))
        # The driver's own lines said of which strip, past the first: its
        # "frame 2" is a place, and frame 8 of the roll.
        self._log_prefix = (f"strip {strip.strip} · "
                            if strip is not None and strip.strip >= 2 else "")
        # Where the aims already sent left this strip, for a run that aims:
        # they are still on it, and its budget counts on from them
        # (`strip_aimed`). A roll counts its walk's and its own earlier
        # runs' -- a resume after a Stop started again from the walk's, and
        # creep could reach twice the budget. A walk added to counts the
        # walk it adds to and the roll beside it, as `tools/scan_roll.py`
        # does; a fresh walk, and a run that aims nothing, start from 0.
        aimed = 0.0
        if job.correct or job.correct_dry_run:
            if job.dry_run:
                beside = None
                if carried and (out / "roll.json").exists():
                    try:
                        beside = _manifest_now(out / "roll.json",
                                               self.live_manifest)
                    except ValueError:
                        beside = None
                on_strip = strip_aimed(earlier, beside)
            else:
                on_strip = strip_aimed(survey, earlier)
            aimed = on_strip.total
            said = strip_aimed_said(on_strip)
            if said:
                self._emit("log", text=said)
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
            only=only,
            max_failures=job.max_failures,
            dry_run=job.dry_run,
            correct=job.correct,
            correct_dry_run=job.correct_dry_run,
            # Keyed the driver's way, from 1-based as the window counts.
            approved={a.number - 1: a for a in job.approved},
            keep_raw=True,
            edge_reader=self.edge_reader,
            # Named in the role of every pass only debug filing keeps -- a
            # probe, a hold's look -- which a frame index alone did not tie
            # to one roll.
            roll=name,
            # This session files a slide's RGB pass beside its RGBI one
            # (`_file_pair`); which film takes one is the driver's call.
            colour_pass=True,
            aimed_units=aimed,
            take_blank=frozenset(n - 1 for n in take_blank),
        )
        # Where the roll ends, asked of the driver rather than worked out
        # again here, so a frame the roll will scan nothing after is known
        # as it arrives; see `last` below.
        ends = DirectScanner.roll_ends(first, 0, job.frames, only)
        stopped = None
        #: Frames the roll reached, scanned, walked or failed, and the last.
        covered = 0
        reached: int | None = None
        #: Places whose prescans held no picture (`RollFrame.blank`): skipped,
        #: counted apart, and never numbered as frames of the roll.
        blanks: list[int] = []
        #: How it ended, beyond `stopped`, for its run: where it found the
        #: strip's end, or why the driver gave up (`RollManifest.ended`).
        ending: dict[str, Any] = {}
        self._rolling = True
        try:
            for rf in frames:
                number = rf.index + 1
                blank = bool(getattr(rf, "blank", False))
                if blank:
                    # Tested first: a place with no picture is no frame of
                    # the roll. It is not counted as covered or reached, and
                    # `StripPlace.number` is not asked: it is said and filed
                    # by its place alone, with no number across the roll.
                    blanks.append(number)
                    rn = number
                else:
                    covered, reached = covered + 1, number
                    # Its number across the roll, given now for a place past
                    # a closed strip's range -- written into the index before
                    # the frame is filed (`StripPlace.number`) -- and its
                    # place otherwise. A number the index would not take is
                    # kept all the same, and the run stops after this frame.
                    rn = (strip.number(number,
                                       say=lambda m: self._emit("log", text=m),
                                       scan=not job.dry_run)
                          if strip is not None else number)
                if strip is not None and strip.unsaved is not None:
                    self._stop_reason = strip.unsaved
                    self.request_stop()
                said = (f"{place_said(strip, number)} (blank)" if blank
                        else frame_said(rn, strip.strip if strip else None,
                                        number))

                def member(kind: str, rn=rn) -> dict[str, Any]:
                    if strip is None:
                        return roll_membership(name, number, kind, out)
                    return roll_membership(
                        name, number, kind, out, strip=strip.strip,
                        roll_number=None if blank else rn)
                # Nothing is scanned after this frame: the count is reached,
                # the last chosen frame is in, or a stop was asked for. Its
                # entry is filed plain and compacted once the device closes,
                # as a single scan's is. Gzipped on the writer, it went on
                # with the device open and idle -- the next frame that made a
                # roll's compression safe was never coming -- and at 3600 dpi
                # RGBI that is some 280 MB, the state that preceded a wedge.
                last = ends(rf.index + 1) or self._stop.is_set()
                # Room for the next frame, asked before it is scanned -- and
                # before this one is filed, so a disk that cannot take the
                # next makes this frame the last and files it plain (P13-a).
                # Asked after its filing, it was queued to gzip with no next
                # frame coming. The disk can fill while a roll runs --
                # anything else writing there, or an open-ended roll longer
                # than it holds. The frames still being filed are not on it
                # yet, so they count, and this one with them.
                if not (job.dry_run or last or blank):
                    coming = 1 if rf.image is not None and not rf.error else 0
                    short = self._room_for(
                        job, 1 + coming + record_of.waiting(), out)
                    if short:
                        self._stop_reason = ("no room for the next frame: "
                                             + "; ".join(short))
                        self.request_stop()
                        last = True
                #: How this frame's walked prescan file was arranged, as
                #: `_file` wrote it; a walk's only.
                walked_as: tuple[int, bool] | None = None
                #: What that file is called: see where it is written.
                surveyed_name = f"prescan{number:02d}.tif"
                #: And the picture from before its aim, where there is one.
                before_name: str | None = None
                #: This frame's record, made now so the writer's answer for
                #: a walk prescan has somewhere to go (`RollManifest.amend`);
                #: filled in and recorded below.
                record: dict[str, Any] = {}
                if rf.position is not None and plausible(rf.position):
                    # Already read for this frame, so the readout follows the
                    # roll without asking the device anything more.
                    self._emit("transport", done=rf.position)
                if rf.prescan is not None:
                    seq = self._deliver(
                        "prescan", f"{said} prescan", rf.prescan,
                        {"resolution_dpi": job.prescan_resolution,
                         "read_direction": (rf.prescan_meta or {})
                         .get("read_direction")},
                        registration=rf.registration, position=rf.position,
                        number=number,
                        roll_number=(rn if strip is not None and not blank
                                     else 0),
                        # Filed below where it published a record of itself.
                        owed=bool(rf.prescan_meta),
                    )
                    # Filed in its own right, raw, with its own bytes, mask
                    # and reference, on a walk and on a real roll alike, and
                    # whether or not the frame then failed. It used to be
                    # filed only on a walk: a real roll's prescan survived
                    # only inside the frame's entry, as the corrected 8-bit
                    # `prescan.tif` -- nothing that could be re-decoded or
                    # corrected again, with nothing in the record to say so --
                    # and a frame that failed kept no prescan at all. It is
                    # what every hold, aim and reversal was judged from.
                    #
                    # On a walk it also goes into the roll directory beside
                    # the manifest, the way `tools/scan_roll.py` writes it, so
                    # a survey can be opened again tomorrow instead of being
                    # walked again, and into the output folder's prescans. A
                    # real roll's goes into neither: those have always held
                    # its frames. Written by the writer thread, not here: it
                    # is only ~370 KB, but nothing local happens on the
                    # scanning thread with the device open.
                    #
                    # With the record the driver took as the pass was taken
                    # (`RollFrame.prescan_capture`). The scanner's own is its
                    # last pass's: on a real roll the frame scan, and on a
                    # walk whatever a hold last read -- the same shape as the
                    # prescan, so no guard could tell. A scanner that does
                    # not carry one is read as before on a walk, where the
                    # prescan is its last pass unless a hold ran, and gives
                    # none on a real roll.
                    capture = rf.prescan_capture
                    if capture is None and not job.dry_run:
                        capture = {}
                    #: The resolution the copies of a prescan that published
                    #: no meta are named and tagged with. Never filed.
                    copies_only = {"resolution_dpi": job.prescan_resolution}
                    if job.dry_run:
                        # Never over a file another frame's record names. A
                        # walk from before frame numbers were places on the
                        # strip named its files by its own count, and
                        # renumbering keeps those names: frame 5's record can
                        # name prescan01.tif. Written over by this walk's
                        # frame 1, the sheet showed frame 1 as frame 5, and
                        # positions set there held the roll to the wrong
                        # picture -- often the only copy of the one lost.
                        taken = record_of.named_by_others(number)
                        # Nor over this frame's own earlier prescan where no
                        # library entry holds it (P18-b): kept beside the
                        # new one and listed as a take, so Delete counts it.
                        # The folder keeps only the latest picture of a
                        # frame -- Stefan's call, 2026-10-09 -- because the
                        # earlier ones are entries; these are not.
                        kept = [(key, name_, listed) for key, name_, listed
                                in record_of.only_copies(number)
                                if (out / name_).is_file()]
                        taken |= {name_ for _k, name_, _l in kept}
                        surveyed_name = _free_name(surveyed_name, taken)
                        if rf.prescan_before is not None:
                            before_name = _free_name(
                                f"prescan{number:02d}-before.tif", taken)
                        fresh = [(key, name_) for key, name_, listed in kept
                                 if not listed]
                        if fresh:
                            record.setdefault("takes", []).extend(
                                {"file": name_, "kind": key,
                                 "replaced_utc": _utc_now()}
                                for key, name_ in fresh)
                            one = len(fresh) == 1
                            self._emit("log", text=(
                                f"{said}: "
                                f"{', '.join(n for _k, n in fresh)} "
                                f"{'is' if one else 'are'} in no library "
                                f"entry, so {'it is' if one else 'they are'} "
                                "kept beside the new prescan, "
                                f"{surveyed_name}"))
                    walked = self._file(
                        seq, number, rf.prescan,
                        # The pass's own meta. A hand-built one here is
                        # what filed 26 prescans describing themselves as
                        # uncorrected raw when they were neither -- and it
                        # was still here as a fallback for a pass that
                        # published none. Such a pass is written where it
                        # goes and not filed (`_unpublished`); what it is
                        # given then names and tags those copies only.
                        dict(rf.prescan_meta or copies_only,
                             roll_membership=member("prescan")),
                        replace(job.notes, frame=roll_frame_label(name, rn)),
                        tuple(job.tags) + ("gui", "roll", "prescan", name)
                        + (("blank",) if blank else ()),
                        kind="prescan",
                        out_number=rn,
                        raw_image=rf.raw_prescan,
                        capture=capture,
                        path=(out / surveyed_name if job.dry_run else None),
                        # The one of its two copies the sheet reads; the
                        # output folder's prescans/ copy is not it.
                        sheet=(out / surveyed_name if job.dry_run else None),
                        copies=job.dry_run,
                        roll=name,
                        plain=last,
                        file_entry=self._unpublished(
                            rf.prescan_meta, f"{said}'s prescan"),
                        called=f"{said} prescan",
                        on_filed=(record_of.prescan_told(
                                      record, out / surveyed_name)
                                  if job.dry_run and self._writer is not None
                                  else None),
                    )
                    if job.dry_run:
                        walked_as = walked
                    if rf.prescan_before is not None:
                        # The picture as the frame arrived, kept beside the
                        # one that replaced it. Without it a correction that
                        # moved a frame somewhere worse is indistinguishable
                        # from one that worked, and the only account of
                        # either would be the detector's own -- which is the
                        # thing under test. An entry of its own now, from
                        # its own record: it used to be written with no entry
                        # at all, because the scanner's last pass by then was
                        # the verification prescan, and on a real roll it was
                        # not kept anywhere.
                        # Its own, never the prescan's that replaced it:
                        # another pass, which can have read the other way.
                        #
                        # Under a number of its own, which no result carries.
                        # The prescan's own "filed" names the entry the window
                        # reads full resolution, Save As and Delete from, and
                        # this one, filed second under the same number,
                        # replaced it: every aimed frame zoomed in to the
                        # picture from before its aim, shifted back by the
                        # aim's distance.
                        before_meta = rf.prescan_before_meta
                        self._file(
                            self._unannounced_seq(), number, rf.prescan_before,
                            dict(before_meta or copies_only,
                                 roll_membership=member("prescan")),
                            replace(job.notes,
                                    frame=roll_frame_label(name, rn)),
                            tuple(job.tags) + ("gui", "roll", "prescan",
                                               "before", name),
                            kind="prescan",
                            out_number=rn,
                            raw_image=rf.raw_prescan_before,
                            capture=rf.prescan_before_capture or {},
                            path=(out / before_name
                                  if before_name is not None else None),
                            copies=job.dry_run,
                            roll=name,
                            plain=last,
                            # Its own name in the output folder too. Named
                            # as the frame's, it was claimed before either
                            # file existed, and written second -- over the
                            # corrected prescan it came before.
                            out_suffix="-before",
                            file_entry=self._unpublished(
                                before_meta,
                                f"{said}'s prescan before its aim"),
                            # Marked, if it must be, on the prescan that
                            # replaced it: no result of its own stands for it.
                            shown=seq, part="prescan before its aim",
                            called=f"{said} · prescan before its aim",
                            on_filed=(record_of.prescan_told(
                                          record, out / before_name,
                                          key="prescan_before",
                                          entry_key="prescan_before_entry")
                                      if before_name is not None
                                      and self._writer is not None
                                      else None),
                        )
                # The scan's own meta, for the manifest below. Bound out here
                # because `record` is written for a dry run too, where there is
                # no scan and no exposure to record.
                scanned: dict[str, Any] | None = None
                #: How this frame's file was arranged, as `_file` wrote it.
                arranged: tuple[int, bool] | None = None
                if blank:
                    self._emit("log", text=(
                        f"{said}: no picture in the window (contrast "
                        f"{(rf.registration or {}).get('contrast', 0):.3f}); "
                        "skipped, its prescan filed"))
                elif rf.error:
                    self._emit("log", text=f"{said}: {rf.error}")
                elif rf.image is not None:
                    label = (
                        f"{said} · {job.resolution} dpi "
                        f"{'RGBI' if job.infrared else 'RGB'}"
                    )
                    # Judged against the framing pass taken of this very
                    # frame a minute earlier, which is the only evidence there
                    # is that the carriage reversed: see `_note_reversal`.
                    prescan_reversed = bool(
                        ((rf.registration or {}).get("approved") or {})
                        .get("row_reversed"))
                    frame_meta = self._note_reversal(
                        rf.meta, rf.image, rf.prescan, rf.prescan_meta,
                        prescan_reversed=prescan_reversed)
                    picture, picture_meta = rf.image, frame_meta
                    paired = None
                    if rf.colour is not None:
                        paired = self._pair(
                            number, rf.image, frame_meta, rf.colour,
                            self._note_reversal(
                                rf.colour.meta, rf.colour.image, rf.prescan,
                                rf.prescan_meta,
                                prescan_reversed=prescan_reversed),
                            what=said)
                        picture, picture_meta = paired[0], paired[1]
                        label = (f"{said} · {job.resolution} dpi "
                                 "RGB + infrared")
                    scanned = picture_meta
                    seq = self._deliver(
                        "frame", label, picture, picture_meta,
                        registration=rf.registration, position=rf.position,
                        number=number, owed=True,
                        roll_number=rn if strip is not None else 0,
                    )
                    # Always the roll's label: a value left in the Film
                    # panel's "frame" note used to replace it on every frame,
                    # which gave a whole roll one library signature and made
                    # its entries unfindable from the roll. By its number
                    # across a roll of strips, never by its place.
                    notes = replace(job.notes, frame=roll_frame_label(name, rn))
                    #: This frame's own file, and what its picture's filing
                    #: said of it: bound to this frame, since the writer
                    #: answers up to `FrameWriter.DEPTH` frames later, and a
                    #: frame that failed yields within seconds.
                    frame_path = out / f"frame{number:02d}.tif"
                    said: dict[str, str] = {}

                    # Done when the writer says it was filed, and not
                    # before: see `RollManifest`. A copy that could not be
                    # written leaves it done: its pass is in the library,
                    # and the record says so (`file_error`) -- it named no
                    # file at all, and every reader took an earlier take's
                    # frameNN.tif on the disk for this one's (PLAT-11).
                    def filed(filing: Filing, n=number, p=frame_path,
                              said=said, slide=paired is not None) -> None:
                        # A slide's file is its picture's, filed before
                        # this pass on the one writer (`_file_pair`); the
                        # RGB pass is filed into no copy of its own.
                        copy = (said or {"file_error": "not written: the "
                                         "picture's filing did not answer"}
                                if slide else filing.copy_said(p))
                        record_of.filed(
                            n, None if filing.failed else filing.entry,
                            filing.error if filing.failed else None, **copy)

                    def told(filing: Filing, p=frame_path,
                             said=said) -> None:
                        said.update(filing.copy_said(p))

                    if paired is not None:
                        arranged = self._file_pair(
                            seq, number, paired, image=rf.image,
                            raw_image=rf.raw_image, capture=rf.capture,
                            colour=rf.colour, notes=notes,
                            tags=tuple(job.tags) + ("gui", "roll", name),
                            path=frame_path,
                            membership=member,
                            prescan=rf.prescan, prescan_meta=rf.prescan_meta,
                            on_filed=filed, on_picture=told, roll=name,
                            plain=last,
                            mono=wants_mono(job.mono, job.film),
                            mono_channel=job.mono_channel, out_number=rn,
                            called=label)
                    else:
                        arranged = self._file(
                            seq, number, rf.image,
                            dict(frame_meta, roll_membership=member("frame")),
                            notes,
                            tuple(job.tags) + ("gui", "roll", name),
                            raw_image=rf.raw_image,
                            prescan=rf.prescan,
                            prescan_meta=rf.prescan_meta,
                            path=frame_path,
                            roll=name,
                            plain=last,
                            mono=wants_mono(job.mono, job.film),
                            mono_channel=job.mono_channel,
                            on_filed=filed,
                            out_number=rn,
                            called=label,
                        )

                record.update({
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
                    # Which code made the readings in `registration`, where
                    # it holds any: on the frame, since the record goes on
                    # into later runs' files (`frame_read_by`).
                    **frame_read_by(rf.registration),
                    # The number it was labelled and delivered as across its
                    # roll of strips, kept with it: what keeps a number from
                    # being given twice when the index lost a write. A blank
                    # place was given none.
                    **({"roll_number": rn} if strip is not None and not blank
                       else {}),
                    # A place with no picture: settled unless chosen again
                    # (Stefan's "blank-resume" answer adopted), never done,
                    # and no place on the strip to any count of its frames.
                    **({"blank": True, "contrast": (rf.registration or {})
                        .get("contrast")} if blank else {}),
                })
                if scanned is not None:
                    # Per frame rather than only in `settings`, because metering
                    # each frame is the default and then no single exposure
                    # describes the roll.
                    for setting in ("exposure", "gain", "offset"):
                        if scanned.get(setting) is not None:
                            record[setting] = scanned[setting]
                if arranged is not None:
                    # How frameNN.tif was arranged, as it was written -- any
                    # reversal the scanner made necessary included. The
                    # manifest's one `rotation` is the session's at the start,
                    # and a turn made in the window while the roll runs
                    # reaches the frames written after it; Export re-makes
                    # each frame from its entry with this.
                    record["rotation"], record["flipped"] = arranged
                if job.dry_run and rf.prescan is not None:
                    # `prescan` and `prescan_before` -- the files, named so a
                    # picture is not left behind when the walk is carried to
                    # another folder (the window's carry_walk) -- and the
                    # entries they were filed as are the writer's to add,
                    # once it has written them: see `RollManifest.amend`.
                    #
                    # How that file was turned, per frame, as it was written:
                    # the pair `_file` applied, not the session's asked for
                    # again afterwards. The manifest's one `rotation` is the
                    # session's at the start of the walk, and a turn made in
                    # the window while it runs reaches every prescan written
                    # after it -- so one pair un-turned those wrong on reopen
                    # and in `scan_roll --approved`. See `read_survey`.
                    turn, mirrored = (walked_as if walked_as is not None
                                      else self._orientation_for(number,
                                                                 "prescan"))
                    record["prescan_rotation"] = turn
                    record["prescan_flipped"] = mirrored
                # This frame's record replaces any earlier attempt's, so a
                # frame rescanned after a failure is not in the file twice
                # saying two different things about itself. Rewritten after
                # every frame, whole and atomically: a roll takes hours and a
                # crash should cost the frame it was on, not the roll.
                record_of.record(record, awaiting=scanned is not None)

                if self._stop.is_set():
                    stopped = self._stopped_how(number, at)
                    self._emit("log", text=stopped)
                    break
            else:
                asked = frames_asked(first + 1, job.frames, job.only)
                # Whether the driver cut the roll short: gave up after its
                # failures in a row, or found the film behind its count.
                cut = getattr(self._scanner, "roll_cut_short", None)
                cut = cut if isinstance(cut, str) and cut else None
                # Where blank places ended it, 1-based (`roll_strip_end`).
                ended_on = getattr(self._scanner, "roll_strip_end", None)
                ended_on = ended_on if isinstance(ended_on, int) else None
                if self._stop.is_set():
                    # Ended by the driver, but at the stop: it looks before
                    # every frame and every advance, the advances past frames
                    # nobody chose on a sheet's roll included, and returns
                    # without a word to this loop. A Stop there, or the one
                    # `_filed` sets for a frame the library refused, was
                    # recorded as "ended after 1 of the 2 frames asked for":
                    # the end of the film, the one thing this record exists
                    # to tell apart from a Stop.
                    stopped = self._stopped_how(reached, at)
                elif ended_early(asked, blanks, covered):
                    # Ended by the driver short of what was asked: two blank
                    # places in a row, or the end of the transport. It used
                    # to finish like any roll. Said out of every frame asked
                    # for, the ones never reached named (`ended_early_said`).
                    stopped = ended_early_said(covered, asked or (), blanks,
                                               ended_on)
                elif cut is not None:
                    # A run to the end of the strip the driver gave up on
                    # ended as one that reached it: its record said nothing,
                    # and the rest of the strip was never offered (RES-n2).
                    stopped = cut
                # Or the strip ended where a run to its end found no
                # picture: what says the strip is whole (`strip_count`),
                # and that no rest of it is left (`roll_left`) -- only for
                # a run to the end of the strip that nothing stopped. A
                # walk says both at its top, a strip's alone, as a roll
                # from before strips always did; a scanning run in its own
                # entry in ``runs``.
                if cut is not None:
                    ending["cut_short"] = cut
                    if job.dry_run and strip is not None:
                        record_of.mark(cut_short=cut)
                elif ended_on is not None or (
                        stopped is None and asked is None
                        and not self._stop.is_set()):
                    # Where blank places ended it, as the driver found it:
                    # the first of them is where the strip ends.
                    ending["strip_end"] = (
                        ended_on if ended_on is not None
                        else reached + 1 if reached else first + 1)
                    if job.dry_run and strip is not None:
                        record_of.mark(strip_end=ending["strip_end"])
                if blanks:
                    # Always said, whether or not they ended the roll: a
                    # blank place is a frame nobody scanned.
                    ending["blanks"] = list(blanks)
                    # ``done`` is 1 only where they ended it short of the
                    # frames asked for -- the early end Stefan asked to be
                    # said prominently. A run to the end of the strip that
                    # ends on two blanks found where the strip ends, which
                    # is no news, and opened a box an unattended walk sat
                    # behind until somebody came back.
                    early = (not self._stop.is_set()
                             and ended_early(asked, blanks, covered))
                    self._emit("blanks", text=blanks_said(
                        blanks, ended_on, covered, asked, job.dry_run),
                        done=1 if early else 0)
        except BaseException as exc:
            # Said in the file too, not only to the window: a device gone
            # suspect, a transport fault. See `RollManifest.ended`.
            stopped = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            # Ends the generator at its yield rather than leaving it suspended
            # with the device half-way through a roll.
            frames.close()
            # Nothing is scanning now, so a frame the writer has yet to start
            # is filed plain, whatever it was queued as (`FrameWriter.idle`):
            # the end the roll gave no warning of -- a blank frame, the end of
            # the strip, a failure -- as well as the one `last` foresaw.
            self._rolling = False
            # Its strip's prefix ends with it: the next job's lines are no
            # strip's.
            self._log_prefix = ""
            # This roll's orientations die with it. A single scan taken
            # afterwards is not frame 3 of anything, and letting it inherit
            # frame 3's arrangement would be a silent wrong answer.
            self._frame_rotation = {}
            self._frame_flip = {}
            # And so does any prescan from before it: the film has been
            # through the transport since, and a counter that comes back to
            # the same number is no longer evidence of the same picture.
            self._last_prescan = None
            if job.dry_run and not job.extend_walk and not record_of.written:
                # A fresh walk that recorded no frame -- a Stop after the
                # seek's check, a fault before the first frame, a driver
                # that yielded none -- writes nothing (FOUND-2): it replaced
                # survey.json with an empty walk, and the next attempt
                # copied that over the `.bak`, the one list left of the old
                # walk's prescans' entries. The old walk's manifest goes on
                # writing its late answers into its own file.
                if live is not None:
                    self._manifests[key] = live
                else:
                    self._manifests.pop(key, None)
                if manifest_path.exists():
                    self._emit("log", text=(
                        f"{manifest_path} is left as it was: nothing was "
                        "walked"))
            else:
                # Last, once the device is left alone: when and how it ended,
                # and for a scanning run how far it got.
                record_of.ended(stopped, reached=reached, **ending)
        return stopped

    @staticmethod
    def _run_settings(job: Roll, start_at: int, count: int | None,
                      rotation: int, flipped: bool) -> dict[str, Any]:
        """A run's own request, as its manifest's ``settings`` say it --
        and its entry in ``runs``: everything a `Roll` needs to be rebuilt
        from the file alone."""
        return {
            "resolution": job.resolution,
            "infrared": job.infrared,
            "fast_infrared": job.fast_infrared,
            "film": job.film,
            "meter": job.meter,
            "mono": job.mono,
            "mono_channel": job.mono_channel,
            "prescan_resolution": job.prescan_resolution,
            # None where it could not act on any frame: see `aiming_said`.
            "correct": aiming_said(job),
            "correct_dry_run": job.correct_dry_run,
            "max_failures": job.max_failures,
            "frames": count,
            "start_at": start_at,
            "only": list(job.only) if job.only is not None else None,
            "rotation": rotation,
            "flipped": flipped,
            # Which code read the frames is not said here: each frame's
            # record names its own (`frame_read_by`), because the frames
            # are carried from run to run and these settings are one run's.
        }

    def _retakes_only(self, job: Roll, earlier: dict) -> bool:
        """Whether every frame this roll would scan is one ``earlier``, the
        roll's manifest, calls done -- a retake, and nothing new."""
        asked = frames_asked(max(1, job.start_at), job.frames, job.only)
        if not asked or job.rest_from is not None:
            return False
        return set(asked) <= scanned_frames(earlier, self.root)

    def _rest_of_strip(self, job: Roll, place: RollPlace | None
                       ) -> str | None:
        """The second part of a roll finished with frames left among done
        ones and a rest of the strip no run reached (`Roll.rest_from`): a
        run of its own, from there to the end of the strip, once the frames
        left went with nothing to stop or shorten them.

        Its seek passes the done frames between with the SLIDE_NEXTs every
        seek sends, and it meters as its metering says -- again, for a roll
        metered once. A Stop, during the first part or between the two,
        starts none of it. A seek that finds the strip ends just before it
        is no failure: the rest was nothing, and the roll is finished -- as
        for any roll to the end of the strip (`_roll`, `_strip_ended`)."""
        assert job.rest_from is not None
        # Said by the strip's numbers, its places beside them: a run into a
        # strip of a roll of strips has its place from before the seek.
        strip = place.strip if place is not None else None
        if strip is not None:
            strip = place_of(strip.folder, strict=False) or strip
        if self._stop.is_set():
            return (f"stopped after the frames left, before the rest of the "
                    f"strip from {place_said(strip, job.rest_from)}")
        rest = replace(job, start_at=job.rest_from, frames=None, only=None,
                       approved=(), rest_from=None)
        self._emit("log", text=(
            f"the frames left are scanned; on to the rest of the strip, from "
            f"{place_said(strip, job.rest_from)}"))
        return self._roll(rest, place)

    def _strip_ended(self, job: Roll, out: Path, exc: FilmNotPlaced,
                     said: Callable[[int], str] | None = None) -> bool:
        """Record where a seek found the strip's end, as a run of its own
        that took no frame, in the roll.json ``out`` holds -- through the
        session's own manifest of it where there is one -- and say so; and
        whether the end agreed with the strip's records. A rest of the
        strip a roll stopped on its last frame left open is closed so; a
        folder with no roll.json, or one recording no frame, gets nothing.
        Taken as the strip's end for this insertion of it: a strip put in
        another way may end elsewhere, which the log says.

        Only an end on the frame just before the one the roll was to start
        at, and on or past every frame its walk and its rolls recorded. A
        seek runs out wherever an advance leaves the counter where it was
        for half a minute: the strip not put back, put in another way, the
        transport stalling. Each of those was recorded as the strip's end,
        and every reader took it for the latest word: a strip of a roll of
        strips scanned to frame 4, its seek for frame 5 run out on frame 1,
        read as scanned to its end, and the next strip closed it at four
        frames -- its frames 5 and 6 numbered after the roll's last when
        they were scanned.

        ``said`` says a place as the roll's lines do (`place_said`)."""
        path = out / "roll.json"
        manifest = self._manifests.get(path.resolve())
        ended_on = int(exc.strip_ended_on or 0)

        def at(place: int) -> str:
            return said(place) if said is not None else f"frame {place}"

        try:
            if manifest is None and not path.exists():
                return False
            data = _manifest_now(path, self.live_manifest)
            walk = _manifest_now(out / "survey.json", self.live_manifest)
        except (OSError, ValueError) as error:
            self._emit("log", text=(
                f"where the strip ended is not recorded: its records cannot "
                f"be read ({error})"))
            return False
        if data.get("numbering") != NUMBERING or not _places(data):
            # Numbered before frames were places on the strip -- its places
            # are not this run's, and a resume renumbers it -- or holding no
            # frame to hold the end to.
            return False
        held = max(_places(data) + _places(walk))
        before = max(1, job.start_at) - 1
        if ended_on != before or ended_on < held:
            self._emit("log", text=(
                f"the strip ending on {at(ended_on)} is not recorded as "
                "its end: "
                + (f"its records hold frames to "
                   f"{at(held).removeprefix('frame ')}, so it went in "
                   "another way this time, or the transport stalled"
                   if ended_on < held else
                   f"the roll was to start at {at(before + 1)}, further "
                   "on")))
            return False
        now = _utc_now()
        rotation, flipped = self._arrangement
        run = {"started": now, "finished": now,
               "settings": self._run_settings(job, job.start_at, job.frames,
                                              rotation, flipped),
               "stopped": str(exc), "reached": None,
               "strip_end": ended_on + 1}
        try:
            if manifest is not None:
                manifest.add_run(run)
                if manifest.unsaved is not None:
                    # Said by the manifest, and kept for its next write.
                    return True
            else:
                data = read_manifest(path)
                data["runs"] = [*roll_runs(data), run]
                write_manifest(path, data, keep_previous=True)
        except (OSError, ValueError) as error:
            self._emit("log", text=(
                f"where the strip ended could not be recorded in {path} "
                f"({error})"))
            return True
        self._emit("log", text=(
            f"recorded in {path.name} that the strip ends at "
            f"{at(ended_on)}, as it was put in this time"))
        return True

    def _numbers_ready(self, job: Roll, out: Path,
                       strip: StripPlace | None) -> StripPlace | None:
        """The place of the strip this run goes into, read again under the
        roll's lock and made ready to number every frame the run can reach,
        before anything moves; None for no strip of a roll of strips.

        - An open strip a number at or past whose first was given elsewhere
          -- an extra whose index write was refused left it open -- is
          closed at the frames it had, or numbered on, first
          (`StripPlace.settle`).
        - A closed strip whose range the run can pass has the roll's highest
          number read now (`StripPlace.prime`): read between passes and
          refused, it was raised out of the roll after the frame's passes,
          and the frame was never filed.

        Raises ValueError, on the failed-job path, where any of it cannot be
        read or written: nothing has moved."""
        if strip is None:
            return None
        try:
            fresh = place_of(out, live=self.live_manifest)
            if fresh is not None:
                strip = fresh
            strip.live = self.live_manifest
            strip.settle(say=lambda m: self._emit("log", text=m))
        except ValueError as exc:
            raise ValueError(f"{exc}. Nothing was moved.") from exc
        if strip.could_give(job.start_at, job.frames, job.only,
                            scan=not job.dry_run, rest_from=job.rest_from):
            try:
                strip.prime()
            except ValueError as exc:
                raise ValueError(
                    f"{exc}: frames of strip {strip.strip} past its numbers, "
                    f"{strip.numbers()}, take a number after the roll's last, "
                    "which cannot be read. Nothing was moved.") from exc
        return strip

    def _walk_refused(self, job: Roll, out: Path, strip: StripPlace | None,
                      plan: StripPlan | None) -> None:
        """Raise `RollFolderInUse` for a fresh walk into ``out`` that would
        mix with or write over what is there (`folder_in_use`), or into a
        strip of a roll of strips at all; `StripFolderUsed` for a new strip
        whose folder holds files its index does not list, walked or
        scanned; nothing for any other job."""
        if plan is not None:
            # Under the lock, before the seek: asked first by the claim,
            # after it, the film had moved by the time it was refused.
            strip_folder_used(plan)
            return
        if not job.dry_run or job.extend_walk:
            return
        if strip is not None:
            raise RollFolderInUse(
                f"strip {strip.strip} of {strip.label} has its walk: walked "
                "again, it is added to ('add to it'), so no number it gave "
                "is lost. Nothing was moved.")
        live = self._manifests.get(_resolved(out / "survey.json"))
        used = folder_in_use(out, survey=live.snapshot()
                             if live is not None else None)
        if used:
            free, _why = walk_dir(out.parent, out.name)
            raise RollFolderInUse(
                f"{out} holds {'; '.join(used)}: a new walk goes into a "
                f"folder of its own ({free.name} is free). Nothing was moved.")

    def _room_for(self, job: Roll, frames: int, out: Path) -> list[str]:
        """What stops ``frames`` more of this roll's frames being filed: each
        disk with less free space than they can take (`check_space`).

        A disk whose room cannot be read -- an output stick pulled out -- is
        said once a roll and does not stop it: it costs that disk's copies,
        each said as it is not written, and the frames are filed all the
        same.
        """
        short, unknown = check_space(self._roll_needs(job, frames, out),
                                     SPACE_REFUSE)
        for line in unknown:
            if line not in self._unknown_said:
                self._unknown_said.add(line)
                self._emit("log", text=(
                    f"{line}; the roll goes on, and a copy that cannot be "
                    "written there is said as it is"))
        return short

    def _hold_roll(self, out: Path) -> None:
        """Hold the roll folder ``out`` for this session until it closes
        (`locks.ROLL`); raise `locks.Refused`, naming who has it, where
        another program does. Taken between jobs, before the seek: never
        inside a pass, and never waited on for more than `locks.WAIT_S`.

        Held after the roll ends, not only while it runs: its last frames
        are filed into it afterwards, and the next roll into the same folder
        carries its manifest on. Inside this window nothing else wants it --
        a second window on these rolls is refused at its start.
        """
        key = locks.key(out)
        if key in self._roll_locks:
            return
        try:
            held = locks.take(locks.ROLL, out, exclusive=True, what=self.who)
        except locks.Refused as exc:
            if not exc.holders:
                raise
            raise locks.Refused(
                f"{out} is in use by {exc.who}: nothing was moved or scanned",
                exc.holders, kind=exc.kind, path=exc.path) from exc
        if held.held:
            self._roll_locks[key] = held
        elif held.note:
            # Not kept: kept, a lock not taken for a moment -- a lock folder
            # that could not be written -- stood for one taken, and the
            # window's later rolls into this folder, days of them, never
            # asked again.
            self._emit("log", text=f"{held.note}; scanning into it unguarded")

    def _let_go_of_rolls(self) -> None:
        """Let go of every roll folder this session held (`_hold_roll`)."""
        held, self._roll_locks = self._roll_locks, {}
        for each in held.values():
            each.release()

    def let_go_of_roll(self, folder: Any) -> bool:
        """Let go of this session's own hold on a roll folder the window's
        Delete or Rename has just moved (P23-b); whether it held one.

        Held until the session closes (`_hold_roll`), a folder moved away
        stayed locked under its old name: `tools/scan_roll.py` was refused
        a new roll of that name, and a restore of another roll there was
        put beside it. Only for a folder that is its own lock -- a roll, or
        one from before strips -- never for a strip moved on its own: its
        roll is still there, and still the session's.
        """
        folder = Path(folder)
        if library.lock_folder(folder) != folder:
            return False
        held = self._roll_locks.pop(locks.key(folder), None)
        if held is None:
            return False
        held.release()
        return True

    def _stopped_how(self, reached: int | None,
                     said: Callable[[int], str] | None = None) -> str:
        """How a roll that stopped says so: "as asked" for an operator's
        Stop, and the reason for the one `_filed` makes itself. ``said``
        says the place it reached, as the roll's lines do (`place_said`)."""
        if reached is None:
            where = "before its first frame"
        else:
            where = f"after {said(reached) if said else f'frame {reached}'}"
        if self._stop_reason is not None:
            return f"stopped {where}: {self._stop_reason}"
        return f"stopped {where}, as asked"

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
        owed: bool = False,
        roll_number: int = 0,
    ) -> int:
        """Hand the UI a working copy small enough to keep.

        A copy rather than a view, so the full frame can be freed as soon as the
        writer has it -- a strided view would pin all 142 MB of a 3600 dpi pass.
        The copy is a few megabytes and takes milliseconds, which is the only
        reason it is allowed to happen with the device still open.

        A copy for a small pass too. A pass of 1400 pixels or less -- every
        one at 300, 600 and 900 dpi -- needs no reducing, and `downscale`
        hands back the array itself, which `ascontiguousarray` passed through:
        the window's picture was then the very array the writer copies to the
        output folder, the last prescan and, through `approved_from_sheet`,
        the reference a hold correlates against. Nothing wrote into it, but
        nothing stopped anything doing so either. One copy always, at most
        about 16 MB.

        ``owed`` for a pass the caller files next under this seq with an
        entry: owed from before the window can see it (`filing`).
        """
        working = np.array(preview.downscale(image, preview.PREVIEW_MAX_SIDE),
                           order="C", copy=True)
        if position is None:
            position = self._position()
        self._seq += 1
        if owed:
            self._owe(self._seq)
        self._emit("result", result=Result(
            seq=self._seq, kind=kind, label=label, image=working, meta=dict(meta),
            registration=dict(registration or {}), position=position,
            number=number, roll_number=roll_number,
        ))
        return self._seq

    def _unannounced_seq(self) -> int:
        """A sequence number for a picture filed with no result of its own.

        Drawn from the results' own count, so its "filed" can never name a
        result the window holds.
        """
        self._seq += 1
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
        rather than a deliverable: `read_survey` un-orients it by the pair
        recorded with it (`prescan_arrangement`), which is the session's, and
        a sheet's per-frame answer here would make that arithmetic wrong --
        the reference would come back arranged a way the film was never in,
        and it would no longer correlate against a fresh pass of the same
        frame.
        """
        # The session's two in one look: a turn and a mirror set in the
        # window between two reads made a pair nobody chose (`arrange`).
        rotation, flip = self._arrangement
        if kind != "prescan":
            turn = self._frame_rotation.get(number)
            if turn is not None:
                return turn, self._frame_flip.get(number, flip)
        return rotation, flip

    def _unpublished(self, meta: dict[str, Any] | None, what: str) -> bool:
        """Whether a pass may be filed on its own meta: False, said, if the
        scanner published none.

        CLAUDE.md: pass the meta the scan returns, never a substitute. One
        built here from the job -- a resolution and a channel order -- is what
        filed 26 prescans describing themselves wrongly, and the roll kept it
        as the fallback for a pass that came with none. Such a pass is still
        written where it goes; it is only kept out of the library, whose
        entries are the record that has to be true.
        """
        if meta:
            return True
        self._emit("log", text=(
            f"{what}: the scanner published no record of this pass, so it "
            "is not filed in the library -- an entry made up here would "
            "describe itself wrongly"))
        return False

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
        on_filed: Callable[..., Any] | None = None,
        capture: dict[str, Any] | None = None,
        copies: bool = True,
        plain: bool = False,
        out_suffix: str = "",
        file_entry: bool = True,
        shown: int | None = None,
        part: str = "",
        pair: int | None = None,
        sheet: Path | None = None,
        out_number: int | None = None,
        called: str = "",
    ) -> tuple[int, bool]:
        """Write this picture and file it in the library.

        ``capture`` is the pass's own `capture_record`, taken when the pass
        was. None reads the scanner's now, which describes its **last** pass
        -- right for the pass that just ran, and nothing else: a roll's
        prescan runs several passes before it is filed, and a hold's
        verification prescan is identically shaped, so the guard below cannot
        catch that swap. ``{}`` files it with none.

        ``copies=False`` leaves the operator's output folder out: a real
        roll's prescans are evidence for the library, and that folder has
        always held its frames.

        ``plain`` files a roll's picture uncompressed, as a single pass is,
        for one that nothing will be scanned after; see `_roll`.
        ``out_suffix`` goes into the output folder's name for it.

        ``file_entry=False`` writes the delivered files and leaves the library
        out, for a pass whose meta cannot describe it (`_unpublished`).

        ``on_filed(filing)`` is called on the writer thread with the
        picture's `Filing` once it has been filed, or has failed to be; see
        `RollManifest`.

        ``shown`` is the result the window shows this pass under, where it
        is filed under a seq of its own (`_unannounced_seq`): a slide's two
        other writes, a picture from before an aim. ``part`` says which of
        the frame's writes it is, and ``pair`` is the seq of the pass a
        picture filed into no entry is made from (`_file_pair`). ``sheet``
        is a walk prescan's own file, which a reopened sheet reads. See
        `Filing`. ``out_number`` is the number the output folder's copy is
        named by where it is not ``number``: a strip's frame across its roll
        of strips, whose file inside the strip is named by its place.
        ``called`` is the picture as its result is labelled, which a filing
        that goes wrong is named by (`Filing.named`).

        Returns the turn and mirror the delivered files are written with, so
        a manifest records the arrangement each file really got rather than
        asking the session again, whose answer a turn in the window can have
        moved in between.
        """
        if self._writer is None:
            return self._orientation_for(number, kind)
        self._owners[seq] = self._current
        # A roll frame has its own place in the roll directory *and* wants a
        # copy wherever the operator asked for one. Setting `path` used to skip
        # the output folder entirely, so a whole roll went missing from it.
        paths = [path] if path is not None else []
        # Only a record read off the scanner here can be another pass's: one
        # handed in was taken as its own pass landed (`RollFrame`), and moves
        # with that pass's pixels through a hold or an aim.
        #
        # And none at all for a picture filed into no entry: the writer reads
        # a capture only to file one. Checked anyway, the scanner's last pass
        # was held against the slide's merged picture, which no pass's bytes
        # describe, and every slide scanned with infrared logged "raw bytes do
        # not describe this image" over a picture nobody was filing.
        read_now = capture is None and file_entry
        if not file_entry:
            capture = {}
        elif capture is None:
            capture = self._scanner.capture_record()
        else:
            capture = dict(capture)
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
            elif read_now and bytes_are_another_pass(self._scanner, raw_image):
                # The same failure in the shape the guard above is blind to:
                # a walk frame whose aim failed after a verification prescan
                # is filed with the prescan it arrived with, and the bytes on
                # hand are the verification's -- the same frame, the same
                # shape. Filed together, the entry decoded to a different
                # picture from the one it held, and `reconstruct` called it a
                # changed decode. The pass's own bytes are in the debug spool
                # when that is on, and are left there (see the claim below).
                #
                # Not for a record handed in. That is the pass's own, and the
                # scanner's last pass is by then some other one -- the frame's
                # scan, a verification prescan -- so asked of it, this dropped
                # the bytes of every roll prescan and every picture from
                # before an aim, which were right.
                self._emit("log", text=(
                    f"{called or f'picture {number}'}: the raw bytes on hand "
                    "are a later pass's; filing it without them rather than "
                    "filing the wrong ones"))
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
        # here keeps -- metering probes, hold and aim prescans. Not when the
        # bytes were dropped above: the spooled copy has them, and is then the
        # only copy that does. The receipt goes back when the writer has filed
        # it, or failed to, and only then is the spooled copy let go.
        claim = getattr(self._scanner, "debug_claim", None)
        receipt = None
        library_root = self.root if file_entry else None
        if (raw_image is not None and library_root is not None
                and callable(claim)
                and (capture.get("raw") is not None
                     or capture.get("raw_path") is not None)):
            receipt = claim(raw_image)
        #: The output folder's name this job reserves, released by the writer
        #: once the job is over (`export.claim`), and a copy that found no
        #: free name, which the job's `Filing` says. Reserved last, so that
        #: nothing but queuing the job can fail while it is held.
        claims: list[Path] = []
        refused: list[tuple[str, str]] = []
        #: Where and as what this job writes its copies, as it started.
        output = self._out()
        if output.folder is not None and copies:
            where = (output.folder / PRESCAN_SUBDIR if kind == "prescan"
                     else output.folder)
            wanted = where / self._out_name(
                number if out_number is None else out_number, meta, roll,
                out_suffix)
            try:
                claims.append(export.claim(wanted))
            except FileExistsError:
                # A folder holding the name and -2 to -999 beside it: that
                # copy goes, and the entry is filed all the same. Taking the
                # asked-for name anyway wrote over the file that had it.
                refused.append((str(wanted), f"{where} has no free name left "
                                             f"for {wanted.name}"))
            except OSError as exc:
                # A folder whose names cannot even be looked at -- access
                # denied, a network share dropped or its logon expired:
                # `Path.exists` raises for those rather than saying no.
                # Raised from here, the job was never queued, and the pass
                # left with it although only its copy was in doubt.
                refused.append((str(wanted), str(exc)))
            paths += claims
        with self._filing_lock:
            if library_root is not None:
                self._owed.add(seq)
            else:
                # Shown as owed (`_deliver`) and then not filed after all.
                self._owed.discard(seq)
            self._unqueued.discard(seq)
        try:
            # The promise a walk prescan's answer makes, made as it is queued.
            queued(self._writer.submit, on_filed,
                seq=seq,
                number=number,
                kind=kind,
                paths=paths,
                claims=claims,
                unwritten=refused,
                # Which result it is shown under, and which of the frame's
                # writes it is, for its `Filing`; and the job that queued it.
                shown=seq if shown is None else shown,
                part=part,
                pair=pair,
                sheet=sheet,
                called=called,
                roll_folder=path.parent if path is not None else None,
                owner=self._job_no,
                # A single scan or prescan is filed with the scanner open and
                # idle between jobs, and compressing then -- gzip, and TIFF
                # deflate -- is what preceded a wedge (CLAUDE.md). So those
                # are written plain -- the entry and the output-folder copy
                # alike -- and the entry is compressed when the session
                # closes. A roll's frames keep compressing on the writer
                # thread while the next frame scans: the device is busy
                # there, which is the exception CLAUDE.md argues and
                # `tools/filing_load_test.py` exists to measure. Not its last
                # frame, which has no next one (`plain`), nor one a Stop
                # caught after `plain` was decided -- pressed during a
                # slide's 9 s merge, between the pass and here (P13-a).
                compress=bool(roll) and not plain and not self._stop.is_set(),
                rotate=turn,
                flip=flip,
                image=image,
                raw_image=raw_image,
                quality=output.quality,
                meta=meta,
                dpi=meta.get("resolution_dpi"),
                library=library_root,
                film=notes,
                tags=list(tags),
                prescan=prescan,
                prescan_meta=prescan_meta,
                inquiry=getattr(self._scanner, "_inquiry", None),
                capture=capture,
                mono=mono,
                mono_channel=mono_channel,
                on_filed=answering(receipt, on_filed),
            )
        except BaseException:
            # Never queued: nothing will answer for it or release its name.
            with self._filing_lock:
                self._owed.discard(seq)
            export.release(claims)
            raise
        return turn, flip

    def _out_name(
        self, number: int, meta: dict[str, Any], roll: str, suffix: str = ""
    ) -> str:
        """A filename that says which roll and which frame it came from.

        NegPy reads these next, so the roll and the frame number lead. What this
        replaced led with a timestamp and ended with a sequence number, which
        sorted by when it was scanned and said nothing about what it was --
        fine for one pass, useless for thirty-eight of them.

        A scan that belongs to no roll keeps a timestamp, because there is
        nothing better to call it. ``suffix`` goes before the extension.

        The film's process follows the frame -- ``c41``, ``e6``, ``bw`` or
        ``k14`` (`film_process`) -- where the pass says its film, so a folder
        of deliveries sorts colour negative from slide from black and white
        by name alone.
        """
        dpi = meta.get("resolution_dpi") or 0
        process = film_process(meta.get("film"))
        film = f"_{process}" if process else ""
        channels = meta.get("channels") or len(meta.get("channel_order") or "")
        ir = "_ir" if channels and int(channels) >= 4 else ""
        # `_ir` still names a pass that *was* infrared even when the format
        # cannot carry the plane: it says what was scanned, and the JPEG's own
        # note says what arrived. Renaming it would lose the first. The
        # format is the running job's, as it started (`Output`).
        end = suffix + export.suffix_for(self._out().format)
        if roll and number:
            return f"{_safe(roll)}_frame{number:02d}{film}_{dpi}dpi{ir}{end}"
        if roll:
            return (f"{_safe(roll)}_{time.strftime('%H%M%S')}{film}_{dpi}dpi"
                    f"{ir}{end}")
        return f"{time.strftime('%Y%m%dT%H%M%S')}{film}_{dpi}dpi{ir}{end}"


#: The longest a temporary name `library._write_atomic` gives a file beside
#: it can be: ``.<name>.<process>-<thread>.part``, with a process id of up to
#: ten digits and a thread id of up to twenty.
_ATOMIC = ".{name}." + "9" * 10 + "-" + "9" * 20 + ".part"
#: What `library._reserve` adds to an entry's id taken already, at most.
_ENTRY_TAKEN = "-99"


def written_beside(path: Path) -> Path:
    """The longest temporary name ``path`` can be written under, by
    `library._write_atomic` or a roll's manifest (`write_manifest`)."""
    return path.with_name(_ATOMIC.format(name=path.name))


def entry_bound(root: Any, entry: str) -> Path:
    """The longest path in a library entry ``entry`` filed under ``root``:
    its record's temporary name, in a folder with `_reserve`'s "-N"."""
    return written_beside(Path(root) / (entry + _ENTRY_TAKEN) / "scan.json")


def manifest_bound(folder: Any) -> Path:
    """The longest path a roll's own records take in ``folder``: the copy
    of `survey.json` kept before a run (`PREVIOUS`), written beside."""
    return written_beside(Path(folder) / f"survey.json{PREVIOUS}")


def copy_bound(wanted: Path) -> Path:
    """``wanted`` at the last free name `export.claim` can give it."""
    return wanted.with_name(
        f"{wanted.stem}-{export.FREE_NAMES - 1}{wanted.suffix}")


def spool_bound(spool: Any) -> Path:
    """The longest path in debug filing's spool under ``spool``: a session's
    folder (`tempfile.mkdtemp`) holding a pass's record written beside.

    ``spool`` is that session folder itself once there is one
    (`DirectScanner.spool_place`), and is not given a second: the path then
    came out 23 characters longer than any written, and on Windows could
    refuse a job under a long library from its second pass on.
    """
    spool = Path(spool)
    prefix = DirectScanner.DEBUG_SPOOL_PREFIX
    if not spool.name.startswith(prefix):
        spool = spool / (prefix + "x" * 8)
    return written_beside(spool / "999-meta.json")


def _refusal(job: Job) -> str:
    """How a refused job begins its message: what it is not doing."""
    if isinstance(job, Calibrate):
        return "not calibrating"
    if isinstance(job, Prescan):
        return "not prescanning"
    if isinstance(job, Roll):
        return f"not starting the {'walk' if job.dry_run else 'roll'}"
    return "not scanning"


#: Names Windows will not give a file or a folder, whatever the extension.
#: They are device names, not reserved words, so `CON` fails where `CON-1`
#: is fine -- and it fails as NotADirectoryError from `mkdir`, which reads
#: like a bug in this driver rather than a name the operator chose.
_RESERVED = frozenset(
    ["con", "prn", "aux", "nul"]
    + [f"com{n}" for n in range(1, 10)]
    + [f"lpt{n}" for n in range(1, 10)]
)


#: The longest a name made here may be, in characters and in UTF-8 bytes as
#: macOS stores it (decomposed, NFD). Uncapped, a roll name became a folder
#: and the head of every copy's name, and a long one failed as a misleading
#: WinError after the film had moved; capped by characters alone, 64 CJK
#: characters past the basic plane are 256 bytes, past every filesystem's
#: 255 for one name. 160 bytes leaves the copy's own tail room under 255.
NAME_CAP = 64
NAME_CAP_BYTES = 160
#: How much of a name over the cap is kept, before a dash and `_HASH_DIGITS`
#: of its hash.
_NAME_KEPT = 56
_HASH_DIGITS = 7


def _safe(name: str, fallback: str = "roll") -> str:
    """`name` with anything a filename should not carry taken out.

    ``fallback`` when nothing of it is left; `roll_dir` asks for "" there, so
    that a name of nothing but slashes is a new roll and not a shared one.

    And no longer than `NAME_CAP` (`_cap`).
    """
    return _cap(_clean(name, fallback))


def _clean(name: str, fallback: str) -> str:
    """`_safe` less the cap: characters and Windows' device names only."""
    kept = [c if (c.isalnum() or c in "-_.") else "-" for c in name.strip()]
    cleaned = "".join(kept).strip("-.") or fallback
    # Checked against the part before the first dot, which is what Windows
    # matches on: `con.tif` is refused as surely as `con`.
    if cleaned and cleaned.split(".")[0].lower() in _RESERVED:
        cleaned = f"{cleaned}-roll"
    return cleaned


def _nfd_bytes(text: str) -> int:
    """How many bytes ``text`` takes as a name on the strictest filesystem
    here: UTF-8, decomposed as macOS stores it."""
    return len(unicodedata.normalize("NFD", text).encode("utf-8"))


def _cap(cleaned: str) -> str:
    """A cleaned name, cut to `NAME_CAP` characters and `NAME_CAP_BYTES`.

    Stefan's call, 2026-10-09: a name over the cap keeps its first 56
    characters, and then a dash and seven hex digits of the SHA-256 of the
    whole name, case-folded. Cut plainly, two rolls named alike for their
    first 64 characters were one folder, and the second run was taken for a
    resume of the first. Case-folded, so a name typed again in other case
    finds the folder it found before on a disk that does not tell the two
    apart. The same name always gives the same folder, so a resume finds it.
    """
    if len(cleaned) <= NAME_CAP and _nfd_bytes(cleaned) <= NAME_CAP_BYTES:
        return cleaned
    digest = hashlib.sha256(cleaned.casefold().encode("utf-8")).hexdigest()
    tail = "-" + digest[:_HASH_DIGITS]
    kept = cleaned[:_NAME_KEPT]
    # Character by character off the end, until the bytes fit too; never
    # through the middle of one.
    while kept and _nfd_bytes(kept) > NAME_CAP_BYTES - len(tail):
        kept = kept[:-1]
    return kept.rstrip("-.") + tail


def name_cut(name: str) -> bool:
    """Whether a roll typed as ``name`` is filed under a name cut to the cap:
    for saying so, beside the folder it made."""
    cleaned = _clean(name, fallback="")
    return _cap(cleaned) != cleaned


def _utc_now() -> str:
    """Now, as the roll tool writes its manifests' times."""
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


def utc_now() -> str:
    """Now, as every roll record writes its times: the window's too, whose
    sheet state and `approved.json` are weighed against each other by them,
    as text (the window's `merge_sheet`), so they are written one way."""
    return _utc_now()


def _free_name(wanted: str, taken: set[str]) -> str:
    """`wanted`, or the first ``stem-N`` beside it that is not in `taken`."""
    if wanted not in taken:
        return wanted
    stem, dot, suffix = wanted.rpartition(".")
    n = 2
    while f"{stem}-{n}{dot}{suffix}" in taken:
        n += 1
    return f"{stem}-{n}{dot}{suffix}"


def _unclaimed(wanted: Path, sidecars: Sequence[str] = ()) -> Path:
    """`wanted`, or the next free name beside it: `export.unclaimed`.

    A frame rescanned after a failure would otherwise land on the file the
    first attempt wrote, and the better of the two is not always the second.

    Free means free for every file `export.write` may leave under the name
    -- a JPEG's companion DNG, its TIFF fallback -- not only the one asked
    for; any other name is only its own. ``sidecars`` are suffixes a caller
    writes beside the picture itself, `tools/scan.py`'s ``.json``: those
    have to be free as well, or ``scan.tif`` after ``scan.jpg`` found its
    own name free and wrote over the first scan's record.

    Raises FileExistsError once a thousand names are taken. It returned the
    name asked for, and the write that followed replaced the file holding
    it. A writer of delivered files claims its name instead
    (`export.claim`); this is for the names that are no delivery -- a
    manifest set aside, an entry being deleted.
    """
    return export.unclaimed(wanted, sidecars=sidecars)


def _read_known(meta: dict[str, Any] | None) -> bool:
    """Whether a pass's own line tags said which way it was read."""
    read = (meta or {}).get("read_direction") or {}
    return read.get("direction") in (FORWARD, REVERSED)


def _same_gains(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    """Whether two gain mismatches are the same one: the scanner's gain and
    the reference's, never the offsets, which wander by a count a read."""
    if not a or not b:
        return False
    return (a["now"]["gain"] == b["now"]["gain"]
            and a["reference"]["gain"] == b["reference"]["gain"])


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
        # Which strip of which roll, for a roll of strips: by the strip the
        # question planned, or the strip folder it goes into.
        strip = roll = None
        if job.new_strip and job.strip_plan is not None:
            strip = job.strip_plan[0]
            roll = Path(job.out).name if job.out else job.name
        elif job.out and library.strip_number(Path(job.out).name) is not None:
            strip = library.strip_number(Path(job.out).name)
            roll = Path(job.out).parent.name
        of = f" of strip {strip} of {roll}" if strip is not None else ""
        if job.rest_from is not None:
            # The frames left, and then the rest of the strip (`Roll.
            # rest_from`): one job, said as both.
            left = ", ".join(str(n) for n in sorted(job.only or ()))
            return (f"{what} "
                    + (f"frames {left}, then " if left else "from frame ")
                    + f"{job.rest_from} to the end of the strip{of}")
        if job.only is not None:
            n = len(job.only)
            return f"{what} {n} chosen frame{'s' if n != 1 else ''}{of}"
        if strip is not None:
            return f"{what} strip {strip} of {roll}" + (
                f", {job.frames} frames" if job.frames else "")
        n = job.frames if job.frames else "?"
        return f"{what} a roll of {n} frames"
    if isinstance(job, Move):
        if job.frames:
            way = "forward" if job.frames > 0 else "back"
            n = abs(job.frames)
            return f"moving {n} frame{'s' if n != 1 else ''} {way} (~7 s each)"
        return f"nudging the film {say_distance(job.units)}"
    return str(job)
