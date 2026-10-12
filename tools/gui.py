#!/usr/bin/env python3
"""A window for scanning negatives on the Reflecta RPS 7200.

    make run                      # the real scanner
    make run-demo                 # stored library entries, nothing on the bus
    make run-sheet                # the contact sheet on a stored walk, with
                                  # the positions measured again every launch

Prescan, scan, walk a roll, and look at what came off -- including the infrared
plane on its own, which is the one channel no ordinary viewer will show you.
Files are written exactly as the command-line tools write them: raw negatives,
filed in the library with their raw bytes, their shading reference and their CCD
mask. Inverting, dust removal and colour are NegPy's job; the inversion in this
window is for your eyes only and never reaches disk.

The output folder can be set to TIFF or JPEG. That is a container choice and not
a picture one: a JPEG holds the same negative at eight bits, uninverted, which
is why it looks orange. A JPEG has no room for the infrared plane, so an RGBI
scan delivered that way leaves a DNG beside it holding all four channels -- that
is the file to open for dust removal -- and a roll's own files under `rolls/`
stay TIFF regardless.

Opening the window claims the device and asks it who it is, and does nothing
else. Nothing moves the mechanism until a button is pressed.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import itertools
import json
import math
import os
import queue
import shutil
import subprocess
import sys
import time
import threading
import traceback
import warnings
import tkinter as tk
from collections.abc import Callable
from pathlib import Path, PureWindowsPath
from typing import NamedTuple
from tkinter import filedialog, font as tkfont, messagebox, simpledialog, ttk

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rps7200 import (                                     # noqa: E402
    export, library, locks, preview, settings, shortcuts, storage,
)
from rps7200.console import (                             # noqa: E402
    ConsoleClose,
    DeferredInterrupt,
    KeptLog,
    logged_to,
    use_utf8_stdout,
)
from rps7200.direct import (                              # noqa: E402
    FILM_BW,
    FILM_TYPES,
    INFRARED_IS_BLIND_TO,
    METER_MODES,
    DirectScanner,
    supports_infrared,
)
from rps7200.framing import (                              # noqa: E402
    FRAME_WIDTH_UNITS,
    FULL_FRAME,
    PRESCAN_COLUMNS,
    measure_shift_mm,
    say_hold,
    scan_window,
    units_per_column,
)
from tools import frame_edges                             # noqa: E402
from rps7200.library import FilmNotes                     # noqa: E402
from rps7200.mono import (                                 # noqa: E402
    MONO_AVERAGE,
    MONO_CHANNEL,
    MONO_CHOICES,
    infrared_left_out,
    to_monochrome,
    wants_mono,
)
from rps7200.protocol import (                             # noqa: E402
    COORD_PER_INCH,
    MM_PER_INCH,
    MM_PER_COMMAND,
    MM_PER_UNIT,
    PROTOCOL_REVISION,
    film_process,
    say_command,
    say_units,
    units,
    units_for_param,
)
from rps7200.session import (                              # noqa: E402
    COPY_FAILED,
    FINE_MAX_MM,
    FINE_MIN_MM,
    FORWARD_FRAME_S,
    INFRARED_TIE_CROSSOVER_DPI,
    LAST_PLAUSIBLE_POSITION,
    NOT_FILED,
    NUMBERING,
    SETTING_ALIASES,
    SPACE_WARN,
    UNFILED,
    Approved,
    Calibrate,
    Move,
    Prescan,
    Result,
    Roll,
    Scan,
    ScanSession,
    StillFiling,
    StripCount,
    StripOffer,
    StripPlace,
    StripPlan,
    _safe,
    approved_legacy,
    as_on_disk,
    check_space,
    close_strip,
    deliverable_mm,
    earlier_manifest,
    entry_trashed,
    estimate_seconds,
    frame_finished,
    frame_said,
    frames_asked,
    highest_given,
    legacy_shift,
    listed_strips,
    manifest_settings,
    new_roll_name,
    open_last_strip,
    place_of,
    plan_next,
    plan_nudges,
    plausible,
    prescan_arrangement,
    read_approved,
    read_index,
    read_manifest,
    read_walk_prescan,
    recorded_numbers,
    recorded_roll_name,
    renumbered,
    roll_differs,
    roll_dir,
    roll_key,
    roll_left,
    roll_name_of,
    roll_of,
    scanned_frames,
    set_strip_end,
    strip_count,
    utc_now,
    walked_prescan_entries,
    walked_prescans,
    # Moved into the session, so the window, its browser and the roll tool
    # read one roll alike; still answered here for what asked it here.
    wanted_frames,  # noqa: F401
    write_manifest,
)

#: Resolutions this scanner has actually been driven at, plus the optical
#: maximum it reports for itself.
#:
#: 300, 600, 900, 1800 and 3600 all appear in the USB captures of the vendor
#: software or in timings measured here; 7200 is what INQUIRY calls the optical
#: resolution. Nothing else has evidence behind it. An earlier version of this
#: list held every integer divisor of 7200 -- 360, 400, 450, 480, 720, 800,
#: 1200, 1440, 2400 -- which came from `docs/dpi-tradeoff-plan.md`, where it is
#: the *candidate* list for an experiment titled "which resolutions the device
#: accepts". That experiment has not been run, so the list was a set of guesses
#: presented as a menu.
#:
#: The box stays editable, so anything can still be typed: there is no
#: client-side validation, the value goes into MODE SELECT as a 16-bit field,
#: and the device refuses what it dislikes with sense 0x26/0x82 before a single
#: byte of image data moves. Guessing is cheap; offering a guess is not.
DPI_LADDER = (300, 600, 900, 1200, 1800, 3600, 7200)

#: What a prescan is worth spending. 300 dpi is the scanner's own fast-preview
#: resolution, reported by INQUIRY and what the vendor uses before every frame,
#: at ~16 s. 600 is what its own session-start previews use. Beyond that a
#: framing pass stops being cheap, which is the only reason it exists.
PRESCAN_LADDER = (300, 600, 900)

#: The controls worth carrying between launches: what the scanner is being
#: asked to do. Not the view -- channel, invert and zoom start where they always
#: did, because they describe the last thing looked at rather than the setup.
#: `last` is a new key rather than the old `frames` reused: that one held a
#: count, and a remembered "6" read as "last frame 6" is a different roll.
REMEMBERED = ("dpi", "predpi", "ir", "fast_ir", "film", "expmode", "exposure",
              "shading", "meter", "dryrun", "correct", "fine", "aim", "reverse",
              "startat", "last", "outfmt", "jpegq", "adjuststep")

#: Which controls each left-hand panel owns, keyed by the title in its header.
#: The header uses this to say how many of them differ from their default and to
#: reset exactly those. Data rather than a walk of the widget tree, so the count
#: and the reset can both be tested without Tk.
#:
#: `mono` is absent deliberately: `_sync_film` derives it from the film type, so
#: resetting the film resets it, and offering it separately would offer a control
#: that immediately disagrees with the one above it.
PANEL_CONTROLS = {
    "Scan": ("dpi", "predpi", "ir", "fast_ir", "film", "mono_channel",
             "expmode", "exposure", "shading"),
    "Transport": ("fine", "aim", "reverse"),
    "Roll": ("startat", "last", "meter", "dryrun", "correct"),
    "Film": ("stock", "roll", "frame", "process", "subject", "notes", "tags"),
    "Save scans to": ("outfmt", "jpegq"),
}

#: Remembered settings that belong to no panel, so "Restore settings ..." can
#: still reach them. `adjuststep` lives on the window rather than on the frame
#: adjuster precisely so it is not re-chosen seventeen times a roll, which
#: leaves it with no header to sit under.
PANEL_LESS = ("adjuststep",)


def as_text(value) -> str:
    """A control's value as one canonical string, for comparing.

    Ticks are the awkward ones. A `BooleanVar` answers `True`, a settings file
    edited by hand may hold `1`, and JSON round-trips `true` -- all three are
    the same tick, and a comparison that disagreed would offer to reset a
    control nobody had touched. Everything else is its own text, stripped, so
    `" 1800"` and `"1800"` are not a change either.
    """
    if isinstance(value, bool):
        return "1" if value else "0"
    text = str(value).strip()
    return {"true": "1", "yes": "1", "false": "0", "no": "0"}.get(
        text.lower(), text)


def changed_controls(values: dict, defaults: dict, names) -> tuple:
    """Which of `names` have been moved off their default, in order.

    A name with no recorded default is skipped rather than counted: it means the
    control did not exist when the defaults were taken, and inventing a default
    for it is how a reset button starts changing things nobody set.
    """
    return tuple(name for name in names
                 if name in defaults
                 and as_text(values.get(name, "")) != as_text(defaults[name]))


#: What a preset carries: the scan settings, and nothing about the film in the
#: transport or where the files go.
PRESET_KEYS = ("dpi", "predpi", "ir", "fast_ir", "film", "expmode", "exposure",
               "shading", "meter")

#: The values a preset's choice-valued keys may take: what their controls
#: offer. A read-only chooser cannot show anything else, and cannot be moved
#: off it either.
PRESET_CHOICES = {
    "film": tuple(FILM_TYPES),
    "meter": tuple(METER_MODES),
    "expmode": ("auto", "manual"),
    "shading": ("measure", "reuse"),
    "ir": ("0", "1"),
    "fast_ir": ("0", "1"),
}


def preset_values(stored: dict) -> tuple[dict, list[str]]:
    """What a stored preset may set, and the keys it had that it may not.

    Presets live in `gui-settings.json`, which is edited by hand. Choosing one
    used to set `v_<key>` for *every* key it held, so a hand-added
    `mono_channel` -- no preset key -- reached that chooser unchecked, and a
    film type the chooser does not offer went straight to the next roll. Only
    `PRESET_KEYS` are taken, and a choice only when it is one on offer.

    A choice is set as the text it was checked as. Checked stripped and set as
    written, `" negative"` passed and then reached the next roll with its
    space, where no film lookup matches it. "1" and "0" set a tick as well as
    `True` and `False` do.
    """
    out: dict = {}
    refused: list[str] = []
    for key, value in stored.items():
        if key not in PRESET_KEYS:
            refused.append(key)
            continue
        allowed = PRESET_CHOICES.get(key)
        if allowed is not None and as_text(value) not in allowed:
            refused.append(key)
            continue
        out[key] = as_text(value) if allowed is not None else value
    return out, refused

#: Film fields safe to carry over. `stock`, `process` and `tags` describe the
#: film and are the same all roll; `roll`, `frame`, `subject` and `notes`
#: describe one shot, and a stale value there would file today's scan under
#: yesterday's name.
REMEMBERED_FILM = ("stock", "process", "tags")

#: How many results keep a full-size working copy. Older ones are shrunk rather
#: than dropped, so every channel and the invert toggle keep working on them.
WORKING_COPIES = 12

#: The filmstrip's mark on a pass the library does not hold -- its raw bytes
#: are nowhere the library can reach -- and on one whose delivered copy was
#: not written, which the library can make again. Two colours because the
#: two want different urgency, and he judges by eye. Not amber: the strip's
#: amber is the selected frame and a missed position.
FILING_FAILED = "#d0342c"
COPY_UNWRITTEN = "#4a90d9"
ARCHIVE_MAX_SIDE = 512

#: The transport aperture across the film, from the full scan frame.
APERTURE_MM = (FULL_FRAME[2] - FULL_FRAME[0] + 1) * MM_PER_INCH / COORD_PER_INCH

#: The smallest and largest a single SLIDE command delivers. Taken from the
#: session rather than copied, because a window offering a move the session
#: then refuses reads to the operator as the button being broken -- and a
#: rounded copy is how the two drift. `FINE_MIN_MM` is param 1, the finest
#: move that exists: `param 0` was measured on 2026-09-22 and does nothing.
FINE_STEP_MM = FINE_MIN_MM
MAX_FINE_MM = FINE_MAX_MM
#: How finely `step_offset` looks for the next reachable position. A quarter
#: of the lattice's own spacing, so it cannot step over one.
FINEST_PROBE_MM = 0.026
#: The planner's own chain limit, matched so the two agree.
MAX_FINE_STEPS = 8

#: How far one fine adjustment may travel: **exactly one command**.
#:
#: It used to be eight chained commands, because one reached only 1.01 mm and
#: the worst real mis-framing seen needed more -- CyberView lost 6 mm on one
#: frame of its own strip. Raising `MAX_CORRECTION_PARAM` to 87 put that whole
#: range inside a single command, so the chain is no longer the way to reach
#: it, and chaining is strictly worse: every command pays the ramp again and
#: scatters again, and the scatter does not shrink with the size of the move.
#:
#: It also bounds the tool. One command reaches about a quarter of the
#: aperture, which is the right size for a fine adjustment; letting a click in
#: the middle of the picture ask for half the aperture would be doing badly and
#: slowly what the slide buttons do properly.
MAX_TRAVEL_MM = MAX_FINE_MM

THUMB_H = 76
POLL_MS = 120
#: How soon after a decision on the contact sheet it is filed in the settings:
#: one write for a burst of clicks, and at most this much lost to a crash.
SHEET_KEEP_MS = 1500
#: How long to wait before opening a roll named on the command line. The window
#: is built inside `__init__`, which runs before `mainloop`, so the root is not
#: mapped yet: `open_roll` ends in a dialog whose parent would be an unmapped
#: window, and the sheet is a Toplevel sized against a geometry Tk has not
#: applied. Behind the 120 ms sash restore, so the panes are placed first.
OPEN_ROLL_MS = 250
#: How soon after the roll box changes the Roll panel's line says again what
#: the next walk is: once for a burst of typing, not a roll's index read per
#: keystroke.
OFFER_MS = 300

#: The body size this window's type was drawn against. Tk reports 13 for
#: TkDefaultFont on macOS (.AppleSystemUIFont); Windows reports 9 (Segoe UI)
#: and Linux around 10. Every size in this file was chosen *relative* to 13 --
#: 9 is a caption, 11 and 12 are small, 13 bold is a heading -- but written as
#: absolute points they did not merely shift off macOS, they inverted: 11 and
#: 12 are smaller than body text on a Mac and larger than it on Windows, and
#: the grey 9pt captions collapsed into body size. So `_font` is given the size
#: the design used and works out what that means here.
_DESIGNED_BODY = 13

#: Below this a size stops being quieter and starts being unreadable. Windows'
#: 9pt body would otherwise put a caption at 6.
_MIN_POINTS = 8

#: What the pixel measurements below were drawn against: 96 dpi, which is what
#: Windows and X11 report at 100%.
_DESIGNED_DPI = 96.0


def _scale() -> float:
    """Pixels per designed pixel, on whatever display this window is on.

    Never less than 1. Tk on Aqua reports 72 dpi whatever the screen is really
    doing, because macOS scales the backing store itself -- so without the
    floor every fixed size would come out a quarter smaller on the machine
    they were all chosen on.
    """
    root = getattr(tk, "_default_root", None)
    if root is None:
        return 1.0
    try:
        return max(1.0, float(root.winfo_fpixels("1i")) / _DESIGNED_DPI)
    except tk.TclError:
        return 1.0


def _px(n: int) -> int:
    """`n` pixels, as drawn at 96 dpi, in this display's pixels."""
    return int(round(n * _scale()))


def _geometry(width: int, height: int) -> str:
    """A `geometry()` string for a window drawn at that size at 96 dpi."""
    return f"{_px(width)}x{_px(height)}"


def _fits(root: tk.Misc, geometry: str | None) -> str | None:
    """`geometry` if this screen can still show it, else None.

    What gets remembered is pixels, and pixels stop meaning the same thing:
    the settings file can be carried to another machine, a monitor can be
    unplugged, and the scaling can change under a window that was sized before
    it. Any of those can restore a window mostly or entirely off-screen, which
    on Windows leaves no way to drag it back.

    Size only, not position -- the window manager places it, and second-
    guessing that is how a window ends up somewhere no one asked for.
    """
    if not geometry:
        return None
    try:
        size = geometry.split("+")[0].split("-")[0]
        width, height = (int(n) for n in size.split("x"))
        screen_w = int(root.winfo_screenwidth())
        screen_h = int(root.winfo_screenheight())
    except (ValueError, tk.TclError):
        return None
    if 0 < width <= screen_w and 0 < height <= screen_h:
        return geometry
    return f"{min(width, screen_w)}x{min(height, screen_h)}"


def _font(designed: int = _DESIGNED_BODY, bold: bool = False,
          fixed: bool = False) -> tuple:
    """The font a size chosen against macOS's 13pt body becomes here.

    `_font(13, bold=True)` is "body, emphasised" everywhere, rather than
    "thirteen points" -- which is a heading on a Mac and oversized on Windows.
    """
    name = "TkFixedFont" if fixed else "TkDefaultFont"
    try:
        base = int(tkfont.nametofont(name).cget("size"))
    except Exception:                                    # no root yet
        base = _DESIGNED_BODY
    # A negative size is pixels rather than points; the ratio is the same.
    base = abs(base) or _DESIGNED_BODY
    size = max(_MIN_POINTS, int(round(base * designed / _DESIGNED_BODY)))
    return (name, size, "bold") if bold else (name, size)

#: The three ways a context menu gets asked for, bound together everywhere one
#: is offered. X11 and a two-button mouse send Button-3, a Mac trackpad's
#: two-finger tap arrives as Button-2, and Control-click is the Mac convention
#: for people without either.
MENU_EVENTS = ("<Button-3>", "<Button-2>", "<Control-Button-1>")

#: How often the picture may be redrawn, in milliseconds. One frame at 60 Hz.
_FRAME_MS = 16

#: While a gesture is running the picture is drawn at a third of the size and
#: enlarged by Tk, which is a ninth of the pixels to sample and render. It is
#: visibly coarser, and lasts only as long as the hand is moving -- a sharp
#: frame follows the moment it stops.
#:
#: Three rather than two because the measurement said so: a moving frame of a
#: 3600 dpi scan costs 16-23 ms at two and 14-20 at three, and past three the
#: blocks are large enough to see while the gain keeps shrinking.
_GESTURE_FACTOR = 3
#: How long after the last gesture event the full-quality frame is drawn.
_SETTLE_MS = 130

LIGHT = {"idle": "#5a5a5a", "busy": "#3fb950", "broken": "#f05050"}
#: The frame-edge reader's light, beside the scanner's: blue while it reads,
#: green when every frame has its final reading, red when the detector failed
#: on one. Grey with nothing to read, or on film it does not read.
EDGE_LIGHT = {frame_edges.IDLE: "#5a5a5a", frame_edges.READING: "#3d7bd9",
              frame_edges.DONE: "#3fb950", frame_edges.FAILED: "#f05050",
              frame_edges.SKIPPED: "#5a5a5a"}


class ScannerGui:
    def __init__(self, root: tk.Tk, session: ScanSession, demo: bool = False,
                 settings_path=None, look_only: bool = False,
                 open_roll=None, logs=None, notes=()):
        self.root = root
        self.session = session
        self.demo = demo
        #: Where the window writes what it says as it is ended -- a Ctrl-C,
        #: SIGTERM or its console closing, with settings it could not save
        #: -- beside the console's own log (`main`). Beside the settings
        #: file where nothing says: a test's own folder, never the checkout.
        self._logs = (Path(logs) if logs is not None
                      else settings.path(settings_path).parent / "logs")
        #: Every line the log pane shows, and what it does not -- each job's
        #: start and end, tracebacks, warnings, the last events before the
        #: window went -- in ``pane-<UTC start>.log`` there, one per launch
        #: (`console.KeptLog`). First, so nothing said is missed.
        self._kept = KeptLog(self._logs, "pane")
        #: Whether the scanner's answer to INQUIRY is in that file yet.
        self._scanner_kept = False
        self._keep(self._header(session, demo, settings_path))
        #: How a refusal in another program names this window
        #: (`rps7200.locks`).
        self._who = "the demo window" if demo else "the window"
        #: There is no film in the transport. **Wording only.** The refusal
        #: belongs to the backend -- `DemoScanner(no_film=True)` raises, the
        #: session reports a failed job, and the window says so through the
        #: path it already has. Gating the controls here instead meant the
        #: sheet-to-roll spine never ran: `on_scan_chosen` writes
        #: `approved.json` and submits the `Roll`, and a demo that cannot
        #: reach them is not demonstrating them.
        self.look_only = look_only
        # First, because the controls and the presets below start from it.
        self._settings_path = settings_path
        self._settings_notes: list[str] = []
        #: Why the settings file could not be read at launch, where the disk
        #: would not let it be: it is not written over until it can be read
        #: (`_remember`). Empty once it has been, or when it was.
        self._settings_unread: list[str] = []
        self.remembered = settings.load(settings_path,
                                        say=self._settings_notes.append,
                                        unread=self._settings_unread)
        self.results: list = []
        self.current = None
        self.busy = False
        #: When this window opened: a cached reference older than this is
        #: another power-on's, as far as the window can tell.
        self._opened_at = time.time()
        #: The output folder outside demo/ the demo has said its copies go
        #: to: said once for each (`_set_outdir`).
        self._demo_out_said: str | None = None
        #: The prescan an aim-click may measure from: the newest one taken of
        #: the film where it still is. None once anything has moved it.
        self._aim_from = None
        #: A calibration handed over whose "calibrated" has not come back.
        self._calibration_pending = False
        #: The open sheet's decisions, booked to be filed; see `_keep_sheet_soon`.
        self._sheet_keep_job = None
        #: Threads writing files (Save all, Export): Quit waits for them.
        self._writing: list[threading.Thread] = []
        #: A Duplicate's Stop, and its dialog, while one is copying.
        self._copy_stop: threading.Event | None = None
        self._copy_dialog: tk.Toplevel | None = None
        #: Threads reading entries -- the full-resolution view -- which the
        #: session's close-time compaction waits for, with the writers above
        #: (`_wait_for_readers`).
        self._reading: list[threading.Thread] = []
        #: The session's ordinal of the job running, from its "state", or
        #: None between jobs; and each job's line as it ended. A filing names
        #: the job that queued it, and lands when it lands -- after its own
        #: job's "finished", during the next one's. See `_filing`.
        self._job_running: int | None = None
        #: The last job started, kept past the "state" that ends it: a
        #: force abort says the session is idle before the worker's own
        #: failure arrives, and that failure was kept with no job number
        #: and no end line (the 2026-10-12 tour).
        self._last_job: int | None = None
        self._job_ended: dict[int, str] = {}
        #: The job running, as its "state" handed it over (`Event.job`), or
        #: None between jobs: which roll's frames are arriving is the job's
        #: to say -- its folder, its approvals -- and never the window's
        #: guess from what it handed over last (`_written_as`).
        self._running_job = None
        #: Each roll the session started, by its ordinal (`RollRun`): what a
        #: filing that lands after its roll has ended -- a roll's last frames
        #: always do -- is weighed against (`_mark_filed`). A summary, never
        #: the job, which holds a commission's prescans.
        self._roll_jobs: dict[int, RollRun] = {}
        #: Each job's passes not filed and copies not written, by outcome;
        #: which of those have had their notice; and each result's marks,
        #: the worst outcome for each part of it (`Filing.part`).
        self._filing_counts: dict[int, dict[str, int]] = {}
        self._filing_noticed: set[tuple[int, str]] = set()
        self._filing_marks: dict[int, dict[str, str]] = {}
        self._filing_window = None
        self.closing = False
        self._photo: tk.PhotoImage | None = None
        self._small: tk.PhotoImage | None = None   # the coarse frame, enlarged
        self._photo_size = None
        self._small_size = None
        self._item = None                    # the one canvas item showing it
        self._item_photo = None
        self.presets: dict = dict(self.remembered.get("presets") or {})
        self._pending: set = set()           # after() jobs still to fire
        self._thumbs: list[tk.PhotoImage] = []
        self._full = None                    # full-resolution pixels, for 1:1
        self._full_seq = None
        self._levels: list = []              # coarser copies, finest last
        self._levels_seq = None
        # What is at 0 and at the rail on the sensor, for the pass `_rail_seq`.
        self._rail = None
        self._rail_seq = None
        self._loading = None
        #: The pass shown while another's full-resolution read was in flight,
        #: read next -- one read at a time; see `_load_full`.
        self._load_next = None
        self._redraw_job = None
        self._settle_job = None
        self._drawn_at = 0.0
        self._alive = True
        self._job = ""                       # what is running, for the stop label
        #: The passes the running job has delivered, and nothing older: what
        #: `_report_held` speaks of when it ends.
        self._job_results: list = []
        # A calibration asked for counts from the moment it is queued: the
        # session runs jobs in order, so a scan pressed next waits behind it.
        # The session's "calibrated" event then says how it actually ended.
        self.calibrated = False
        self._calibrate_prompt = None        # "calibrate first", while open
        self._session_closed = False
        self._last_nudge = 0                 # which way the film last went
        self._zoom = 0.0                     # 0 = fit; otherwise pixels per pixel
        self._drag = None
        self._pointer = (0.0, 0.0)           # where a zoom should pivot
        self._view = [0.0, 0.0]              # the picture point at the corner
        self._reads: queue.Queue = queue.Queue()   # full-resolution reads landing
        self._measured: queue.Queue = queue.Queue()   # histograms landing
        self._histogram_token = 0            # which measurement is still wanted
        self._shown = None                   # what was last drawn, for clicks
        self._scrollers: list = []           # (widget, handler) for the wheel
        self._zoom_travel = 0                # trackpad pixels not yet spent
        self.rotation = 0                    # applied to new passes and files
        self.flip = False                    # and whether they read left to right
        #: What one arrow press moves a frame in the position window. Lives
        #: here rather than on that window because it is a preference about
        #: how the operator works, and that window is opened and closed all
        #: through a roll -- a setting that died with it would be re-chosen
        #: seventeen times.
        self.v_adjuststep = tk.StringVar(value=ADJUST_STEPS[0])
        #: What each key does. `shortcut_overrides` is only what the operator
        #: changed -- see `shortcuts.overrides_from` for why the whole table is
        #: not stored -- and `self.keys` is that laid over the defaults.
        self.shortcut_overrides: dict[str, str] = {}
        self.keys: dict[str, str] = shortcuts.defaults()
        self._bound: list[str] = []          # what is on the root right now
        self._shortcut_editor = None
        #: How each *photograph* is arranged, keyed by what identifies it --
        #: its frame number on a roll, or the transport position it was taken
        #: at. Not per pass and not per window: a prescan and the scan that
        #: replaces it are two passes over one picture, and arranging one of
        #: them is a statement about the picture rather than about the pass.
        #: That is what makes a turn in the contact sheet show up in the
        #: preview behind it, and what makes a scan come back the way its
        #: prescan was left.
        self.orientations: dict[tuple, tuple[int, bool]] = {}
        self.survey: list = []               # the prescans a dry run walked
        self._surveying = False              # a dry run is running right now
        #: The frames a walk that adds to the sheet found already on it, and
        #: which of those it walked again. Empty on a fresh walk. A frame walked
        #: again replaces its old prescan, and a position set on the old one is
        #: dropped when the walk ends: it was measured from that prescan.
        self._kept_walk: set[int] = set()
        self._rewalked: set[int] = set()
        #: Of those, each whose position was dropped as its prescan arrived
        #: (`_drop_position`), for the walk's end to name: gone from the
        #: sheet's state by then, it was named by nothing.
        self._dropped: set[int] = set()
        #: The folder whose walk could not be read as its sheet's decisions
        #: were filed (`UNKNOWN`), once that has been said.
        self._unfiled_said: str | None = None
        #: The prescan resolution the survey walked at. A commissioned scan is
        #: held to it when approved positions are in play: checking a frame
        #: against a reference from another resolution works geometrically but
        #: costs about half the correlation confidence -- 93.5 against 47.4 on
        #: real passes -- enough to drop a good match below the floor and
        #: report a frame as unverified for no reason.
        self._survey_predpi = None
        #: The film the survey was walked on, which is what its edges are read
        #: as: the detector reads negatives only, and on colour and B&W
        #: differently. None until a walk or a reopened roll says.
        self._survey_film: str | None = None
        #: The transport's own counter as it last reported it -- 0-based, so
        #: frame N of the strip is N-1 -- or None before it has said anything.
        #: Only ever what it last *said*: the scanner's keys move the film
        #: without a word over USB, which is why a roll asks again itself
        #: before it moves anything, and this only feeds the sentence the
        #: confirm dialog shows.
        self._transport = None
        self.sheet = None                    # the contact sheet, while it is open
        #: What the contact sheet was last left holding -- ticks, offsets,
        #: rotations, flips. The sheet is a Toplevel that is destroyed when it
        #: closes, so without this the decisions die with the window: closing
        #: it and pressing "Contact sheet ..." again rebuilt it from the survey
        #: and every position set by hand was gone. Kept on the window rather
        #: than in the sheet because the point is to outlive the sheet.
        self.sheet_state: dict = {}
        #: Frames this roll has already scanned, from its manifest. A fact
        #: rather than a decision, so it is not stored with the state above --
        #: but the sheet needs it to grey them out, and reopening the sheet
        #: from the button has no manifest to hand.
        self._sheet_done: set[int] = set()
        #: And how that roll held each of them, as the manifest said when the
        #: roll was opened -- the hold's own record, by frame number. The
        #: sheet marks the frames it left off their positions, from this under
        #: what the roll.json says now (`_holds_for_sheet`).
        self._sheet_holds: dict[int, dict] = {}
        #: Which roll the sheet's decisions belong to. Set both by opening a
        #: roll and by finishing a walk -- a strip walked in this session has
        #: a folder too, and until the roll is commissioned its manifest holds
        #: no positions, so that folder is the only thing the decisions can be
        #: filed against. Kept apart from `_loaded_roll`, which answers the
        #: different question of which roll the browser should mark as open.
        self._sheet_roll = None
        #: The walk `_sheet_roll` held when the sheet was opened on it, by
        #: its id, with the folder it is of (`_sheet_walk`): what the sheet's
        #: decisions are filed under, and what a commission checks the folder
        #: still holds. Kept rather than read again each time, so a walk
        #: written over since is seen as another walk, not taken for this one.
        self._walk_seen: tuple[str, str] | None = None
        #: The roll name this window last put in the roll box itself -- a
        #: new roll's generated name, a reopened roll's folder. A fresh walk
        #: with that still in the box is a new strip and gets a new name; only
        #: a name he typed sends a fresh walk into a folder that exists.
        self._roll_named: str | None = None
        #: The roll folder that name is of: a roll shown from outside this
        #: window's rolls names nothing here (`_typed_roll_name`, P18-d).
        self._roll_named_folder: Path | None = None
        #: While a walk of a new strip after a roll's first runs, the strip
        #: before it, whose first frame the walk's is compared with as it
        #: arrives (`_check_same_strip`); None otherwise.
        self._same_strip_from: Path | None = None
        #: The Roll panel's line saying what the next walk is, booked to be
        #: worked out again shortly after the roll box changes.
        self._offer_job = None
        self.browser = None                  # the rolls list, while it is open
        self._saving = False                 # a batch save is on a thread
        self._loaded_roll = None             # which roll folder is open, if any
        #: Each roll opened from outside `session.rolls`, and the folder here
        #: it is scanned into; see `_roll_folder`.
        self._carried: dict[str, Path] = {}
        #: What each control was built holding, taken between `_build` and
        #: `_restore`. The panels' headers and every reset are measured against
        #: it. See `_take_defaults`.
        self.defaults: dict = {}
        self._panel_marks: dict = {}         # title -> (count label, reset button)
        self._panel_boxes: dict = {}         # title -> the LabelFrame
        self._panel_job = None               # a pending recount
        self._saves: queue.Queue = queue.Queue()
        #: Reads the walk's frame edges on its own thread as the prescans
        #: arrive -- or all at once for a walk opened from disk -- so the
        #: contact sheet opens on answers rather than starting the detector.
        #: `_pump` takes each newer answer to the light and the sheet.
        self.edge_watch = frame_edges.EdgeWatch()
        self._edge_seen = -1                 # the watch's version last shown
        self._edge_said: tuple | None = None  # (generation, state) logged

        # -- the two live time estimates -----------------------------------
        # One pass, interpolated from its own line count -- reset whenever the
        # total changes (a new pass) or done goes backwards (defensive).
        self._pass_started_at: float | None = None
        self._pass_total_seen = 0
        self._pass_done_seen = 0
        # The whole roll -- rough from estimate_seconds() until the first
        # frame lands, then recomputed from the roll's own measured pace.
        # `_roll_wall_start` being None is what says no roll is running.
        self._roll_wall_start: float | None = None
        self._roll_dry = False
        self._roll_frames_total: int | None = None
        self._roll_frames_done = 0
        self._roll_seconds_per_frame = 0.0
        # True from a roll's submission until its seek has landed: the roll
        # moves the film to its first frame before scanning anything, and the
        # pace is measured from there. The session says where the seek put
        # the film with the roll's first "transport" event.
        self._roll_seeking = False

        root.title("Reflecta RPS 7200" + ("  --  demo" if demo else ""))
        root.geometry(_fits(root, self.remembered["window"].get("geometry"))
                      or _geometry(1280, 860))
        root.minsize(_px(900), _px(600))
        # A callback that raises is kept and said, not printed to a stderr
        # nobody reads (`_tk_error`).
        root.report_callback_exception = self._tk_error
        self._build()
        # Between the two, deliberately: this is the one moment the window holds
        # its shipped defaults and nothing a settings file has said.
        self._take_defaults()
        self._bind_control_menus()
        self._watch_controls()
        self._restore()
        self._refresh_panels()
        self._keep(self._setup_said())
        # Heard before there was a log to say it in: the settings file's,
        # and ``notes`` from `main` -- a lock `hold_window` could not take --
        # and the kept log's own trouble, if it had any by then.
        for note in self.__dict__.pop("_said_later", []):
            self._pane(note)
        for note in (*self._settings_notes, *notes):
            self._say(note)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        # Named as this window to every other program, and its compaction at
        # close made to wait for this window's own readers and writers.
        self.session.who = self._who
        self.session.before_compact = self._wait_for_readers
        # One binding at the root, dispatched by what the pointer is actually
        # over. Binding per widget did not work: the panel's children sit on
        # top of the canvas, so <Enter> fired for them and never for it.
        for sequence in ("<MouseWheel>", "<Shift-MouseWheel>",
                         "<Button-4>", "<Button-5>"):
            root.bind_all(sequence, self._on_wheel, add="+")
        _bind_optional(root, "<TouchpadScroll>", self._on_touchpad,
                       everywhere=True)
        self.session.start()
        self._bind_shortcuts()
        self._later(POLL_MS, self._pump)
        self._look_for_leftovers()
        # The Roll panel's line, from now and as the roll box changes.
        self.fields["roll"].trace_add("write", lambda *_: self._offer_strip())
        self._offer_strip_now()
        if open_roll is not None:
            # Deferred through `_later` rather than called here, and rather
            # than `root.after`: `_later` registers in `self._pending`, so
            # closing the window inside the delay cancels it instead of firing
            # into a destroyed widget.
            self._later(OPEN_ROLL_MS, lambda: self.open_roll(open_roll))

    def _look_for_leftovers(self) -> None:
        """Say in one line what earlier runs left behind, as the window opens
        (`library.leftovers`): a spool with passes nobody filed, a pass kept
        outside the library, a Duplicate cut short. Nothing reported them,
        and a pass not in the library was found only by whoever reread the
        log line that named it.

        On a thread of its own, because it looks at every entry -- whether
        each holds `raw.bin`, and no more -- and a library on a network
        share must not hold up the window's first frame. Its line goes to
        the log through the pump (`_saves`), never from the thread. It says
        nothing where nothing is left, and files and compacts nothing:
        those gzip, which is for a closed scanner.
        """
        root, rolls = self.session.root, self.session.rolls
        also = (Path(self.session.reference).parent,
                settings.path(self._settings_path).parent)

        def look() -> None:
            if root is None:
                return
            try:
                said = library.leftovers_said(library.leftovers(
                    root, rolls=rolls, quick=True, also=also))
            except Exception as exc:                      # noqa: BLE001
                said = (f"could not look for what earlier runs left behind "
                        f"({type(exc).__name__}: {exc})")
            if said:
                self._saves.put(("line", said))
            # And what the trashes hold: a Delete no longer frees the disk,
            # and nothing here empties them (`library.trash_said`).
            try:
                said = library.trash_said(root, rolls)
            except Exception as exc:                      # noqa: BLE001
                said = (f"could not look in the trash "
                        f"({type(exc).__name__}: {exc})")
            if said:
                self._saves.put(("line", said))

        threading.Thread(target=look, daemon=True, name="leftovers").start()

    # -- the rolls open here -------------------------------------------------
    #
    # `_loaded_roll` and `_sheet_roll` name the roll folders this window
    # shows. Whatever one names is held shared against another program's
    # Rename or Delete (`locks.ROLL_VIEW`) -- a demo window showing a real
    # walk, which the real window could otherwise rename from under it --
    # and let go when it names another or none.
    #
    # Whichever of the two was set last to a folder is the one in front
    # (`_roll_focus`), which a plain Roll finishes (`_current_strip`): a
    # strip opened from Rolls ... with no walk sets only `_loaded_roll`,
    # and the sheet still named the strip opened before it.

    @property
    def _loaded_roll(self):
        return self.__dict__.get("_loaded_folder")

    @_loaded_roll.setter
    def _loaded_roll(self, folder) -> None:
        self.__dict__["_loaded_folder"] = folder
        if folder is not None:
            self.__dict__["_roll_focus"] = Path(folder)
        self._view_rolls()

    @property
    def _sheet_roll(self):
        return self.__dict__.get("_sheet_folder")

    @_sheet_roll.setter
    def _sheet_roll(self, folder) -> None:
        self.__dict__["_sheet_folder"] = folder
        if folder is not None:
            self.__dict__["_roll_focus"] = Path(folder)
        self._view_rolls()

    def _view_rolls(self) -> None:
        """Hold the view lock on each roll folder shown here, and no other."""
        views: dict = self.__dict__.setdefault("_views", {})
        shown = {}
        for folder in (self.__dict__.get("_loaded_folder"),
                       self.__dict__.get("_sheet_folder")):
            if folder is not None:
                # A strip by its roll's folder (`library.lock_folder`): a
                # Rename or Delete of the roll takes every strip with it.
                held = library.lock_folder(folder)
                shown.setdefault(locks.key(held), held)
        for gone in [key for key in views if key not in shown]:
            views.pop(gone).release()
        for key, folder in shown.items():
            if key in views:
                continue
            try:
                held = locks.take(locks.ROLL_VIEW, folder, exclusive=False,
                                  what=getattr(self, "_who", "the window"))
            except locks.Refused as exc:
                # Shown all the same: what is on screen was read already.
                self._say(f"{Path(folder).name} is being renamed or deleted "
                          f"by {exc.who}; what is shown here was read before")
                continue
            if held.note:
                self._say(held.note)
            views[key] = held

    # -- layout ------------------------------------------------------------

    def _build(self) -> None:
        head = ttk.Frame(self.root, padding=(10, 6))
        head.pack(fill="x")
        ttk.Button(head, text="About the scanner",
                   command=self.on_about).pack(side="left")
        ttk.Button(head, text="Shortcuts ...",
                   command=self.on_shortcuts).pack(side="left", padx=6)
        ttk.Button(head, text="Restore settings ...",
                   command=self.on_restore_settings).pack(side="left")
        self.v_state = tk.StringVar(value="opening ...")
        ttk.Label(head, textvariable=self.v_state).pack(side="right")
        self.light = tk.Canvas(head, width=_px(14), height=_px(14), highlightthickness=0)
        self.light.pack(side="right", padx=(0, 8))
        self._bulb = self.light.create_oval(2, 2, 12, 12, fill=LIGHT["idle"],
                                            outline="")
        # The frame-edge reader, which works on its own thread whatever the
        # scanner is doing: how many of the walk's frames it has read.
        self.v_edges = tk.StringVar(value="frame edges")
        ttk.Label(head, textvariable=self.v_edges).pack(side="right", padx=(0, 24))
        self.edge_light = tk.Canvas(head, width=_px(14), height=_px(14),
                                    highlightthickness=0)
        self.edge_light.pack(side="right", padx=(0, 8))
        self._edge_bulb = self.edge_light.create_oval(
            2, 2, 12, 12, fill=EDGE_LIGHT[frame_edges.IDLE], outline="")
        # Where a roll has got to, in the middle where it is seen from across
        # the room: "frame 12 of 36". Empty when no roll is running.
        self.v_frame_status = tk.StringVar()
        ttk.Label(head, textvariable=self.v_frame_status,
                  font=_font(13, bold=True)).place(relx=0.5, rely=0.5,
                                                   anchor="center")
        ttk.Separator(self.root).pack(fill="x")

        # Every divider is a sash, so the widths and heights are the
        # operator's to set rather than mine to guess.
        outer = ttk.PanedWindow(self.root, orient="horizontal")
        outer.pack(fill="both", expand=True)
        left = self._scrollable(outer)
        right = ttk.PanedWindow(outer, orient="vertical")
        outer.add(right, weight=4)
        self._outer, self._right = outer, right

        self._build_scan(left)
        self._build_transport(left)
        self._build_roll(left)
        self._build_film(left)
        self._build_output(left)
        self._build_preview(right)

        # Last, so every control in the column already exists and gets its own
        # binding rather than relying on the event finding its way up.
        canvas, inner = self._column
        # `inner` alone: it is a child of the canvas and holds every control,
        # so binding both put two handlers on each widget and scrolled twice
        # per event.
        self._scrolls(
            inner,
            lambda n, _side: canvas.yview_scroll(n, "units"),
            precise=lambda dx, dy: _scroll_pixels(canvas, 0, dy),
        )
        self._scrolls(
            canvas,
            lambda n, _side: canvas.yview_scroll(n, "units"),
            precise=lambda dx, dy: _scroll_pixels(canvas, 0, dy),
            deep=False,
        )

    def _restore(self) -> None:
        """Put back what was set last time, control by control.

        Each is applied on its own and a bad value is skipped rather than
        stopping the rest: a settings file edited by hand, or written by an
        older version, should cost the one control it got wrong.
        """
        for key, value in self.remembered["controls"].items():
            if key not in REMEMBERED:
                continue
            variable = getattr(self, f"v_{key}", None)
            if variable is None:
                continue
            try:
                variable.set(value)
            except tk.TclError:
                pass
        stored = self.remembered.get("shortcuts")
        reverted: list[str] = []
        self.keys = shortcuts.resolve(stored if isinstance(stored, dict) else None,
                                      reverted)
        for action_id in reverted:
            self._say(f"shortcut {stored.get(action_id)!r} for {action_id} is "
                      "already another action's key here; using its default "
                      f"{self.keys[action_id] or 'none'!r}")
        self.shortcut_overrides = shortcuts.overrides_from(self.keys)
        for key, value in self.remembered["film"].items():
            if key in REMEMBERED_FILM and key in self.fields:
                self.fields[key].set(str(value))
        # An explicit --out wins: it was typed for this run.
        if self.remembered["output"] and self.session.out_dir is None:
            self._set_outdir(str(self.remembered["output"]))
            # Kept as it was set, and said: nothing makes it again, and every
            # job that writes a copy is refused until it is back, chosen
            # again or cleared -- by the session, the demo's included, never
            # by a greyed button. Made again on C: as a plain folder, the
            # iCloud folder it is would have been filled behind iCloud's back.
            try:
                storage.require_dir(self.remembered["output"])
            except OSError as exc:
                self._say(f"{exc} -- scans, prescans and rolls are refused "
                          "until it is")
        elif self.session.out_dir is not None:
            self.v_outdir.set(str(self.session.out_dir))
        # The loop above set the variables; this is what carries them into the
        # session, which is what actually writes the files.
        self._sync_format()
        self._sync_exposure()
        self._sync_film()
        self._show_estimate()
        self._refresh_presets()
        # Sashes only once the panes have a size to divide, or the positions
        # are clamped to a window that has not been laid out yet.
        self._later(120, self._restore_sashes)

    def _restore_sashes(self) -> None:
        for name, pane in (("outer", self._outer), ("right", self._right)):
            wanted = self.remembered["window"].get(name) or []
            for index, position in enumerate(wanted):
                try:
                    pane.sashpos(index, int(position))
                except (tk.TclError, ValueError, TypeError):
                    pass

    def _read_settings_again(self) -> bool:
        """Read the settings file the launch could not, and keep what it
        holds under what the window holds; False while it still cannot be.

        Stored as the base, the window's own laid over it key by key: its
        presets, shortcuts, rolls and sheet decisions. The controls, the
        film panel, the output and the geometry are what the window shows
        now, and are written as they are. A preset deleted here since
        launch comes back from the file: the price of keeping every other.
        """
        again: list[str] = []
        heard: list[str] = []
        stored = settings.load(self._settings_path, say=heard.append,
                               unread=again)
        if again:
            # Not said: the launch said it, and `_remember` says once that
            # nothing was written. Handed `_say`, `load` said it again on
            # every save -- a sheet decision, a preset, a roll opened -- each
            # time adding that the window had opened with its defaults.
            return False
        for name, mine in (("presets", self.presets),
                           ("shortcuts", self.shortcut_overrides),
                           ("rolls", self.remembered.setdefault("rolls", {})),
                           ("sheet", self.remembered.setdefault("sheet", {}))):
            theirs = stored.get(name)
            if isinstance(theirs, dict) and isinstance(mine, dict):
                for key, value in theirs.items():
                    mine.setdefault(key, value)
        if not self.remembered.get("last_roll"):
            self.remembered["last_roll"] = stored.get("last_roll") or ""
        self._settings_unread.clear()
        if heard:
            # Read now, and nonsense: moved aside, and said -- the window's
            # own are written in its place, as over any corrupt file.
            for line in heard:
                self._say(line)
        else:
            self._say("the settings file can be read again: what it held is "
                      "kept, with what was changed here laid over it")
        try:
            self._refresh_presets()
        except Exception:                                 # noqa: BLE001
            pass
        return True

    def _remember(self) -> tuple[bool, str]:
        """Gather the setup and write it. Never allowed to stop the window.

        Returns whether it reached the disk and, where it did not, why:
        Quit asks before it goes on without it (`on_close`). It used to be
        said only in the log pane, which the window closing a moment later
        took with it.

        Nothing is written over a settings file the disk would not let be
        read at launch (`settings.load`'s ``unread``) until it can be: the
        window opened on defaults, and written whole from those, the file
        lost every preset, shortcut and other walk's sheet decisions it
        held. Read now, what it holds is laid under what the window holds
        (`_read_settings_again`).
        """
        if self._settings_unread and not self._read_settings_again():
            said = ("the settings file could not be read at launch and "
                    "still cannot, so it was not written over")
            if not self.__dict__.get("_unread_said"):
                self._unread_said = True
                self._say(f"could not save the window's settings ({said}) "
                          "-- what was changed here is kept only while the "
                          "window is open")
            return False, said
        why: list[str] = []
        try:
            controls = {key: getattr(self, f"v_{key}").get()
                        for key in REMEMBERED if hasattr(self, f"v_{key}")}
            film = {key: self.fields[key].get()
                    for key in REMEMBERED_FILM if key in self.fields}
            window = {"geometry": self.root.winfo_geometry()}
            for name, pane in (("outer", self._outer), ("right", self._right)):
                window[name] = _sash_positions(pane)
            saved = settings.save({
                "controls": controls,
                "film": film,
                "output": self.v_outdir.get(),
                "window": window,
                "presets": self.presets,
                "shortcuts": self.shortcut_overrides,
                # Carried through rather than rebuilt: `_note_roll_opened` is
                # the only writer and it writes into `self.remembered`. Leaving
                # this key out did not merely fail to save it -- it *erased* it,
                # because the payload is written whole, so the rolls table's
                # "Last opened" column never survived a launch.
                "rolls": self.remembered.get("rolls") or {},
                # Carried through for the same reason as `rolls` above, and it
                # is the same trap: this payload is written whole, so a key
                # left out is not merely unsaved, it is erased.
                "sheet": self.remembered.get("sheet") or {},
                # The roll of strips Continue offers after a restart
                # (`_note_strip_roll`), carried the same way.
                "last_roll": self.remembered.get("last_roll") or "",
            }, self._settings_path, why=why)
            if saved is None:
                # Said, not swallowed: a sheet's decisions made before
                # commissioning live only here until then.
                said = why[0] if why else "the disk would not take it"
                self._say(f"could not save the window's settings ({said}) "
                          "-- the contact sheet's decisions and the setup "
                          "were not kept")
                return False, said
        except Exception as exc:                         # noqa: BLE001
            self._say(f"could not save the settings: {exc}")
            return False, f"{type(exc).__name__}: {exc}"
        return True, ""

    # -- the keyboard ------------------------------------------------------

    def _actions(self) -> dict:
        """What each action id does, for the main window.

        A table rather than a method per key, because the editor has to be able
        to say what every id is and the tests have to be able to check that
        none of them reaches the scanner. The two extra windows keep their own;
        see `_ContactSheet._actions` and `_FrameAdjuster._actions`.

        Viewing and arranging, bar three. Prescan, Scan and Roll start a pass,
        and each asks first (`_confirm_then`, and `on_roll`'s own question).
        Nothing moves film or calibrates -- `shortcuts.NEVER_BOUND` names those
        and a test holds the line. `stop` is here because `request_stop`
        finishes the pass already running rather than abandoning a read.
        """
        return {
            "previous_pass": lambda: self._walk(-1),
            "next_pass": lambda: self._walk(1),
            "first_pass": lambda: self._jump(0),
            "last_pass": lambda: self._jump(-1),
            "rotate_right": lambda: self._on_current(self.on_rotate, 90),
            "rotate_left": lambda: self._on_current(self.on_rotate, 270),
            "rotate_180": lambda: self._on_current(self.on_rotate, 180),
            "straighten": self._straighten,
            "flip": lambda: self._on_current(self.on_flip),
            "save_as": lambda: self._on_current(self.on_save_as),
            "show_prescan": lambda: self._on_current(self.on_show_prescan),
            "delete_pass": lambda: self._on_current(self.on_delete),
            "zoom_in": lambda: self._zoom_by(2.0),
            "zoom_out": lambda: self._zoom_by(0.5),
            "zoom_fit": lambda: self._set_zoom(0.0),
            "zoom_actual": lambda: self._set_zoom(self._finest()),
            "invert": self._toggle_invert,
            # The only keys that reach the hardware, and the only ones that ask
            # first. `roll` is not wrapped because `on_roll` already asks its
            # own question and naming the cost twice would train the habit of
            # dismissing both. See `rps7200/shortcuts.py`.
            "prescan": lambda: self._confirm_then(
                "a prescan", self._prescan_cost, self.on_prescan),
            "scan": lambda: self._confirm_then(
                "a scan of this frame", self._scan_cost, self.on_scan),
            "roll": self.on_roll,
            "save_all": self.on_save_all,
            "channel_next": lambda: self._cycle_channel(1),
            "channel_previous": lambda: self._cycle_channel(-1),
            "contact_sheet": self.on_contact_sheet,
            # Only when there is something to stop. The worker clears the flag
            # as each job starts, so a stray press cannot reach the next job --
            # but the log is
            # evidence, and "finishing what is already running" with nothing
            # running is a line that will be read back one day and believed.
            # Asked of the session, which knows a job handed over this very
            # tick: `busy` alone follows its report, read a tick later, and an
            # Escape pressed in between was dropped.
            "stop": lambda: (self.on_stop() if self.busy
                             or self.session.outstanding() else None),
            "shortcuts": self.on_shortcuts,
        }

    def _on_current(self, method, *args) -> None:
        """Run a menu action against whatever is on screen, or do nothing."""
        if self.current is not None:
            method(self.current, *args)

    def _straighten(self) -> None:
        if self.current is not None and self.current.rotation:
            self.on_rotate(self.current, -self.current.rotation)

    def _walk(self, by: int) -> None:
        """The pass before or after this one in the filmstrip."""
        shown = self._visible()
        if not shown:
            return
        try:
            at = shown.index(self.current)
        except ValueError:
            at = 0 if by > 0 else len(shown) - 1
            self._show_seq(shown[at].seq)
            return
        self._show_seq(shown[max(0, min(len(shown) - 1, at + by))].seq)

    def _jump(self, at: int) -> None:
        shown = self._visible()
        if shown:
            self._show_seq(shown[at].seq)

    def _toggle_invert(self) -> None:
        self.v_invert.set(not self.v_invert.get())
        self._redraw_all()

    def _cycle_channel(self, by: int) -> None:
        """The next view this pass can actually show.

        Only what `channels_available` allows: a prescan is RGB 8-bit and has
        no infrared, and stepping onto a view that does not exist would render
        a black rectangle and leave the operator wondering whether the IR
        really came back empty.
        """
        if self.current is None or self.current.image is None:
            return
        offered = [c for c in preview.CHANNELS
                   if c in preview.channels_available(self.current.image)]
        if not offered:
            return
        here = offered.index(self.v_channel.get()) if self.v_channel.get() in offered else 0
        self.v_channel.set(offered[(here + by) % len(offered)])
        self._on_view_chosen()

    def _on_view_chosen(self) -> None:
        """The view switch, pressed or stepped through by key."""
        self._remember_view()
        self._schedule_redraw()

    def _remember_view(self) -> None:
        """A black-and-white picture is exported as it is being looked at.

        The view picked on it -- MONO for the mean of R, G and B, R, G or B
        for that plane alone, RGB for all three -- is kept on the picture as
        its ``export_mix``, and Save As, Save all and a roll's Export write
        that (`_mono_for`). Stefan's choice, 2026-10-04: view = export, so a
        scan can be delivered another way after it was taken.

        Black and white only. A colour picture reduced to one plane is a
        different photograph, and IR is the dust plane, not a way to deliver
        one; looking at either leaves the export as it was.
        """
        r = self.current
        if r is None or (getattr(r, "meta", None) or {}).get("film") != FILM_BW:
            return
        view = self.v_channel.get()
        if view == "MONO":
            r.export_mix = (True, MONO_AVERAGE)
        elif view in ("R", "G", "B"):
            r.export_mix = (True, view)
        elif view == "RGB":
            r.export_mix = (False, MONO_CHANNEL)

    @staticmethod
    def _view_of(mix: tuple[bool, str] | None) -> str | None:
        """The view switch's name for an ``export_mix``."""
        if mix is None:
            return None
        mono, channel = mix
        if not mono:
            return "RGB"
        return "MONO" if channel == MONO_AVERAGE else channel

    def _typing(self) -> bool:
        """Whether a key belongs to a text field rather than to the window.

        Without this, typing "rotate" into the subject field turns the picture
        four times and deletes a pass on the "e".
        """
        try:
            widget = self.root.focus_get()
        except (tk.TclError, KeyError):
            return False
        return isinstance(widget, (tk.Entry, ttk.Entry, tk.Text,
                                   tk.Spinbox, ttk.Spinbox, ttk.Combobox))

    def _confirm_then(self, what: str, cost, run) -> None:
        """Ask before a key starts the scanner, then start it.

        The whole reason these actions may have keys at all. `on_prescan` and
        `on_scan` submit immediately with nothing asked -- which is right for a
        button, because reaching for one is the decision, and wrong for a key,
        because a slip is not. So the key asks and the button does not, and both
        end in the same method.

        `cost` is called rather than passed as text: the estimate depends on the
        controls as they are *now*, and a string built when the table was made
        would be describing whatever the window held at start-up.

        `_working`, not `busy`: `busy` is read up to a tick after a job is
        taken, so a key pressed twice inside it asked twice and queued a
        second pass behind the first.
        """
        if self._working():
            self._say(f"the scanner is working -- that key starts {what} once "
                      "it has finished")
            return
        if self._calibration_missing():
            return
        if messagebox.askokcancel(
            what[0].upper() + what[1:],
            f"Start {what} now?\n\n{cost()}\n\nThe key asks; the button "
            "does not.", parent=self.root,
        ):
            run()

    def _prescan_cost(self) -> str:
        dpi = self._prescan_dpi()
        if dpi is None:
            return "The prescan resolution is not a number."
        return (f"A framing pass over the whole transport at {dpi} dpi, "
                f"about {_duration(estimate_seconds(dpi, False))}.")

    def _scan_cost(self) -> str:
        dpi = self._dpi()
        if dpi is None:
            return "The resolution is not a number."
        per = estimate_seconds(dpi, self.v_ir.get(), self.v_fast_ir.get())
        return (f"One frame at {dpi} dpi"
                + (" with infrared" if self.v_ir.get() else "")
                + f", about {_duration(per)}.")

    def _bind_shortcuts(self) -> None:
        """Put the current keys on the window, and take the old ones off.

        Bound on the root rather than with `bind_all`: a widget's bindtags run
        widget, class, toplevel, all -- so this catches a key pressed anywhere
        in this window and *only* in this window. With `bind_all`, `r` in the
        contact sheet would rotate the preview underneath it as well.
        """
        for sequence in self._bound:
            try:
                self.root.unbind(sequence)
            except tk.TclError:
                pass
        self._bound = []
        actions = self._actions()
        scoped = shortcuts.in_scope(self.keys, "window")
        for sequence, action_id in scoped.items():
            run = actions.get(action_id)
            if run is None:
                continue
            bound = self._bind_key(self.root, sequence, action_id, run,
                                   taken=scoped)
            if bound:
                self._bound.append(bound)

    def _bind_key(self, widget, sequence: str, action_id: str, run,
                  taken: dict[str, str] | None = None) -> str | None:
        """Bind one key, or its default where Tk will not take the one set.

        `shortcuts.resolve` promises a hand-editing mistake costs a key rather
        than the window, and it cannot keep that promise alone: it accepts any
        string, and a sequence Tk does not know -- `<Foo>` -- raised TclError
        here, from the window's constructor, and the window never opened.
        Returns the sequence bound, or None.

        ``taken`` is the scope's own `{sequence: action id}`. A default another
        action there holds is not fallen back on: Tk's `bind` replaces what a
        key did, so the fallback took that key from its owner, whose shortcut
        then silently ran this action instead -- while the log said "using
        its default".
        """
        taken = taken or {}
        default = shortcuts.defaults().get(action_id, "")
        held = taken.get(default) not in (None, action_id)
        for candidate in dict.fromkeys((sequence, default)):
            if not candidate:
                continue
            if candidate != sequence and held:
                continue
            try:
                widget.bind(candidate, self._runner(run, candidate))
            except tk.TclError as exc:
                if candidate != sequence or not default or default == sequence:
                    then = "left unbound"
                elif held:
                    then = (f"left unbound: its default {default!r} is "
                            f"{taken[default]}'s")
                else:
                    then = "using its default"
                self._say(f"shortcut {candidate!r} for {action_id} is not a "
                          f"key Tk knows ({exc}); {then}")
                continue
            return candidate
        return None

    def _runner(self, run, sequence: str = ""):
        """One handler shape, and the rule about text fields.

        A modified key fires wherever the focus is. ⌘S in the middle of typing
        a subject line is a save, and every other application treats it as
        one -- refusing it would be this window inventing a rule of its own.

        An unmodified one does not: a bare `r` in a text field is an `r`, and
        the version of this that suppressed nothing turned the picture four
        times while somebody typed "rotate" into the subject line.
        """
        guard = not shortcuts.is_modified(sequence)

        def handler(_event=None):
            if guard and self._typing():
                return None
            run()
            return "break"
        return handler

    def on_shortcuts(self) -> None:
        """The editor. One at a time, like the contact sheet."""
        if self._shortcut_editor is not None and self._shortcut_editor.alive():
            self._shortcut_editor.top.lift()
            self._shortcut_editor.top.focus_force()
            return
        self._shortcut_editor = _ShortcutSettings(self)

    def set_keys(self, keys: dict[str, str]) -> None:
        """Take a whole new set, bind it, and write it down."""
        self.keys = dict(keys)
        self.shortcut_overrides = shortcuts.overrides_from(self.keys)
        self._bind_shortcuts()
        for window in (self.sheet, self._shortcut_editor):
            if window is not None and window.alive():
                rebind = getattr(window, "rebind", None)
                if rebind is not None:
                    rebind()
        self._remember()

    # -- presets -----------------------------------------------------------

    def _refresh_presets(self) -> None:
        names = sorted(self.presets)
        self.preset_box.configure(values=names)
        if self.v_preset.get() not in names:
            self.v_preset.set("")

    def on_preset_chosen(self, _event=None) -> None:
        stored = self.presets.get(self.v_preset.get())
        if not stored or not isinstance(stored, dict):
            return
        values, refused = preset_values(stored)
        if refused:
            self._say(f"preset {self.v_preset.get()!r}: ignored "
                      + ", ".join(f"{k}={stored[k]!r}" for k in refused)
                      + " -- not a setting a preset carries, or not one of "
                      "its choices")
        for key, value in values.items():
            variable = getattr(self, f"v_{key}", None)
            if variable is not None:
                try:
                    variable.set(value)
                except tk.TclError:
                    pass
        self._sync_exposure()
        self._sync_film()
        self._show_estimate()

    def on_preset_save(self) -> None:
        name = simpledialog.askstring(
            "Save preset", "A name for these settings:",
            initialvalue=self.v_preset.get() or self._suggested_preset(),
            parent=self.root)
        if not (name or "").strip():
            return
        self.presets[name.strip()] = {
            key: getattr(self, f"v_{key}").get()
            for key in PRESET_KEYS if hasattr(self, f"v_{key}")
        }
        self.v_preset.set(name.strip())
        self._refresh_presets()
        self._remember()
        self._say(f"saved the preset {name.strip()!r}")

    def on_preset_delete(self) -> None:
        name = self.v_preset.get()
        if name and name in self.presets and messagebox.askokcancel(
            "Delete preset", f"Forget the preset {name!r}?", parent=self.root
        ):
            del self.presets[name]
            self._refresh_presets()
            self._remember()

    def _suggested_preset(self) -> str:
        stock = self.fields["stock"].get().strip()
        return (f"{stock + ' at ' if stock else ''}{self.v_dpi.get()} dpi "
                f"{'RGBI' if self.v_ir.get() else 'RGB'}")

    def _on_wheel(self, event: tk.Event) -> str | None:
        """Route a wheel or two-finger scroll to the region it happened in.

        The fallback path, for anything that has not been bound directly.
        """
        amount, sideways = _wheel_amount(event)
        if amount == 0:
            return None
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            for target, handler in self._scrollers:
                if widget is target:
                    handler(amount, sideways)
                    return "break"
            name = widget.winfo_parent()
            if not name:
                return None
            try:
                widget = self.root.nametowidget(name)
            except KeyError:
                return None
        return None

    def _on_touchpad(self, event: tk.Event) -> str | None:
        """Fallback for anything not bound directly."""
        dx, dy = _touchpad_deltas(event)
        if dx == 0 and dy == 0:
            return None
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            for target, handler in self._scrollers:
                if widget is target:
                    handler(-1 if (dy or dx) > 0 else 1, bool(dx and not dy))
                    return "break"
            name = widget.winfo_parent()
            if not name:
                return None
            try:
                widget = self.root.nametowidget(name)
            except KeyError:
                return None
        return None

    def _scrolls(self, widget: tk.Misc, handler, precise=None,
                 deep: bool = True) -> None:
        """Make scrolling do something over `widget` and everything inside it.

        Two different events, because a mouse and a trackpad are not the same
        thing here. A wheel sends <MouseWheel> in notches; a trackpad on macOS
        under Tk 9 sends <TouchpadScroll> in pixels, and never a MouseWheel at
        all -- which is why binding only the wheel left the trackpad dead.

        The second of those exists only from Tk 8.7, so it goes through
        `_bind_optional` rather than `bind`: Windows and most Linux ship 8.6,
        where asking for it is a TclError.

        Bound on each widget itself rather than at the root: a widget's own
        bindings run before its class's and before the "all" tag, so nothing
        can swallow the event first.
        """
        self._scrollers.append((widget, handler))

        def wheel(event: tk.Event) -> str | None:
            amount, sideways = _wheel_amount(event)
            if amount == 0:
                return None
            self._pointer = (event.x, event.y)
            handler(amount, sideways)
            return "break"

        def touchpad(event: tk.Event) -> str | None:
            dx, dy = _touchpad_deltas(event)
            if dx == 0 and dy == 0:
                return None
            self._pointer = (event.x, event.y)
            if precise is not None:
                precise(dx, dy)
            else:
                # No pixel path: fall back to notches, which is coarse but
                # still moves.
                if dy:
                    handler(-1 if dy > 0 else 1, False)
                elif dx:
                    handler(-1 if dx > 0 else 1, True)
            return "break"

        self._bind_scroll(widget, wheel, touchpad, deep)

    def _bind_scroll(self, widget: tk.Misc, wheel, touchpad,
                     deep: bool = True) -> None:
        for sequence in ("<MouseWheel>", "<Shift-MouseWheel>",
                         "<Button-4>", "<Button-5>"):
            widget.bind(sequence, wheel, add="+")
        _bind_optional(widget, "<TouchpadScroll>", touchpad)
        if deep:
            for child in widget.winfo_children():
                self._bind_scroll(child, wheel, touchpad)

    def _panel(self, parent, title: str):
        """A group box whose header says what in it has been changed.

        `labelwidget` rather than `text`, which is the only way Tk will put
        anything but a string in a group's header -- and a reset that lives
        anywhere but the header of the thing it resets is a reset you have to go
        looking for.

        The count and the button appear only when something differs from its
        default, which is NegPy's arrangement and for the same reason: a reset
        button that is always there is one more thing to read past on every
        panel, every launch.
        """
        box = ttk.LabelFrame(parent, padding=8)
        header = ttk.Frame(box)
        ttk.Label(header, text=title).pack(side="left")
        count = ttk.Label(header, foreground="#a8761f")
        reset = ttk.Button(header, text="↺", width=2,
                           command=lambda t=title: self.on_reset_panel(t))
        box.configure(labelwidget=header)
        self._panel_marks[title] = (count, reset)
        self._panel_boxes[title] = box
        return box

    # -- defaults, and getting back to them --------------------------------

    def _control(self, name: str):
        """The variable behind a control name, whichever kind it is.

        The panels hold two sorts: the window's own `v_<name>`, and the film
        fields, which live in `self.fields` because they are written as a block.
        A reset has to reach both, and nothing else should have to know which is
        which.
        """
        own = getattr(self, f"v_{name}", None)
        return own if own is not None else self.fields.get(name)

    def _resettable_names(self) -> tuple:
        """Every control a reset can reach, panels first then the homeless."""
        names = [n for panel in PANEL_CONTROLS.values() for n in panel]
        return tuple(names) + PANEL_LESS

    def _take_defaults(self) -> None:
        """Remember what every control was built holding.

        Called between `_build` and `_restore`, which is the one moment the
        window shows its shipped defaults and nothing else. **Captured rather
        than written down**: a table of default values would be a second copy of
        what the `_build_*` methods set, and the two would disagree the first
        time either moved -- as a reset button that quietly changed a setting.
        """
        self.defaults = {}
        for name in self._resettable_names():
            variable = self._control(name)
            if variable is not None:
                self.defaults[name] = variable.get()

    def _values(self, names) -> dict:
        """What those controls hold now."""
        out = {}
        for name in names:
            variable = self._control(name)
            if variable is not None:
                out[name] = variable.get()
        return out

    def _watch_controls(self) -> None:
        """Recount a panel's changes whenever one of its controls moves."""
        for name in self._resettable_names():
            variable = self._control(name)
            if variable is not None:
                variable.trace_add("write", lambda *_a: self._panels_changed())

    def _panels_changed(self) -> None:
        """Ask for a recount, once, however many variables just moved.

        A trace fires per variable, and `_restore`, a preset and a roll's own
        settings each write a dozen in a row -- recounting on every one would
        redraw five headers twelve times for one action.
        """
        if self._panel_job is None:
            self._panel_job = self._later(60, self._refresh_panels)

    def _refresh_panels(self) -> None:
        self._panel_job = None
        for title, (count, reset) in self._panel_marks.items():
            names = PANEL_CONTROLS.get(title, ())
            changed = changed_controls(self._values(names), self.defaults, names)
            if changed:
                count.configure(text=f"· {len(changed)}")
                count.pack(side="left", padx=(5, 0))
                reset.pack(side="left", padx=(5, 0))
            else:
                # Cleared as well as unpacked. An unpacked label keeps its text,
                # which is invisible and still wrong -- and the next thing to
                # read that text believes it.
                count.configure(text="")
                count.pack_forget()
                reset.pack_forget()

    def _reset_names(self, names) -> list:
        """Put those controls back, and say which ones actually moved."""
        moved = []
        for name in changed_controls(self._values(names), self.defaults, names):
            variable = self._control(name)
            if variable is None:
                continue
            try:
                variable.set(self.defaults[name])
            except tk.TclError:
                continue
            moved.append(name)
        if moved:
            # The same four the preset path calls, for the same reason: the
            # variables are set, and these are what carry them into the session
            # and into the labels that describe them.
            self._sync_format()
            self._sync_exposure()
            self._sync_film()
            self._show_estimate()
            self._refresh_panels()
        return moved

    def on_reset_panel(self, title: str) -> None:
        """The header's arrow: that panel, and nothing else."""
        moved = self._reset_names(PANEL_CONTROLS.get(title, ()))
        if moved:
            self._say(f"{title}: put back {', '.join(moved)}")

    def on_reset_control(self, name: str) -> None:
        was = self.defaults.get(name)
        if self._reset_names((name,)):
            self._say(f"put {name} back to {was!r}")

    def on_restore_settings(self) -> None:
        """Every control back to what the window shipped with.

        Not the shortcuts -- they have their own restore in their own editor, and
        sweeping them up here would mean an operator who wanted his dpi back lost
        his keys with it. Not the presets either: those are things he made.
        """
        names = self._resettable_names()
        changed = changed_controls(self._values(names), self.defaults, names)
        if not changed:
            messagebox.showinfo("Restore settings",
                                "Every control is already at its default.")
            return
        if not messagebox.askokcancel(
            "Restore settings",
            f"Put {len(changed)} control"
            f"{'s' if len(changed) != 1 else ''} back to the defaults this "
            f"window shipped with?\n\n"
            "Your keyboard shortcuts and your presets are left alone -- the "
            "shortcuts have their own restore, in their own editor. So is the "
            "output folder, which has its own Clear.",
        ):
            return
        moved = self._reset_names(names)
        self._say(f"restored {len(moved)} controls to their defaults")

    def _bind_control_menus(self) -> None:
        """Right-click any resettable control to put just that one back.

        The widgets are found by the variable each is bound to rather than
        registered at every creation site: twenty call sites that each have to
        remember to register is twenty places to forget, and the variable is
        already the thing that identifies a control.

        Right-click rather than the double-click NegPy uses on its sliders,
        because these are entries and comboboxes -- double-click there already
        means select-a-word, and taking it would break typing to gain a reset.
        """
        wanted = {}
        for name in self._resettable_names():
            variable = self._control(name)
            if variable is not None:
                wanted[str(variable)] = name
        for box in self._panel_boxes.values():
            for widget in _descendants(box):
                for option in ("textvariable", "variable"):
                    try:
                        bound = str(widget.cget(option))
                    except tk.TclError:
                        continue
                    name = wanted.get(bound)
                    if name is None:
                        continue
                    for sequence in MENU_EVENTS:
                        widget.bind(sequence,
                                    lambda e, n=name: self._control_menu(e, n))
                    break

    def _control_menu(self, event: tk.Event, name: str) -> str:
        """One item, and it says so when there is nothing to undo."""
        if name not in self.defaults:
            return "break"
        default = self.defaults[name]
        menu = tk.Menu(self.root, tearoff=0)
        if changed_controls(self._values((name,)), self.defaults, (name,)):
            menu.add_command(label=f"Reset to {default!r}",
                             command=lambda n=name: self.on_reset_control(n))
        else:
            menu.add_command(label=f"Already {default!r}", state="disabled")
        menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def _scrollable(self, parent: ttk.PanedWindow) -> ttk.Frame:
        """A left column that scrolls, because it is taller than the window.

        Tk has no scrollable frame, so it is the usual Canvas with a Frame
        inside, kept in step by their <Configure> events.
        """
        host = ttk.Frame(parent)
        parent.add(host, weight=1)
        canvas = tk.Canvas(host, width=_px(262), highlightthickness=0,
                           borderwidth=0)
        bar = ttk.Scrollbar(host, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = ttk.Frame(canvas, padding=8)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(window, width=e.width))
        self._column = (canvas, inner)
        return inner

    def _build_scan(self, parent: ttk.Frame) -> None:
        box = self._panel(parent, "Scan")
        box.pack(fill="x")

        row = ttk.Frame(box)
        row.pack(fill="x", pady=(0, 4))
        ttk.Label(row, text="preset", width=10).pack(side="left")
        self.v_preset = tk.StringVar()
        self.preset_box = ttk.Combobox(row, textvariable=self.v_preset, width=13,
                                       state="readonly", values=[])
        self.preset_box.pack(side="left", fill="x", expand=True)
        self.preset_box.bind("<<ComboboxSelected>>", self.on_preset_chosen)
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(0, 4))
        ttk.Label(row, text="", width=10).pack(side="left")
        ttk.Button(row, text="Save", width=6,
                   command=self.on_preset_save).pack(side="left")
        ttk.Button(row, text="Forget", width=7,
                   command=self.on_preset_delete).pack(side="left", padx=4)

        row = ttk.Frame(box)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="scan dpi", width=10).pack(side="left")
        self.v_dpi = tk.StringVar(value="1800")
        box_dpi = ttk.Combobox(row, textvariable=self.v_dpi, width=8,
                               values=[str(d) for d in DPI_LADDER])
        box_dpi.pack(side="left")
        box_dpi.bind("<<ComboboxSelected>>", lambda _e: self._show_estimate())
        box_dpi.bind("<KeyRelease>", lambda _e: self._show_estimate())

        row = ttk.Frame(box)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="prescan dpi", width=10).pack(side="left")
        self.v_predpi = tk.StringVar(value="300")
        ttk.Combobox(row, textvariable=self.v_predpi, width=8,
                     values=[str(d) for d in PRESCAN_LADDER]).pack(side="left")

        self.v_ir = tk.BooleanVar(value=True)
        # Through `_sync_infrared`, which greys the tie below by this box and
        # ends in the estimate. Wired to the estimate alone, unticking left the
        # tie live under an infrared cost, and ticking after a black and white
        # film left it greyed.
        self.c_ir = ttk.Checkbutton(box, text="infrared (RGBI)",
                                    variable=self.v_ir,
                                    command=self._sync_infrared)
        self.c_ir.pack(anchor="w", pady=2)
        # Tied to the scan resolution by default. Untying it restores the fixed
        # ~220 s floor, which is worth having only where the plane matters more
        # than the wait -- the reason it is a checkbox rather than a constant.
        self.v_fast_ir = tk.BooleanVar(value=True)
        self.c_fast_ir = ttk.Checkbutton(
            box, text="infrared at scan resolution", variable=self.v_fast_ir,
            command=self._show_estimate)
        # Packed once, here, and greyed by `_sync_infrared` -- not packed and
        # unpacked by it, which is what it used to do.
        self.c_fast_ir.pack(anchor="w", padx=(20, 0))
        self.l_fast_ir = ttk.Label(box, text="", foreground="#555555",
                                   wraplength=240, justify="left")
        self.l_fast_ir.pack(anchor="w", padx=(38, 0))
        self.l_ir = ttk.Label(box, text="", foreground="#8a6d00",
                              wraplength=240, justify="left")

        row = ttk.Frame(box)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="film", width=10).pack(side="left")
        self.v_film = tk.StringVar(value="negative")
        film_box = ttk.Combobox(row, textvariable=self.v_film, width=12,
                                state="readonly", values=list(FILM_TYPES))
        film_box.pack(side="left")
        film_box.bind("<<ComboboxSelected>>", lambda _e: self._sync_film())

        self.v_mono = tk.BooleanVar(value=False)
        self.c_mono = ttk.Checkbutton(
            box, text="deliver one channel (black and white)",
            variable=self.v_mono, command=self._sync_mono_view)
        self.c_mono.pack(anchor="w", pady=2)

        row = ttk.Frame(box)
        row.pack(fill="x", pady=2)
        self.l_mono_channel = ttk.Label(row, text="channel", width=10)
        self.l_mono_channel.pack(side="left")
        self.v_mono_channel = tk.StringVar(value=MONO_CHANNEL)
        self.b_mono_channel = ttk.Combobox(
            row, textvariable=self.v_mono_channel, width=6, state="readonly",
            values=list(MONO_CHOICES))
        self.b_mono_channel.pack(side="left")
        self.b_mono_channel.bind("<<ComboboxSelected>>",
                                 lambda _e: self._sync_mono_view())

        ttk.Label(box, text="exposure").pack(anchor="w", pady=(6, 0))
        self.v_expmode = tk.StringVar(value="auto")
        ttk.Radiobutton(box, text="meter automatically", value="auto",
                        variable=self.v_expmode,
                        command=self._sync_exposure).pack(anchor="w")
        ttk.Radiobutton(box, text="set by hand", value="manual",
                        variable=self.v_expmode,
                        command=self._sync_exposure).pack(anchor="w")
        row = ttk.Frame(box)
        row.pack(fill="x", padx=(18, 0))
        self.v_exposure = tk.StringVar(value="1.0")
        self.e_exposure = ttk.Entry(row, textvariable=self.v_exposure, width=16)
        self.e_exposure.pack(side="left")
        # Typing in the box means you want it, so it selects itself rather than
        # swallowing what you typed while disabled.
        self.e_exposure.bind("<FocusIn>", self._claim_exposure)
        self.e_exposure.bind("<Button-1>", self._claim_exposure)
        self.v_exposure.trace_add("write", lambda *_a: self._show_exposure())
        self.v_expected = tk.StringVar()
        ttk.Label(box, textvariable=self.v_expected, foreground="#777",
                  wraplength=210, justify="left").pack(anchor="w", padx=(18, 0))

        ttk.Label(box, text="shading").pack(anchor="w", pady=(6, 0))
        self.v_shading = tk.StringVar(value="measure")
        for value, text in (("measure", "measure it now"),
                            ("reuse", "reuse the cached reference")):
            ttk.Radiobutton(box, text=text, value=value,
                            variable=self.v_shading).pack(anchor="w")
        self.b_calibrate = ttk.Button(box, text="Calibrate",
                                      command=self.on_calibrate_pressed)
        self.b_calibrate.pack(fill="x", pady=(6, 2))
        self.b_prescan = ttk.Button(box, text="Prescan", command=self.on_prescan)
        self.b_prescan.pack(fill="x", pady=2)
        self.b_scan = ttk.Button(box, text="Scan", command=self.on_scan)
        self.b_scan.pack(fill="x", pady=2)
        self.v_estimate = tk.StringVar()
        ttk.Label(box, textvariable=self.v_estimate,
                  foreground="#777").pack(anchor="w")

    def _build_transport(self, parent: ttk.Frame) -> None:
        box = self._panel(parent, "Transport")
        box.pack(fill="x", pady=(8, 0))

        row = ttk.Frame(box)
        row.pack(fill="x")
        self.v_position = tk.StringVar(value=position_label(None))
        ttk.Label(row, textvariable=self.v_position).pack(side="left")
        ttk.Button(row, text="ⓘ", width=3,
                   command=self.on_transport_help).pack(side="right")

        row = ttk.Frame(box)
        row.pack(fill="x", pady=(6, 2))
        self.b_prev = ttk.Button(row, text="◀ prev slide",
                                 command=lambda: self.on_move_frames(-1))
        self.b_prev.pack(side="left", expand=True, fill="x")
        self.b_next = ttk.Button(row, text="next slide ▶",
                                 command=lambda: self.on_move_frames(1))
        self.b_next.pack(side="left", expand=True, fill="x", padx=(4, 0))

        row = ttk.Frame(box)
        row.pack(fill="x", pady=2)
        self.b_fine_back = ttk.Button(row, text="◀ back",
                                      command=lambda: self.on_nudge(-1))
        self.b_fine_back.pack(side="left", expand=True, fill="x")
        self.b_fine_fwd = ttk.Button(row, text="forward ▶",
                                     command=lambda: self.on_nudge(1))
        self.b_fine_fwd.pack(side="left", expand=True, fill="x", padx=(4, 0))

        row = ttk.Frame(box)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="units", width=5).pack(side="left")
        self.v_fine = tk.StringVar(value=f"{units(FINE_STEP_MM):.1f}")
        ttk.Entry(row, textvariable=self.v_fine, width=7).pack(side="left")
        # A live preview rather than a static range. What he types and what the
        # transport can deliver are not the same number, and the gap between
        # them is the thing this window never told him.
        self.v_fine_note = tk.StringVar(value=fine_preview(self.v_fine.get()))
        ttk.Label(row, textvariable=self.v_fine_note,
                  foreground="#777").pack(side="left", padx=4)
        self.v_fine.trace_add("write", lambda *_: self.v_fine_note.set(
            fine_preview(self.v_fine.get())))

        self.v_aim = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, variable=self.v_aim, command=self._schedule_redraw,
                        text="click the prescan to align an edge").pack(
                            anchor="w", pady=(4, 0))
        self.v_reverse = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, variable=self.v_reverse,
                        text="reverse the direction").pack(anchor="w")

    def _build_roll(self, parent: ttk.Frame) -> None:
        box = self._panel(parent, "Roll")
        box.pack(fill="x", pady=(8, 0))
        # A range, both ends included, because that is how a strip is thought
        # about: "1 to 10", then "11 to the end". It was a count and a start,
        # and an empty count was an error rather than the end of the strip.
        for label, var, default in (("first frame", "v_startat", "1"),
                                    ("last frame", "v_last", "")):
            row = ttk.Frame(box)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=label, width=11).pack(side="left")
            setattr(self, var, tk.StringVar(value=default))
            ttk.Entry(row, textvariable=getattr(self, var), width=6).pack(side="left")
        ttk.Label(box, text="last frame empty: to the end of the strip",
                  foreground="#777", wraplength=210,
                  justify="left").pack(anchor="w")

        row = ttk.Frame(box)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="meter", width=11).pack(side="left")
        self.v_meter = tk.StringVar(value="each")
        ttk.Combobox(row, textvariable=self.v_meter, width=8, state="readonly",
                     values=list(METER_MODES)).pack(side="left")

        self.v_dryrun = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, text="dry run -- prescan and advance only",
                        variable=self.v_dryrun).pack(anchor="w", pady=2)
        self.v_correct = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, text="aim each frame while prescanning",
                        variable=self.v_correct).pack(anchor="w")
        self.v_correct_dry = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, text="    ... but only say what it would do",
                        variable=self.v_correct_dry).pack(anchor="w")
        self.b_roll = ttk.Button(box, text="Scan roll", command=self.on_roll)
        self.b_roll.pack(fill="x", pady=(6, 0))
        # What the next walk from the button is -- a new roll, or which strip
        # of which roll, numbered from what -- said before it is pressed, as
        # its question says it again (`strip_line`). A film is walked a strip
        # at a time over several evenings, and the roll box is not kept
        # across a restart: Continue puts the roll walked last back in one
        # click, and nothing is filed under it unless he presses it.
        self.v_strip_note = tk.StringVar()
        ttk.Label(box, textvariable=self.v_strip_note, foreground="#777",
                  wraplength=210, justify="left").pack(anchor="w",
                                                        pady=(4, 0))
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(2, 0))
        ttk.Button(row, text="New roll",
                   command=self.on_new_roll).pack(side="left")
        self.b_continue = ttk.Button(row, text="Continue",
                                     command=self.on_continue_roll)
        #: The roll Continue puts back, while it is offered.
        self._continue_roll: str | None = None
        # Opens by itself when a dry run ends; this is for getting back to it
        # after it has been closed, which is most of the time it is wanted.
        self.b_sheet = ttk.Button(box, text="Contact sheet ...", state="disabled",
                                  command=self.on_contact_sheet)
        self.b_sheet.pack(fill="x", pady=(4, 0))
        # "Rolls" rather than "Reopen a survey": it now lists both the walks
        # and the rolls that were scanned from them, and finishing one that
        # died part-way is the reason most likely to bring anyone here.
        ttk.Button(box, text="Rolls ...",
                   command=self.on_reopen_survey).pack(fill="x", pady=(4, 0))
        ttk.Label(box, foreground="#777", wraplength=210, justify="left",
                  text=("A dry run walks the strip in about 20 seconds a frame "
                        "and opens a contact sheet. Tick the frames worth "
                        "having and only those are scanned. Walking again "
                        "asks whether to add to the sheet you have.")).pack(
            anchor="w", pady=(4, 0))

    def _build_film(self, parent: ttk.Frame) -> None:
        box = self._panel(parent, "Film")
        box.pack(fill="x", pady=(8, 0))
        self.fields = {}
        for key in ("stock", "roll", "frame", "process", "subject", "notes",
                    "tags"):
            row = ttk.Frame(box)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=key, width=9).pack(side="left")
            var = tk.StringVar()
            ttk.Entry(row, textvariable=var, width=14).pack(
                side="left", fill="x", expand=True)
            self.fields[key] = var

    def _build_output(self, parent: ttk.Frame) -> None:
        box = self._panel(parent, "Save scans to")
        box.pack(fill="x", pady=(8, 0))
        self.v_outdir = tk.StringVar(
            value=str(self.session.out_dir) if self.session.out_dir else "")
        # Typed or pasted, it is taken on Return or on leaving the box. Only
        # Choose ... and Clear reached the session, so a folder typed here
        # went unused -- and was saved, and used from the next launch on.
        self.e_outdir = ttk.Entry(box, textvariable=self.v_outdir)
        self.e_outdir.pack(fill="x")
        for sequence in ("<Return>", "<FocusOut>"):
            self.e_outdir.bind(sequence, lambda _e: self._set_outdir(
                self.v_outdir.get().strip()))
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(4, 0))
        ttk.Button(row, text="Choose ...",
                   command=self.on_choose_out).pack(side="left")
        ttk.Button(row, text="Clear",
                   command=lambda: self._set_outdir("")).pack(side="left", padx=4)

        fmt = ttk.Frame(box)
        fmt.pack(fill="x", pady=(6, 0))
        self.v_outfmt = tk.StringVar(value=self.session.out_format)
        for label, value in (("TIFF", "tiff"), ("JPEG", "jpeg")):
            ttk.Radiobutton(fmt, text=label, value=value,
                            variable=self.v_outfmt,
                            command=self._sync_format).pack(side="left")
        # Always there, and greyed by `_sync_format` when the format has no
        # quality to set. It used to be packed and unpacked: a control that
        # disappears is a control whose state you cannot see, and the value is
        # still in force the moment JPEG is chosen again.
        self.quality_row = ttk.Frame(box)
        self.quality_row.pack(fill="x", pady=(4, 0))
        self.l_jpegq = ttk.Label(self.quality_row, text="Quality")
        self.l_jpegq.pack(side="left")
        self.v_jpegq = tk.StringVar(value=str(export.DEFAULT_QUALITY))
        self.b_jpegq = spin = ttk.Spinbox(
            self.quality_row, from_=60, to=100, width=5,
            textvariable=self.v_jpegq, command=self._sync_format)
        spin.pack(side="left", padx=(6, 0))
        self.l_jpegq_why = ttk.Label(self.quality_row, foreground="#999")
        self.l_jpegq_why.pack(side="left", padx=(8, 0))
        # On leaving the box, not on every keystroke: typing "8" on the way to
        # "85" must not be corrected to 60 under the operator's hands.
        spin.bind("<FocusOut>", lambda _e: self._sync_format())
        self.v_outnote = tk.StringVar()
        ttk.Label(box, foreground="#777", wraplength=210, justify="left",
                  textvariable=self.v_outnote).pack(anchor="w", pady=(4, 0))
        self._sync_format()

    def _sync_format(self) -> None:
        """Show the quality knob only for JPEG, and say what will be written."""
        jpeg = self.v_outfmt.get() == "jpeg"
        was = (self.session.out_format, self.session.jpeg_quality)
        self.session.out_format = "jpeg" if jpeg else "tiff"
        quality = jpeg_quality(self.v_jpegq.get())
        self.session.jpeg_quality = quality
        self._output_changed("format", was[0], self.session.out_format)
        if jpeg:
            self._output_changed("JPEG quality", was[1], quality)
        # Put the understood value back, so what is shown, what is written and
        # what `_remember` stores are the same number. Without this a typo is
        # saved verbatim and read back as a typo next launch.
        if self.v_jpegq.get() != str(quality):
            self.v_jpegq.set(str(quality))
        # Greyed with the reason rather than hidden, so the number in force is
        # always readable -- it is what a switch back to JPEG will use.
        for widget in (self.l_jpegq, self.b_jpegq):
            widget.configure(state="normal" if jpeg else "disabled")
        self.l_jpegq_why.configure(text="" if jpeg else "TIFF has none to set")
        self.v_outnote.set(output_note(self.session.out_format))

    def _build_preview(self, parent: ttk.PanedWindow) -> None:
        top = ttk.Frame(parent)
        parent.add(top, weight=5)
        self.canvas = tk.Canvas(top, background="#1b1b1b", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self._schedule_redraw())
        self.canvas.bind("<Button-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_drag", None))
        self.canvas.bind("<Double-Button-1>", self.on_double_click)
        self.canvas.bind("<Motion>", self.on_hover)
        self.canvas.bind("<Leave>", lambda _e: self.v_readout.set(""))
        # The picture on screen is the one an operator wants to turn, and until
        # now it was the only one that could not be: the menu lived on the
        # filmstrip alone, so turning what you were looking at meant finding its
        # thumbnail first.
        for seq in MENU_EVENTS:
            self.canvas.bind(seq, self.on_canvas_menu)
        # Two fingers zoom, because that is the gesture people reach for on
        # a picture; panning is the drag, which needs no gesture support at all.
        self._scrolls(self.canvas, self._wheel_over_picture,
                      precise=self._touchpad_over_picture)

        bar = ttk.Frame(top, padding=(6, 4))
        bar.pack(fill="x")
        ttk.Label(bar, text="view").pack(side="left")
        self.v_channel = tk.StringVar(value="RGB")
        self.channel_buttons = {}
        for name in preview.CHANNELS:
            b = ttk.Radiobutton(bar, text=name, value=name,
                                variable=self.v_channel,
                                command=self._on_view_chosen)
            b.pack(side="left", padx=1)
            self.channel_buttons[name] = b
        self.v_invert = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="invert", variable=self.v_invert,
                        command=self._redraw_all).pack(side="left", padx=(12, 0))
        ttk.Button(bar, text="+", width=3,
                   command=lambda: self._zoom_by(2.0)).pack(side="right")
        ttk.Button(bar, text="−", width=3,
                   command=lambda: self._zoom_by(0.5)).pack(side="right")
        ttk.Button(bar, text="1:1", width=4,
                   command=lambda: self._set_zoom(self._finest())).pack(side="right")
        ttk.Button(bar, text="Fit", width=4,
                   command=lambda: self._set_zoom(0.0)).pack(side="right")
        self.v_zoomtext = tk.StringVar(value="fit")
        ttk.Label(bar, textvariable=self.v_zoomtext, width=7,
                  anchor="e").pack(side="right", padx=(0, 8))
        # Fixed width and monospaced: it changes on every mouse move, and a
        # label that resizes with its text drags the whole toolbar with it.
        self.v_readout = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.v_readout, width=40, anchor="w",
                  font=_font(9, fixed=True),
                  foreground="#888").pack(side="left", padx=(12, 0))
        self.v_caption = tk.StringVar(value="nothing scanned yet")
        ttk.Label(top, textvariable=self.v_caption,
                  foreground="#777").pack(anchor="w", padx=6)

        # Placed rather than packed, so it floats in the corner of the picture
        # instead of taking width from it -- and so a canvas redraw, which
        # clears everything the canvas is holding, cannot touch it.
        self.histogram = _HistogramPanel(top)
        self.histogram.place()

        middle = ttk.Frame(parent)
        parent.add(middle, weight=1)
        self.strip = tk.Canvas(middle, height=_px(THUMB_H + 12), background="#111",
                               highlightthickness=0)
        self.strip.pack(fill="both", expand=True)
        self._scrolls(
            self.strip,
            lambda n, _side: self.strip.xview_scroll(n, "units"),
            # A filmstrip is a row, so either axis of a two-finger swipe
            # should walk along it.
            precise=lambda dx, dy: _scroll_pixels(self.strip, dx or dy, 0),
        )
        for seq in MENU_EVENTS:
            self.strip.bind(seq, self.on_strip_menu)
        self.menu = tk.Menu(self.root, tearoff=0)

        bottom = ttk.Frame(parent)
        parent.add(bottom, weight=2)
        prog = ttk.Frame(bottom, padding=(6, 4))
        prog.pack(fill="x")
        # Empty and taking no attention outside a roll -- set only from
        # on_roll() onward, cleared when the roll ends. See _update_roll_eta.
        self.v_roll_eta = tk.StringVar()
        ttk.Label(prog, textvariable=self.v_roll_eta,
                 foreground="#888").pack(anchor="w")
        prow = ttk.Frame(prog)
        prow.pack(fill="x")
        self.v_progress = tk.StringVar()
        ttk.Label(prow, textvariable=self.v_progress).pack(side="left", anchor="w")
        # The current pass's own ETA, interpolated from its line count -- see
        # _progress. Separate label so it can sit flush right regardless of
        # how long the "done/total lines" text on the left runs.
        self.v_pass_eta = tk.StringVar()
        ttk.Label(prow, textvariable=self.v_pass_eta,
                 foreground="#888").pack(side="right", anchor="e")
        self.progress = ttk.Progressbar(prog, mode="determinate", maximum=1000)
        self.progress.pack(fill="x", pady=2)
        buttons = ttk.Frame(prog)
        buttons.pack(fill="x")
        self.b_stop = ttk.Button(buttons, text="Stop", command=self.on_stop,
                                 state="disabled")
        self.b_stop.pack(side="left")
        self.b_abort = ttk.Button(buttons, text="Force abort",
                                  command=self.on_abort, state="disabled")
        self.b_abort.pack(side="right")

        logbox = ttk.Frame(bottom)
        logbox.pack(fill="both", expand=True)
        self.log = tk.Text(logbox, height=6, wrap="none", background="#111",
                           foreground="#bbb", insertbackground="#bbb",
                           highlightthickness=0, borderwidth=0)
        self.log.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(logbox, command=self.log.yview)
        scroll.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scroll.set, state="disabled")
        self._scrolls(self.log,
                      lambda n, _side: self.log.yview_scroll(n, "units"),
                      precise=lambda dx, dy: _scroll_pixels(self.log, 0, dy))

        self._sync_exposure()
        self._sync_film()
        self._show_estimate()

    # -- reading the controls ---------------------------------------------

    def _notes(self) -> FilmNotes:
        return FilmNotes(
            stock=self.fields["stock"].get().strip(),
            process=self.fields["process"].get().strip(),
            frame=self.fields["frame"].get().strip(),
            subject=self.fields["subject"].get().strip(),
            notes=self.fields["notes"].get().strip(),
        )

    def _tags(self) -> tuple[str, ...]:
        raw = self.fields["tags"].get()
        return tuple(t.strip() for t in raw.replace(",", " ").split() if t.strip())

    def _int(self, var: tk.StringVar, what: str, low: int, high: int) -> int | None:
        try:
            value = int(var.get().strip())
        except ValueError:
            messagebox.showerror(what, f"{what} has to be a whole number.")
            return None
        if not low <= value <= high:
            messagebox.showerror(what, f"{value} is outside {low}-{high}.")
            return None
        return value

    def _dpi(self) -> int | None:
        return self._int(self.v_dpi, "Scan dpi", 25, 7200)

    def _prescan_dpi(self) -> int | None:
        return self._int(self.v_predpi, "Prescan dpi", 25, 7200)

    def _exposure(self):
        if self.v_expmode.get() == "auto":
            return 1.0
        values = _numbers(self.v_exposure.get())
        if values is None:
            messagebox.showerror("Exposure", "Exposure must be numbers.")
            return None
        if not values:
            return 1.0
        return values[0] if len(values) == 1 else values

    def _claim_exposure(self, _event=None) -> None:
        """Typing in the box means you meant to use it."""
        if self.v_expmode.get() != "manual":
            self.v_expmode.set("manual")
            self._sync_exposure()
            self.e_exposure.focus_set()

    def _sync_exposure(self) -> None:
        manual = self.v_expmode.get() == "manual"
        self.e_exposure.configure(state="normal" if manual else "disabled")
        self._show_exposure()

    def _sync_film(self) -> None:
        """Everything that follows the film type, in one place."""
        self._sync_infrared()
        self._sync_mono()

    def _sync_mono(self) -> None:
        """One channel out for black and white, three for everything else.

        Follows the film rather than being remembered on its own: a consumer
        cannot tell a B&W scan from a slide by looking at the pixels -- measured
        across this library the two overlap -- so the delivered file has to say
        which it is by its shape. See rps7200/mono.py.

        The controls are greyed out for every other film because reducing a
        colour negative or a slide to one channel would throw the picture away,
        not just a redundant copy of it.
        """
        bw = self.v_film.get() == FILM_BW
        self.v_mono.set(bw)
        state = "normal" if bw else "disabled"
        self.c_mono.configure(state=state)
        self.b_mono_channel.configure(state="readonly" if bw else "disabled")
        self.l_mono_channel.configure(foreground="" if bw else "#999")
        self._sync_mono_view()

    def _sync_mono_view(self) -> None:
        """Show what will be delivered.

        A black and white scan goes out as one channel, so the prescan and the
        scan on screen are that channel -- and `preview.render` draws a single
        plane grey rather than tinted, so it looks like the photograph rather
        than like a green separation. The view switch still works: this sets
        where it starts, it does not take the others away.
        """
        if self.v_mono.get():
            # "avg" is not a plane, so the view for it is MONO -- the same
            # average that will be delivered, so the screen and the file
            # agree whichever setting is chosen.
            picked = self.v_mono_channel.get()
            self.v_channel.set("MONO" if picked == MONO_AVERAGE else picked)
        elif self.v_channel.get() != "RGB":
            self.v_channel.set("RGB")
        self._redraw_all()

    def _sync_infrared(self) -> None:
        """Infrared off and unavailable on film that absorbs it.

        Silver-halide black and white and Kodachrome both do. The pass costs
        its ~212 s floor and hands back a plane holding the picture instead of
        the dust -- measured at +0.97 correlation with green on a B&W frame
        here. The scanner refuses it, so the box has to go with it rather than
        letting someone arm a scan that will fail at the last moment.
        """
        film = self.v_film.get()
        blind = film in INFRARED_IS_BLIND_TO
        if blind:
            self.v_ir.set(False)
            self.c_ir.configure(state="disabled")
            self.l_ir.configure(
                text=f"infrared is off: {film} absorbs it, so the plane would "
                     f"hold the picture rather than the dust"
            )
            self.l_ir.pack(anchor="w", padx=(20, 0))
        else:
            self.c_ir.configure(state="normal")
            self.l_ir.pack_forget()
        # Greyed rather than hidden when there is no infrared plane for it to
        # govern. It stays ticked while it is greyed and the driver gates it on
        # `infrared` for itself, so this is a setting that is still *set* -- and
        # a set thing you cannot see is worse than a dead control you can.
        infrared = self.v_ir.get()
        self.c_fast_ir.configure(state="normal" if infrared else "disabled")
        self.l_fast_ir.configure(
            text=infrared_cost_note(dpi_or(self.v_dpi.get()),
                                    self.v_fast_ir.get()) if infrared
            else "nothing to tie: an RGB pass has no infrared plane",
            foreground="#555555" if infrared else "#999999")
        self._show_estimate()

    def _show_exposure(self) -> None:
        """Say what will actually be sent, so the box cannot lie quietly."""
        if self.v_expmode.get() != "manual":
            self.v_expected.set("the scanner meters it, two RGB rounds, ~48 s")
            return
        values = _numbers(self.v_exposure.get())
        if values is None:
            self.v_expected.set("not numbers")
            return
        if not values:
            self.v_expected.set("empty -- the device's own exposure")
            return
        if len(values) == 1:
            self.v_expected.set(f"x{values[0]:g} on R, G, B and IR")
            return
        names = ["R", "G", "B", "IR"]
        shown = ", ".join(f"{n} x{v:g}" for n, v in zip(names, values))
        if len(values) < 4:
            shown += f", {' and '.join(names[len(values):])} unchanged"
        self.v_expected.set(shown)

    def _show_estimate(self) -> None:
        try:
            dpi = int(self.v_dpi.get().strip())
        except ValueError:
            self.v_estimate.set("")
            return
        fast = self.v_fast_ir.get()
        self.v_estimate.set(
            f"about {_duration(estimate_seconds(dpi, self.v_ir.get(), fast))} "
            f"a pass")
        # Kept in step here as well as in `_sync_infrared`: the resolution box
        # and this checkbox both change what the line below should say, and
        # only this method sees both.
        if self.v_ir.get():
            self.l_fast_ir.configure(text=infrared_cost_note(dpi, fast))

    def _set_outdir(self, path: str) -> None:
        self.v_outdir.set(path)
        was = self.session.out_dir
        self.session.out_dir = Path(path) if path else None
        self._output_changed("output folder", was, self.session.out_dir)
        if (self.demo and path and not _within(path, DEMO_ROOT)
                and self._demo_out_said != path):
            # Allowed, as any choice made in the window is -- and said, once
            # for each folder: what the demo writes is synthetic, under
            # names a real scan would have. `--out` outside demo/ is refused
            # at launch (`main`), where a flag slips in unseen.
            self._demo_out_said = path
            self._say(f"the demo's scans are made up, and their copies now "
                      f"go to {path}, outside {DEMO_ROOT}/, under the names "
                      "real scans are given")

    def _output_changed(self, what: str, before, after) -> None:
        """Say that a change to the output waits for the next job.

        A job takes the output folder, format and JPEG quality as it starts
        (`session.Output`), and goes on with them however they are changed
        while it runs: so a change says so, once, where a job is running
        that took another value. Nothing is said between jobs, nor for a
        value that did not change.
        """
        if before == after:
            return
        running = self.session.job_output()
        if running is None:
            return
        held = {"output folder": running.folder, "format": running.format,
                "JPEG quality": running.quality}[what]
        if held == after:
            return

        def shown(value) -> str:
            if what == "format":
                return str(value).upper()
            return str(value) if value not in (None, "") else "none"

        self._say(f"the {what} is now {shown(after)} -- the job running now "
                  f"goes on with {shown(held)}; this applies from the next "
                  "job to start")

    # -- actions -----------------------------------------------------------

    def on_about(self) -> None:
        messagebox.showinfo(
            "About the scanner",
            self.session.inquiry_text or "The scanner has not answered yet.")

    def on_transport_help(self) -> None:
        messagebox.showinfo(
            "Moving the film",
            "Prev / next slide step whole pictures. The transport counts them "
            "and READ_STATE confirms the move, so these are the reliable "
            "ones. The readout above is that count as a frame number, the "
            "same one a roll files each frame under: frame 1 is where the "
            "counter reads 0, which is where the strip went in.\n\n"
            "Back / forward move a fraction of a frame. The frame counter does "
            "not see these at all, so only a prescan shows whether one landed. "
            f"The smallest step the hardware can make is "
            f"{say_units(FINE_STEP_MM, signed=False)}; one command delivers "
            f"at most {say_units(MAX_FINE_MM, signed=False)}, and anything "
            "the calibration stops being trustworthy.\n\n"
            "Changing direction swallows two or three steps to backlash, so a "
            "small move that reverses may not move the film at all.\n\n"
            "Back and forward follow the film, which may not be left and right "
            "as you see it. If it goes the wrong way, tick 'reverse the "
            "direction'.")

    def on_choose_out(self) -> None:
        path = filedialog.askdirectory(title="Save scans to", parent=self.root)
        if path:
            self._set_outdir(path)

    def on_calibrate_pressed(self) -> None:
        """The panel's Calibrate button: asks what is in the transport first.

        It used to submit the calibration the moment it was pressed. A
        measurement runs the carriage over the calibration frame, and only
        Stefan can see whether the strip is in: calibrating an empty
        transport is a state the vendor never creates, and the one time it
        was done here it preceded a wedge. So the button opens the prompt a
        scan without a calibration opens, where the film is confirmed before
        anything starts. Loading the cached reference reads a file and moves
        nothing, so that is not asked about.

        Straight through only for a reference measured since this window
        opened. "Reuse" is remembered across launches, so it used to load the
        cached file whatever its age -- another power-on's, a lamp change
        ago -- skipping the one prompt that says how old it is, and every
        frame after was corrected with it. An older one goes through the
        prompt, which offers it with its age beside it.
        """
        # Said before the prompt asks about the film, not after it is answered.
        if self._no_scanner("Calibrate"):
            return
        if (self.v_shading.get() == "reuse" and self._cached_reference()
                and self._cached_since_opened()):
            self.on_calibrate("reuse")
            return
        self.ask_to_calibrate(for_scan=False)

    def _cached_since_opened(self) -> bool:
        """Whether the cached reference was written while this window was open."""
        try:
            written = Path(self.session.reference).stat().st_mtime
        except OSError:
            return False
        # Two seconds' grace: FAT keeps modification times to two seconds and
        # HFS+ to one, so a file written just after opening can read as older.
        return written >= self._opened_at - 2

    def _cached_reference(self) -> bool:
        """Whether "reuse" would load a file rather than measure.

        `ensure_shading` measures when the cached reference is not there, so a
        "reuse" with nothing cached is a calibration like any other, and asks
        like one.
        """
        return Path(self.session.reference).exists()

    def on_calibrate(self, mode: str | None = None) -> None:
        """Start the calibration: the answer to the prompt, never a control.

        Reached once the film has been confirmed in the transport, or for a
        cached reference, which moves nothing. Nothing else calls it, and no
        key does (`shortcuts.NEVER_BOUND`).

        Not while the scanner works. The prompt is not a modal, so it stays up
        through a move or a roll, and a calibration queued behind one that
        Stop then dropped never ran and never said so: the window went on
        calling itself calibrated, and every scan after it failed in the
        driver instead of being asked about here.
        """
        if self._working():
            messagebox.showinfo(
                "Calibrate",
                "The scanner is working. Calibrate once it has finished -- "
                "this question stays open until then.",
                parent=self._calibrate_prompt or self.root)
            return
        # And not with nothing to run it: queued there, it left the window
        # calling itself calibrated with no reference anywhere.
        if self._no_scanner("Calibrate", self._calibrate_prompt):
            return
        self._hand_over(Calibrate(mode=mode or self.v_shading.get(),
                                  reference=self.session.reference))
        # Ahead of the answer, so a scan pressed next is queued behind it
        # rather than asking again; the "calibrated" event then says how it
        # ended -- or, when a stop kept it from running, the job's end does.
        self.calibrated = True
        self._calibration_pending = True
        self._sync_calibration()
        # Whichever button started it, the question the prompt asks is answered.
        top, self._calibrate_prompt = self._calibrate_prompt, None
        if top is not None:
            try:
                top.destroy()
            except tk.TclError:
                pass

    def _sync_calibration(self) -> None:
        self.b_calibrate.configure(
            text="Calibrate again" if self.calibrated else "Calibrate")

    def _calibration_missing(self, parent=None) -> bool:
        """True, having asked for one, when a scan would run uncalibrated.

        Called by every control that takes a picture -- prescan, scan, a roll
        or a walk, the sheet's scan, and their keys -- and by nothing else:
        without a calibration every setting can still be changed, the film
        moved and a stored walk opened. It asks rather than greying the
        buttons, because a greyed button does not say what it is waiting for.
        """
        if self.calibrated:
            return False
        self.ask_to_calibrate(parent)
        return True

    def ask_to_calibrate(self, parent=None, *, for_scan: bool = True,
                         moved: str | None = None) -> None:
        """Offer a calibration, and start one only once the film is confirmed.

        Three ways in: a scan that has none (``for_scan``), the panel's
        Calibrate button, and a scan refused because the scanner's gain or
        offsets have moved off the ones the reference was taken at
        (``moved``, the session's words for it; "reference_moved"). The
        reference belongs to the power-on that measured it, so a session that
        scans before calibrating is a session whose corrections describe some
        other day's sensor.

        A moved reference can also be kept: "Use it anyway" lets the next
        scan go on with this very mismatch (`ScanSession.accept_reference`).
        Stefan's choice, 2026-10-04: warn, and offer a calibration.

        **It asks what is in the transport, every time.** Only Stefan can see
        it, and `READ_STATE`'s media bit has read clear with film loaded, so
        it cannot answer for him. A measurement waits for the tick; a bare
        Return, which used to start one from here, says so instead. The tick
        is never remembered: a strip taken out since the last calibration is
        exactly the case it exists for. The cached reference loads without
        it, since loading moves nothing.

        Not a modal: a modal here sits inside the event pump and stops it.
        Pressed twice, it raises the one already open.
        """
        if self._calibrate_prompt is not None:
            try:
                self._calibrate_prompt.lift()
                self._calibrate_prompt.focus_force()
                return
            except tk.TclError:
                self._calibrate_prompt = None
        parent = parent or self.root
        top = tk.Toplevel(parent)
        self._calibrate_prompt = top
        top.title("Calibrate again" if moved
                  else "Calibrate first" if for_scan else "Calibrate")
        top.transient(parent)
        top.resizable(False, False)
        frame = ttk.Frame(top, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, font=_font(13, bold=True),
                  text=("The calibration no longer matches the scanner"
                        if moved else
                        "Without a calibration no picture can be scanned"
                        if for_scan else "Calibrate with the film loaded")
                  ).pack(anchor="w")
        if moved:
            ttk.Label(frame, wraplength=430, justify="left", padding=(0, 8, 0, 0),
                      text=(moved[0].upper() + moved[1:] + ".\n\nThe scanner "
                            "sets its own gain when it calibrates, and has been "
                            "seen to change it again without one. Nothing was "
                            "scanned; press the scan button again after "
                            "choosing.")).pack(anchor="w")
        ttk.Label(
            frame, wraplength=430, justify="left", padding=(0, 8),
            text=("The scanner measures its own per-column response and hands "
                  "it back; this driver does the dividing. Without it the "
                  "vertical striping is simply left in.\n\n"
                  "The reference belongs to the power-on that measured it, so "
                  "it is done once per session -- and with the film loaded, "
                  "which is what the vendor software does. The calibration "
                  "frame is the lower part of the transport, which the film "
                  "does not cover, so the sensor is measured with the strip "
                  "still in. Calibrating an empty transport is a state the "
                  "vendor never creates, and once it preceded a wedge."
                  + ("\n\nNothing is scanned now. Press the scan button again "
                     "once the calibration has finished." if for_scan else ""))
        ).pack(anchor="w")
        cached = Path(self.session.reference)
        if cached.exists():
            age = (time.time() - cached.stat().st_mtime) / 3600
            note = f"A cached reference exists, {_age(age)} old."
            if age > 2:
                note += " It is from a different power-on."
        else:
            note = "There is no cached reference."
        ttk.Label(frame, foreground="#777", text=note).pack(anchor="w")

        # Unticked every time the prompt opens; see the docstring.
        loaded = tk.BooleanVar(master=top, value=False)
        check = ttk.Checkbutton(frame, variable=loaded,
                                text="The film is in the transport")
        check.pack(anchor="w", pady=(12, 0))
        refused = ttk.Label(frame, foreground="#e0605a", wraplength=430,
                            justify="left")
        refused.pack(anchor="w")

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(14, 0))

        def choose(mode: str | None) -> None:
            if mode:
                # "reuse" measures after all when the file has gone since
                # this opened, so it is held to the same question then.
                measures = mode != "reuse" or not self._cached_reference()
                if measures and not loaded.get():
                    refused.configure(
                        text="Nothing started. Tick that the film is in the "
                             "transport first -- only you can see it.")
                    check.focus_set()
                    return
                self.v_shading.set(mode)
                self.on_calibrate(mode)          # closes this prompt
                return
            self._calibrate_prompt = None
            top.destroy()

        def anyway() -> None:
            self.session.accept_reference()
            self._say("scanning with the calibration in use; the next scan "
                      "goes on despite the gain it was taken at")
            self._calibrate_prompt = None
            top.destroy()

        top.protocol("WM_DELETE_WINDOW", lambda: choose(None))
        ttk.Button(buttons, text="Not now",
                   command=lambda: choose(None)).pack(side="left")
        if moved:
            # The cache is usually the very reference that no longer matches.
            ttk.Button(buttons, text="Use it anyway",
                       command=anyway).pack(side="right", padx=4)
        elif cached.exists():
            ttk.Button(buttons, text="Use the cached one",
                       command=lambda: choose("reuse")).pack(side="right", padx=4)
        start = ttk.Button(buttons, text="Calibrate now",
                           command=lambda: choose("measure"))
        start.pack(side="right")
        # The tick first, so the keyboard's way through is Space, then Return
        # -- and Return alone is refused rather than taken as the answer.
        check.focus_set()
        top.bind("<Return>", lambda _e: choose("measure"))
        top.bind("<Escape>", lambda _e: choose(None))
        top.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - top.winfo_width()) // 2
        y = parent.winfo_rooty() + 120
        top.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _refused_up_front(self, title: str, predpi: int, dpi: int | None = None,
                          infrared: bool = False, film: str = "",
                          parent=None) -> bool:
        """Refuse here what a roll's driver would refuse only part-way in.

        A roll goes to its first frame before the driver looks at what it
        was asked for, and a pass it cannot correct it finds out frame by
        frame: at 7200 dpi each frame was wound to, prescanned, held and
        metered before `scan` refused it, three frames and minutes of
        transport before the roll gave up. And the sheet's own options let
        infrared through on a film it is blind to, refused once the film had
        moved. The driver's own tests (`DirectScanner.correctable_at`,
        `supports_infrared`), asked before anything is handed over.
        """
        problems = []
        for value in dict.fromkeys(v for v in (predpi, dpi) if v):
            if not DirectScanner.correctable_at(value):
                best = max((d for d in DPI_LADDER
                            if DirectScanner.correctable_at(d)), default=None)
                problems.append(
                    f"A {value} dpi pass cannot be shading-corrected on this "
                    "scanner: its calibration gives no reference that wide, "
                    "at any resolution, so the driver refuses every frame."
                    + (f" Scan at {best} dpi or below." if best else ""))
        try:
            blind = bool(infrared and film and not supports_infrared(film))
        except ValueError:
            blind = False
        if blind:
            problems.append(
                f"Infrared is blind to {film}: the plane would hold the "
                "picture rather than the dust, and the driver refuses it. "
                "Untick infrared, or change the film.")
        if not problems:
            return False
        messagebox.showerror(title, "\n\n".join(problems) + "\n\nNothing has "
                             "moved.", parent=parent or self.root)
        return True

    def _working(self) -> bool:
        """Whether the scanner has a job, running or handed over and not begun.

        A calibration handed over and not begun is not one: a pass pressed
        next is queued behind it on purpose, rather than asked to calibrate
        a second time.

        Asked of the session (`ScanSession.outstanding`), which holds every
        job from its hand-over to its end and lets go of all of them as it
        closes. The window kept a copy from the session's events, a tick
        behind it, and each way that copy and the session disagreed was a
        bug: a second pass queued in that tick, a job counted for good after
        the scanner had gone. `busy` stays beside it, so a job that has
        ended but whose end the window has not read yet still counts.
        """
        return self.busy or any(not isinstance(job, Calibrate)
                                for job in self.session.outstanding())

    def _hand_over(self, job) -> None:
        """Hand any job to the session, and be working from now.

        Every job, moves and calibrations as well as passes; the session
        counts each until it is over (`_working`). And Stop is offered from
        now, not from the worker's report of the start a tick later: a stop
        pressed in between was not there to press.
        """
        self.session.submit(job)
        if self.session.outstanding() and not self.session.dead:
            self.b_stop.configure(state="normal")
        if isinstance(job, Roll):
            self._aim_from = None                # a roll moves the film

    def _scannerless(self) -> str:
        """Why nothing would take a job handed over now, or "" if something would.

        The scanner not found at launch, or the session closed or aborted
        since: nothing reads a job handed over then. A pass handed over once
        never said it had started, and the window counted it as about to for
        good -- every Open, Rename, Delete and Calibrate after it answered
        that the scanner was working, where browsing and exporting rolls
        with the scanner off is ordinary use of this window. The session
        holds no job once closed now (`ScanSession.outstanding`); this says
        why the press did nothing.
        """
        if getattr(self.session, "dead", False):
            return ("There is no scanner to do that now: it was force-aborted. "
                    "Power-cycle it at its own switch, then start this window "
                    "again.")
        if self._session_closed:
            return ("There is no scanner to do that now: the session that "
                    "drives it has closed. Start this window again once the "
                    "scanner is connected and switched on.")
        return ""

    def _no_scanner(self, title: str, parent=None) -> bool:
        """True, having said why, when there is nothing to take a job."""
        why = self._scannerless()
        if why:
            messagebox.showinfo(title, why + "\n\nRolls can still be opened, "
                                "looked at and exported.",
                                parent=parent or self.root)
        return bool(why)

    def on_prescan(self) -> None:
        # The buttons grey only once the worker reports the job it took, so a
        # double press reached here twice and queued two passes.
        if self._working():
            return
        if self._no_scanner("Prescan"):
            return
        if self._calibration_missing():
            return
        dpi = self._prescan_dpi()
        if dpi is None:
            return
        self._hand_over(Prescan(resolution=dpi, film=self.v_film.get(),
                                notes=self._notes(), tags=self._tags()))

    def on_scan(self) -> None:
        if self._working():                     # see `on_prescan`
            return
        if self._no_scanner("Scan"):
            return
        if self._calibration_missing():
            return
        dpi, exposure = self._dpi(), self._exposure()
        if dpi is None or exposure is None:
            return
        self._pin_arrangement()
        self._hand_over(Scan(
            resolution=dpi, infrared=self.v_ir.get(),
            fast_infrared=self.v_fast_ir.get(), film=self.v_film.get(),
            auto_exposure=self.v_expmode.get() == "auto",
            exposure_scale=exposure, mono=self.v_mono.get(),
            mono_channel=self.v_mono_channel.get(),
            notes=self._notes(), tags=self._tags(),
        ))

    def _pin_arrangement(self) -> None:
        """File the next pass the way the one on screen is shown.

        The session carried the last arrangement set anywhere, which drifts:
        frame the picture, turn it, frame a second picture and turn that one
        differently, then scan the first -- and the file came out the way the
        *second* was left. The pass on screen is the one being scanned, so it
        is the one that decides, and the preview and the file cannot then
        disagree about a photograph.
        """
        if self.current is not None:
            self.session.arrange(self.current.rotation, self.current.flipped)

    def on_roll(self, *, opened: Path | None = None,
                restored: str = "") -> None:
        """The Roll button: a walk or a roll, placed (`roll_target`), asked
        about once, and handed over.

        ``opened`` is a strip this press has just opened -- one whose walk
        stopped, for a walk, or the one "finish strip N" names, for a roll --
        and ``restored`` what opening it put back into the controls, said
        (`controls_said`). The press is then asked again from the top, so
        everything below reads the strip's settings, not those it was
        pressed with.
        """
        # The key reaches here as well as the button, and only the button is
        # greyed while the scanner works: a second roll was queued behind the
        # first, and a dry run reset the walk still being read.
        if self._working():
            self._say("the scanner is working -- a roll starts once it has "
                      "finished")
            return
        # Before anything below: a walk forgets the survey and starts reading
        # one, and with nothing to take it, it would read nothing for good.
        if self._no_scanner("Scan roll"):
            return
        if self._calibration_missing():
            return
        dry = self.v_dryrun.get()
        # Where it goes, decided before anything is asked so the question can
        # say it (`roll_target`): a new roll, the next strip of the roll the
        # box names, a strip of it the window has open, or a roll from before
        # strips resumed -- and what is in that folder already.
        typed = self._typed_roll_name(fresh=dry)
        if typed is None:
            return
        try:
            target = roll_target(self.session.rolls, typed,
                                 own=self._roll_named, dry=dry,
                                 current=opened or self._current_strip(),
                                 library_root=self.session.root)
        except ValueError as exc:
            messagebox.showerror("Scan roll", f"{exc}\n\nNothing has moved.")
            return
        # Strip L's walk did not reach its end and no sheet is open: it is
        # opened first, as Rolls ... opens it, and the question offers to add
        # to it before the next strip is numbered after it. Before any
        # control is read: opening it puts its own settings back into them,
        # and read before, the walk went at the resolution the window was
        # pressed at with the film the strip was walked with, which no
        # question said. His first and last frame stay: they say where on
        # the strip this press walks, and the question says how that meets
        # the strip's walk.
        again = target.offer.again if target.offer is not None else None
        if (dry and opened is None and again is not None
                and not self._sheet_to_keep() and target.kind == "next strip"):
            stopped = strip_folder(target.roll, again.strip)
            if stopped is not None:
                reopened = self._open_quietly(stopped, keep_range=True)
                if reopened is not None:
                    self.on_roll(opened=stopped, restored=reopened)
                    return
        dpi, predpi = self._dpi(), self._prescan_dpi()
        if dpi is None or predpi is None:
            return
        # A walk scans nothing but its prescans.
        if self._refused_up_front(
                "Scan roll", predpi, None if dry else dpi,
                infrared=self.v_ir.get() and not dry,
                film=self.v_film.get()):
            return
        # A shape check only. Whether a strip has that frame is the seek's to
        # say, and it refuses before it reads or moves anything -- one place
        # for the refusal, the backend, where the failed-job path shows it.
        try:
            start_at, frames = frame_range(self.v_startat.get(),
                                           self.v_last.get())
        except ValueError as exc:
            messagebox.showerror("Roll", str(exc))
            return
        # "Aim each frame" at a prescan resolution the edge reader cannot
        # read aims nothing -- every frame is refused and left as it came --
        # while the roll looks centred. `tools/scan_roll.py` refuses
        # `--correct` there, and this said the same only as a warning under
        # one OK. Refused as the tool refuses; the dry-run form still warns.
        unread = frame_edges.unread_at(predpi, self.v_film.get())
        if unread and self.v_correct.get():
            messagebox.showerror(
                "Roll", f"'aim each frame' with a {predpi} dpi prescan: "
                f"{unread}")
            return
        per = (23.0 if dry else
               estimate_seconds(dpi, self.v_ir.get(),
                                self.v_fast_ir.get()) + 70)
        # Said here, before the walk, and not only frame by frame after it: at
        # a prescan resolution the frame-edge detector cannot read, every
        # frame is refused, the sheet's light still goes green and "correct"
        # leaves each frame as it came -- a roll that looks centred and is
        # not. A warning rather than a refusal for a walk or the aim's dry
        # run: the walk's prescans are still a survey of the strip, and that
        # may be what he wants from it. The aim itself is refused above.
        unread = (frame_edges.unread_at(predpi, self.v_film.get())
                  if dry or self.v_correct.get() or self.v_correct_dry.get()
                  else None)

        def head(start_at: int, frames: int | None,
                 only: tuple[int, ...] | None = None,
                 rest_from: int | None = None) -> str:
            """The question's opening, for the frames a job takes."""
            return self._roll_head(restored, dry, dpi, per, start_at, frames,
                                   only, rest_from, unread)

        # The range as pressed, for a walk and for the questions a roll asks
        # before its frames are settled; a roll's last question says the
        # frames it takes (`_roll_into`).
        question = head(start_at, frames)
        only: tuple[int, ...] | None = None
        rest_from: int | None = None
        keep = False
        if dry:
            # One question either way: `on_roll` is the only confirmation the
            # `roll` key gets, and asking twice trains the habit of dismissing
            # both.
            if self._sheet_to_keep():
                answer = self._ask_keep_sheet(question, start_at, frames,
                                              predpi, target)
                if answer is None:
                    return
                keep = answer
            elif not messagebox.askokcancel(
                    "Scan roll", question + target.note + "\n\nStart?"):
                return
        else:
            chosen = self._roll_into(
                question, target, start_at, frames,
                reopened=opened is not None,
                now={"resolution": dpi, "infrared": self.v_ir.get(),
                     "film": self.v_film.get(), "meter": self.v_meter.get(),
                     "fast_infrared": self.v_fast_ir.get()},
                head=head)
            if isinstance(chosen, Reopened):
                # "Finish strip N": it is open now with its own settings back,
                # and the press is asked again with them -- the question
                # above said the window's, which are not what it would scan.
                self.on_roll(opened=chosen.folder, restored=chosen.restored)
                return
            if chosen is None:
                return
            # Which frames, as the question said them: the frames left, and
            # where it goes on to the end of the strip after them.
            target, finish = chosen
            only, rest_from = finish.only, finish.rest_from
            start_at, frames = finish.start_at, finish.frames
        if keep:
            # The sheet's own folder -- under this session's rolls, so a walk
            # added to a roll opened from elsewhere (`--open-roll` under
            # `--demo`) is not written back into it; see `_roll_folder`. And
            # that walk goes with it, or this one would be added to nothing.
            # A strip from elsewhere is not carried here at all (P18-d).
            try:
                folder = self._roll_folder()
            except ForeignStrip as exc:
                messagebox.showinfo("Scan roll", f"{exc}\n\nNothing has "
                                    "moved.")
                return
            try:
                carried = carry_walk(self._sheet_roll, folder,
                                     rolls=self.session.rolls, who=self._who)
            except (OSError, ValueError) as exc:
                messagebox.showerror(
                    "Scan roll",
                    f"The walk in {self._sheet_roll} could not be copied into "
                    f"{folder}, so this walk could not be added to it: {exc}"
                    "\n\nNothing has moved.")
                return
            if carried:
                self._say(f"copied the walk in {self._sheet_roll} into "
                          f"{folder}, which this walk adds to; the original "
                          "is left as it was")
        else:
            folder = target.folder
            self._show_roll_name(target.roll.name, ours=target.ours,
                                 folder=target.roll)
        if not dry:
            # A roll from this button carries no per-frame turns: its files
            # follow the session's arrangement. Turns a commissioned roll or
            # the sheet left against frame numbers would otherwise be put on
            # this roll's frames of the same number -- on screen and in Save
            # As, but not in the files it writes.
            self.orientations = {key: turn for key, turn
                                 in self.orientations.items()
                                 if key[0] != "frame"}
        if dry:
            # The sheet open now belongs to the walk before this one. Closed
            # here, keeping what was decided in it under its own roll, before
            # anything below forgets that walk or files more frames into it.
            # Left open, the end of this walk raised it again showing the old
            # frames, and closing it filed them under the new roll.
            self._close_sheet()
            self._survey_film = self.v_film.get()
        if dry and keep:
            # The same strip, further along: what the sheet holds stays, and
            # this walk's frames are added to it -- on screen here, on disk by
            # the session (`Roll.extend_walk`), and in the frame-edge reader,
            # which reads the old frames again against the new ones.
            self.sheet_state = self._recall_sheet_state()
            self._kept_walk = {int(r.number) for r in self.survey if r.number}
            self.edge_watch.extend(
                None if frames is None else
                len(self._kept_walk | set(range(start_at, start_at + frames))))
        elif dry:
            # Only cleared here, so a survey outlives the window that showed it
            # and the sheet can be opened again without walking the strip twice.
            self.survey = []
            # A fresh strip has no arrangements yet, and the last one's would
            # be applied to whatever pictures happen to land on the same frame
            # numbers -- a different film, shown and written sideways. The
            # positions go too: the film has moved, so they name nothing now.
            self.orientations = {}
            # And the sheet's own copy of all of that, for the same reason:
            # ticks, positions and turns are keyed by frame number, and frame
            # numbers on a new strip name different pictures.
            self.sheet_state = {}
            self._sheet_done = set()
            self._sheet_holds = {}
            # Set again when the walk finishes and the folder is known. Cleared
            # here so a walk that fails partway cannot leave the next set of
            # decisions filed against the roll before it.
            self._sheet_roll = None
            self._kept_walk = set()
            self.edge_watch.begin(self._survey_film, expected=frames)
        if dry:
            self._rewalked, self._dropped = set(), set()
            self._surveying = True
            self._survey_predpi = predpi
        # Starts the whole-roll estimate at the same rough figure the dialog
        # above just showed, so the number on screen does not jump the moment
        # scanning begins. `frames` is already "how many this run will do",
        # counted from `start_at` rather than added on top of it.
        # Refused above while a job runs or waits to start, which is what keeps
        # this from racing one -- the key reaches here as well as the button.
        self._roll_wall_start = time.monotonic()
        self._roll_seeking = True
        self._roll_dry = dry
        # Open where it goes on to the end of the strip after the frames left.
        self._roll_frames_total = (None if rest_from is not None
                                   else frames if only is None else len(only))
        self._roll_frames_done = 0
        self._roll_seconds_per_frame = per
        self._update_roll_eta()
        if unread:
            # In the log too, where a frame's "refused" is read afterwards.
            self._say(unread)
        # A walk of a new strip after the roll's first: its first frame is
        # held up against the strip before it's as it arrives, to say so
        # where the strip in the transport was not swapped.
        plan = target.plan
        self._same_strip_from = (
            strip_folder(target.roll, plan.number - 1)
            if dry and not keep and plan is not None and plan.number >= 2
            and not plan.reuse else None)
        self._hand_over(Roll(
            frames=frames, start_at=start_at, resolution=dpi,
            prescan_resolution=predpi, infrared=self.v_ir.get(),
            fast_infrared=self.v_fast_ir.get(),
            film=self.v_film.get(), meter=self.v_meter.get(), dry_run=dry,
            correct=self.v_correct.get(),
            correct_dry_run=self.v_correct_dry.get(),
            mono=self.v_mono.get(),
            mono_channel=self.v_mono_channel.get(),
            # The folder decided above: a kept walk's is the one the sheet came
            # from, whatever the roll box or the date says now -- a walk
            # continued after midnight would otherwise start a new roll named
            # by the new day. The session labels the frames by the name that
            # folder already records.
            out=str(folder),
            # And what was typed for it, which the session records where the
            # folder had to be called something else -- a name cut to its
            # cap is said in the log. Not for a kept walk: its folder is the
            # sheet's, and its records already say what it was typed as. Nor
            # for a name the window chose: a dated one, or the `-2` a name
            # from before strips was moved on to.
            typed_name="" if keep or target.ours else typed,
            extend_walk=keep,
            # A strip the run claims once the film is placed, as the
            # question named it: the session refuses it before the seek
            # where the roll has moved on since (`StripMoved`).
            new_strip=target.new_strip and not keep,
            strip_plan=((target.plan.number, target.plan.first)
                        if target.plan is not None and not keep else None),
            only=only,
            # And on from there to the end of the strip, after them.
            rest_from=rest_from,
            notes=self._notes(), tags=self._tags(),
        ))
        self._offer_strip()

    def _roll_head(self, restored: str, dry: bool, dpi: int, per: float,
                   start_at: int, frames: int | None,
                   only: tuple[int, ...] | None = None,
                   rest_from: int | None = None,
                   unread: str | None = None) -> str:
        """The Roll button's question as it opens: what was put back
        (``restored``), the frames it walks or scans at what, where the film
        goes first, the time, and the frame-edge reader's warning.

        Said of the frames a job takes: from ``start_at`` -- ``frames`` of
        them or to the end of the strip -- or ``only`` those, then on from
        ``rest_from`` to the end. Built from the range pressed, a strip with
        1-3 done and pressed from 1 opened "Scan every frame from frame 1
        ... goes to frame 1 from there" over a job that starts at 4."""
        if only is None:
            what = range_words(start_at, frames)
        else:
            what = (f"{'frame' if len(only) == 1 else 'frames'} "
                    f"{number_spans(only)}")
            if rest_from is not None:
                what += (f", then every frame from frame {rest_from} to the "
                         "end of the strip")
        move, move_s = seek_note(self._transport, start_at)
        # No figure for the frames when the seek will refuse the first: the
        # line above says it costs nothing, and "Roughly 4m 31s" under it
        # was the time of frames nobody would scan.
        count = len(only) if only and rest_from is None else frames
        cost = (f"{roll_estimate(per, count, move_s)}\n\n"
                if plausible(start_at - 1) else "")
        return ((f"{restored}\n\n" if restored else "")
                + f"{'Walk' if dry else 'Scan'} {what}, at {dpi} dpi"
                f"{' with infrared' if self.v_ir.get() and not dry else ''}."
                f"\n\n{move}\n\n{cost}"
                + (f"{unread}\n\n" if unread else ""))

    def _sheet_to_keep(self) -> bool:
        """Whether there is a walked sheet a new walk could be added to."""
        return bool(self.survey) and self._sheet_roll is not None

    def _ask_keep_sheet(self, question: str, start_at: int,
                        frames: int | None, predpi: int,
                        target: RollTarget | str = "") -> bool | None:
        """Ask whether this walk adds to the sheet there is: True, False or None.

        None is Cancel. A walk of 1 to 10 on a strip of 12 left two frames
        nobody has seen, and walking them used to throw the first ten away --
        ticks, positions and turns with them, on screen and in `survey.json`.

        Refused, with the reason, when the prescan resolution or the film
        differs from the walk being kept: those frames would be references at
        another resolution, or read by the detector as another film, beside
        frames that are not.

        ``target`` is where a walk not added to it goes (`roll_target`) --
        or a note saying so -- said beside the answer that would put it
        there. For a strip of a roll of strips the answers name the strip
        and the next one with its numbers, and the default button follows
        the strip: No -- the next strip -- after a walk to its end, Yes --
        add to it -- after one that stopped. The meanings of Yes and No
        never change.
        """
        where = target.note if isinstance(target, RollTarget) else target
        walked = [int(r.number) for r in self.survey if r.number]
        strip = place_of(self._sheet_roll, strict=False)
        if strip is not None:
            # Up to where "Strip ends here" put its end: the stub past it is
            # no frame of the roll, and counted, the question said "frames
            # 7-12" beside a next strip numbered from 12 (the 2026-10-12 tour).
            shown = [n for n in (shown_number(strip, r.number,
                                              getattr(r, "roll_number", 0))
                                 for r in self.survey
                                 if r.number
                                 and not strip.past_its_end(r.number))
                     if n is not None]
            have = (f"The contact sheet has frames {number_spans(shown)} of "
                    f"{strip.label}, strip {strip.strip}.")
        else:
            have = (f"The contact sheet has frames {number_spans(walked)} of "
                    f"{Path(self._sheet_roll).name}.")
        differ = []
        try:
            kept_dpi = int(self._survey_predpi) if self._survey_predpi else None
        except (TypeError, ValueError):
            kept_dpi = None
        if kept_dpi is not None and kept_dpi != predpi:
            differ.append(f"prescans at {kept_dpi} dpi, and this is set to "
                          f"{predpi}")
        film = self.v_film.get()
        if self._survey_film and self._survey_film != film:
            differ.append(f"{self._survey_film} film, and this is set to {film}")
        if differ:
            started = messagebox.askokcancel(
                "Scan roll",
                question + have + " It was walked with "
                + " and with ".join(differ) + ", so this walk cannot be added "
                "to it. Set them back to add to it.\n\n"
                "OK starts a new sheet; the old one stays under Rolls ..."
                + (f"\n\n{where}" if where else ""))
            return False if started else None
        again = rewalked(walked, start_at, frames)
        note = ""
        if again:
            # By their numbers across the roll, as the sheet's ticks read --
            # the places alone said strip 1's frames of strip 2 -- and of
            # Yes alone: No walks nothing on the sheet again.
            one = len(again) == 1
            which = (f"{'frame' if one else 'frames'} {spans_said(strip, again)}"
                     f" {'is' if one else 'are'}")
            note = (f"\n\nWith Yes, {which} on it already: walked again, the "
                    "new prescans replace the old, and a position set on "
                    f"{'it' if one else 'them'} is dropped -- it was measured "
                    "from the old one.")
            # P18-b: an old prescan no library entry holds is not replaced.
            only = only_copies_in(self._sheet_roll, again)
            if only:
                note += (f" The old prescan of {spans_said(strip, only)} "
                         "exists nowhere else, so it is kept beside the new "
                         "one.")
        if strip is None:
            return messagebox.askyesnocancel(
                "Scan roll",
                question + have + " Keep it?\n\n"
                "Yes -- add what this walk finds to it. Its ticks, positions "
                "and turns stay.\n"
                "No -- start a new sheet. The old one stays under Rolls ...\n"
                "Cancel -- nothing moves." + note
                + (f"\n\nA new sheet: {where}" if where else ""))
        # A strip of a roll of strips: the same two answers, said of the
        # strip, and the default by whether its walk reached its end.
        try:
            count = strip_count(self._sheet_roll, self.session.live_manifest)
        except ValueError:
            count = None
        whole = count is None or count.complete or strip.last is not None
        default = messagebox.NO if whole else messagebox.YES
        plan = target.plan if isinstance(target, RollTarget) else None
        if (plan is not None and isinstance(target, RollTarget)
                and target.kind == "next strip"
                and _folder_key(target.roll) == _folder_key(strip.roll_folder)):
            # The strip the next one follows is the roll's last, which need
            # not be the sheet's: Rolls ... opens any of them. Said of the
            # sheet's, strip 5 was to go in "the way strip 2 went in", and
            # the default walked it past a strip 4 that had stopped.
            prior, last = plan.number - 1, plan.closes
            if prior == strip.strip:
                had = (f"strip {strip.strip} has {count.count}, {count.how}"
                       if count is not None and count.complete
                       else f"strip {strip.strip}'s {count.how}"
                       if count is not None else f"strip {strip.strip}")
            elif last is not None:
                had = (f"strip {last.strip} has {last.count}, {last.how}"
                       if last.complete else f"strip {last.strip}'s {last.how}")
            else:
                had = ""
            no = (f"No -- walk the next strip: strip {plan.number} of "
                  f"{plan.label}, numbered from {plan.first}"
                  + (f" ({had})" if had else "") + "."
                  + (f" Put strip {plan.number} in the way strip {prior} went "
                     "in." if prior >= 1 else ""))
            if last is not None and not last.complete:
                no += " " + closing_said(last, plan)
            for warning in plan.warnings:
                note += f"\n\n{warning}"
            stopped = target.offer.again if target.offer is not None else None
            if stopped is not None and stopped.strip != strip.strip:
                # Neither answer adds to it, so neither is the default.
                note += (f"\n\nStrip {stopped.strip} is the roll's last, and "
                         f"its {stopped.how}: to add to it instead, Cancel "
                         f"and open strip {stopped.strip} under Rolls ...")
                default = messagebox.CANCEL
            where = ""
        else:
            no = "No -- start a new sheet. The old one stays under Rolls ..."
            # Not the next strip of the sheet's roll: New roll was pressed,
            # or another roll's name typed. He asked for somewhere else, so
            # adding to the sheet's strip is never the default here -- it
            # was, after a walk that stopped, and the new film's prescans
            # replaced the strip's. Where that roll's own last strip
            # stopped, No would close it short, so neither answer is the
            # default, as for an earlier strip of the sheet's own roll.
            if isinstance(target, RollTarget):
                other = (target.offer.again if target.offer is not None
                         else None)
                default = messagebox.NO
                if other is not None:
                    note += (f"\n\nStrip {other.strip} of {target.roll.name} "
                             f"is that roll's last, and its {other.how}: to "
                             "add to it instead, Cancel and open it under "
                             "Rolls ...")
                    default = messagebox.CANCEL
        if (count is not None and not whole and count.count
                and start_at <= count.count):
            # "First frame" counts places; the frames walked again are said
            # by their numbers.
            one = start_at == count.count
            note += (f"\n\nTo add to strip {strip.strip}: set first frame to "
                     f"{count.count + 1} to walk on from there; from "
                     f"{start_at}, {'frame' if one else 'frames'} "
                     f"{spans_said(strip, range(start_at, count.count + 1))} "
                     f"{'is' if one else 'are'} walked again and "
                     f"{'replaces its prescan' if one else 'replace their prescans'}.")
        # Its numbers fixed, a place past them takes the number after the
        # roll's last, for good -- a walk's prescan too.
        extras = extras_said(strip, start_at, frames,
                             live=self.session.live_manifest)
        return messagebox.askyesnocancel(
            "Scan roll",
            question + have + " Keep it?\n\n"
            f"Yes -- add what this walk finds to strip {strip.strip} (the same "
            "strip, further along). Its ticks, positions and turns stay."
            + (f" {extras}" if extras else "") + "\n"
            + no + "\n"
            "Cancel -- nothing moves." + note
            + (f"\n\nA new sheet: {where}" if where else ""),
            default=default)

    def _typed_roll_name(self, fresh: bool) -> str | None:
        """The roll box as he typed it, "" for a roll that wants a new name,
        or None -- having said why -- where nothing may go where it says.

        What it names is `roll_target`'s to place: the window's own name for
        a roll of strips is the roll its next walk continues, and for a roll
        from before strips a fresh walk takes a roll of its own, as before.

        A name the window put there for a roll it showed from outside this
        window's rolls (`--open-roll`) names nothing here, and a folder of
        that name here may be another roll altogether (P18-d): a fresh walk
        takes a new dated roll, and a plain roll is refused, with directions
        -- it goes into a folder here, never back into the one shown.
        """
        typed = self.fields["roll"].get().strip()
        named = self.__dict__.get("_roll_named_folder")
        if (typed and typed == self._roll_named and named is not None
                and self._outside(named)):
            if fresh:
                return ""
            messagebox.showinfo("Scan roll", self._foreign_said(
                self._loaded_roll or self._sheet_roll or named))
            return None
        return typed

    def _next_roll_folder(self, fresh: bool) -> Path:
        """Where a roll from the Roll button would go (`roll_target`): a
        walk (``fresh``) or a plain roll, the folder its `Roll.out` names."""
        typed = self.fields["roll"].get().strip()
        try:
            return roll_target(self.session.rolls, typed, own=self._roll_named,
                               dry=fresh, current=self._current_strip(),
                               library_root=self.session.root).folder
        except ValueError:
            return roll_dir(self.session.rolls, typed)

    def _show_roll_name(self, name: str, ours: bool = True,
                        folder=None) -> None:
        """Put the roll's name in the roll box, so the operator sees it.

        ``ours`` says the window chose it -- a new roll's name, a reopened
        roll's folder -- rather than echoing back one he typed; see
        `_typed_roll_name`. ``folder`` is the roll folder it names, by which
        a roll shown from outside this window's rolls is known (P18-d).
        """
        if self.fields["roll"].get().strip() != name:
            self.fields["roll"].set(name)
        if ours:
            self._roll_named = name
            self._roll_named_folder = (Path(folder) if folder is not None
                                       else None)
        self._offer_strip()

    def _current_strip(self) -> Path | None:
        """The strip of a roll of strips this window has open -- the sheet's,
        or the one opened from Rolls ... -- or None: a plain roll with its
        roll's name in the box goes into it (`roll_target`).

        Of two, the one opened or walked last (`_roll_focus`): a strip
        opened with no walk leaves the sheet on the strip before it, and
        Roll went into the sheet's -- strip 2's film filed as strip 1's
        frame 3 -- or offered a new strip, never the one just opened."""
        focus = self.__dict__.get("_roll_focus")
        shown = [Path(f) for f in (self._sheet_roll, self._loaded_roll)
                 if f is not None]
        first = ([focus] if focus is not None and any(
            _folder_key(focus) == _folder_key(f) for f in shown) else [])
        for folder in first + shown:
            if roll_of(folder) not in (None, folder):
                return folder
        return None

    def _roll_controls(self) -> dict:
        """The controls a roll's settings put back (`ROLL_CONTROLS`), as
        they read now."""
        return {name: getattr(self, f"v_{name}").get()
                for name in ROLL_CONTROLS}

    def _open_quietly(self, folder: Path, keep_range: bool = False
                      ) -> str | None:
        """Open a strip for the Roll button (`open_roll`, without its box)
        and say what that put back into the controls (`controls_said`); None
        where it would not open, having said why.

        ``keep_range`` keeps the first and last frame he pressed with, as a
        walk does: they say where on the strip this press walks. A roll
        takes the strip's (`open_roll`), which point one scanned without a
        walk at its first frame left -- what "finish strip N" means.
        """
        before = self._roll_controls()
        if not self.open_roll(folder, quiet=True):
            return None
        if keep_range:
            self.v_startat.set(before["startat"])
            self.v_last.set(before["last"])
        place = place_of(folder, strict=False)
        return controls_said(
            f"Strip {place.strip} of {place.label}" if place is not None
            else Path(folder).name, before, self._roll_controls())

    def _scan_when_rolled(self) -> None:
        """Untick "dry run" for a roll opened to be finished, and say so.

        Its question says "press Roll" and the frames left are scanned; with
        "dry run" left ticked from an earlier walk, Roll walked instead -- the
        2026-10-12 tour walked a stopped roll's frames into the sheet that was
        open and wrote over that walk's prescans. "Dry run" was never one of
        the settings a reopened roll puts back (`RESTORABLE`), so nothing else
        set it. Not for the quiet open Roll makes of a strip it is about to
        go into: there the box was ticked or not for that press.
        """
        if self.v_dryrun.get():
            self.v_dryrun.set(False)
            self._say("\"dry run\" unticked: this roll is opened to be "
                      "finished, and Roll scans what is left")

    def _outside(self, folder) -> bool:
        """Whether ``folder`` -- a roll, or a strip by its roll -- lies
        outside this window's rolls: shown here by `--open-roll`."""
        lock = library.lock_folder(folder)
        rolls = self.session.rolls
        return (_folder_key(lock) == _folder_key(rolls)
                or not storage.inside(lock, rolls))

    def _foreign_said(self, folder) -> str:
        """Why nothing from this window goes into ``folder``, shown from
        outside its rolls, and what does (P18-d)."""
        folder = Path(folder)
        rolls = self.session.rolls
        strip = place_of(folder, strict=False)
        if strip is not None:
            return (f"Strip {strip.strip} of {strip.label} was opened from "
                    f"{folder}, outside this window's rolls ({rolls}). A strip "
                    "is scanned, walked and added to in its own roll and no "
                    "other, so nothing from here goes into it. Open the window "
                    "on its rolls folder (--rolls "
                    f"{_quoted_path(strip.roll_folder.parent)}).")
        return (f"{folder.name} was opened from {folder.parent}, outside this "
                f"window's rolls ({rolls}). A roll from here goes into a "
                "folder here, never back into it. Scan its frames from its "
                "contact sheet (\"Scan chosen frames\"), which copies its walk "
                "across and skips what is done; to finish a roll that has no "
                "walk, open the window on its own rolls folder (--rolls "
                f"{_quoted_path(folder.parent)}).")

    def _roll_into(self, question: str, target: RollTarget, start_at: int,
                   frames: int | None, reopened: bool = False,
                   now: dict | None = None,
                   head: Callable[..., str] | None = None
                   ) -> tuple[RollTarget, Finish] | Reopened | None:
        """Ask about a plain roll from the Roll button, and settle where it
        goes and which frames it takes: ``(target, finish)``; `Reopened`
        where a strip was opened to be finished, and the press is to be
        asked again with its settings; or None where it was cancelled or
        refused, having said why.

        - Into a strip the window has open, or a roll from before strips it
          opened: the frames already done are passed over, as a reopened
          roll's always were, and to the end of the strip the rest no run
          reached is taken after the frames left (`_skip_done`). A strip
          with every frame done that was walked to its end offers the next
          strip instead.
        - The next strip of a roll whose last strip stopped short: three
          ways, Yes finishing that strip -- opened first, as Rolls ...
          opens it, and asked about again -- No the next strip, numbered
          after it, Cancel. Not once ``reopened``: that strip is open.
        - The next strip otherwise, or a new roll: scanned without walking
          it first, as the question says.

        ``now`` is the request -- resolution, infrared, film, metering --
        held to the settings the roll's frames were taken at, across the
        whole roll (`_held_to_roll`): a frame not done yet at other settings
        is refused before anything moves, and a retake of done frames named.

        ``head`` builds the question's opening for the frames a job takes
        (`_roll_head`): the last question says those, once they are settled,
        where ``question`` says the range pressed.
        """
        title = "Scan roll"
        plain = Finish(None, target.note, None, start_at, frames)
        again = target.offer.again if target.offer is not None else None
        if target.kind == "next strip" and again is not None and not reopened:
            stopped = strip_folder(target.roll, again.strip)
            plan = target.plan
            assert plan is not None
            # Yes asks again rather than starting: opening the strip puts its
            # own settings back, so what this question said of resolution and
            # infrared is not what it would scan, and which of its frames go
            # depends on the first and last frame it comes back with. It
            # promised the done ones were skipped, and with no last frame they
            # were scanned again, each new take replacing its file.
            answer = messagebox.askyesnocancel(
                title, question
                + f"Strip {again.strip} of {plan.label}'s {again.how}.\n\n"
                f"Yes -- finish strip {again.strip} ({again.numbers()} so "
                "far): it is opened first, with its own settings, and the "
                "question after this one says which of its frames are "
                "scanned.\n"
                f"No -- {next_strip_said(plan, dry=False)}\n"
                "Cancel -- nothing moves.", default=messagebox.YES)
            if answer is None:
                return None
            if not answer:
                # The next strip: every frame of it new, so held to the
                # roll's settings before anything moves.
                held = self._held_to_roll(target.folder, now, new=True)
                return None if held is None else (target, plain)
            if stopped is None:
                return None
            restored = self._open_quietly(stopped)
            return None if restored is None else Reopened(stopped, restored)
        finish = plain
        if target.kind in ("strip", "legacy"):
            skipped = self._skip_done(target, start_at, frames)
            if skipped == NEXT_STRIP:
                return self._next_after(question, target, now, frames)
            if not isinstance(skipped, Finish):
                return None
            finish = skipped
            if target.kind == "strip":
                # A strip whose numbers are fixed gives a frame past them the
                # number after the roll's last, for good: said before the run,
                # where the per-frame line comes once the number is used up --
                # the rest of the strip after the frames left among them.
                extras = extras_said(place_of(target.folder, strict=False),
                                     start_at, frames,
                                     live=self.session.live_manifest,
                                     scan=True)
                if extras:
                    finish = finish._replace(note=finish.note
                                             + f"\n\n{extras}")
        # Its opening said of the frames the job takes, settled now: the
        # first frame it goes to, the frames left and the rest, and their
        # time -- not the range pressed, which the note below contradicted.
        if head is not None:
            question = head(finish.start_at, finish.frames, finish.only,
                            finish.rest_from)
        held = self._held_to_roll(target.folder, now,
                                  new=self._takes_new(target, finish))
        if held is None:
            return None
        if not messagebox.askokcancel(title, question + finish.note + held
                                      + "\n\nStart?"):
            return None
        return target, finish

    def _takes_new(self, target: RollTarget, finish: Finish) -> bool:
        """Whether a roll would scan any frame its folder has not done: a
        new strip or roll always, a rest of the strip always, and otherwise
        a frame it asks for that is not done. Only frames all done are a
        retake."""
        if target.kind not in ("strip", "legacy") or finish.rest_from:
            return True
        asked = frames_asked(max(1, finish.start_at), finish.frames,
                             finish.only)
        if asked is None:
            return True
        done = done_in(target.folder, self.session.root,
                       live=self.session.live_manifest)
        return any(n not in done for n in asked)

    def _held_to_roll(self, folder, now: dict | None, new: bool,
                      title: str = "Scan roll", parent=None) -> str | None:
        """Hold a run into ``folder`` to the settings its roll's frames were
        taken at -- resolution, infrared, film, metering -- across every
        strip of a roll of strips (`session.roll_differs`; Stefan's calls,
        2026-10-10): "" where they agree; where they differ, None, refused
        and said, for a run with any ``new`` frame, and for a retake of done
        frames only the line its question says it with. The roll keeps its
        own settings after a retake (`ScanSession._roll`), so reopening it
        and `tools/scan_roll.py` go on holding it to them.

        Refused here, before anything moves, and the same with the scanner
        as in the demo: a window refusal, never a greyed control. The tool
        refuses every difference; a walk scans nothing and is never held."""
        if now is None:
            return ""
        try:
            differs = roll_differs(folder, now,
                                   live=self.session.live_manifest)
        except (OSError, ValueError):
            differs = []
        if not differs:
            return ""
        path, was, _how = differs[0]
        roll = roll_name_of(path.parent)
        if new:
            # What to set, and where -- not "open it from Rolls ...": that
            # opens a strip with its own settings, which for one only walked
            # are the walk's, and the same refusal came back.
            wanted = settings_to_set(was, now)
            where = ("on the contact sheet (\"Scan the chosen frames with\")"
                     if title == "Scan chosen frames" else "in the window")
            messagebox.showerror(
                title, f"Frames of {roll} were taken at "
                f"{taken_at(was, now)}; this asks for {taken_at(now, was)}. "
                "One film's frames are scanned alike: set "
                + (_and_joined(wanted) if wanted else "them as those were")
                + f" {where}, or scan into a new roll.\n\nNothing has moved.",
                parent=parent or self.root)
            return None
        return (f"\n\nTaken again at {taken_at(now, was)}; the roll's other "
                f"frames are {taken_at(was, now)}, and the roll keeps its "
                "settings.")

    def _skip_done(self, target: RollTarget, start_at: int,
                   frames: int | None) -> Finish | str | None:
        """A roll into a folder there is passes over what it has done: the
        frames it takes (`Finish`); `NEXT_STRIP` for a strip with every
        frame done that was walked to its end, whose plain roll is the next
        strip instead; or None, having said why, where every frame asked
        for is done.

        The Roll button carried only a first frame and a count, so with
        frames 1, 2, 4 and 5 of six done, "first frame 3" scanned 3 to 6
        and took 4 and 5 again, replacing their frameNN.tif. Only a roll the
        window has open -- reopened, or a strip walked here.

        To the end of the strip, with frames done ahead (Stefan's call,
        2026-10-10, "frames left, then the rest"): one job that scans the
        frames left up to the highest any run recorded, passing the done
        ones with the advance a sheet's unticked frames get, and then seeks
        on to the first frame never reached and scans from there to the end
        of the strip (`session.Roll.rest_from`). They were named and then
        scanned again, each new take replacing its file -- hours at 3600
        dpi. A frame still being filed counts as done, and is named. So
        does any frame recorded ahead, failed or done: frame 1 failed and
        nothing done went as a plain roll from frame 1, which the demo
        takes for a new strip in the transport.

        Frames are named by their numbers across the roll, as the browser
        and the sheet's commission name them (`spans_said`, `place_said`).
        """
        folder = target.folder
        where = target.note
        open_here = [f for f in ((self._loaded_roll,) if target.kind == "legacy"
                                 else (self._loaded_roll, self._sheet_roll))
                     if f is not None]
        if not any(_folder_key(folder) == _folder_key(f) for f in open_here):
            return Finish(None, where, None, start_at, frames)
        live = self.session.live_manifest
        done = done_in(folder, self.session.root, live=live)
        pending = pending_in(folder, live)
        strip = place_of(folder, strict=False)

        def said(passed, how: str) -> str:
            """The frames passed over, and those still being filed."""
            scanned = [n for n in passed if n not in pending]
            filing = [n for n in passed if n in pending]
            return ((f"\n\nAlready scanned, and {how}: "
                     f"{spans_said(strip, scanned)}." if scanned else "")
                    + (f"\n\nStill being filed: {spans_said(strip, filing)} "
                       "-- not taken again." if filing else ""))

        # The strip by its roll, never a bare strip-04 (`row_said`).
        of_strip = (folder.name if strip is None
                    else f"strip {strip.strip} of {strip.label}")
        filing = ", or still being filed" if pending else ""
        retake = ("\n\nTo take a frame again, tick it on the contact sheet "
                  "and press \"Scan chosen frames\".")

        def whole_strip() -> bool:
            """Whether the press asks for the whole strip from its start:
            frame 1 to the end of the strip, or a range reaching every place
            it has. Only that is the next strip's on a finished strip: a
            retake asked of the Roll button -- first and last frame 5 --
            became a new strip that started at its place 5, filing the
            frame under a number the question never said and using up the
            four before it with no picture."""
            if target.kind != "strip" or start_at != 1:
                return False
            if frames is None:
                return True
            try:
                return frames >= strip_count(folder, live).count
            except ValueError:
                return False

        if frames is not None:
            span = range(start_at, start_at + frames)
            skipped = [n for n in span if n in done]
            if skipped:
                only = tuple(n for n in span if n not in done)
                if not only:
                    if whole_strip() and self._strip_finished(folder):
                        return NEXT_STRIP
                    one = frames == 1
                    if strip is None:
                        asked = f"{range_words(start_at, frames)} of {of_strip}"
                    else:
                        # The strip once: "(frames 1-6 of strip 4)" names it
                        # where the numbers are not the places.
                        same = all(shown_number(strip, n) == n for n in span)
                        asked = (f"{'frame' if one else 'frames'} "
                                 f"{spans_said(strip, span)} of "
                                 + (of_strip if same else strip.label))
                    messagebox.showinfo(
                        "Scan roll", asked[0].upper() + asked[1:]
                        + f" {'is' if one else 'are'} scanned already{filing}."
                        + retake)
                    return None
                return Finish(only, where + said(skipped, "skipped"), None,
                              start_at, frames)
            return Finish(None, where, None, start_at, frames)
        if whole_strip() and self._strip_finished(folder):
            return NEXT_STRIP
        progress = held_now(Path(folder) / "roll.json", live)
        # Recorded ahead, done or not -- as `done_in`, only in the strip's
        # own numbering.
        ahead = ([n for n in recorded_numbers(progress) if n >= start_at]
                 if progress.get("numbering") == NUMBERING else [])
        if (not ahead and target.kind == "strip" and strip is not None
                and strip.ends_at is None):
            # Started past the end of a strip walked or scanned to its end:
            # there is no frame there to go to, and the film in the
            # transport may be the next strip by now. Past "Strip ends here"
            # the session refuses it, saying how to name the frames.
            try:
                count = strip_count(folder, live)
            except ValueError:
                count = None
            if count is not None and count.complete and start_at > count.count:
                messagebox.showinfo(
                    "Scan roll", f"{of_strip[0].upper() + of_strip[1:]} has "
                    f"{counted(count.count, 'frame')} ({count.numbers()}), "
                    f"{count.how}: first frame {start_at} is past its end."
                    + retake)
                return None
        if not ahead:
            # Nothing recorded ahead: from here to the end of the strip, as
            # it always did -- or to where "Strip ends here" put it, past
            # which a scan is refused (`session.past_strip_end`), as
            # `roll_left` bounds the branch below. Unbounded, it was asked
            # about and then failed red after Start. A start past that end
            # is the session's to refuse, in words, before anything moves.
            ends = strip.ends_at if strip is not None else None
            if ends is not None and start_at <= ends:
                return Finish(None, where + f"\n\nTo {place_said(strip, ends)}"
                              ", where the strip ends.", None, start_at,
                              ends - start_at + 1)
            return Finish(None, where, None, start_at, None)
        walk = held_now(Path(folder) / "survey.json", live)
        # Where the strip ends, where that is known and leaves no rest: by
        # its runs, its walk, or "Strip ends here" -- as the browser reads
        # it (`roll_left`). Read from roll.json alone, a strip walked to its
        # end read as having a rest past it, which Roll wound the film to.
        found = roll_left(walk if walk.get("frames") else progress, progress,
                          ends_at=strip.ends_at if strip is not None else None)
        end = found.strip_end if found.rest_from is None else None
        # The first frame no run reached.
        after = max(ahead) + 1
        left = [n for n in range(start_at, after) if n not in done]
        rest: int | None = after
        if end is not None:
            # The strip's end is known: the places up to it no run took are
            # frames left, and past it there is no rest.
            left += list(range(after, end))
            rest = None
        passed = [n for n in range(start_at, after) if n in done]
        note = where + said(passed, "passed over")
        if not left and rest is None:
            messagebox.showinfo(
                "Scan roll", f"Every frame of {of_strip} from "
                f"{place_said(strip, start_at)} to the end of the strip is "
                f"scanned already{filing}." + retake)
            return None
        if rest is not None:
            note += (f"\n\nThen on from {place_said(strip, rest)} to the end "
                     "of the strip, which no run reached -- one job, and one "
                     "Stop ends it.")
        if not left:
            return Finish(None, note, None, rest or start_at, None)
        return Finish(tuple(left), note, rest, left[0], None)

    def _strip_finished(self, folder) -> bool:
        """Whether a strip has every frame it wants scanned and was walked
        to its end -- one whose plain roll offers the next strip instead.

        Read as the rest of `_skip_done` reads it, through the session's
        manifests, a frame still being filed counted as done (`pending_in`):
        read from the disk, a strip whose roll had just ended still had its
        last frame left, and the press said every frame was scanned and did
        nothing. The walk over the roll and the strip's trim, as
        `roll_summary` takes them; a strip is always numbered by place."""
        live = self.session.live_manifest
        progress = held_now(Path(folder) / "roll.json", live)
        if not progress.get("frames"):
            return False
        walk = held_now(Path(folder) / "survey.json", live)
        place = place_of(folder, strict=False)
        left = roll_left(walk if walk.get("frames") else progress, progress,
                         self.session.root,
                         ends_at=place.ends_at if place is not None else None)
        pending = pending_in(folder, live)
        if (left.rest_from is not None
                or any(n not in pending for n in left.remaining)
                or not (left.done or pending)):
            return False
        try:
            return strip_count(folder, live).complete
        except ValueError:
            return False

    def _next_after(self, question: str, target: RollTarget,
                    now: dict | None = None, frames: int | None = None
                    ) -> tuple[RollTarget, Finish] | None:
        """A plain roll for a whole strip with nothing left: the next strip
        of its roll instead, from its first frame -- ``frames`` of it, or to
        its end -- scanned without walking it first, in one OK/Cancel
        question; or None, said, where it cannot be planned, or its settings
        are not the roll's (`_held_to_roll`)."""
        if target.kind != "strip":
            return None
        strip = place_of(target.folder, strict=False)
        try:
            following = _next_strip(target.roll, dry=False, ours=target.ours)
        except ValueError as exc:
            messagebox.showerror("Scan roll", f"{exc}\n\nNothing has moved.")
            return None
        if self._held_to_roll(following.folder, now, new=True) is None:
            return None
        which = (f"strip {strip.strip} of {strip.label}" if strip is not None
                 else target.folder.name)
        # Not flatly "scanned" while one is still with the writer.
        pending = sorted(pending_in(target.folder, self.session.live_manifest))
        filing = (f" ({'frame' if len(pending) == 1 else 'frames'} "
                  f"{spans_said(strip, pending)} still being filed)"
                  if pending else "")
        if not messagebox.askokcancel(
                "Scan roll", question + f"Every frame of {which} is scanned"
                f"{filing}, and it was walked to its end.\n\n"
                + following.note + "\n\nStart?"):
            return None
        # From the new strip's first frame: only a press for its strip from
        # frame 1 comes here (`_skip_done`), and a start carried over began
        # the new strip part way, at numbers the question never said.
        return following, Finish(None, following.note, None, 1, frames)

    # -- the Roll panel's line, New roll and Continue -----------------------

    def _offer_strip(self) -> None:
        """Say again, shortly, what the next walk from the Roll button is:
        once for a burst of typing in the roll box, `OFFER_MS` after it."""
        if self._offer_job is not None:
            try:
                self.root.after_cancel(self._offer_job)
            except (tk.TclError, ValueError):
                pass
            self._pending.discard(self._offer_job)
        self._offer_job = self._later(OFFER_MS, self._offer_strip_now)

    def _offer_strip_now(self) -> None:
        """The Roll panel's line (`strip_line`), and Continue where it is
        offered. A few small reads -- a roll's index, its last strip's
        records -- on this thread, as every other look at a roll is."""
        self._offer_job = None
        if not self._alive:
            return
        last = self.remembered.get("last_roll")
        typed = self.fields["roll"].get().strip()
        named = self._roll_named_folder
        if (typed and typed == self._roll_named and named is not None
                and self._outside(named)):
            # A roll shown from outside this window's rolls names nothing
            # here: a walk starts a new roll (`_typed_roll_name`).
            typed = ""
        try:
            line, offered = strip_line(
                self.session.rolls, typed, own=self._roll_named,
                last_roll=last if isinstance(last, str) and last else None)
        except Exception as exc:                          # noqa: BLE001
            line, offered = (f"what the next walk is cannot be worked out: "
                             f"{exc}"), None
        self.v_strip_note.set(line)
        self._continue_roll = offered
        try:
            if offered:
                self.b_continue.configure(text=f"Continue {offered}")
                if not self.b_continue.winfo_ismapped():
                    self.b_continue.pack(side="left", padx=(6, 0))
            else:
                self.b_continue.pack_forget()
        except tk.TclError:
            pass

    def on_new_roll(self) -> None:
        """Empty the roll box: the next walk starts a new roll, by a dated
        name, rather than the next strip of the roll the box named. Typing
        another name does the same. Nothing on disk changes."""
        self.fields["roll"].set("")
        self._roll_named = None
        self._roll_named_folder = None
        self._say("the next walk starts a new roll")
        self._offer_strip()

    def on_continue_roll(self) -> None:
        """Put the roll of strips walked or claimed last back in the roll
        box, as the window's own: the next walk is its next strip. Offered
        after a restart, where the box is empty by design."""
        name = self._continue_roll
        if not name:
            return
        folder = Path(self.session.rolls) / name
        self._show_roll_name(name, ours=True, folder=folder)
        self._say(f"continuing {name}: the next walk under it is its next "
                  "strip")

    def _note_strip_roll(self, folder) -> None:
        """Remember ``folder``'s roll, where it is a roll of strips in this
        window's rolls, as the one Continue offers after a restart."""
        if folder is None:
            return
        try:
            roll = roll_of(folder)
        except OSError:
            return
        if roll is None or self._outside(roll):
            return
        if self.remembered.get("last_roll") != roll.name:
            self.remembered["last_roll"] = roll.name
            self._remember()

    def on_strip_end(self, place: int | None) -> None:
        """"Strip ends here" on a cell of the sheet, at that frame's
        ``place`` -- or, with None, "Count to the end of the strip" -- for
        the sheet's strip, its roll's open last one (`session.
        set_strip_end`): a stub at a strip's end is left out of the numbers
        before the next strip is numbered after them.

        Refused in words, never greyed: while the scanner works, which may
        be scanning that strip; for a strip shown from outside this
        window's rolls, whose roll is not this window's to write; and where
        the session refuses it -- a closed strip, a frame scanned past it,
        its roll held by another program."""
        title = ("Strip ends here" if place is not None
                 else "Count to the end of the strip")
        sheet = self.sheet if self.sheet is not None and self.sheet.alive() \
            else None
        parent = sheet.top if sheet is not None else self.root
        folder = self._sheet_roll
        if folder is None:
            return
        if self._working():
            messagebox.showinfo(
                title, "The scanner is working, and may be on this strip. "
                "Where a strip ends is set between jobs: try again once it "
                "has finished.\n\nNothing was changed.", parent=parent)
            return
        if self._outside(folder):
            messagebox.showinfo(title, self._foreign_said(folder)
                                + "\n\nNothing was changed.", parent=parent)
            return
        try:
            line = set_strip_end(folder, place, who=self._who,
                                 live=self.session.live_manifest)
        except ValueError as exc:
            messagebox.showinfo(title, f"{exc}.\n\nNothing was changed.",
                                parent=parent)
            return
        self._say(line)
        if sheet is not None:
            sheet.renumber(place_of(folder, strict=False))
        self._offer_strip()

    def _roll_folder(self, keep: bool = True) -> Path:
        """The folder the sheet's roll is scanned into, and its decisions filed in.

        The sheet's own -- its walk's, or the reopened roll's -- so the frames,
        `approved.json` and the walk are one folder. They were three: the
        session wrote to the roll box's name or the date, `approved.json` went
        to `_safe` of the box ("roll" when it was empty), and a reopened roll's
        name was never put back, so continuing it scanned somewhere else.

        Always under this session's `rolls`. A roll opened from elsewhere --
        `--open-roll` under `--demo` shows a real walk -- is scanned into the
        folder of the same name here, never back into the one it was read
        from. A walk added to it copies that walk across first (`carry_walk`).

        ``keep`` False only asks: a folder not chosen yet is worked out and not
        remembered, so reading the sheet's badges (`_holds_for_sheet`) never
        decides where a roll goes. Chosen as the roll was opened, the folder
        of its name free then would be kept once another roll had been
        written there, and this strip's frames and approvals added to that
        one's -- what the search for a free folder below exists to prevent.

        A strip of a roll of strips is its roll's, and is inside when its
        roll is. One from elsewhere is not carried here (P18-d): its numbers
        are its roll's index's, which a folder here has no copy of, so
        `ForeignStrip` is raised, saying where to open it -- except when
        ``keep`` is False, which only reads, and is answered with the strip.
        """
        rolls = Path(self.session.rolls)
        if self._sheet_roll is not None:
            sheet = Path(self._sheet_roll)
            lock = library.lock_folder(sheet)
            try:
                inside = lock.resolve().parent == rolls.resolve()
            except OSError:
                inside = False
            if inside:
                return sheet
            if lock != sheet:
                if not keep:
                    return sheet
                raise ForeignStrip(self._foreign_said(sheet))
            # Not simply the folder of the same name here: that may be another
            # roll altogether -- two rolls from different roots named by the
            # same date -- and `carry_walk` leaves a folder that has a walk
            # alone, so this strip's frames and approvals were added to that
            # one's. The first folder of that name, or `-2`, `-3`, ..., that
            # is free or already holds this walk; and the same one for the
            # rest of the session, when a walk added to it has made its
            # survey this one's no longer.
            key = _folder_key(sheet)
            chosen = self._carried.get(key)
            if chosen is None:
                chosen = base = roll_dir(rolls, sheet.name)
                n = 2
                while chosen.exists() and not same_walk(sheet, chosen):
                    chosen, n = base.with_name(f"{base.name}-{n}"), n + 1
                if keep:
                    self._carried[key] = chosen
            return chosen
        return self._next_roll_folder(fresh=False)

    def _close_sheet(self) -> None:
        """Close the contact sheet if it is open, keeping what was decided.

        The same way out `_ContactSheet._scan` takes: the big view first, then
        `_dismiss`, which is what files the sheet's decisions.
        """
        sheet = self.sheet
        if sheet is None or not sheet.alive():
            return
        adjuster = getattr(sheet, "_adjuster", None)
        if adjuster is not None and adjuster.alive():
            adjuster.top.destroy()
        sheet._dismiss()

    def on_contact_sheet(self) -> None:
        """The surveyed strip, all of it, with a tick against each picture."""
        if not self.survey:
            messagebox.showinfo(
                "Contact sheet",
                "Nothing has been walked yet.\n\nTick 'dry run' and press "
                "Scan roll. It prescans and advances only -- about 20 seconds "
                "a frame -- and opens the sheet when it reaches the end of the "
                "strip.")
            return
        # The sheet's strip is in front again: a plain Roll pressed while
        # looking at it finishes it (`_current_strip`).
        if self._sheet_roll is not None:
            self.__dict__["_roll_focus"] = Path(self._sheet_roll)
        if self.sheet is not None and self.sheet.alive():
            self.sheet.top.lift()
            self.sheet.top.focus_force()
            return
        kept = self._recall_sheet_state()
        # Said out loud, because a restored decision that is wrong is worse
        # than none: the operator has to be able to see that the sheet came
        # back holding something, and how much. His decisions, not every
        # entry kept: a straight frame and the detector's proposals are
        # recorded too (`sheet_counts`).
        restored = counts_said(sheet_counts(kept))

        self._say(f"contact sheet: {len(self.survey)} walked "
                  f"{[getattr(r, 'number', '?') for r in self.survey]}"
                  + (f" -- kept {restored}" if restored else ""))
        self._open_sheet(kept["offsets"], kept.get("sources"),
                         rotations=kept["rotations"], flips=kept["flips"],
                         ticks=kept["ticks"], options=kept["options"],
                         done=self._sheet_done, holds=self._holds_for_sheet())

    def _holds_for_sheet(self) -> dict[int, dict]:
        """How the sheet's roll held each frame it scanned, for its badges.

        What the reopened roll said, under what the roll.json of the folder
        its frames are scanned into says now (`holds_in`): a commission since
        it was opened has scanned frames there -- and, for a roll opened from
        outside this session's rolls, one in an earlier session may have.
        That folder is `_roll_folder`'s, the one `_scanned_in` asks about,
        worked out without being chosen. This read the sheet's own folder,
        which for a roll from outside is never scanned into, so a frame the
        roll left off its position was marked only in the list at the roll's
        end, shown once. One reading, however the sheet is opened.
        """
        if self._sheet_roll is None:
            return dict(self._sheet_holds)
        return holds_in(self._roll_folder(keep=False), self._sheet_holds)

    def _open_sheet(self, offsets: dict, sources, **cells) -> None:
        """The sheet, now, on whatever the frame-edge reader has so far.

        It does not wait for the reader: the positions it has not read yet
        arrive through `_edges_changed` while the sheet is open, and the light
        in the header says how far it has got. His own positions (``offsets``
        whose ``sources`` say he set them) stand whatever it reads.
        """
        progress = self.edge_watch.progress()
        kept, notes = _merge_kept({}, {}, offsets, sources)
        # A strip's place in its roll, for the numbers its cells show.
        numbers = (place_of(self._sheet_roll, strict=False)
                   if self._sheet_roll is not None else None)
        self.sheet = _ContactSheet(self, self.survey, offsets=kept,
                                   proposals=notes,
                                   readings=(progress.offsets, progress.notes),
                                   generation=progress.generation,
                                   numbers=numbers, **cells)

    def _per_frame_seconds(self, dpi=None, ir=None, fast_ir=None) -> float:
        """Roughly what one frame of the roll will cost, metering included.

        The contact sheet passes its own three, because that is where a roll
        is commissioned: the figure under its ticks has to be the cost of what
        its button will actually ask for, not of whatever the main window
        happens to be showing.
        """
        if dpi is None:
            try:
                dpi = int(self.v_dpi.get().strip())
            except ValueError:
                dpi = 1800
        return estimate_seconds(
            dpi,
            self.v_ir.get() if ir is None else ir,
            self.v_fast_ir.get() if fast_ir is None else fast_ir) + 70

    def on_reopen_survey(self) -> None:
        """The rolls on disk, to look at again or to finish.

        A survey is four minutes of transport and a roll is hours of it, and
        both used to die with the window: `survey.json` has been written since
        rolls existed and `roll.json` beside it, and neither was ever read back.
        Closing the app between walking a strip and deciding on it meant walking
        it twice; a roll that died at frame 11 of 24 could not be picked up at
        all.

        A list rather than a folder picker, because the thing worth knowing
        before opening one is how far it got, and no file dialog can say that.
        """
        if self._working():
            messagebox.showinfo(
                "Open a roll",
                "The scanner is working. Wait for it to finish, then try "
                "again -- opening a roll replaces whatever is loaded now.")
            return
        if self.browser is not None and self.browser.alive():
            self.browser.top.lift()
            return
        rolls = rolls_on_disk(self.session.rolls, self.session.root)
        # When each was last opened lives in the settings file, not in the roll
        # folder -- see `_note_roll_opened` -- so it is laid over here.
        stored = self.remembered.get("rolls") or {}
        for summary in roll_rows(rolls):
            summary["opened"] = opened_at(summary, stored)
        if not rolls:
            messagebox.showinfo(
                "Open a roll",
                f"Nothing under {self.session.rolls} yet.\n\nA roll folder "
                "appears there the first time a strip is walked or scanned.")
            return
        self.browser = _RollBrowser(self, rolls)

    # -- acting on rolls from the browser ----------------------------------

    def _note_roll_opened(self, folder) -> None:
        """Record when this roll was last opened, for the browser's column.

        In the settings file rather than in the roll folder: opening a roll to
        look at it must not modify it, and a roll on a read-only backup should
        still open.
        """
        rolls = self.remembered.setdefault("rolls", {})
        if not isinstance(rolls, dict):
            rolls = self.remembered["rolls"] = {}
        # Keyed as the sheet's decisions are (`session.roll_key`): a strip
        # by its roll, so strip-02 of two rolls is opened apart.
        rolls[roll_key(folder)] = {"opened": time.time()}
        self._remember()

    # -- what the contact sheet was left holding ---------------------------

    def _sheet_key(self) -> str | None:
        """Which roll the sheet's decisions belong to, if any.

        Frame numbers only mean something within one strip, so a stored set
        has to say which strip it came from. A walk that has not been
        commissioned yet has no folder to name -- those decisions are kept for
        this session only, which is the honest answer rather than filing them
        under a roll they do not belong to.
        """
        # By `session.roll_key`: "Gold200/strip-02" for a strip, so strip-02
        # of two rolls never share decisions, and the bare folder name for a
        # roll from before strips, as every key filed before was.
        return roll_key(self._sheet_roll) if self._sheet_roll else None

    @staticmethod
    def _clean_sheet_state(raw) -> dict:
        """A stored state made safe to hand to the sheet.

        JSON has no integer keys, so a round trip through `gui-settings.json`
        comes back with every frame number as a string and every offset as
        whatever JSON made of it. Anything that will not convert is dropped
        rather than raising: a file edited by hand should cost the one entry
        it got wrong, which is how `_restore` treats the controls.
        """
        out: dict[str, dict] = {"ticks": {}, "offsets": {},
                                "rotations": {}, "flips": {}, "sources": {},
                                "options": {}}
        if not isinstance(raw, dict):
            return out
        for name, cast in (("ticks", bool), ("offsets", float),
                           ("rotations", int), ("flips", bool)):
            section = raw.get(name)
            if not isinstance(section, dict):
                continue
            for key, value in section.items():
                try:
                    kept = cast(value)
                    if name == "offsets" and not math.isfinite(kept):
                        continue            # NaN is a mistake, not a position
                    out[name][int(key)] = kept
                except (TypeError, ValueError, OverflowError):
                    continue
        # Only the five words the ensemble and the sheet actually use. A
        # hand-edited file naming anything else would reach a caption and a
        # count, and "measured" is a claim about a detector having read the
        # frame -- not something a settings file gets to assert.
        known = set(MACHINE_SOURCES) | {"operator", "none"}
        sources = raw.get("sources")
        if isinstance(sources, dict):
            for key, value in sources.items():
                try:
                    number = int(key)
                except (TypeError, ValueError):
                    continue
                if str(value) in known:
                    out["sources"][number] = str(value)
        # The scan options are keyed by name, not by frame number, and only
        # the names the sheet actually offers are let through: a key left over
        # from an older version would be handed to a widget that is not there.
        options = raw.get("options")
        if isinstance(options, dict):
            for key in _ContactSheet.OPTIONS:
                if key in options:
                    out["options"][key] = options[key]
        return out

    def _store_sheet_state(self, state: dict) -> None:
        """Keep the sheet's decisions past the window that made them.

        Filed under the folder's name *and* the walk it holds (`walk_stamp`).
        By the name alone, a fresh walk into a folder of that name -- a name
        typed again for the next strip of the same film, a name reused after
        a Delete or a Rename -- opened its sheet on the last strip's ticks,
        turns and hand-set positions, positions measured against prescans
        that were no longer there.

        And found by the walk: another walk's decisions under the name are
        set aside rather than written over (`set_aside_sheet`), and found
        again by their walk under whatever name it comes back as
        (`_stored_sheet`). For a walk not yet commissioned they are the only
        record, and a walk in the trash, its name taken by the next, lost
        them -- as did one restored as ``<name>-2``.
        """
        if self._file_sheet_state(state):
            self._remember()

    def _file_sheet_state(self, state: dict) -> bool:
        """`_store_sheet_state` without the save: whether there was a roll to
        file it under, and so anything for a save to keep.

        Not while the walk cannot be told (`UNKNOWN`): kept here, and filed
        by the next store. Filed under None, or under the walk the copy
        beside it holds, it was found by no walk, or by the wrong one. Said
        once a folder, since a survey.json that stays damaged -- its sheet
        opened from the copy beside it -- keeps every tick and turn out of
        the settings file until the window closes, and loses them then.
        Stamped with when it was saved (``saved_utc``), which a reopened
        sheet weighs against when each frame was commissioned
        (`merge_sheet`)."""
        self.sheet_state = state
        key = self._sheet_key()
        if key is None:
            return False
        walk = self._sheet_walk()
        if walk is UNKNOWN:
            self._say_unfiled()
            return False
        self._unfiled_said = None
        sheets = self.remembered.setdefault("sheet", {})
        if not isinstance(sheets, dict):
            sheets = self.remembered["sheet"] = {}
        set_aside_sheet(sheets, key, walk)
        sheets[key] = dict(state, walk=walk, saved_utc=utc_now())
        return True

    def _say_unfiled(self) -> None:
        """Say, once for the sheet's folder, that its decisions are kept in
        this window alone: its survey.json cannot be read now."""
        folder = self._sheet_roll
        where = _folder_key(folder)
        if self._unfiled_said == where:
            return
        self._unfiled_said = where
        line = (f"{Path(folder) / 'survey.json'} cannot be read now, so what "
                "is decided on its contact sheet is kept in this window only, "
                "and filed once the file can be read; quit before then, and "
                "it is lost")
        try:
            self._say(line)
        except tk.TclError:
            # The pane gone with a window closing: the kept log still has it.
            self._keep(line)

    def _sheet_walk(self):
        """Which walk the sheet's folder holds (`walk_stamp`): by its id, as
        it was when the sheet was opened on it -- kept from then -- or as
        the folder says now, for a walk from before ids, none, or one that
        cannot be read (`UNKNOWN`)."""
        folder = self._sheet_roll
        if folder is None:
            return None
        key = _folder_key(folder)
        if self._walk_seen is not None and self._walk_seen[0] == key:
            return self._walk_seen[1]
        stamp = walk_stamp(folder)
        if isinstance(stamp, str):
            self._walk_seen = (key, stamp)
        return stamp

    def _see_walk(self) -> None:
        """Take the walk the sheet's folder holds now as the sheet's: as a
        walk ends, and as a roll is opened."""
        self._walk_seen = None
        self._sheet_walk()

    def _keep_sheet_soon(self) -> None:
        """File the open sheet's decisions shortly, as they are made.

        They were filed when the sheet closed, and only then: a crash, a
        kill, or a power cut with the sheet open lost everything decided in it
        since it opened -- for a walk not yet commissioned, the only record of
        it. One write for a burst of changes, `SHEET_KEEP_MS` after the first.
        """
        if self._sheet_keep_job is not None:
            return

        def keep() -> None:
            self._sheet_keep_job = None
            sheet = self.sheet
            if sheet is None or not sheet.alive():
                return
            try:
                self._store_sheet_state(sheet.state())
            except Exception as exc:                      # noqa: BLE001
                self._say(f"could not keep the contact sheet settings: {exc}")

        self._sheet_keep_job = self._later(SHEET_KEEP_MS, keep)

    def _recall_sheet_state(self) -> dict:
        """What the sheet should open holding.

        This session's copy first: it is the one the operator has been
        working in, and it is already in the right types. The settings file
        is the fallback for a window that has been restarted since.
        """
        if self.sheet_state:
            return self._clean_sheet_state(self.sheet_state)
        return self._clean_sheet_state(self._stored_sheet())

    def _stored_sheet(self) -> dict | None:
        """The settings file's decisions for the walk the sheet's folder holds.

        None where it has none for that walk -- nothing stored, or stored for
        a walk the folder no longer holds, or by a version that did not say
        which walk it was for, which is the case that leaked.

        Under the folder's own name first; then under any name, by the walk
        alone: a walk restored from the trash as ``<name>-2``, or set aside
        when another walk took its name (`set_aside_sheet`). A Duplicate
        made with its times kept finds its original's, which is right: it
        holds the same walk's prescans.
        """
        key = self._sheet_key()
        if key is None:
            return None
        walk = self._sheet_walk()
        sheets = self.remembered.get("sheet")
        if walk is None or walk is UNKNOWN or not isinstance(sheets, dict):
            return None
        stored = sheets.get(key)
        if isinstance(stored, dict) and stored.get("walk") == walk:
            return stored
        return next((other for other in sheets.values()
                     if isinstance(other, dict) and other.get("walk") == walk),
                    None)

    def _roll_is_busy(self, summaries, what: str) -> bool:
        """Refuse to touch a roll the scanner or the window is using."""
        if self._working():
            messagebox.showinfo(
                what, "The scanner is working. Wait for it to finish -- a roll "
                "it is writing into is not one to move or remove.")
            return True
        if self._copy_stop is not None:
            # Its dialog holds the window while it copies; this is for
            # whatever reaches the window around it.
            messagebox.showinfo(
                what, "A roll is being copied. Wait for the copy to finish, "
                "or stop it.")
            return True
        # The roll open here is the one reopened from the browser, and the one
        # the sheet belongs to -- a walk made in this session is only the
        # latter. Renamed or deleted under an open sheet, its "Scan chosen
        # frames" recreated the old folder empty and scanned into it, so the
        # walk and the frames ended up in two folders.
        open_here = [Path(f) for f in (self._loaded_roll, self._sheet_roll)
                     if f is not None]
        # And any folder the session is still filing into: the job is over,
        # but its last frames are still with the writer and land in roll.json
        # afterwards.
        # A copy of the items: the worker adds to it as a roll starts.
        filing = [Path(path).parent for path, manifest
                  in list(getattr(self.session, "_manifests", {}).items())
                  if manifest.ahead_of_disk()]

        def within(folder: Path, paths: list[Path]) -> bool:
            """Whether any of ``paths`` is ``folder`` or, for a roll of
            strips, one of its strips: a roll moves or goes with them."""
            key = _folder_key(folder)
            return any(_folder_key(p) == key
                       or _folder_key(library.lock_folder(p)) == key
                       for p in paths)

        for summary in summaries:
            folder = Path(summary["folder"])
            if within(folder, filing):
                messagebox.showinfo(
                    what, f"{folder.name} is still being filed: its last "
                    "frames are on their way to the library and its "
                    "roll.json. Try again in a moment.")
                return True
            if within(folder, open_here):
                messagebox.showinfo(
                    what, f"{folder.name} is the roll open in this window"
                    + (", or holds the strip that is" if summary.get("kind")
                       == "roll" else "")
                    + ". Open another, or restart, before changing it on "
                    "disk.")
                return True
        return False

    def _rolls_held(self, what: str, folders, *, going: bool = False,
                    targets=()) -> contextlib.ExitStack | None:
        """These roll folders held against every other program for as long
        as ``what`` takes, or None -- having said who has one -- where
        another program has it.

        `_roll_is_busy` sees this window only. A `tools/scan_roll.py`
        writing into the folder, or another window showing it -- a demo
        window's real walk -- was renamed, copied half-written or deleted
        from under them. Each folder is held as written into
        (`locks.ROLL`), and as shown (`locks.ROLL_VIEW`): shared for a copy,
        exclusive where it is ``going``, as a Rename's or a Delete's is.
        ``targets`` -- a Rename's new name, a Duplicate's copy -- are held
        as written into too, so nothing starts writing there meanwhile.
        """
        held = contextlib.ExitStack()
        # A strip by its roll's folder, once for all the strips of one roll
        # (`library.lock_folder`): a strip Delete is refused while another
        # window shows any strip of that roll.
        folders = list({locks.key(f): f for f in
                        (library.lock_folder(folder) for folder in folders)
                        }.values())
        wanted = [(locks.ROLL, folder, True) for folder in folders]
        wanted += [(locks.ROLL_VIEW, folder, going) for folder in folders]
        wanted += [(locks.ROLL, target, True) for target in targets]
        try:
            for kind, folder, exclusive in wanted:
                taken = held.enter_context(locks.take(
                    kind, folder, exclusive=exclusive, what=self._who))
                if taken.note:
                    self._say(f"{taken.note}; going ahead")
        except locks.Refused as exc:
            held.close()
            name = Path(exc.path).name
            # A window shows a roll -- holds its view lock -- until it opens
            # another or quits: closing the contact sheet leaves the roll
            # loaded, and the sheet can be opened again on it.
            messagebox.showinfo(what, (
                f"{name} is open in {exc.who}. Open another roll there, or "
                "quit it, first -- closing its contact sheet does not let "
                "go of it."
                if exc.kind == locks.ROLL_VIEW else
                f"{name} is in use by {exc.who}. Try again once it has "
                "finished.") + "\n\nNothing was changed.")
            return None
        return held

    def on_export_rolls(self, summaries) -> None:
        """Every frame of these rolls into one folder, re-corrected.

        From the library entries with *today's* correction code, which is the
        repo's standing contract for anything exported -- not a copy of the
        `frameNN.tif` the roll wrote at the time. On a thread for the same reason
        `on_save_all` is: a long roll is minutes of re-correction.
        """
        if self._saving:
            messagebox.showinfo(
                "Export", "Still writing the last batch. The log says where it "
                "has got to.")
            return
        plans = [(s, roll_exports(s)) for s in summaries]
        total = sum(len(items) for _, items in plans)
        if not total and all(
                s.get("kind") == "roll"
                and not any(strip.get("state") == "here"
                            for strip in s.get("strips") or ())
                for s in summaries):
            # A roll whose strips were each deleted on its own: none has a
            # folder here to export from, whatever its entries hold.
            messagebox.showinfo(
                "Export",
                "No strip of "
                + (row_said(summaries[0]) if len(summaries) == 1
                   else "those rolls")
                + " is in its roll: each is in the rolls' trash, or gone. "
                "`tools/library.py restore` brings one back to its place, and "
                "it can be exported then.\n\nNothing was exported.")
            return
        if not total:
            messagebox.showinfo(
                "Export",
                "None of the frames in "
                + (row_said(summaries[0]) if len(summaries) == 1
                   else "those rolls")
                + " has a library entry left, so there is nothing to re-correct "
                  "from.\n\nThe roll's own frame files are still in its folder.")
            return
        # Which scanned frames have no entry left, by folder: the question
        # promises the log names them, and nothing did. Found by place, and
        # named as their files would have been, by number across the roll.
        skipped = [(row_said(s), export_skipped(s, items))
                   for s, items in plans]
        skipped = [(name, numbers) for name, numbers in skipped if numbers]
        missing = sum(len(numbers) for _, numbers in skipped)
        folder = filedialog.askdirectory(
            parent=self.root, title="Export these frames into ...",
            initialdir=str(self.session.out_dir or Path.home()))
        if not folder:
            return
        fmt = self.session.out_format
        if not messagebox.askokcancel(
            "Export",
            f"Write {total} frame{'s' if total != 1 else ''} into "
            f"{Path(folder).name} as {fmt.upper()}, re-corrected from the "
            f"library at full resolution.\n\n"
            + (f"{counted(missing, 'scanned frame')} "
               f"{'has' if missing == 1 else 'have'} no library entry left and "
               f"{'is' if missing == 1 else 'are'} skipped -- the log names "
               f"{'it' if missing == 1 else 'them'}.\n\n"
               if missing > 0 else "")
            + "This takes a moment per frame. Nothing already there is "
              "overwritten.\n\nStart?",
        ):
            return

        quality = jpeg_quality(self.v_jpegq.get())
        # Each roll's own film, decided on this thread; see `_mono_for`.
        for _, items in plans:
            for item in items:
                item.mono, item.mono_channel = self._mono_for(item)
        out = Path(folder)
        self._saving = True
        for name, numbers in skipped:
            self._say(f"{name}: {'frame' if len(numbers) == 1 else 'frames'} "
                      f"{number_spans(numbers)} {'has' if len(numbers) == 1 else 'have'}"
                      " no library entry left, and "
                      f"{'is' if len(numbers) == 1 else 'are'} skipped")
        self._say(f"exporting {counted(total, 'frame')} into {out} ...")

        def run() -> None:
            written = 0
            for summary, items in plans:
                for item in items:
                    path: Path | None = None
                    try:
                        # Claimed, as the session claims its copies: these
                        # are its names too, and a frame it had queued and
                        # not yet written lost its file to an Export taking
                        # the name meanwhile (`export.claim`).
                        path = export.claim(
                            out / f"{_safe(summary['roll'])}_"
                                  f"{batch_name(item, fmt)}")
                        said = self._deliver_one(item, path, quality,
                                                 item.mono, item.mono_channel)
                    except Exception as exc:             # noqa: BLE001
                        self._saves.put((
                            "line", f"could not export "
                            f"{Path(summary['folder']).name} frame "
                            f"{item.number}: {exc}"))
                        continue
                    finally:
                        if path is not None:
                            export.release([path])
                    if said:
                        written += 1
                        self._saves.put(("line", f"exported {said}"))
            self._saves.put(("done", written, total, "frame", "frames"))

        self._start_writing(run, "export-rolls")

    def on_duplicate_roll(self, summary) -> None:
        """A second copy of the roll under the next free name.

        So a strip can be rescanned at different settings without losing the
        first scan *or* what went with it that the library does not hold --
        the walk, and the positions and turns in `approved.json`, which
        nothing rebuilds (`roll_losses`).

        Copied on a thread, under a modal dialog with a Stop button (Stefan's
        call, 2026-10-09): tens of gigabytes froze the window for the whole
        copy, with no progress and no way out. Modal, so nothing opens,
        scans into, renames or deletes the source or the copy meanwhile, and
        the copy is the roll as it was when Duplicate was pressed. The target
        is reserved and marked `INCOMPLETE` before anything is copied, the
        manifests go in last and the marker after them (`copy_roll`), so a
        copy cut short never lists as a roll -- one that failed part way
        used to list with frames and no walk, or not at all, and nothing
        could show or delete it -- and one that failed or was stopped is
        removed. The passes kept in `unfiled/` are left out: the library
        refused them, and two copies would be filed twice once moved in.
        """
        if self._roll_is_busy([summary], "Duplicate"):
            return
        source = Path(summary["folder"])
        try:
            target = duplicate_name(source)
        except ValueError as exc:
            messagebox.showerror("Duplicate", str(exc))
            return
        need = roll_copy_bytes(source)
        kept = unfiled_kept(source)
        # The room first, as every job that writes asks it (`storage`): a
        # copy that filled the disk part way was the failure that left a half
        # roll behind. Refused below once what it writes and the reserve,
        # warned below twice.
        #
        # With what the writer has still to file on the copy's disk, as the
        # session's own jobs count it (`ScanSession.queued_needs`): a roll
        # at 3600 dpi is filed for a while after it ends, and its last
        # frames are not on the disk yet to be seen. That disk's only, so a
        # busy library elsewhere neither refuses the copy nor adds lines to
        # its question.
        queued_needs = getattr(self.session, "queued_needs", None)
        queued = [(place, size)
                  for place, size in (queued_needs() if queued_needs else [])
                  if storage.same_disk(place, target.parent)]
        wanted = [(target.parent, need), *queued]
        short, unknown = check_space(wanted)
        if short:
            filing = bool(queued) and not check_space(
                [(target.parent, need)])[0]
            messagebox.showerror(
                "Duplicate", f"Not enough room to copy {source.name}: "
                f"{short[0]}."
                + (" Part of that is a scan just taken, still being filed: "
                   "try again once it has landed." if filing else "")
                + "\n\nNothing was copied.")
            return
        tight = check_space(wanted, factor=SPACE_WARN)[0]
        if not messagebox.askokcancel(
            "Duplicate",
            f"Copy {source.name} to {target.name} -- "
            f"{human_size(need)}.\n\nThe copy keeps the walk, the "
            "approvals and the frames already scanned, so the original is safe "
            "to rescan over."
            + (f"\n\nThe {counted(len(kept), 'pass', 'passes')} the library "
               "refused, kept in its unfiled/ folder, "
               f"{'is' if len(kept) == 1 else 'are'} not copied: "
               f"{'it exists' if len(kept) == 1 else 'they exist'} nowhere "
               "else, and two copies would be filed twice." if kept else "")
            + "".join(f"\n\n{line}." for line in unknown)
            + (f"\n\nThe disk will be nearly full afterwards: {tight[0]}."
               if tight else "")
            + "\n\nCopy?",
        ):
            return
        held = self._rolls_held("Duplicate", [source], targets=[target])
        if held is None:
            return
        try:
            reserve_copy(source, target, who=self._who)
        except OSError as exc:
            held.close()
            messagebox.showerror("Duplicate", f"Could not start the copy: "
                                 f"{exc}\n\nNothing was copied.")
            return
        stop = threading.Event()
        self._copy_stop = stop
        self._copy_dialog = self._copying(source, target, stop)
        self._say(f"copying {source.name} to {target.name} ...")

        def progress(done: int, total: int) -> None:
            self._saves.put(("copying", done, total))

        def run() -> None:
            error: BaseException | None = None
            marked: OSError | None = None
            # Removed before the copy is let go of, so nothing starts
            # writing into a folder this is about to take away.
            with held:
                try:
                    marked = copy_roll(source, target, stop, progress)
                except BaseException as exc:              # noqa: BLE001
                    error = exc
                    remove_copy(target)
            left = target if error is not None and target.exists() else None
            self._saves.put(("copied", source, target, error, left, marked))

        self._start_writing(run, "duplicate-roll")

    def _copying(self, source: Path, target: Path,
                 stop: threading.Event) -> tk.Toplevel:
        """Duplicate's dialog: how far the copy has got, and Stop.

        Modal -- the rest of the window takes no input while it is up -- and
        the event pump runs under it, so the log, the progress and the
        copy's end all still arrive. Closing it is Stop.
        """
        top = tk.Toplevel(self.root)
        top.title("Duplicate")
        top.transient(self.root)
        top.resizable(False, False)
        body = ttk.Frame(top, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=f"Copying {source.name} to {target.name} ...",
                  wraplength=_px(420)).pack(anchor="w")
        self._copy_said = tk.StringVar(value="starting ...")
        ttk.Label(body, textvariable=self._copy_said,
                  foreground="#555").pack(anchor="w", pady=(6, 0))
        self._copy_bar = ttk.Progressbar(body, length=_px(420),
                                         mode="determinate", maximum=1.0)
        self._copy_bar.pack(fill="x", pady=(8, 8))
        button = ttk.Button(body, text="Stop")

        def stopping() -> None:
            if stop.is_set():
                return
            stop.set()
            self._copy_said.set("stopping -- what was copied is removed ...")
            button.state(["disabled"])

        button.configure(command=stopping)
        button.pack(anchor="e")
        top.protocol("WM_DELETE_WINDOW", stopping)

        def grab(tries: int = 20) -> None:
            # A window not drawn yet cannot be grabbed; asked again shortly.
            try:
                top.grab_set()
            except tk.TclError:
                if tries and top.winfo_exists():
                    self._later(100, lambda: grab(tries - 1))

        grab()
        return top

    def _copy_progress(self, done: int, total: int) -> None:
        """Duplicate's dialog, told how far the copy has got."""
        dialog = self._copy_dialog
        if dialog is None or not dialog.winfo_exists():
            return
        if self._copy_stop is not None and self._copy_stop.is_set():
            return
        self._copy_said.set(f"{human_size(done)} of {human_size(total)}")
        self._copy_bar.configure(value=done / total if total else 1.0)

    def _copied(self, source: Path, target: Path,
                error: BaseException | None, left: Path | None,
                marked: OSError | None = None) -> None:
        """Duplicate's end: its dialog put away, and what it came to said."""
        self._copy_stop = None
        dialog, self._copy_dialog = self._copy_dialog, None
        if dialog is not None:
            try:
                dialog.grab_release()
                dialog.destroy()
            except tk.TclError:
                pass
        if error is None and marked is not None:
            said = (f"duplicated {source.name} to {target.name}, but its "
                    f"{library.INCOMPLETE} marker could not be removed "
                    f"({marked}): the copy is whole, and lists as a roll once "
                    "that file is deleted")
            self._say(said)
            messagebox.showinfo("Duplicate", said[0].upper() + said[1:],
                                parent=self.root)
        elif error is None:
            self._say(f"duplicated {source.name} to {target.name}")
        else:
            what = ("the copy was stopped" if isinstance(error, CopyStopped)
                    else f"could not copy {source.name}: {error}")
            # Marked where `remove_copy` kept the marker, which it does
            # while anything else is left; a folder it emptied but could not
            # remove has none, and is no roll either.
            said = (f"{what}; nothing was left behind" if left is None else
                    f"{what}; {left} is left part-copied, marked "
                    f"{library.INCOMPLETE} -- delete it"
                    if (left / library.INCOMPLETE).exists() else
                    f"{what}; {left} is left part-copied -- delete it")
            self._say(f"Duplicate: {said}")
            if not isinstance(error, CopyStopped) or left is not None:
                messagebox.showerror("Duplicate", said[0].upper() + said[1:],
                                     parent=self.root)
        if self.browser is not None and self.browser.alive():
            self.browser._reload()

    def _stop_copying(self) -> None:
        """Quit's word to a Duplicate in progress: stop, and remove what it
        copied. Quit then waits for it as for any writer (`_wait_to_quit`)."""
        if self._copy_stop is not None:
            self._copy_stop.set()

    def on_delete_rolls(self, summaries) -> None:
        """Move roll folders into the rolls' trash. Never the library entries.

        Whole, by one rename each, into ``.trash/<UTC time>/`` beside them
        (`library.trash`): it cannot stop half way, as the `rmtree` it
        replaces could on Windows with one file held open, and `tools/
        library.py restore` brings a folder back. Stefan's call, 2026-10-09.

        The question says what goes for good once the trash is emptied
        (`roll_losses`), which is more than the folder's own record: the
        walk (`survey.json` -- without it the roll never opens as a contact
        sheet again), what was done (`roll.json`), the positions and turns
        set by hand (`approved.json`); and beside them any picture whose
        raw bytes are nowhere else -- a prescan from before an in-walk
        correction or a walk prescan with no entry, a frame whose entry has
        gone, its `frameNN.tif` the last copy. It used to promise that the
        walk's prescans stay and every frame could be exported, and counted
        every `prescanNN-before.tif` as having no entry, when both front ends
        file one; and its size left out `unfiled/`. A pass the library
        refused, kept in `unfiled/`, is said apart: emptying the trash
        leaves its delete alone until it is filed (`library.empty_trash`).

        A sheet's decisions remembered for the walk are left where they are:
        they are found by its walk, under its own name or the one a restore
        gives it, and by no new walk into a name used again -- which sets
        them aside rather than writing over them, as a Rename onto the name
        does (`set_aside_sheet`).

        A roll of strips goes whole, its strips with it, and what goes is
        summed over them. A strip goes on its own (`_delete_strips`), its
        numbers kept given.
        """
        strips = [s for s in summaries if s.get("kind") == "strip"]
        summaries = [s for s in summaries if s.get("kind") != "strip"]
        if strips:
            self._delete_strips(strips)
        if not summaries:
            return
        if self._roll_is_busy(summaries, "Delete"):
            return
        def held(summary) -> int:
            """A roll of strips' strips its folder holds: one deleted on its
            own is in the rolls' trash already, and was counted as going."""
            return sum(1 for strip in summary.get("strips") or ()
                       if strip.get("state") == "here")

        # The folders, as the browser lists them: a duplicate keeps its
        # original's roll name inside, so by that name the question could not
        # say which of two identically named rolls was about to go.
        names = ", ".join(
            Path(s["folder"]).name
            + (f" ({counted(held(s), 'strip')})" if s.get("kind") == "roll"
               else "")
            for s in summaries)
        losses = [roll_losses(s, self.session.root) for s in summaries]
        size = human_size(sum(lost["bytes"] for lost in losses))
        one = len(summaries) == 1
        gone, refused = self._losses_said(summaries, losses)
        rolls_trash = sorted({str(Path(s["folder"]).parent / library.TRASH)
                              for s in summaries})
        if not messagebox.askokcancel(
            "Delete",
            f"Move {len(summaries)} roll folder{'' if one else 's'} -- "
            f"{names} -- into the rolls' trash "
            f"({', '.join(rolls_trash)})? "
            f"{'It stays' if one else 'They stay'} whole there until the "
            f"trash is emptied, and {size} stays on the disk until then.\n\n"
            "The library entries are not touched.\n\n"
            + ("Once the trash is emptied, what goes for good is "
               + "; ".join(gone) + ".\n\n" if gone else "")
            + refused
            + "Move to the trash?",
        ):
            return
        held = self._rolls_held("Delete", [s["folder"] for s in summaries],
                                going=True)
        if held is None:
            return
        with held:
            self._delete_rolls(summaries)

    def _losses_said(self, summaries, losses) -> tuple[list[str], str]:
        """What a Delete's question says goes for good once the trash is
        emptied, item by item (`roll_losses`), and the passes the library
        refused, said apart: emptying the trash leaves a delete holding them
        alone, and `verify` names them there until each is filed
        (`library.empty_trash`). A roll of strips counts each strip's walk
        and decisions; its frames are said by their numbers across it."""
        one = len(summaries) == 1

        def parts(summary) -> list[dict]:
            """The folders of records a row stands for."""
            if summary.get("kind") == "roll":
                return [s for s in summary.get("strips") or ()
                        if s.get("state") == "here"]
            return [summary]

        # Positions as well as turns and flips.
        decided = sum(1 for s in summaries
                      if any(any(read_approved(p["folder"])[0:3])
                             for p in parts(s)))
        walked = sum(1 for s in summaries for p in parts(s) if p.get("walked"))
        before = sum(len(lost["before"]) for lost in losses)
        prescans = sum(len(lost["prescans"]) for lost in losses)
        unfiled = [p for lost in losses for p in lost["unfiled"]]

        def frames(key: str) -> tuple[str, int]:
            """Each roll's frames of one kind, said, and how many."""
            said = [f"{'frame' if len(lost[key]) == 1 else 'frames'} "
                    f"{number_spans(lost[key])} of {row_said(s)}"
                    for s, lost in zip(summaries, losses) if lost[key]]
            return " and ".join(said), sum(len(lost[key]) for lost in losses)

        last, lasts = frames("last")
        trashed, trashes = frames("trashed")
        gone = ([f"{walked} walk{'s' if walked != 1 else ''} (survey.json), "
                 "which nothing rebuilds -- a roll without its walk never "
                 "opens as a contact sheet again"] if walked else []) \
            + (["what each roll has done (roll.json)"]
               if any(s.get("scanned") for s in summaries) else []) \
            + ([("the positions and turns you set by hand in it"
                 if one else
                 f"the positions and turns you set by hand in {decided} of "
                 "them") + " (approved.json)"] if decided else []) \
            + ([f"{before} prescan{'s' if before != 1 else ''} taken before an "
                "in-walk correction with no library entry"] if before else []) \
            + ([f"{prescans} walk prescan{'s' if prescans != 1 else ''} with "
                "no library entry"] if prescans else []) \
            + ([f"{last}, whose {'entry is' if lasts == 1 else 'entries are'}"
                f" gone: {'its' if lasts == 1 else 'each'} frameNN.tif is "
                "the last copy"] if lasts else []) \
            + ([f"{trashed}, whose "
                f"{'entry is' if trashes == 1 else 'entries are'} in the "
                "library's trash and go when that is emptied"] if trashes
               else [])
        refused = (f"{'It holds' if one else 'They hold'} "
                   f"{counted(len(unfiled), 'pass', 'passes')} the library "
                   f"refused, kept in unfiled/ "
                   f"({human_size(sum(library._size(p) for p in unfiled))}), "
                   "whose raw bytes are nowhere else: make verify names "
                   f"{'it' if len(unfiled) == 1 else 'them'} in the trash, "
                   "and emptying the trash leaves this delete alone, until "
                   f"{'it is' if len(unfiled) == 1 else 'each is'} moved into "
                   "the library.\n\n" if unfiled else "")
        return gone, refused

    def _delete_strips(self, strips) -> None:
        """Move strips of rolls of strips into the rolls' trash, one roll's
        at a time: a question, then the roll held as a Delete holds it
        (`_rolls_held`), its open last strip closed at the frames it had
        (`session.close_strip`), and the strips moved (`library.trash`,
        kind "strips"). `tools/library.py restore` puts each back into its
        place in its roll.

        A strip deleted keeps its numbers (Stefan's "decided strict"): the
        next strip walked is numbered after the roll's last, so a delivered
        file of the deleted strip never meets one of the next with its
        number. Not one in the trash already, nor one gone: said, in words.
        """
        away = [s for s in strips if s.get("state") != "here"]
        for strip in away:
            roll = Path(strip["roll_folder"]).name
            messagebox.showinfo(
                "Delete", f"Strip {strip.get('strip')} of {roll} is "
                + ("in the rolls' trash already."
                   if strip.get("state") == "in the trash"
                   else "not here: its folder has gone.")
                + "\n\nNothing was moved.")
        by_roll: dict[str, list[dict]] = {}
        for strip in strips:
            if strip.get("state") == "here":
                by_roll.setdefault(_folder_key(strip["roll_folder"]),
                                   []).append(strip)
        for group in by_roll.values():
            group.sort(key=lambda s: int(s.get("strip") or 0))
            if self._roll_is_busy(group, "Delete"):
                continue
            roll = Path(group[0]["roll_folder"])
            losses = [roll_losses(s, self.session.root) for s in group]
            gone, refused = self._losses_said(group, losses)
            one = len(group) == 1
            which = _and_joined(str(s.get("strip")) for s in group)
            numbers = "; ".join(f"strip {s.get('strip')} has "
                                f"{strip_numbers_said(s)}" for s in group)
            if not messagebox.askokcancel(
                "Delete",
                f"Move strip{'' if one else 's'} {which} of {roll.name} into "
                f"the rolls' trash ({roll.parent / library.TRASH})? "
                f"{'It stays' if one else 'They stay'} whole there until the "
                f"trash is emptied, and "
                f"{human_size(sum(lost['bytes'] for lost in losses))} stays on "
                "the disk until then.\n\n"
                f"{numbers}. {'Its numbers stay' if one else 'Their numbers stay'}"
                " given: the next strip walked is numbered after the roll's "
                f"last. To walk strip {group[0].get('strip')} again, open it "
                "and walk with 'add to it' instead.\n\n"
                "The library entries are not touched.\n\n"
                + ("Once the trash is emptied, what goes for good is "
                   + "; ".join(gone) + ".\n\n" if gone else "")
                + refused + "Move to the trash?"):
                continue
            held = self._rolls_held("Delete", [roll], going=True)
            if held is None:
                continue
            with held:
                self._trash_strips(roll, group)

    def _trash_strips(self, roll: Path, group: list[dict]) -> None:
        """`_delete_strips` for one roll, held: the open last strip closed,
        then the strips moved, each said, with the command that brings them
        back."""
        named = ", ".join(Path(s["folder"]).name for s in group)
        for strip in group:
            try:
                line = close_strip(strip["folder"], who=self._who,
                                   live=self.session.live_manifest)
            except ValueError as exc:
                self._say(f"could not close strip {strip.get('strip')} of "
                          f"{roll.name}: {exc}; nothing was moved")
                return
            if line:
                self._say(line)
        try:
            moved = library.trash(roll.parent, [s["folder"] for s in group],
                                  by=self._who,
                                  why="the roll browser's Delete of a strip",
                                  kind="strips")
        except (OSError, ValueError) as exc:
            self._say(f"could not move {named} of {roll.name} to the trash: "
                      f"{exc}; nothing was moved")
            return
        for name, why in moved.left.items():
            self._say(f"could not move {name} of {roll.name} to the trash: "
                      f"{why}; it is left whole")
        if moved.batch is not None:
            # The roll stays this session's where it scanned into it, until
            # the window closes (`ScanSession._hold_roll`): a restore takes
            # the roll's lock, and is refused till then. Said, not found out.
            held = locks.key(roll) in getattr(self.session, "_roll_locks", {})
            self._say(
                f"moved {', '.join(moved.moved)} of {roll.name} to the trash "
                f"({moved.batch}); "
                f"`{self._restore_command(moved.batch.name, roll.parent)}` "
                + ("brings it back to its place" if len(moved.moved) == 1
                   else "brings them back to their places")
                + (f" -- once this window has closed, as it holds "
                   f"{roll.name}, which it scanned into" if held else ""))
        self._offer_strip()

    def _delete_rolls(self, summaries) -> None:
        """`on_delete_rolls`, with the folders held: each into the trash of
        the folder it is in, one delete per folder of rolls."""
        by_parent: dict[Path, list[Path]] = {}
        for summary in summaries:
            folder = Path(summary["folder"])
            by_parent.setdefault(folder.parent, []).append(folder)
        for parent, folders in by_parent.items():
            named = ", ".join(f.name for f in folders)
            try:
                moved = library.trash(parent, folders, by=self._who,
                                      why="the roll browser's Delete",
                                      kind="rolls")
            except (OSError, ValueError) as exc:
                self._say(f"could not move {named} to the trash: {exc}; "
                          "nothing was moved")
                continue
            for name, why in moved.left.items():
                self._say(f"could not move {name} to the trash: {why}; it is "
                          "left whole")
            # The session's hold on each folder moved goes with it (P23-b):
            # kept, a roll of that name was refused for the session's life,
            # and a restore of another roll there put it beside instead.
            for name in moved.moved:
                self._let_go_of(parent / name)
                # The box emptied where it held the window's own name for
                # it, as New roll empties it: kept, the next walk started a
                # fresh roll by the name of the one in the trash.
                if self._names_in_box(parent / name):
                    self.fields["roll"].set("")
                    self._roll_named = self._roll_named_folder = None
            if moved.batch is not None:
                self._say(
                    f"moved roll folder{'s' if len(moved.moved) != 1 else ''}"
                    f" {', '.join(moved.moved)} to the trash ({moved.batch}); "
                    f"`{self._restore_command(moved.batch.name, parent)}` "
                    f"brings {'it' if len(moved.moved) == 1 else 'them'} back")
        # The Roll panel's line said again, as a strip's Delete says it: it
        # went on offering the next strip of a roll in the trash, and
        # Continue for it.
        self._offer_strip()

    def _strip_away_said(self, strip: dict, then: str = "") -> str:
        """Why a strip's row in the roll browser has nothing here to act on:
        in the rolls' trash, with the command that brings it back to its
        place -- and ``then``, what can be done once it is -- or gone."""
        roll = Path(strip["roll_folder"]).name
        name = Path(strip["folder"]).name
        said = f"Strip {strip.get('strip')} of {roll} is "
        if strip.get("state") == "in the trash":
            return (said + "in the rolls' trash; `" + self._restore_command(
                        strip.get("batch") or f"{roll}/{name}",
                        Path(self.session.rolls))
                    + "` brings it back to its place"
                    + (f", and {then}" if then else "") + ".")
        return said + f"not in {roll}: its folder, {name}, has gone."

    def _restore_command(self, name: str, rolls: Path | None = None) -> str:
        """The command that brings ``name`` back from a trash, with the
        folders it is to look in where they are not its defaults."""
        root = Path(self.session.root or library.DEFAULT_ROOT)
        said = f"tools/library.py restore {name}"
        if root != library.DEFAULT_ROOT:
            said += f" --root {_quoted_path(root)}"
        if rolls is not None and _folder_key(rolls) != _folder_key(
                root.parent / "rolls"):
            said += f" --rolls {_quoted_path(rolls)}"
        return said

    def on_rename_roll(self, summary) -> None:
        """Rename the folder. The manifests keep the roll's own name inside.

        A roll of strips is renamed whole, its strips with it; its frames
        go on carrying the label its index keeps (``roll``). A strip on its
        own is not renamed -- its folder is its place in the roll -- and is
        refused in words.

        A rename that changes only the case of the name goes through, on a
        disk that folds case as on one that does not (PLAT-06): the folder
        there under the new spelling is the same folder, which
        `os.path.samefile` says by the file id both report. Anything else
        there by that name is refused, as before.
        """
        if summary.get("kind") == "strip":
            roll = Path(summary["folder"]).parent.name
            messagebox.showinfo(
                "Rename", "A strip's folder is named by its place in the "
                f"roll. Rename the roll, {roll}, instead.\n\nNothing was "
                "changed.")
            return
        if self._roll_is_busy([summary], "Rename"):
            return
        source = Path(summary["folder"])
        wanted = simpledialog.askstring(
            "Rename", f"A new folder name for {source.name}:",
            initialvalue=source.name, parent=self.root)
        if not wanted or wanted.strip() == source.name:
            return
        target = source.with_name(_safe(wanted.strip()))
        if target.name == source.name:
            messagebox.showinfo("Rename", f"{source.name} is already called "
                                "that.")
            return
        if os.path.lexists(target) and not same_folder(source, target):
            messagebox.showerror("Rename", f"{target.name} is already there.")
            return
        held = self._rolls_held("Rename", [source], going=True,
                                targets=[target])
        if held is None:
            return
        try:
            with held:
                source.rename(target)
        except OSError as exc:
            messagebox.showerror("Rename", f"Could not rename: {exc}")
            return
        # The session's own hold on the folder it scanned into goes with the
        # name (P23-b): kept, the old name stayed locked for the session's
        # life, and `tools/scan_roll.py` was refused a new roll of that name.
        self._let_go_of(source)
        self._rename_keys(source.name, target.name)
        # The roll box follows, where it holds the window's own name for
        # this folder -- Continue, an opened strip's roll: left on the old
        # name, the next walk started a fresh roll by it, whose labels
        # repeated the renamed roll's. A name he typed is his, and stays.
        if self._names_in_box(source):
            self._show_roll_name(target.name, ours=True, folder=target)
        self._say(f"renamed {source.name} to {target.name}")

    def _names_in_box(self, folder) -> bool:
        """Whether the roll box holds the window's own name for ``folder``
        (`_show_roll_name`), not one he typed."""
        named = self.__dict__.get("_roll_named_folder")
        return (self._roll_named == Path(folder).name and named is not None
                and _folder_key(named) == _folder_key(folder)
                and self.fields["roll"].get().strip() == Path(folder).name)

    def _let_go_of(self, folder) -> None:
        """Have the session let go of its hold on a roll folder this window
        has just moved away -- renamed, or into the rolls' trash
        (`session.ScanSession.let_go_of_roll`, P23-b). Never of a roll
        because one of its strips went: the roll is still there."""
        let_go = getattr(self.session, "let_go_of_roll", None)
        if callable(let_go):
            try:
                let_go(folder)
            except Exception as exc:                      # noqa: BLE001
                self._say(f"could not let go of {Path(folder).name}: {exc}")

    def _rename_keys(self, old: str, new: str) -> None:
        """Move what the settings file keeps of a renamed roll to its new
        name: the sheet's decisions and when it was opened, for the folder
        and -- a roll of strips -- for each of its strips (``<old>/...``,
        `session.roll_key`); and the roll Continue offers. A key the new
        name already has is another walk's: set aside, never written over
        (`set_aside_sheet`), for a restore finds it by its walk."""
        changed = False
        for section in ("sheet", "rolls"):
            stored = self.remembered.get(section)
            if not isinstance(stored, dict):
                continue
            for key in [k for k in stored
                        if k == old or k.startswith(old + "/")]:
                moved = stored.pop(key)
                renamed = new + key[len(old):]
                if section == "sheet":
                    set_aside_sheet(stored, renamed, moved.get("walk")
                                    if isinstance(moved, dict) else None)
                stored[renamed] = moved
                changed = True
        if self.remembered.get("last_roll") == old:
            self.remembered["last_roll"] = new
            changed = True
        if changed:
            self._remember()
        self._offer_strip()

    def on_reveal_roll(self, summary) -> None:
        """Show the folder in the platform's own file manager -- a strip not
        here, its roll's."""
        folder = Path(summary["folder"])
        if not folder.exists() and summary.get("roll_folder") is not None:
            folder = Path(summary["roll_folder"])
        folder = str(folder)
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", folder], check=False)
            elif sys.platform.startswith("win"):
                subprocess.run(["explorer", folder], check=False)
            else:
                subprocess.run(["xdg-open", folder], check=False)
        except OSError as exc:
            self._say(f"could not open {folder}: {exc}")

    def open_roll(self, folder, quiet: bool = False) -> bool:
        """Load one roll folder: its walk, its decisions, and how far it got.

        The settings come back from the manifest rather than from
        `gui-settings.json`, and that distinction is the whole point of
        resuming: the window's own settings have moved on to other film, while
        the manifest still describes *this* roll. A year later it is the only
        thing that still does.

        A roll of strips opens on one of its strips: the first with frames
        left, or else its last (`strip_to_open`). A strip opens as a roll
        from before strips always did, and the roll box names its roll.

        ``quiet`` leaves out the box that says what was opened -- for the
        Roll button, which opens a strip whose walk stopped and asks its own
        question about it next. True once the roll is loaded.
        """
        folder = Path(folder)
        # Here rather than only where the browser is opened: the browser stays
        # open, and its Open went straight through while a walk ran. The walk's
        # later prescans joined the opened roll's survey under the same frame
        # numbers, its end pointed the sheet at the new folder, and the other
        # strip's positions and references were commissioned into it.
        if self._working() or self._surveying:
            messagebox.showinfo(
                "Open a roll",
                "The scanner is working. Wait for it to finish, then try "
                "again -- opening a roll replaces whatever is loaded now.")
            return False
        if library.roll_kind(folder) == "strips":
            strip = strip_to_open(folder, self.session.root)
            if strip is None:
                messagebox.showinfo(
                    "Open a roll", f"{folder.name} has no strip here to open: "
                    "each is in the rolls' trash, or was never walked.")
                return False
            place = place_of(strip, strict=False)
            self._say(f"opened strip {place.strip if place else strip.name} "
                      f"of {folder.name}; pick a strip's row under Rolls ... "
                      "to open another")
            folder = strip
        self._note_roll_opened(folder)
        try:
            out = read_survey(folder, say=self._say,
                              library_root=self.session.root)
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror(
                "Open a roll",
                f"{folder.name} does not hold a roll this can read.\n\n"
                f"{exc}\n\nA roll folder has a survey.json or a roll.json, "
                "and the prescanNN.tif files beside it.")
            return False
        # Only once it has been read: a folder that failed to open is not the
        # roll open in this window, and was kept from Rename and Delete as if
        # it were until the window restarted.
        self._loaded_roll = folder
        # Frame numbers name this roll's pictures from here: the turns the
        # window held against them were another strip's, and arranged the
        # prescans of a walk added to this one by that strip's frames. Set
        # again from this roll's own below, once its sheet has them.
        self.orientations = {key: turn for key, turn
                             in self.orientations.items() if key[0] != "frame"}

        done = out["scanned"]
        remaining = [n for n in out["wanted"] if n not in done]
        #: Where the rest of the strip starts that no run reached, if any.
        rest = out.get("rest_from")
        restored = self._restore_roll_settings(out["settings"])
        # The roll box names the folder now, so a roll continued from here --
        # the Roll button for a roll with no walk, as well as the sheet --
        # scans into this folder. It was never put back, and "continue"
        # scanned into rolls/<today> beside it. A strip's box names its roll.
        self._show_roll_name(roll_name_of(folder),
                             folder=library.lock_folder(folder))
        # Not the roll Continue offers: that is the roll walked or claimed
        # last (`_note_strip_roll`). Noted here, a roll opened to export it
        # or retake one frame was offered after a restart, and the next
        # strip of the film being walked went into it.
        strip = out.get("strip")
        name = (f"strip {strip.strip} of {strip.label}" if strip is not None
                else folder.name)

        if not out["results"]:
            # A roll commissioned without a walk has no prescans, so there is
            # no sheet to show -- but it can still be finished, which is what
            # matters. Point the plain Roll button at the first frame left,
            # and at the end of the strip where its rest was never reached:
            # a roll to the end of the strip stopped after frame 9 said
            # "nothing left to scan" (RES-n1).
            if not remaining and rest is None:
                if not quiet:
                    messagebox.showinfo(
                        "Open a roll",
                        f"{name} has no prescans to show and nothing left to "
                        "scan.")
                return True
            first = min([*remaining, *([rest] if rest is not None else [])])
            # The last frame too: the end of the strip where its rest is
            # left, the last frame left otherwise. Put back from the last
            # run's range, it could stop short of frames left on both sides
            # of it -- "5 to 9" then "1 to 4" reopened as 7 to 4 -- and
            # Roll refused, or scanned less than this box promised (RES-n7).
            last = "" if rest is not None else str(max(remaining))
            if restored:
                self.v_startat.set(str(first))
                self.v_last.set(last)
            if not quiet and not self._outside(folder):
                self._scan_when_rolled()
            # Said as it was done: without settings in its manifest nothing was
            # put back, and the box was left alone -- which this used to say
            # had been set.
            span = (f"\"first frame\" is set to frame {first} and \"last "
                    + ("frame\" emptied, for the end of the strip"
                       if rest is not None else f"frame\" to {last}"))
            ready = (f"Its settings are back and {span}" if restored else
                     f"It records no settings, so the window's are left as they "
                     f"are: set them, \"first frame\" to frame {first} and "
                     "\"last frame\" "
                     + ("empty" if rest is not None else f"to {last}"))
            # From outside this window's rolls, Roll goes into a folder here
            # and never back into it (P18-d): said in its place.
            how = (self._foreign_said(folder) if self._outside(folder) else
                   f"{ready} -- put the strip in the way it went in before "
                   "and press Roll, and the film is wound there first; the "
                   "frames already done are skipped.")
            reached = (f", and the strip from {place_said(strip, rest)} on "
                       "was never reached" if rest is not None else "")
            if not quiet:
                messagebox.showinfo(
                    "Open a roll",
                    f"{name} was scanned without walking the strip first, "
                    f"so there is no contact sheet to show.\n\n"
                    f"{counted(len(done), 'frame')} {is_are(len(done))} done "
                    f"and {len(remaining)} {is_are(len(remaining))} left"
                    f"{reached}. {how}\n\n" + STRIP_NUMBERS)
            self._say(f"reopened {name}: {len(done)} scanned, "
                      f"{len(remaining)} left"
                      + (f", the strip from {place_said(strip, rest)} on not "
                         "reached" if rest is not None else "") + ", no sheet")
            return True

        # The sheet open now is the outgoing roll's. Closed the way its own
        # Close button closes it, so what was decided in it is kept -- under
        # its own roll, which `_sheet_roll` still names until below. It was
        # destroyed, and twenty minutes of positions went with it.
        self._close_sheet()
        self.survey = out["results"]
        self._survey_predpi = out["prescan_resolution"]
        self._survey_film = out.get("film")
        for result in out["results"]:
            self.results.append(result)
        self.b_sheet.configure(state="normal")
        self._redraw_strip()
        # Turns and mirrors he made, not every frame approved.json records:
        # it records each commissioned frame's, straight ones included
        # (`sheet_counts`). The positions are the ones committed, whoever
        # set them.
        arranged = sheet_counts(out)
        self._say(f"reopened {name}: "
                  f"{counted(len(out['results']), 'frame')}"
                  + (f", {len(done)} already scanned" if done else "")
                  + (f", {len(out['offsets'])} with a position already set"
                     if out["offsets"] else "")
                  + (f", {arranged['turned']} already turned"
                     if arranged["turned"] else "")
                  + (f", {arranged['flipped']} flipped"
                     if arranged["flipped"] else "")
                  + (f", settings restored: {', '.join(sorted(restored))}"
                     if restored else ""))
        self._sheet_done = {int(n) for n in done}
        self._sheet_holds = dict(out.get("holds") or {})
        self._sheet_roll = folder
        self._see_walk()
        # The manifest is the record for this roll, so it replaces whatever
        # the window was holding -- including another roll's decisions, which
        # are keyed by frame number and would otherwise be read as this one's.
        self.sheet_state = {
            "ticks": {}, "offsets": dict(out["offsets"]),
            "rotations": dict(out["rotations"]), "flips": dict(out["flips"]),
            # Carried, or the next sheet built from this state stamps every
            # one of them `operator`: `_merge_kept` reads a kept offset
            # with no recorded source as one he set by hand. That turns the
            # ensemble's numbers into his, in the count the confirm dialog
            # shows him before the film moves.
            "sources": dict(out["sources"]),
            # Left empty on purpose: `_restore_roll_settings` above has just
            # put this roll's own settings back into the window's controls,
            # and the sheet pre-fills from those. Carrying another roll's
            # options across would override the ones just restored.
            "options": {},
        }
        # What was decided on the sheet lives in the settings file until it
        # is commissioned, and what is decided after a commission does too.
        # Read back now, for this walk only (`_stored_sheet`), and laid over
        # approved.json frame by frame (`merge_sheet`): the commission's
        # word for each frame it recorded unless the sheet was saved since,
        # the sheet's for every other -- never as a tick on a frame already
        # scanned, unless it was ticked again after it was. Once there was
        # an approved.json the settings file was not read at all, and what
        # was decided after a part commission came back as nothing (P22-a).
        stored = self._stored_sheet()
        # Frames already scanned and ticked again since, to be scanned
        # again: said here and in the message below, which says the frames
        # already done are not ticked.
        again: list[int] = []
        if stored is not None:
            self.sheet_state, theirs, since = merge_sheet(out, stored, done)
            again = sorted(n for n, tick in self.sheet_state["ticks"].items()
                           if tick and n in done)
            # Counted as he decided them (`sheet_counts`): by entry, the
            # detector's proposals were "positioned" and a straight frame
            # "turned".
            held = counts_said(sheet_counts(self.sheet_state))
            self._say("the sheet comes back as it was left: "
                      + (held or "nothing positioned, turned or flipped")
                      + (f" -- {theirs} from approved.json, {since} decided "
                         "since" if out.get("decided") else "")
                      + (f"; {ticked_again_said(strip, again)}" if again
                         else ""))
        state = self.sheet_state
        # Re-proposed, not merely restored. `approved.json` holds positions
        # that were *committed*; a roll walked and then closed without
        # commissioning has none, so this used to reopen with nothing at all --
        # every proposal the walk made died with the window that made it, and
        # the roll scanned uncorrected with no sign anything was missing.
        #
        # Read in the background, and the sheet opens without waiting for it.
        # Stored positions still win: they go in as `kept`, which
        # `_merge_kept` leaves alone, and their recorded source with them so a
        # proposal is not relabelled as his on the way back.
        self.edge_watch.load(
            [(r.number, r.image) for r in self.survey if r.image is not None],
            self._survey_film or self.v_film.get())
        # As the sheet's button reads them, so the badges do not depend on
        # which way the sheet was opened: for a roll from outside this
        # session's rolls, the frames are scanned into a folder here.
        holds = self._holds_for_sheet()
        if stored is not None:
            self._open_sheet(state["offsets"], state["sources"],
                             rotations=state["rotations"], flips=state["flips"],
                             ticks=state["ticks"], options=state["options"],
                             done=done, holds=holds)
        else:
            self._open_sheet(out["offsets"], out.get("sources"),
                             rotations=out["rotations"], flips=out["flips"],
                             done=done, holds=holds)
        # This roll's turns, as its sheet now holds them -- laid over each
        # frame it decided -- for the passes of its frames that arrive next:
        # a walk added to it, a roll scanned into it.
        sheet = self.sheet
        decided = (set(sheet.rotations) | set(sheet.flips)
                   if sheet is not None else set())
        for result in self.survey:
            if result.number in decided:
                self.orientations[("frame", int(result.number))] = (
                    result.rotation, result.flipped)
        # The film is almost certainly not where the walk left it, and that no
        # longer matters: the roll goes to each frame by the transport's own
        # counter. What does matter, and only Stefan can see it, is that the
        # strip is back in the way it went in, because that is where the
        # counter starts from. Said rather than guessed at, and the sentence
        # differs by what he is about to do.
        #
        # The rest of the strip no run reached is past the walk, so on no
        # sheet: the Roll button takes it, from there to the end of the
        # strip, and "Scan chosen frames" never does.
        rest_said = ""
        if rest is not None:
            self.v_startat.set(str(rest))
            self.v_last.set("")
            if not quiet and not self._outside(folder):
                self._scan_when_rolled()
            # By its number across the roll, as the browser says it; the
            # box takes its place on the strip.
            from_rest = place_said(strip, rest)
            rest_said = (f"\n\nThe strip from {from_rest} on was never "
                         f"reached: \"first frame\" is set to {rest} and "
                         "\"last frame\" emptied -- then press Roll for the "
                         f"strip from {from_rest} on.")
            self._say(f"{name}: the strip from {from_rest} on was never "
                      "reached; Roll takes it, from the first frame set")
        if quiet:
            return True
        if done and remaining:
            messagebox.showinfo(
                "Continue this roll",
                f"{name}: {len(done)} of {len(out['wanted'])} frames "
                f"scanned, {len(remaining)} left "
                f"({places_said(strip, remaining)}).\n\n"
                "Those are ticked in the sheet and the ones already done are "
                "not"
                + (f" -- but {ticked_again_said(strip, again)}" if again
                   else "")
                + ". Its settings are back. Put the strip in the way it went "
                "in before and press \"Scan chosen frames\" -- the transport "
                "goes to each frame itself, from wherever the film is.\n\n"
                + STRIP_NUMBERS + "\n\n"
                # Said as the window does it: nothing calibrates by itself. A
                # window that has not calibrated since it opened asks first,
                # and one that has uses the reference it measured.
                + ("It uses the calibration this window made earlier."
                   if self.calibrated else
                   "It asks for a calibration first, which is the right default "
                   "rather than a limitation: a reference describes the sensor "
                   "at the exposure and gain of the pass that measured it, and "
                   "months later neither is the same. The question offers the "
                   "saved one too, with its age.")
                + (f"\n\nRestored: {', '.join(sorted(restored))}."
                   if restored else "") + rest_said)
        else:
            # "All of them" only when it is every frame walked. With a sheet
            # that asked for some, `remaining` runs out once those are done,
            # and six walked frames with two of them scanned were called "all
            # of them already scanned".
            walked = {r.number for r in out["results"] if r.number}
            if done and not remaining and walked <= set(done):
                scanned = (", all of them already scanned" if len(walked) > 1
                           else ", already scanned")
            elif done and not remaining:
                scanned = (f"; {places_said(strip, done)} scanned, which is "
                           "every frame asked for")
            else:
                scanned = ""
            if again:
                scanned += f" -- {ticked_again_said(strip, again)}"
            messagebox.showinfo(
                "Open a roll",
                f"{counted(len(out['results']), 'frame')} from {name}"
                + scanned
                + ".\n\n"
                + ("The positions are measured from these prescans every time "
                   "this opens, so the sheet shows what the frames say today "
                   "rather than what was recorded about them."
                   if self.look_only else STRIP_NUMBERS) + rest_said)
        return True

    def _restore_roll_settings(self, settings: dict) -> list[str]:
        """Put a roll's stored settings back into the controls.

        Returns which ones moved, so the operator is told rather than having
        the window silently change under him. Each is applied on its own and a
        bad value costs that control alone -- the same line `_restore` takes
        about a hand-edited settings file, and for the same reason: this file
        may be a year old and written by an older version.
        """
        moved = []
        for control, value in restorable(settings).items():
            variable = getattr(self, f"v_{control}", None)
            if variable is None:
                continue
            try:
                variable.set(value)
            except tk.TclError:
                continue
            moved.append(control)
        if moved:
            self._sync_format()
            self._sync_exposure()
            self._sync_film()
            self._show_estimate()
        return moved

    def on_scan_chosen(self, numbers: tuple[int, ...], approved=(),
                       options=None, readings=None, detected=None) -> bool:
        """Go to the first frame ticked, then scan only what was ticked.

        The frame numbers are places on the strip, so the roll goes to the
        first one from wherever the transport says the film is. It used to
        count back from where the walk had ended, which scanned the wrong
        frames without a word whenever the film had moved since -- by the
        window's buttons, by the scanner's own keys, or by a strip put back
        in to finish a roll another day.

        Runs even where there is no film. This is the sole writer of
        `approved.json` and the sole submitter of a `Roll` from the sheet, so
        stopping it here is stopping everything the sheet exists to reach --
        the backend refuses instead, and the refusal arrives as a failed job
        the way a real transport fault would.

        `approved` carries a position for every ticked frame -- his where he
        set one, the ensemble's where he did not, each saying which it is. They
        are written to `approved.json` first, so the numbers can be read back
        afterwards whatever the roll then does, and then **the roll holds every
        frame to its own**: `Roll(approved=...)` reaches `_hold_to_approved`,
        which moves film. A sentence here used to say nothing consumed them,
        left over from the increment before holding was wired up.

        `options` is what the sheet's own panel was set to, and it **wins**:
        the sheet is where a roll is decided, so the roll is scanned with what
        was set there rather than with whatever the window behind it shows.
        The window's controls are left alone, because they go on describing
        the next single scan. Absent -- a caller that has no sheet -- every
        value falls back to the window, which is what used to happen always.

        `readings` is the sheet's note per frame (`_ContactSheet.proposals`):
        for a position the detector decided, what it read. It goes into
        `approved.json` beside that position; see `_write_approved`.
        `detected` is the detector's own note per frame
        (`_ContactSheet.detected`), filed beside a position he set.

        True once the roll is handed over, and only then: the sheet closes on
        that, so a refusal or a Cancel leaves it open as it was.
        """
        if not numbers:
            return False
        if self._working():
            # The sheet stays open and readable while the scanner is working,
            # so this button is reachable mid-roll. Queueing a second roll
            # behind the first is not what anyone pressing it means.
            messagebox.showinfo(
                "Scan chosen frames",
                "The scanner is busy. Wait for it to finish, or stop it, then "
                "press this again -- the ticks stay where they are.")
            return False
        # Over the sheet when the sheet asked, so the prompt is not hidden
        # behind the window it was pressed in.
        over = (self.sheet.top if self.sheet is not None and self.sheet.alive()
                else None)
        if self._no_scanner("Scan chosen frames", over):
            return False
        if self._calibration_missing(over):
            return False
        # A frame ticked past where "Strip ends here" put the strip's end:
        # the session refuses it before the film moves, but only after this
        # question had offered it by a number the next strip took, and the
        # refusal reached the caption as a ValueError (the 2026-10-12 tour).
        # Said here, before anything is asked, as the session would say it.
        ends = (place_of(self._sheet_roll, strict=False)
                if self._sheet_roll is not None else None)
        stub = ([n for n in numbers if ends.past_its_end(n)]
                if ends is not None else [])
        if stub:
            messagebox.showinfo(
                "Scan chosen frames",
                f"{counted(len(stub), 'frame')} ticked "
                f"({_and_joined(stub)} of strip {ends.strip}) "
                f"{is_are(len(stub))} past where strip {ends.strip} ends "
                f"(frame {ends.ends_at}): untick "
                f"{'it' if len(stub) == 1 else 'them'}, or choose \"Count to "
                "the end of the strip\" on its sheet first. Nothing was "
                "moved.", parent=over)
            return False
        if options:
            # Read from the sheet rather than through `_dpi`, which reads the
            # window's box and would complain about a value this roll is not
            # going to use.
            try:
                dpi = int(str(options.get("dpi", "")).strip())
                predpi = int(str(options.get("predpi", "")).strip())
            except (TypeError, ValueError):
                dpi = predpi = None
            if (dpi is None or predpi is None
                    or not 25 <= dpi <= 7200 or not 25 <= predpi <= 7200):
                messagebox.showerror(
                    "Scan chosen frames",
                    "The contact sheet's scan dpi and prescan dpi have to be "
                    "whole numbers between 25 and 7200.")
                return False
        else:
            dpi, predpi = self._dpi(), self._prescan_dpi()
            if dpi is None or predpi is None:
                return False

        def chose(key, variable):
            """The sheet's value where it set one, else the window's."""
            return options[key] if options and key in options else variable.get()

        infrared = bool(chose("ir", self.v_ir))
        fast_ir = bool(chose("fast_ir", self.v_fast_ir))
        film = str(chose("film", self.v_film))
        meter = str(chose("meter", self.v_meter))
        # No aim where every frame is held: a frame with a position is never
        # aimed (`scan_roll`), so the sheet's tick acted on nothing and is
        # gone (GUI2-10), and the roll records "aim each frame" as not
        # applicable (`session.aiming_said`) -- reopened, the walk's own
        # answer stands. The scanner is sent what it always was. A caller
        # with no positions -- no sheet -- aims as the window says.
        correct = False if approved else bool(self.v_correct.get())
        # Derived, never offered as its own control -- `_sync_mono`'s rule.
        # Only derived when the sheet actually changed the film, so a black
        # and white roll whose single channel was deliberately turned off in
        # the window stays off.
        mono = (self.v_mono.get() if film == self.v_film.get()
                else film == FILM_BW)

        if approved and self._survey_predpi and self._survey_predpi != predpi:
            self._say(f"prescan held at {self._survey_predpi} dpi to match the "
                      f"survey your positions were set on (you asked for "
                      f"{predpi})")
            predpi = self._survey_predpi
        if self._refused_up_front(
                "Scan chosen frames", predpi, dpi, infrared=infrared,
                film=film, parent=self.sheet.top
                if self.sheet is not None and self.sheet.alive() else None):
            return False
        start_at, span = chosen_span(numbers)
        walked = len(self.survey)
        per = self._per_frame_seconds(dpi=dpi, ir=infrared, fast_ir=fast_ir)
        move, move_s = seek_note(self._transport, start_at)
        if self._walk_replaced():
            return False
        try:
            folder = self._roll_folder()
        except ForeignStrip as exc:
            messagebox.showinfo(
                "Scan chosen frames", f"{exc}\n\nNothing has moved.",
                parent=self.sheet.top if self.sheet is not None
                and self.sheet.alive() else self.root)
            return False
        # A frame the session is still filing -- the last of a roll that
        # has just ended -- is neither taken again nor left out in silence:
        # read from roll.json alone it was not done yet, and was scanned a
        # second time (RES-n8).
        pending = pending_in(folder, self.session.live_manifest)
        filing = [n for n in numbers if n in pending]
        if filing:
            numbers = tuple(n for n in numbers if n not in pending)
            approved = tuple(a for a in approved if a.number not in pending)
            if not numbers:
                messagebox.showinfo(
                    "Scan chosen frames",
                    f"{places_said(place_of(folder, strict=False), filing)}"
                    f" {is_are(len(filing))} still being filed -- not taken "
                    "again. Nothing has moved.",
                    parent=over or self.root)
                return False
            start_at, span = chosen_span(numbers)
            move, move_s = seek_note(self._transport, start_at)
        # Named before anything is spent: "All", or Return through the
        # position window, ticks a frame already scanned as readily as any
        # other, and the count alone did not say that hours of it were
        # scans being taken a second time.
        scanned = self._scanned_in(folder)
        again = [n for n in numbers if n in scanned]
        # Held to the settings the roll's frames were taken at, across the
        # whole roll: a frame not done at other settings is refused, a
        # retake of done ones named (`_held_to_roll`).
        held = self._held_to_roll(
            folder, {"resolution": dpi, "infrared": infrared, "film": film,
                     "meter": meter, "fast_infrared": fast_ir},
            new=any(n not in scanned for n in numbers),
            title="Scan chosen frames", parent=over)
        if held is None:
            return False
        sheet = self.sheet
        if not messagebox.askokcancel(
            "Scan chosen frames",
            (f"Scan {len(numbers)} of the {walked} frames walked: "
             if walked != 1 else "Scan the one frame walked: ")
            + places_said(place_of(folder, strict=False), numbers, scan=True)
            + ".\n\n"
            f"{move}\n\n"
            f"At {dpi} dpi{' with infrared' if infrared else ''}, {film}, "
            f"roughly {_duration(per * len(numbers) + move_s)}. The frames "
            "nobody ticked cost their advance only."
            + (f"\n\nAlready scanned, and scanned again if you go on: "
               f"{places_said(place_of(folder, strict=False), again)}. Each "
               "new take replaces its frameNN.tif; the library keeps both."
               if again else "")
            + (f"\n\nStill being filed: "
               f"{places_said(place_of(folder, strict=False), filing)} -- "
               "not taken again." if filing else "")
            + held
            + self._approved_note(approved)
            + self._options_note(options) + self._edges_pending()
            + "\n\nStart?",
            # Over the sheet, which is still open while he decides.
            parent=sheet.top if sheet is not None and sheet.alive()
            else self.root,
        ):
            return False
        # A sheet opened from outside this session's rolls is scanned into a
        # folder here, and its walk goes with it, as a walk added to it does.
        # Without it the roll here held the frames and approved.json and no
        # survey: it reopened as "scanned without walking the strip first",
        # and the positions could no longer be reviewed against their
        # prescans.
        if self._sheet_roll is not None:
            try:
                carried = carry_walk(self._sheet_roll, folder,
                                     rolls=self.session.rolls, who=self._who)
            except (OSError, ValueError) as exc:
                messagebox.showerror(
                    "Scan chosen frames",
                    f"The walk in {self._sheet_roll} could not be copied into "
                    f"{folder}: {exc}\n\nNothing has moved.")
                return False
            if carried:
                self._say(f"copied the walk in {self._sheet_roll} into "
                          f"{folder}, which the roll is scanned into; the "
                          "original is left as it was")
        # The roll's name, never a strip's folder: a strip is its roll's.
        shown = roll_name_of(folder)
        self._show_roll_name(
            shown, ours=shown != self.fields["roll"].get().strip(),
            folder=library.lock_folder(folder))
        self._write_approved(approved, folder, readings, detected)
        # Counted like a roll from the Roll button, so the header says which
        # of the chosen frames it is on and the line under the picture times
        # it. A sheet's roll used to run with neither.
        self._roll_wall_start = time.monotonic()
        self._roll_seeking = True
        self._roll_dry = False
        self._roll_frames_total = len(numbers)
        self._roll_frames_done = 0
        self._roll_seconds_per_frame = per
        self._update_roll_eta()
        # Each picture's answer, as it is commissioned, for its passes after
        # the roll. Its own frames come back shown as it writes them
        # whatever is turned meanwhile (`_written_as`): without that a roll
        # returned pictures the filmstrip drew one way up and the file on
        # disk held another.
        for record in approved:
            self.orientations[("frame", record.number)] = (
                record.rotation, bool(record.flipped))
        # The move is the roll's own, not a `Move` queued in front of it: a
        # `Move` reports a short rewind by returning a string, which the worker
        # logs before taking the next job -- so a rewind that got three of
        # fourteen was followed straight away by a roll scanning frames it had
        # mis-numbered. The roll refuses instead, and scans nothing.
        self._hand_over(Roll(
            frames=span, start_at=start_at, resolution=dpi,
            prescan_resolution=predpi, infrared=infrared,
            fast_infrared=fast_ir,
            film=film, meter=meter, dry_run=False,
            correct=correct, only=tuple(numbers),
            # Not the 'reverse the direction' tick: that is for hand moves,
            # and holding is closed in the picture -- see `_hold_to_approved`.
            approved=tuple(approved),
            mono=mono,
            mono_channel=self.v_mono_channel.get(),
            # Beside its walk and its `approved.json`; see `_roll_folder`.
            out=str(folder),
            notes=self._notes(), tags=self._tags(),
        ))
        return True

    def _walk_replaced(self) -> bool:
        """Refuse a commission from a sheet whose walk was written over
        since it was opened -- a `tools/scan_roll.py` walk into the folder
        the window shows, which a view lock does not keep out: its positions
        were measured on prescans that are no longer there. By the walks'
        ids only: a walk from before them is known by its file's time, which
        its writer's late answers move, and was never refused for it."""
        seen = self._sheet_walk()
        now = walk_stamp(self._sheet_roll)
        if not (isinstance(seen, str) and isinstance(now, str) and seen != now):
            return False
        folder = Path(self._sheet_roll)
        messagebox.showerror(
            "Scan chosen frames",
            f"The walk in {folder.name} was replaced since this sheet was "
            "opened, so its positions were measured on prescans that are no "
            f"longer there. Nothing has moved. Reopen {folder.name} under "
            "Rolls ... to see the walk it holds now.",
            parent=self.sheet.top if self.sheet is not None
            and self.sheet.alive() else self.root)
        return True

    def _scanned_in(self, folder) -> set[int]:
        """The frames of the sheet's roll that are already scanned.

        What the reopened roll said, and what its roll.json says now -- a
        commission earlier in this session has scanned frames since. A frame
        whose entry went to the trash is in neither: Delete takes it out of
        what the reopened roll said (`on_delete`).
        """
        return set(self._sheet_done) | done_in(
            folder, self.session.root, live=self.session.live_manifest)

    def _options_note(self, options) -> str:
        """Name the settings this roll uses that the window does not show.

        The sheet is opened on top of the window it was pre-filled from, and
        the window goes on showing its own values afterwards. A roll scanned
        at something other than what is visible behind the dialog is the one
        surprise this panel can cause, so the dialog says it before anything
        moves.
        """
        if not options:
            return ""
        named = {"dpi": "scan dpi", "predpi": "prescan dpi", "ir": "infrared",
                 "fast_ir": "infrared at scan resolution", "film": "film",
                 "meter": "metering"}
        differ = []
        # What the sheet offers, and nothing else a caller hands in: its
        # "correct", gone, would be said as a setting this roll is scanned at.
        for key, value in options.items():
            if key not in _ContactSheet.OPTIONS:
                continue
            variable = getattr(self, f"v_{key}", None)
            if variable is not None and str(variable.get()) != str(value):
                differ.append(f"{named.get(key, key)} {value}")
        if not differ:
            return ""
        return ("\n\nSet on the contact sheet, not in the main window: "
                + ", ".join(differ) + ".")

    def _approved_note(self, approved) -> str:
        """What the sheet's positions will do, said plainly in the dialog.

        This is the last thing shown before the film moves, and it used to say
        that every frame carried "a position you set by hand". That was true
        when typing was the only way to have one. Since the sheet began
        pre-filling a position for every frame it can read, most of them are
        the ensemble's -- so the dialog was attributing the machine's decisions
        to him, on the screen where he confirms them.

        Counted by provenance now, in the ensemble's own words, which is what
        `Approved.source` was added to carry. `tools/scan_roll.py` prints the
        same breakdown for the same reason.
        """
        carried = [a for a in approved if a.offset_mm]
        if not carried:
            return ""
        tally: dict[str, int] = {}
        for a in carried:
            tally[a.source or "operator"] = tally.get(a.source or "operator", 0) + 1
        said = ", ".join(
            f"{tally[name]} {label}" for name, label in (
                ("operator", "by you"),
                ("measured", "where two detectors agreed"),
                ("unconfirmed", "from one detector, uncorroborated"),
                ("neighbours", "read from the frames either side"),
                ("none", "that nothing could read"),
            ) if tally.get(name))
        note = (f"\n\n{counted(len(carried), 'frame')} "
                f"{'carries' if len(carried) == 1 else 'carry'} a position: "
                f"{said}."
                "\n\nEach is used exactly as given.")
        # The automatic nudge is not mentioned. Every ticked frame gets an
        # `Approved`, including the ones left at zero, and the driver takes
        # the held branch for any frame that has one -- so it could not act
        # on a single frame of a commissioned roll. This said it "stays on
        # for the frames you did not adjust", which never happened, and the
        # sheet's tick for it is gone (`_ContactSheet.OPTIONS`).
        return note

    def _write_approved(self, approved, folder, readings=None,
                        detected=None) -> None:
        """Record the positions beside the roll before anything is scanned.

        Written from here rather than by the scan thread because it is the
        operator's decision, made before the roll starts -- and it is the file
        that says what was asked for, whatever the roll then does about it.
        A kilobyte of JSON with the device idle between jobs.

        ``folder`` is the one the roll is scanned into (`_roll_folder`), so
        the decisions and the frames they are about cannot part.

        A position the detector decided carries what it read (``reading``:
        the edges, each member's answer, the prescan's width and digest, the
        frames it was read against and one digest of theirs, the move in
        units and columns, the frame width it centred on) and which code
        read it (``read_by``). The snapped offset alone could not say whether
        a frame that came out off centre was read wrong or moved wrong, and a
        detector changed since could not be told from the one that ran.

        A position he set carries the detector's reading of that frame too
        (``detector_reading``, from ``detected``), where there was one: the
        frames he overruled are where a reading is most worth having, and
        they kept none.
        """
        if not approved:
            return
        readings = readings or {}
        detected = detected or {}
        read_by = None
        #: The folder held as written into while this writes in it, against
        #: a `tools/scan_roll.py` or another window doing the same.
        held = None
        try:
            folder = Path(folder)
            # A strip by its roll's folder, as everything that writes in a
            # roll of strips holds it (`library.lock_folder`).
            held = locks.take(locks.ROLL, library.lock_folder(folder),
                              exclusive=True,
                              what=getattr(self, "_who", "the window"))
            name = recorded_roll_name(folder) or folder.name
            # Under this session's rolls (`_roll_folder`), which may have its
            # own folder made and never the ones above it (`storage`).
            storage.make_within(self.session.rolls, folder)
            path = folder / "approved.json"
            # Merged, frame by frame, into what earlier commissions decided.
            # Each used to replace the file with only the frames ticked this
            # time, so resuming a roll to scan its last three frames erased
            # the turns of the first fifteen -- which Export then used. One
            # that cannot be read is kept aside and said, never taken for
            # "no decisions" and written over.
            earlier = earlier_manifest(path, say=self._say)
            # The walk these were decided on, which the file names (P22-b):
            # the sheet's, as it was when the sheet was opened on it. And a
            # file decided on another walk than the folder's is no earlier
            # decision about these frames: not merged, kept beside as the
            # copy this write keeps, and said.
            walk = self._sheet_walk()
            walk_id = walk if isinstance(walk, str) else None
            here = walk_stamp(folder)
            theirs = earlier.get("walk_id")
            if (isinstance(here, str) and isinstance(theirs, str) and theirs
                    and theirs != here):
                self._say(f"approved.json in {folder.name} was decided on "
                          "another walk; it is kept as approved.json.bak, "
                          "and these decisions start it afresh")
                earlier = {}
            # An older file's numbers onto the strip's first, the way
            # `read_survey` reads them, so a kept frame is not filed under
            # another frame's number beside the new ones.
            shift = 0
            if earlier and earlier.get("numbering") != NUMBERING:
                shift = approved_legacy(
                    read_manifest(folder / "survey.json", say=self._say),
                    read_manifest(folder / "roll.json", say=self._say))
            now = {int(a.number) for a in approved}
            read = {}
            for a in approved:
                # His position keeps the detector's reading beside it; any
                # other keeps the reading it is.
                key, note = (("detector_reading", detected.get(a.number))
                             if a.source == "operator"
                             else ("reading", readings.get(a.number)))
                if not note:
                    continue
                if read_by is None:
                    # The driver's and the reader's own source, as imported:
                    # a commit names them only from a clean tree, and the
                    # reader takes code from the driver. Never git: this is
                    # the Tk thread, and `provenance` asked it twice.
                    read_by = library.code_identity()
                try:
                    kept_note = json_ready(dict(
                        note, frame_units=self.edge_watch.frame_units))
                except Exception as exc:                  # noqa: BLE001
                    # A note is bookkeeping: one that will not go into the
                    # file costs this field, never the file and its turns.
                    kept_note = {"record_error": f"{type(exc).__name__}: {exc}"}
                read[a.number] = {key: kept_note, "read_by": read_by}
            kept = []
            for record in earlier.get("frames") or ():
                try:
                    number = int(record["number"]) + shift
                except (KeyError, TypeError, ValueError):
                    continue
                if number not in now:
                    kept.append(dict(record, number=number))
            decided_utc = utc_now()
            write_manifest(path, {
                "roll": name,
                # The numbers below are places on the strip; a file without
                # this was numbered the way its walk was. See `read_approved`.
                "numbering": NUMBERING,
                **({"walk_id": walk_id} if walk_id else {}),
                "frames": sorted(kept + [
                    dict({"number": a.number,
                          "offset_mm": round(a.offset_mm, 4),
                          "rotation": int(a.rotation),
                          "flipped": bool(a.flipped),
                          "reference_entry": str(a.reference_entry or ""),
                          "source": str(a.source or "operator"),
                          # When, so a reopened sheet can tell a decision
                          # made on it since from this one (`merge_sheet`).
                          "decided_utc": decided_utc},
                         # His "as surveyed", said as one: a zero is otherwise
                         # what every untouched ticked frame carries, and
                         # older files called those his too.
                         **({"as_walked": True}
                            if a.source == "operator" and not a.offset_mm
                            else {}),
                         **read.get(a.number, {}))
                    for a in approved], key=lambda r: int(r["number"])),
            }, keep_previous=True)
        except locks.Refused as exc:
            # Another program writes into this folder. The roll is handed
            # over all the same, and the session refuses it before the film
            # moves, naming the same holder (`ScanSession._hold_roll`) --
            # unless it has let go by then. "Scanning anyway" promised a
            # scan the next line took back.
            self._say(f"{folder} is in use by {exc.who}: approved.json not "
                      "written, and the roll will be refused unless that "
                      "lets go of it first")
            return
        except storage.DriveMissing as exc:
            # The rolls folder cannot be had (`make_within`): its drive has
            # gone, or it is a file. The session refuses the roll for the
            # same reason before the film moves (`ScanSession._preflight`,
            # `missing_root` of the same rolls root), so "scanning anyway"
            # would promise a scan the next line takes back.
            self._say(f"approved.json not written in {folder} ({exc}), and "
                      "the roll will be refused until the rolls folder is "
                      "back")
            return
        except Exception as exc:                          # noqa: BLE001
            # Broad on purpose. This file is a note about what was asked for;
            # the scan is the work. Losing the note must never cost the roll,
            # and it did once -- a Path where a str was expected raised inside
            # json.dumps, took the Tk callback with it, and the operator saw
            # a button that did nothing at all.
            # The folder is named here, not left to the exception: on Windows
            # a folder that cannot be made is reported by its parent's name.
            self._say(f"could not write approved.json in {folder} ({exc}); "
                      "scanning anyway")
            return
        finally:
            if held is not None:
                held.release()
        told = ", ".join(f"{a.number}:{say_units(a.offset_mm)}"
                         for a in approved if a.offset_mm) or "none moved"
        self._say(f"approved positions written to "
                  f"{folder / 'approved.json'} ({told})")

    def _moving_refused(self) -> bool:
        """True, having said why, when the film must not be moved now.

        The buttons grey while the scanner works; the keys and an aim-click
        on the picture did not, and queued a move that ran wherever the job
        ended -- a distance measured on one frame applied to another. Asked
        of `_working()`, not of `busy` alone: `busy` comes a tick after the
        job is handed over, and a key in that tick after a Scan still queued
        its move behind the pass.
        """
        if self._working():
            self._say("the scanner is working -- the film is not moved "
                      "while it does")
            return True
        why = self._scannerless()
        if why:
            self._say(f"the film is not moved -- {why[0].lower()}{why[1:]}")
            return True
        return False

    def on_move_frames(self, frames: int) -> None:
        if self._moving_refused():
            return
        self._hand_over(Move(frames=frames))
        self._aim_from = None                    # the film is somewhere else now

    def on_nudge(self, direction: int, millimetres: float | None = None) -> None:
        if self._moving_refused():
            return
        if millimetres is None:
            values = _numbers(self.v_fine.get())
            if not values:
                messagebox.showerror("Fine adjustment", "That has to be a number.")
                return
            # The field is in the transport's own unit. Everything below this
            # line, and the whole session interface, stays in millimetres.
            millimetres = abs(values[0]) * MM_PER_UNIT
        # Asked of the planner rather than of a rounded copy of its thresholds.
        # Two hand-maintained numbers used to decide here what the mover would
        # accept, and a window that refuses what the transport would happily do
        # reads to the operator as a broken button.
        if deliverable_mm(millimetres) == 0:
            messagebox.showerror(
                "Fine adjustment",
                f"The smallest move the transport can make is "
                f"{say_units(FINE_STEP_MM, signed=False)}.\n\n"
                f"{say_units(millimetres, signed=False)} is less than that, so "
                f"it would not move the film at all.\n\nparam 0 was sent to "
                "the scanner and measured: it is accepted and does nothing.")
            return
        try:
            plan_nudges(millimetres)
        except ValueError as exc:
            messagebox.showerror(
                "Fine adjustment",
                f"{say_units(millimetres, signed=False)} is further than a fine "
                f"adjustment goes.\n\n{exc}\n\n"
                "Use the slide buttons for anything this far.")
            return
        if millimetres > MAX_TRAVEL_MM:
            messagebox.showerror(
                "Fine adjustment",
                f"{say_units(millimetres, signed=False)} is past what one "
                f"command delivers "
                f"({say_units(MAX_TRAVEL_MM, signed=False)}), and chaining "
                "them pays the ramp and the scatter again for each.\n\n"
                "Use the slide buttons for anything this far.")
            return
        if self.v_reverse.get():
            direction = -direction
        if self._last_nudge and direction != self._last_nudge:
            self._say("changing direction: expect the first two or three steps "
                      "to go into backlash")
        self._last_nudge = direction
        self._hand_over(Move(millimetres=millimetres * direction))
        self._aim_from = None                    # the film is somewhere else now

    def on_stop(self) -> None:
        # A job not yet begun is dropped by the stop, and reports only that
        # (`_undo_hand_over`); one taken a moment ago is stopped before its
        # first command.
        self.session.request_stop()
        self._say("stop requested -- finishing what is already running")
        self.b_stop.configure(state="disabled")

    def on_abort(self) -> None:
        answer = simpledialog.askstring(
            "Force abort",
            "This abandons the read that is running.\n\n"
            "The frame is lost, and the scanner will almost certainly need a "
            "power cycle at its own switch before it will talk again. It can "
            "also take this window down with it.\n\n"
            "Type ABORT to do it anyway:", parent=self.root)
        if (answer or "").strip().upper() != "ABORT":
            self._say("force abort cancelled")
            return
        self._say("force abort: closing the transport under the read")
        self.session.force_abort()

    def on_close(self) -> None:
        # The settings first, and asked about first: for a walk not yet
        # commissioned they are the only record of the sheet's decisions.
        # Before the question below, whose Yes stops every job from here on
        # (`_stop_after_the_frame_in_flight`) and is never taken back, and
        # before the sheet is put away -- so "keep the window open" costs
        # nothing at all.
        if not self._settings_kept():
            return
        # A job handed over and not begun counts, calibrations included:
        # `busy` follows the worker's report, read up to a tick after the
        # job was taken, and a Quit inside that tick asked nothing and let
        # the whole job run behind the shutdown. The session's own count
        # knows it from the hand-over (`ScanSession.outstanding`).
        if self.busy or self.session.outstanding():
            # Three answers, because "wait" alone meant waiting for a whole
            # roll and everything queued behind it -- hours -- with no way to
            # say "after this frame", which is what invites a hard kill.
            answer = messagebox.askyesnocancel(
                "Quit",
                "The scanner is still working. Quitting never abandons a read "
                "-- that is what wedges the scanner.\n\n"
                "Yes: stop after what is in flight -- a roll's frame, or a "
                "scan's pass or metering probe -- then quit.\n"
                "No: let everything queued finish, then quit.\n"
                "Cancel: keep working.", parent=self.root)
            if answer is None:
                return
            if answer:
                self._stop_after_the_frame_in_flight()
        self.closing = True
        self.v_state.set("closing ...")
        # Every way out of the sheet keeps what was decided in it, and quitting
        # with it open is one: for a walk not yet commissioned its state is the
        # only record of the ticks, positions and turns. Before `_remember`,
        # which is what writes them. Saved and asked about above; a failure
        # here, of what changed since, is only said.
        self._stop_copying()
        self._close_sheet()
        self._remember()
        self.session.shutdown()
        self._wait_to_quit()

    def _settings_kept(self) -> bool:
        """Save the settings, the open sheet's decisions with them, and ask
        what to do where they will not go: False to keep the window open.

        Try again, quit without them, or keep the window open -- to free
        the disk, say, and quit again. Stefan's call, 2026-10-09.
        """
        while True:
            sheet = self.sheet
            if sheet is not None and sheet.alive():
                try:
                    self._file_sheet_state(sheet.state())
                except Exception as exc:                  # noqa: BLE001
                    self._say(f"could not keep the contact sheet settings: "
                              f"{exc}")
            ok, why = self._remember()
            if ok:
                return True
            key = self._sheet_key()
            sheets = self.remembered.get("sheet")
            lost = (f", and with them the contact sheet's decisions for {key}"
                    if key is not None and isinstance(sheets, dict)
                    and key in sheets else "")
            answer = messagebox.askyesnocancel(
                "Quit",
                f"The window's settings could not be saved ({why}){lost}."
                "\n\nYes: try again.\nNo: quit without them.\n"
                "Cancel: keep the window open.", parent=self.root)
            if answer is None:
                return False
            if not answer:
                return True

    def on_interrupt(self, console_closed: bool = False) -> None:
        """Ctrl-C in the terminal the window came from, SIGTERM, or that
        terminal closing: Quit's "stop after the frame in flight".

        Asked of nobody, because whoever sent it may not be at the window --
        the terminal may be gone. Each of these used to end the process at
        once, the scanner thread inside its bulk read and the writer part way
        through an entry: the abandoned read, left INCOMPLETE. A second one
        aborts, as in the tools (`DeferredInterrupt`).

        Whether or not the window has seen a job start: one handed over and
        not yet begun is not the frame in flight, and used to run to its end
        -- a whole roll -- before the quit. Whatever the worker has taken is
        stopped before its first command, a single scan's metering before
        its next probe, and nothing starts after (`ScanSession.halt`).

        ``console_closed`` is Windows' console closing (`ConsoleClose`),
        which Windows follows about 5 s later by ending the process whatever
        it is doing. Those seconds put away an idle window; they do not
        finish a pass, and the log says so. It stops the job even when Quit
        has already said "let everything queued finish": what is let run
        then is device work that is certain to be cut off part way, and a
        pass that ends inside the 5 s leaves the device idle at the end. A
        slide's colour pass is not started after it (`_without_colour`).
        `main` has the session halt from Windows' own thread as the console
        closes, before this runs on Tk's; asked again here, it is already in
        force.
        """
        if console_closed:
            self._say("the console this window was started from was closed: "
                      "stopping after the frame in flight, then quitting. "
                      "Windows ends the process about 5 s after its console "
                      "closes, whatever it is doing, so a pass still being "
                      "read then is abandoned. `make run` starts the window "
                      "apart from its console, where closing it cannot do "
                      "this.")
            self._stop_after_the_frame_in_flight(console_closed=True)
        if self.closing:
            return
        if not console_closed:
            self._say("interrupted from the terminal: stopping after the frame "
                      "in flight, then quitting")
            self._stop_after_the_frame_in_flight()
        self.closing = True
        self.v_state.set("closing ...")
        self._stop_copying()
        self._close_sheet()
        ok, why = self._remember()
        if not ok:
            # Nobody to ask: whoever sent this may not be at the window, and
            # a closed console's process is ended about 5 s from now. So the
            # decisions go where a person can find them, as the console's own
            # close is written down (`console.logged_to`).
            how = ("its console was closed" if console_closed
                   else "it was interrupted (Ctrl-C or SIGTERM)")
            logged_to(self._logs / "window-closed.log", say=self._say)(
                f"the window closed because {how}, and its settings could "
                f"not be saved ({why}); the contact sheet's decisions were: "
                + json.dumps(self.remembered.get("sheet") or {},
                             default=str, sort_keys=True))
        self.session.shutdown()
        self._wait_to_quit()

    def _stop_after_the_frame_in_flight(self, console_closed: bool = False
                                        ) -> None:
        """Quit's stop: finish what is running, and start nothing after it.

        The session's halt (`ScanSession.halt`): every job the worker has
        not taken is dropped, the one it has stops at its next safe point --
        a roll after its frame, a scan after its pass or metering probe --
        and none starts after it, ever; when nothing is running it costs
        nothing. Kept in the window, it was asked again as each job's start
        was read, a tick late: a job taken in that tick could run whole.
        """
        self.session.halt(console_closed=console_closed)

    def _start_writing(self, run, name: str) -> None:
        """Run a thread that writes files, and let Quit wait for it."""
        thread = threading.Thread(target=run, daemon=True, name=name)
        self._writing = [t for t in self._writing if t.is_alive()] + [thread]
        thread.start()

    def _wait_for_readers(self) -> None:
        """Wait for every thread of this window that reads or writes library
        entries: the session's `before_compact`, run on its own thread once
        the scanner has closed.

        Save all, Export and Save As read entries through
        `library.corrected`, and the full-resolution view reads one; each
        holds its files open, and on Windows a compaction swapping a file
        someone holds open gave up after 0.4 s, part way through. Quit
        already waited for the writers, so this costs it nothing; the
        scanner is closed by now, so nothing waits with it open.
        """
        me = threading.current_thread()
        while True:
            busy = [t for t in (*self._writing, *self._reading)
                    if t.is_alive() and t is not me]
            if not busy:
                return
            for thread in busy:
                thread.join()

    def _wait_to_quit(self) -> None:
        """Close once the worker has finished with the device, and no sooner.

        There is no timeout on purpose: if a pass is in flight, the wait is the
        whole point -- tearing the window down would abandon the read and cost
        a power cycle. But a session whose worker has already stopped closes at
        once, which is what the no-scanner case needs. It used to wait forever
        there, for a "closed" event that had already been and gone before the
        window knew it was closing, and the only way out was to kill it.
        """
        if not self._alive:
            return
        thread = self.session._thread
        # And for any file still being written by Save all or Export: those
        # threads die with the window, which left truncated files behind.
        writing = [t for t in self._writing if t.is_alive()]
        if (not writing and (self._session_closed or thread is None
                             or not thread.is_alive())):
            self._quit()
            return
        if writing:
            self.v_state.set("closing -- finishing the files being written ...")
        self._later(150, self._wait_to_quit)

    def _quit(self) -> None:
        # The session's last words, which the pump has not read yet -- it
        # can close a moment before they are: into the kept log alone, the
        # pane going with the window. Then the log is closed.
        try:
            last = self.session.poll()
        except Exception:                                 # noqa: BLE001
            last = []
        for event in last:
            if event.kind in ("log", "failed") and event.text:
                self._keep(event.text)
            if event.kind == "failed" and getattr(event, "detail", ""):
                self._keep(event.detail.rstrip())
            # A Quit while a job ran ends here, with the pump stopped, and
            # the session's close went unsaid (the 2026-10-12 tour).
            if event.kind == "closed":
                self._keep("the scanner session closed")
        self._keep("the window closed")
        self._kept.close()
        self._alive = False
        self.edge_watch.close()
        for held in self.__dict__.pop("_views", {}).values():
            held.release()
        # Anything still scheduled is cancelled first. A callback that fires
        # after the widgets are gone cannot do anything useful, and Tk complains
        # about the command it can no longer find -- which is how a clean quit
        # ends up printing errors.
        for job in list(self._pending):
            try:
                self.root.after_cancel(job)
            except (tk.TclError, ValueError):
                pass
        self._pending.clear()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def _edges_changed(self, progress) -> None:
        """The frame-edge reader said something new: the light, the count, the sheet."""
        self.edge_light.itemconfigure(self._edge_bulb,
                                      fill=EDGE_LIGHT[progress.state])
        self.v_edges.set(edges_label(progress))
        if (self.sheet is not None and self.sheet.alive()
                and self.sheet.generation == progress.generation):
            self.sheet.take_readings(progress.offsets, progress.notes)
            self.sheet.v_edges.set(edges_label(progress))
        said = (progress.generation, progress.state)
        if (progress.state in (frame_edges.DONE, frame_edges.FAILED)
                and self._edge_said != said):
            self._edge_said = said
            self._say(proposals_said(progress.notes)
                      or f"frame edges: {progress.done} frame(s) read")
            for problem in progress.errors:
                self._say(f"frame edges: {problem}")

    def _edges_pending(self) -> str:
        """For the dialog that starts a roll: whether positions are still coming."""
        progress = self.edge_watch.progress()
        if progress.state != frame_edges.READING:
            return ""
        return (f"\n\nThe frame edges are still being read ({progress.done} of "
                f"{progress.total}). A frame the reader has not placed yet is "
                "scanned where the walk left it.")

    def _later(self, milliseconds: int, call):
        """`after`, remembered so it can be cancelled when the window closes."""
        if not self._alive:
            return None

        def run():
            self._pending.discard(job)
            if self._alive:
                call()

        job = self.root.after(milliseconds, run)
        self._pending.add(job)
        return job

    # -- the event pump ----------------------------------------------------

    def _pump(self) -> None:
        """Everything the threads have handed back, then the next tick.

        The next tick is booked whatever happens in between, and each message
        is handled on its own. It used to be booked on the last line, so one
        exception anywhere above it -- a sheet torn down under its readings,
        a pyramid too big for memory -- stopped the pump for good, and the
        events `session.poll` had already drained behind it were dropped with
        it: the worker scanned and filed on, and the window showed nothing
        more, not even the job's end, with its buttons greyed. An operator
        who took that for a hang and killed the process abandoned the read in
        flight, which is the wedge.
        """
        if not self._alive:
            return
        try:
            self._drain()
        finally:
            self._later(POLL_MS, self._pump)

    def _safely(self, what: str, call, *args) -> None:
        """Run one handler; a failure is said and costs that message only."""
        try:
            call(*args)
        except Exception as exc:                          # noqa: BLE001
            # Printed in full as well, since the log line is for the operator
            # and the traceback is for whoever fixes it -- and kept, where
            # stderr is kept only under `make run` on Windows.
            traceback.print_exc()
            self._keep(f"the window could not handle {what}:\n"
                       + traceback.format_exc())
            try:
                self._say(f"the window could not handle {what}: "
                          f"{type(exc).__name__}: {exc}")
            except Exception:                             # noqa: BLE001
                pass

    def _drain(self) -> None:
        version = self.edge_watch.version
        if version != self._edge_seen:
            self._edge_seen = version
            self._safely("the frame edges", self._edges_changed,
                         self.edge_watch.progress())
        while True:
            try:
                message = self._saves.get_nowait()
            except queue.Empty:
                break
            self._safely("a save", self._saved, message)
        while True:
            try:
                seq, image, problem, how, rail = self._reads.get_nowait()
            except queue.Empty:
                break
            if problem:
                self._say(problem)
            self._safely("the full-resolution pixels", self._loaded, seq, image,
                         how, rail)
        while True:
            try:
                token, counts, clipped, problem = self._measured.get_nowait()
            except queue.Empty:
                break
            if token != self._histogram_token:
                continue          # a later measurement is already the answer
            if problem:
                self._say(f"could not measure: {problem}")
                self._safely("the histogram", self.histogram.failed)
            else:
                self._safely("the histogram", self.histogram.show,
                             counts, clipped)
        for event in self.session.poll():
            self._safely(f"a {event.kind!r} event", self._handle, event)
            if not self._alive:
                return
        # Ticks the roll countdown between frames, not only when one lands --
        # cheap string formatting, and "left" that only moved at frame
        # boundaries would sit still for minutes at a time.
        self._update_roll_eta()

    def _saved(self, message) -> None:
        """One message from a Save all, an Export or a Duplicate thread."""
        if message[0] == "line":
            self._say(message[1])
        elif message[0] == "copying":
            self._copy_progress(*message[1:])
        elif message[0] == "copied":
            self._copied(*message[1:])
        elif message[0] == "failed":
            _, name, why = message
            self._say(f"could not save {name}: {why}")
            messagebox.showerror("Save as", f"{name} was not saved.\n\n{why}",
                                 parent=self.root)
        else:
            self._saving = False
            # A batch says what it wrote: passes from Save all, frames from
            # Export, which said "saved 1 of 1 pass" of a frame.
            written, total = message[1], message[2]
            noun = message[3:5] if len(message) >= 5 else ("pass", "passes")
            self._say(f"saved {written} of {counted(total, *noun)}"
                      + ("" if written == total
                         else " -- the rest are in the log above"))

    def _handle(self, event) -> None:
        if event.kind == "log":
            # Words for people, and only that: what failed is said by the
            # "filing" event. Read from these lines by its wording, a
            # reworded message stopped the marking without a sound.
            self._say(event.text)
        elif event.kind == "state":
            self.v_state.set(event.text.splitlines()[0])
            if (not event.busy and not self._scanner_kept and event.text
                    and event.text == self.session.inquiry_text):
                # The scanner, as it answered INQUIRY: the kept log's header
                # names the code and the folders, and this what it drove.
                self._scanner_kept = True
                self._keep("scanner: " + event.text)
            if event.busy:
                # Which job this is, as the session numbers them: its
                # filings name it, however late they land. Its start, in
                # the kept log alone, where each job's end is too.
                self._keep(f"job {event.done}: {event.text}")
                self._job_running = event.done
                self._last_job = event.done
                self._running_job = event.job
                if isinstance(event.job, Roll):
                    self._roll_jobs[event.done] = RollRun.of(event.job)
                self._job = event.text
                self._job_results = []
                self.v_progress.set(event.text)
                self.v_pass_eta.set("")
                self.progress.configure(value=0)
                # A new job's first pass has not reported a line yet; without
                # this its ETA would measure against whatever pass the last
                # job left behind, for however long a calibration or a
                # prescan runs before the next "progress" event resets it.
                self._pass_started_at = None
                self._pass_total_seen = 0
                self._pass_done_seen = 0
            if not event.busy and self.busy and self._calibration_pending:
                # A job ended and no "calibrated" came: a stop kept the
                # calibration from running at all. What the session has is
                # the answer, not the hope set when it was queued.
                self._calibration_pending = False
                self.calibrated = bool(self.session.calibrated)
                self._sync_calibration()
            if not event.busy:
                self._job_running = None
                self._running_job = None
            self._set_busy(event.busy)
        elif event.kind == "progress":
            self._progress(event.done, event.total)
        elif event.kind == "result":
            self._add_result(event.result)
            self._job_results.append(event.result)
            if event.result.kind == "prescan" and not event.result.number:
                # A prescan of the film where it is: the one an aim-click may
                # measure from, until the film next moves.
                self._aim_from = event.result.seq
            # One of these two kinds is the "this frame is done" signal,
            # depending on which the roll actually produces -- a dry run
            # never delivers a "frame" result, only "prescan". Recomputes
            # the per-frame pace from the roll's own measured average rather
            # than trusting the first frame alone, so it keeps refining as
            # the roll continues rather than freezing after frame 1.
            done_kind = "prescan" if self._roll_dry else "frame"
            if (self._roll_wall_start is not None
                    and event.result.number and event.result.kind == done_kind):
                # Counted, not read off the number: a frame's number is its
                # place on the strip, so a roll started on frame 11 would
                # otherwise have "done" eleven frames after its first.
                self._roll_frames_done += 1
                elapsed = time.monotonic() - self._roll_wall_start
                self._roll_seconds_per_frame = elapsed / self._roll_frames_done
                self._update_roll_eta()
        elif event.kind == "transport":
            known = event.done if event.done >= 0 else None
            self.v_position.set(position_label(known))
            # Unknown replaces what was known. Keeping the older value had the
            # Roll dialog forecast "the transport last said frame 11" beside a
            # readout saying "film on frame ?", from a counter since lost.
            self._transport = known
            if self._roll_seeking:
                # The roll's seek has landed -- the session reports where it
                # put the film before the first frame -- so the roll's pace is
                # timed from here. From the submission, a minute's wind back
                # counted as frame 1's and inflated every "left" after it.
                self._roll_seeking = False
                if self._roll_wall_start is not None:
                    self._roll_wall_start = time.monotonic()
                    self._update_roll_eta()
        elif event.kind == "reference_moved":
            self.ask_to_calibrate(moved=event.text)
        elif event.kind == "calibrated":
            self._calibration_pending = False
            self.calibrated = bool(event.done)
            self._sync_calibration()
        elif event.kind == "filed":
            for r in self.results:
                if r.seq == event.done:
                    r.entry = Path(event.text)
        elif event.kind == "filing":
            self._filing(event.filing)
            self._mark_filed(event.filing)
        elif event.kind == "finished":
            self._keep(f"job {self._job_running} ended: {event.text}")
            # Kept, so a filing of this job that lands once nothing is
            # running can say it here again with its count (`_filing`).
            if self._job_running is not None:
                self._job_ended[self._job_running] = f"{event.text} -- done"
            self.v_progress.set(f"{event.text} -- done"
                                + self._filing_said(self._job_running))
            self.v_pass_eta.set("")
            self.progress.configure(value=1000)
            self._roll_wall_start = None
            self._roll_seeking = False
            self.v_roll_eta.set("")
            if self._surveying:
                self._walk_ended()
                if self.survey:
                    self.on_contact_sheet()
            else:
                self._report_held()
                self._roll_ended()
        elif event.kind == "failed":
            # Which job failed: the one running, or the last one started where
            # something said the session idle first -- a force abort -- and
            # that job has not been said to end.
            failing = self._job_running
            if failing is None and self._last_job not in self._job_ended:
                failing = self._last_job
            # Reported in place, not in a modal: a modal sits inside this pump
            # and stops it, so one failed frame would freeze the window and the
            # rest of a roll would arrive unseen.
            self._say(event.text)
            detail = getattr(event, "detail", "")
            if detail:
                # The traceback, in the kept log alone: the pane stays one
                # line, and the file is what an overnight roll that died of
                # a bug is diagnosed from.
                which = (f"job {failing}" if failing is not None
                         else "the job")
                self._keep(f"{which} failed with:\n{detail.rstrip()}")
            if failing is not None:
                self._keep(f"job {failing} ended: failed, as said above")
                self._job_ended[failing] = event.text
            self.v_progress.set(event.text
                                + self._filing_said(self._job_running))
            self.v_pass_eta.set("")
            self.v_caption.set(event.text)
            self.progress.configure(value=0)
            self._roll_wall_start = None
            self._roll_seeking = False
            self.v_roll_eta.set("")
            self._set_busy(False)
            self._light("broken")
            if self._surveying:
                # What was walked before the failure is still a walk, and the
                # sheet still opens on it -- and can be walked on from.
                self._walk_ended()
            else:
                # The frames scanned before it went out wherever they went,
                # and are named as a finished roll's are: this only ever ran
                # on "finished", so a roll that failed at frame 7 never said
                # which of frames 1 to 6 had missed their positions.
                self._report_held()
                self._roll_ended()
            if not self.session.inquiry_text:
                messagebox.showerror("No scanner", event.text)
        elif event.kind == "dropped":
            self._keep(f"dropped before it started: {event.text}")
            self._undo_hand_over(event.job)
        elif event.kind == "closed":
            # Nothing handed over now will start: the session has let go of
            # every job it held (`ScanSession.outstanding`).
            self._keep("the scanner session closed")
            self._session_closed = True
            self._running_job = None
            self._set_busy(False)
            self.v_state.set("scanner closed")
            # Through the wait, not straight to `_quit`: an idle session
            # closes a second or two after Quit, and going from here to the
            # destroy killed a Save all or an Export mid-file -- a truncated
            # TIFF under its final name, and the rest of the batch never
            # written. The wait quits at once when nothing is being written.
            if self.closing:
                self._wait_to_quit()

    def _undo_hand_over(self, job) -> None:
        """Set back what handing ``job`` over set ahead of its start, for a
        job the session let go of without starting it ("dropped").

        A job is offered Stop from its hand-over (`_hand_over`), so a Stop
        can drop one before the worker takes it -- at launch, while the
        scanner is still being opened, for seconds. Nothing else is ever said
        of it, and what its end would have undone stayed: a dropped
        calibration left the window calling itself calibrated, and the next
        Scan went to the driver with no reference; a dropped walk left the
        window walking, and the next job's end opened the sheet for it; a
        roll's estimate stayed on screen.

        Not while another job of the same kind is outstanding: handed over
        after the drop and before this was read, what the hand-over set is
        that job's.
        """
        others = self.session.outstanding()
        if isinstance(job, Calibrate):
            if (self._calibration_pending
                    and not any(isinstance(j, Calibrate) for j in others)):
                self._calibration_pending = False
                self.calibrated = bool(self.session.calibrated)
                self._sync_calibration()
        elif isinstance(job, Roll) and not any(isinstance(j, Roll)
                                               for j in others):
            self._roll_wall_start = None
            self._roll_seeking = False
            self.v_roll_eta.set("")
            if job.dry_run and self._surveying:
                # As a walk stopped before its first frame ends: the sheet
                # its hand-over put away comes back on what was walked.
                self._walk_ended()
                if self.survey and not self.closing:
                    self.on_contact_sheet()

    def _filing(self, filing) -> None:
        """A pass not filed, or a copy not written: said where it is seen.

        Both were only ever appended to the log -- which, twenty frames into
        a roll, nobody is reading. So the pass is marked on the filmstrip and
        in its caption, the progress line says so, and the first of each
        kind in a job opens a notice. Not a modal, and not from here: a modal
        inside the event pump stops it, and the rest of the roll would arrive
        unseen.

        From the session's own record (`Filing`), never from the words of a
        log line: the result is the one the filing names (`Filing.shown`),
        never the newest of its frame number -- that marked a slide's Scan
        "not filed" for its picture's copy, a moment before its "filed" --
        and it counts against the job that queued it (`Filing.owner`), not
        the one running when it lands. A filing no result stands for is
        counted and said, and marks nothing.
        """
        if filing is None or filing.outcome not in (NOT_FILED, COPY_FAILED):
            return
        counts = self._filing_counts.setdefault(filing.owner, {})
        counts[filing.outcome] = counts.get(filing.outcome, 0) + 1
        result = next((r for r in self.results
                       if filing.shown and r.seq == filing.shown), None)
        what = filing_words({filing.part: filing.outcome})
        if result is not None:
            marks = self._filing_marks.setdefault(result.seq, {})
            # The worst for each part, never lowered: a slide's RGB pass
            # filed after its infrared pass was refused leaves it red.
            if marks.get(filing.part) != NOT_FILED:
                marks[filing.part] = filing.outcome
            result.filing = (NOT_FILED if NOT_FILED in marks.values()
                             else COPY_FAILED)
            result.error = f"{filing_words(marks)} -- see the log"
            if result is self.current:
                self._show(result)
            self._redraw_strip()
        named = (result.label if result is not None
                 else f"picture {filing.number}")
        if (filing.owner in self._job_ended
                and self._job_running in (None, filing.owner)):
            # Its job ended before it landed, as a single pass's always
            # does: its line is said again, with what it cost. Another job's
            # line is left alone; the mark, the log and the notice say it.
            self.v_progress.set(self._job_ended[filing.owner]
                                + self._filing_said(filing.owner))
        elif self._job_running == filing.owner:
            self.v_progress.set(f"{named}: {what} -- see the log")
        noticed = (filing.owner, filing.outcome)
        if noticed not in self._filing_noticed:
            self._filing_noticed.add(noticed)
            text = "\n".join(filing.trouble())
            outcome = filing.outcome
            self._later(0, lambda: self._filing_notice(outcome, text))

    def _filing_said(self, ordinal: int | None) -> str:
        """What one job's filings cost, for its progress line: "" for none."""
        counts = self._filing_counts.get(ordinal) if ordinal else None
        if not counts:
            return ""
        said = []
        if counts.get(NOT_FILED):
            said.append(f"{counts[NOT_FILED]} not filed")
        if counts.get(COPY_FAILED):
            said.append(counted(counts[COPY_FAILED], "copy", "copies")
                        + " not written")
        return " -- " + ", ".join(said) + " -- see the log"

    def _filing_notice(self, outcome: str, text: str) -> None:
        """The first of a job's filings of one kind, in a window of its own."""
        top = tk.Toplevel(self.root)
        top.title("Not filed" if outcome == NOT_FILED else "Copy not written")
        top.transient(self.root)
        frame = ttk.Frame(top, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, wraplength=460, justify="left", text=(
            text + "\n\nAny more in this job are marked "
            + ("red" if outcome == NOT_FILED else "blue")
            + " on the filmstrip and said in the log.")).pack(anchor="w")
        ttk.Button(frame, text="Close", command=top.destroy).pack(
            anchor="e", pady=(10, 0))
        self._filing_window = top

    def _mark_filed(self, filing) -> None:
        """A frame of the sheet's roll filed: done, in the window and on the
        sheet, unticked and badged as the roll held it.

        What the sheet called done was what its roll was opened with, and
        nothing since: reopened during a commission or after it, the frames
        just scanned came back ticked and not done -- their ticks from the
        commission kept over them -- while their badges, read from the folder,
        said they were scanned (GUI2-27). From the filing that makes a frame
        done (`RollManifest.filed`), never from roll.json as this is read: the
        session says a frame was filed before its manifest says it is done,
        and a roll's last frames are filed after its "finished". So: the frame
        itself filed, not a prescan, a slide's infrared pass or its picture
        (`Filing.part`); for a roll that scans, not a walk; into the folder
        the sheet's frames are scanned into (`_roll_folder`), by the roll's
        own record (`RollRun`) and not by whatever runs when this lands.

        Only that frame's tick goes, and only here: a done frame ticked again
        on purpose -- a rescan -- stays ticked.
        """
        if (filing is None or filing.failed or filing.kind == "prescan"
                or filing.part or not filing.number):
            return
        run = self._roll_jobs.get(filing.owner)
        if (run is None or run.dry or run.out is None
                or self._sheet_roll is None):
            return
        try:
            here = self._roll_folder(keep=False)
        except (OSError, ValueError):
            return
        if _folder_key(run.out) != _folder_key(here):
            return
        number = int(filing.number)
        self._sheet_done.add(number)
        ticks = self.sheet_state.get("ticks") if isinstance(
            self.sheet_state, dict) else None
        if isinstance(ticks, dict):
            ticks.pop(number, None)
        sheet = self.sheet
        if sheet is not None and sheet.alive():
            shown = next((r for r in self.results
                          if filing.shown and r.seq == filing.shown), None)
            sheet.mark_done(number, hold_of(shown) if shown is not None
                            else None)

    def _walk_ended(self) -> None:
        """A walk is over, finished or failed: settle what the sheet holds."""
        self._surveying = False
        self.edge_watch.finish()
        # Where the walk wrote its manifest, asked for rather than rebuilt from
        # the roll's name. The decisions about to be made in the sheet belong
        # to this strip, and until the roll is commissioned that folder is the
        # only thing to file them against -- without it they would last only
        # as long as the window, which is most of what was wrong here.
        #
        # Only once a prescan has arrived, which the session delivers after it
        # has named this walk's folder: a walk refused or stopped before its
        # first frame never set it, and the folder then is some earlier roll's.
        # A kept walk went into the sheet's own folder, which is already set.
        kept = bool(self._kept_walk)
        if self.survey and not kept:
            self._sheet_roll = getattr(self.session, "last_roll_dir", None)
        # The walk the folder holds now is the sheet's: a fresh one's new
        # id, or a walk from before ids that this one added to and gave one.
        self._see_walk()
        if self.survey:
            self._say_strip_walked(kept)
        # In strip order, whichever end the walk added to.
        self.survey.sort(key=lambda r: int(r.number or 0))
        # Opened while the walk was still going, so it holds the frames walked
        # by then and no others: raised as it was, the frames after it had no
        # cell, no tick and no approval, and a commission from it left them
        # unscanned with nothing on screen to say so. Closed here, keeping
        # what was decided in it, and built again on the whole walk below.
        #
        # Closed *before* the positions of re-walked frames are dropped: its
        # way out files its own copy of every decision, and closed after, it
        # put back what had just been dropped -- a position measured on a
        # prescan this walk has replaced, reopened, and then held to.
        reopen = self.sheet is not None and self.sheet.alive()
        if reopen:
            self._close_sheet()
        again = sorted(self._rewalked)
        here: list[int] = []
        if again:
            # Measured from the prescan this walk has just replaced, so it no
            # longer says where anything is. Turns and ticks are about the
            # picture and stay. Most went as their prescan arrived
            # (`_dropped`); these are what the sheet just closed filed back.
            here = [n for n in again
                    if n in (self.sheet_state.get("offsets") or {})]
            for name in ("offsets", "sources"):
                section = self.sheet_state.get(name)
                if isinstance(section, dict):
                    for number in again:
                        section.pop(number, None)
            dropped = sorted(set(here) | (self._dropped & set(again)))
            # By their numbers across the roll, as the rest of the log says
            # them (`spans_said`).
            strip = (place_of(self._sheet_roll, strict=False)
                     if self._sheet_roll is not None else None)
            self._say(f"walked frames {spans_said(strip, again)} again: their "
                      "new prescans replace the old"
                      + (f", and the positions set on "
                         f"{spans_said(strip, dropped)} are dropped"
                         if dropped else ""))
        if kept and self.sheet_state:
            # Once for a kept walk, whatever was dropped: a walk from before
            # ids that this one added to has one now, and what was decided on
            # it is filed under that (P22-b).
            self._store_sheet_state(self.sheet_state)
        elif here:
            self._store_sheet_state(self.sheet_state)
        self._kept_walk, self._rewalked, self._dropped = set(), set(), set()
        self.b_sheet.configure(state="normal" if self.survey else "disabled")
        if reopen:
            self.on_contact_sheet()

    def _roll_ended(self) -> None:
        """Any job that is no walk has ended: a roll that claimed a new strip
        makes its roll the one Continue offers -- the roll walked or claimed
        last -- and the Roll panel's line is said again: a strip scanned to
        its end makes the next one next.

        Only a claim: a roll into a strip already there -- a finish, a
        retake, a commission from its sheet -- is a roll opened, which
        Continue never offers; noted after every job, the last roll folder
        came back after a calibration or a single scan too."""
        job = self._running_job
        folder = getattr(self.session, "last_roll_dir", None)
        if (isinstance(job, Roll) and job.new_strip and folder is not None
                and roll_of(folder) is not None
                and _folder_key(roll_of(folder)) == _folder_key(job.out)):
            self._note_strip_roll(folder)
        self._offer_strip()

    def _say_strip_walked(self, kept: bool) -> None:
        """A walk of a strip of a roll of strips is over: say its frames by
        their numbers across the roll and how its walk ended, and what the
        next walk under the roll will be -- the next strip from which
        number, or this one added to first."""
        folder = self._sheet_roll
        strip = place_of(folder, strict=False) if folder is not None else None
        if strip is None:
            return
        self._note_strip_roll(folder)
        self._offer_strip()
        live = self.session.live_manifest
        try:
            count = strip_count(folder, live)
            offer = plan_next(strip.roll_folder, live)
        except ValueError as exc:
            self._say(f"strip {strip.strip} of {strip.label}: {exc}")
            return
        roll = strip.roll_folder.name
        if strip.last is not None:
            self._say(f"strip {strip.strip} of {strip.label} walked again: its "
                      f"numbers, {strip.numbers()}, were fixed when the strip "
                      "after it was walked")
            return
        if kept:
            self._say(f"strip {strip.strip} of {strip.label} now has "
                      f"{counted(count.count, 'frame')} ({count.numbers()})")
            return
        ended = ("to the end of the strip"
                 if count.complete and count.how.startswith("walked")
                 else count.how if count.complete else f"its {count.how}")
        following = offer.next
        tail = (f"the next walk under {roll} adds to it, or starts strip "
                f"{following.number} from {following.first}"
                if offer.again is not None else
                f"the next walk under {roll} is strip {following.number}, "
                f"from {following.first}")
        self._say(f"strip {strip.strip} of {strip.label} walked: frames "
                  f"{count.numbers()}, {ended}; {tail}")

    def _report_held(self) -> None:
        """Say, once and by name, which frames did not reach their position.

        A line in the log scrolls away -- six frames of a roll went out
        uncorrectable once because the warning was above the fold. This is the
        last thing seen when a roll ends, and it names frames rather than
        outcome codes, because "not_converged" is not what anyone needs to
        read at the end of an hour.

        Of the job that ended, and no other. It read every pass the window
        held, so one roll's missed frame was announced again after every job
        that followed -- another roll, a single scan, a prescan -- naming a
        frame of a roll nobody was scanning.

        In two lists, because they ask different things of him: a frame the
        hold's last look measured away from its position says how far, and one
        nothing could place has to be looked at to be known (`hold_level`).
        The words are `framing.say_hold`'s, which the tools say too. Frames
        that were scanned only: one that failed is in no file and is offered
        again when the roll is continued.

        A roll that fails part way says this as well as one that finishes.
        Only the finished one did, so the frames scanned before a failure
        went unannounced however far off they were.
        """
        scanned = {getattr(r, "number", None) for r in self._job_results
                   if r.kind == "frame"}
        missed: list[str] = []
        unverified: list[str] = []
        seen = set()
        # Named as the sheet's ticks, the filmstrip and the files name them:
        # by the number across the roll, the place on the strip beside it
        # (`frame_said`). "frame 2" on strip 2 was strip 1's frame. Kept by
        # place, as one job is one strip.
        folder = getattr(self.session, "last_roll_dir", None)
        strip = place_of(folder, strict=False) if folder is not None else None
        for result in self._job_results:
            held = hold_of(result)
            level = hold_level(held)
            number = getattr(result, "number", None)
            # A frame's prescan and its scan share one registration dict, so
            # both carry the outcome and the frame would be named twice --
            # and counted twice, which made the number in the first line
            # disagree with the list under it.
            if level is None or number not in scanned or number in seen:
                continue
            seen.add(number)
            named = frame_said(getattr(result, "roll_number", 0) or number,
                               strip.strip if strip is not None else None,
                               number)
            if level == MISSED:
                out = say_units(hold_out_mm(held) or 0.0, signed=False)
                missed.append(f"{named}: {hold_words(held)} -- {out} out")
            else:
                unverified.append(f"{named}: {hold_words(held)}")
        if not seen:
            return
        parts = [(f"{len(seen)} frames were" if len(seen) != 1 else "1 frame was")
                 + " scanned without reaching the position you approved."]
        if missed:
            parts.append("Measured away from it:\n" + "\n".join(missed))
        if unverified:
            parts.append("Not verified -- nothing could measure where "
                         + ("they" if len(unverified) != 1 else "it")
                         + " ended up:\n" + "\n".join(unverified))
        parts.append("The scans are filed and usable; these are the frames to "
                     "look at first. Their thumbnails and the contact sheet "
                     "mark them.")
        messagebox.showwarning("Positions not reached", "\n\n".join(parts))

    def _light(self, state: str) -> None:
        self.light.itemconfigure(self._bulb, fill=LIGHT[state])

    def _run_buttons(self) -> tuple:
        return (self.b_scan, self.b_prescan, self.b_roll, self.b_calibrate,
                self.b_prev, self.b_next, self.b_fine_back, self.b_fine_fwd)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        if self.session.dead:
            for b in (*self._run_buttons(), self.b_stop, self.b_abort):
                b.configure(state="disabled")
            self._light("broken")
            self.v_caption.set("aborted -- power-cycle the scanner at its own "
                               "switch, then start this window again")
            return
        run = "disabled" if busy else "normal"
        for b in self._run_buttons():
            b.configure(state=run)
        # Stop while there is anything to stop, a job handed over behind the
        # one that just ended included: it is offered from the hand-over
        # (`_hand_over`), and greyed between two jobs it could not be pressed
        # on the second as it was taken.
        stoppable = busy or bool(self.session.outstanding())
        self.b_stop.configure(state="normal" if stoppable else "disabled",
                              text=stop_label(self._running_job
                                              or self._job))
        self.b_abort.configure(state="normal" if busy else "disabled")
        self._light("busy" if busy else "idle")

    def _progress(self, done: int, total: int) -> None:
        if total <= 0:
            return
        now = time.monotonic()
        # A new pass, structurally rather than by reading any label: its own
        # total-lines differs from the last one seen, or done has gone back
        # down. Either resets the clock this pass's ETA is measured against.
        if total != self._pass_total_seen or done < self._pass_done_seen:
            self._pass_started_at = now
        self._pass_total_seen = total
        self._pass_done_seen = done

        self.progress.configure(value=int(1000 * done / total))
        self.v_progress.set(f"{done}/{total} lines")

        # Linear interpolation from lines/second so far this pass. Reads think
        # in chunks (216 lines a request here), so the first callback already
        # has done > 0 -- the clock above starts at that callback, not at the
        # pass's true first byte, so an estimate made in the first chunk or two
        # runs a little fast. It settles as more chunks arrive.
        eta = ""
        if done > 0 and done < total:
            elapsed = now - self._pass_started_at
            remaining = elapsed * (total - done) / done
            eta = f"~{_duration(remaining)} left"
        self.v_pass_eta.set(eta)

    def _update_roll_eta(self) -> None:
        """The whole-roll line above the per-pass one.

        Two regimes, per the frame count: before any frame has completed,
        `_roll_seconds_per_frame` is the rough guess `on_roll` made from
        `estimate_seconds()` -- the same one its own confirmation dialog
        showed, so the number does not change the moment scanning starts.
        From the first completed frame on, `_handle` replaces it with the
        roll's own measured average, and this recomputes from that instead --
        which is what folds in whatever prescans, metering and advances
        actually cost on this strip, not an estimate of them.
        """
        if self._roll_wall_start is None:
            self.v_roll_eta.set("")
            self.v_frame_status.set("")
            return
        elapsed = time.monotonic() - self._roll_wall_start
        per = self._roll_seconds_per_frame
        done, total = self._roll_frames_done, self._roll_frames_total
        self.v_frame_status.set(frame_status(done, total))
        measured = " (measured)" if done else " (estimated)"
        verb = "walked" if self._roll_dry else "scanned"
        if total:
            remaining = max(0.0, per * total - elapsed)
            self.v_roll_eta.set(
                f"roll: {verb} {done}/{total} frames -- about "
                f"{_duration(per * total)} total{measured}, "
                f"~{_duration(remaining)} left")
        else:
            # No frame count was given -- "as many as there are" -- so there
            # is nothing to count down to. Say the pace instead of a false
            # total.
            self.v_roll_eta.set(
                f"roll: {verb} {counted(done, 'frame')} so far, "
                f"{_duration(elapsed)} elapsed, ~{_duration(per)}/frame"
                f"{measured}")

    def _say(self, message: str) -> None:
        """A line in the log pane, and in the kept log beside it."""
        self._pane(message)
        self._keep(message)

    def _pane(self, message: str) -> None:
        """A line in the log pane alone; kept for it until it exists."""
        log = getattr(self, "log", None)
        if log is None:
            self.__dict__.setdefault("_said_later", []).append(message)
            return
        log.configure(state="normal")
        log.insert("end", message + "\n")
        log.see("end")
        log.configure(state="disabled")

    def _keep(self, text: str) -> None:
        """A line in the kept log alone (`KeptLog`): what the pane does not
        show. The file failing is said in the pane, once."""
        why = self._kept.write(text)
        if why is not None:
            self._pane(why)

    def _header(self, session, demo: bool, settings_path) -> str:
        """The kept log's first lines: which program and code, on what.

        The driver's commit and the source hashes taken as it was imported
        (`library.provenance`'s, without the two git calls it makes again),
        so a log can be set beside the entries it speaks of; and every
        folder this window writes in.
        """
        built = getattr(library, "_AT_IMPORT", {}) or {}
        commit = built.get("commit")
        code = (f"commit {commit[:7]}" + (" with changes" if built.get("dirty")
                                         else "")
                if commit else "no git commit known")

        def short(digest) -> str:
            return digest[:12] if isinstance(digest, str) else "unknown"

        def where(path) -> str:
            return os.path.abspath(path) if path else "none"

        return "\n".join((
            f"Reflecta RPS 7200 {'demo ' if demo else ''}window, pid "
            f"{os.getpid()}, Python {sys.version.split()[0]} on "
            f"{sys.platform}",
            f"driver: {code}; package source {short(built.get('source_sha256'))}"
            f", edge reader {short(built.get('edge_reader_sha256'))} as "
            f"imported; protocol revision {PROTOCOL_REVISION}",
            f"library {where(session.root)}; rolls {where(session.rolls)}; "
            f"reference {where(session.reference)}",
            f"settings {where(settings.path(settings_path))}; output "
            f"{where(session.out_dir)}; this log in {where(self._logs)}",
        ))

    def _setup_said(self) -> str:
        """The setup the window opened on, for the kept log: what its
        controls held, the film panel and the output, as `_remember`
        would store them."""
        controls = {key: getattr(self, f"v_{key}").get()
                    for key in REMEMBERED if hasattr(self, f"v_{key}")}
        film = {key: self.fields[key].get()
                for key in REMEMBERED_FILM if key in self.fields}
        output = {"folder": str(self.session.out_dir or ""),
                  "format": self.session.out_format,
                  "quality": self.session.jpeg_quality}
        return ("setup: " + json.dumps({"controls": controls, "film": film,
                                        "output": output},
                                       sort_keys=True, default=str))

    def _tk_error(self, kind, value, tb) -> None:
        """A Tk callback that raised, as Tk's own handler says it -- on
        stderr -- and in the kept log, with a line in the pane.

        Tk's default printed it to stderr alone, which only `make run` on
        Windows keeps (`logs/window-*.log`), and the operator saw nothing.
        Set on the window's root as it is built, so the tour's own, set
        after, still wins there.
        """
        text = "".join(traceback.format_exception(kind, value, tb))
        try:
            print("Exception in Tkinter callback\n" + text, end="",
                  file=sys.stderr, flush=True)
        except (OSError, ValueError, AttributeError):
            pass
        self._keep("a window callback raised:\n" + text)
        try:
            self._pane(f"the window could not handle an event: "
                       f"{kind.__name__}: {value} -- the traceback is in "
                       "the window's log file")
        except Exception:                                 # noqa: BLE001
            pass

    # -- results and the filmstrip ----------------------------------------

    def _add_result(self, result) -> None:
        result.hidden = False
        result.supersedes = None
        # A walk's prescan, as `read_survey` marks one reopened: what is
        # marked as left off its position is a frame a roll scanned
        # (`hold_of`). The walks this window starts hold nothing today.
        result.walked = bool(self._surveying and result.kind == "prescan")
        # Measured once, from the whole picture. Recomputing per redraw was
        # most of what made zooming feel dead, and it also meant the brightness
        # changed as you panned -- the same negative looking different
        # depending on where you were looking.
        result.levels = preview.levels(result.image) if result.image is not None else None
        # A real scan stands in for the prescan of the same picture, but only
        # when the film has not moved since -- a prescan of a different frame is
        # a different photograph, and hiding it would lose it.
        #
        # Worked out before the arrangement below, which reads it: a scan and
        # the prescan it replaces are two passes over one photograph, and the
        # answer to "which way up is this" belongs to the photograph.
        superseded = None
        if result.kind in ("scan", "frame") and result.position is not None:
            for earlier in reversed(self.results):
                if earlier.kind != "prescan":
                    break
                if earlier.position == result.position and not earlier.hidden:
                    earlier.hidden = True
                    result.supersedes = earlier.seq
                    superseded = earlier
                    break
        if self._surveying and result.kind == "prescan" and result.number:
            # Here, where a walked prescan arrives, and nowhere else. It was
            # done in `remember_arrangement`, which a turn or a flip also
            # calls -- so turning a prescan while the walk ran put it in the
            # survey a second time, and turning an older walk's put that one
            # into this walk.
            self._into_survey(result)
            self.edge_watch.add(result.number, result.image)
        # In the list before it is arranged, because arranging it is what
        # shows it and redraws the filmstrip. The other way round, the strip
        # was drawn without the pass that had just arrived: always a pass
        # behind, and the last frame of a roll -- with its badge, if the roll
        # left it off its position -- missing until something else redrew it.
        # After the superseding above, which walks back from the newest and
        # would otherwise meet this pass first.
        self.results.append(result)
        self._arrange(result, superseded)

    def _arrange(self, result, superseded=None) -> None:
        """How this pass should be shown, in order of what knows best.

        A frame of a roll that scans, first: it is shown the way that roll
        writes it (`_written_as`), whatever was turned since the roll began.
        Then the prescan it replaces: you framed that picture and said which
        way up it was, and the scan of it is the same photograph. Then anything
        already said about this picture, which is what a turn in the contact
        sheet leaves behind. Then the last arrangement set anywhere, because a
        strip goes into the transport one way round and the frame after this
        one is almost certainly the same way up.
        """
        written = self._written_as(result)
        known = written
        if known is None and superseded is not None:
            known = (superseded.rotation, superseded.flipped)
        if known is None:
            known = self.orientations.get(picture_of(result))
        rotation, flipped = known or (self.rotation, self.flip)
        # The scanner sometimes hands a pass back reversed with nothing to say
        # it has, and the session says so here after comparing it against its
        # own prescan. Applied first, because it brings the pixels into the
        # arrangement the operator was looking at when he chose the rest -- the
        # same composition `_file` does, from the same number, so the picture
        # on screen and the file on disk agree about which way up this is.
        reversal = (result.meta or {}).get("reversal")
        if reversal:
            rotation, flipped = preview.compose(
                (int(reversal[0]), bool(reversal[1])), (rotation, flipped))
        result.rotation, result.flipped = rotation, flipped
        # A pass just taken says where the film is. Here, as it arrives, and
        # not in `remember_arrangement`, which a turn also reaches: turning an
        # old walk's frame 3 in the sheet told the Roll dialog the film was on
        # frame 3 while it sat on 17. Through the same filter the session's
        # readout reports use, so a counter no strip can have never becomes a
        # forecast either.
        if plausible(result.position):
            self._transport = result.position
        # Whatever it turned out to be, the picture now has an answer, so the
        # next pass over it agrees with this one rather than with the session
        # -- unless the running roll gave it and the picture has one already:
        # that is a turn made since the roll began, for the next commission,
        # and the roll's own answer is for its file alone.
        self.remember_arrangement(
            result, answer=written is None
            or picture_of(result) not in self.orientations)

    def _written_as(self, result) -> tuple[int, bool] | None:
        """How the roll running now writes this pass of a frame, or None
        where no roll that scans is running, or the pass is of no frame.

        The commission's own turn and mirror for a frame it approved, and the
        session's for any other: the frame's own pass -- and a slide's
        picture -- as `ScanSession._orientation_for` writes it, so the
        filmstrip, Save As and frameNN.tif agree. The roll's prescans of that
        frame are shown with the same pair, so every pass of the frame agrees
        on screen, though that rule exempts prescans and the session files
        them by its own arrangement: they are references, and a roll that
        scans writes no copy of them to disagree with. A turn in the sheet or
        the filmstrip while a commission ran wrote the key `_arrange`
        arranged the arriving frame by, so it was shown turned and written as
        it was commissioned (GUI2-27). From the job the session says is
        running (`Event.job`), never from what the window handed over last. A
        walk scans no frame and is arranged as before.
        """
        job = self._running_job
        number = getattr(result, "number", 0)
        if not isinstance(job, Roll) or job.dry_run or not number:
            return None
        for approved in job.approved:
            if int(approved.number) == int(number):
                return int(approved.rotation) % 360, bool(approved.flipped)
        return int(self.session.rotation) % 360, bool(self.session.flip)

    def _roll_in_hand(self) -> tuple[Roll | None, bool]:
        """The roll that scans which the scanner is on, or the first handed
        to it behind what it is on, and whether it is running yet; ``(None,
        False)`` where there is none.

        From the session's own list of the jobs it holds (`ScanSession.
        outstanding`), so a roll a Stop dropped before it began is no roll in
        hand -- the window's copy of the queue was a tick behind, and was the
        one place a dropped roll lived on. A walk scans no frame, and is none.
        """
        for job in self.session.outstanding():
            if isinstance(job, Roll) and not job.dry_run:
                return job, job is self._running_job
        return None, False

    def _into_survey(self, result) -> None:
        """Add a walked prescan to the survey, replacing a kept one of its frame.

        Only a frame the sheet already held is replaced: the walk adding to it
        took that frame again, and one frame is one cell. Anything else is
        appended, as every walk's prescans always were -- once: a result the
        survey already holds is left where it is.
        """
        if any(r is result for r in self.survey):
            return
        number = int(result.number)
        if number == 1 and self._same_strip_from is not None:
            previous, self._same_strip_from = self._same_strip_from, None
            self._check_same_strip(previous, result)
        if number in self._kept_walk:
            for i, earlier in enumerate(self.survey):
                if earlier.number == number:
                    self.survey[i] = result
                    self._rewalked.add(number)
                    self._drop_position(number)
                    return
        self.survey.append(result)

    def _check_same_strip(self, previous: Path, result) -> None:
        """Warn -- only warn -- where the first frame of a new strip's walk
        looks like the first frame of the strip before it: the strip in the
        transport was not swapped, and this walk is numbering the same film
        again. Compared with that strip's ``prescan01.tif``, in the film's
        own orientation, by the hold's own test of one picture against
        another (`framing.measure_shift_mm`, its gate and no other), on a
        thread of its own; its line comes back through the pump. The walk
        goes on whatever it finds: it is the operator who can see the
        transport (risk 2 of the roll model, the other way round)."""
        image = getattr(result, "image", None)
        if image is None:
            return
        strip = place_of(previous, strict=False)

        def look() -> None:
            try:
                manifest = read_manifest(Path(previous) / "survey.json")
                found = next(((path, record) for number, path, record
                              in walked_prescans(previous, manifest)
                              if number == 1), None)
                if found is None:
                    return
                reference = read_walk_prescan(manifest, 1, found[0],
                                              found[1])
                if reference is None:
                    return
                shift, _detail = measure_shift_mm(reference, image)
            except Exception:                             # noqa: BLE001
                return                    # a warning, never a failure
            if shift is None:
                return
            which = (f"strip {strip.strip}" if strip is not None
                     else Path(previous).name)
            self._saves.put(("line", (
                f"frame 1 of this walk looks like frame 1 of {which} -- if "
                f"{which} is still in, stop and swap it")))

        threading.Thread(target=look, daemon=True,
                         name="same-strip").start()

    def _drop_position(self, number: int) -> None:
        """A kept frame walked again: its position was measured on the
        prescan this one replaces, so it is dropped, and filed so at once --
        its turn stays, being about the picture. Only once the walk ended
        was it dropped, so a kill part way left a stored position measured
        on a prescan no longer there, and a restart held the frame to it
        (P22-b). `_walk_ended` drops it again from what the sheet files as
        it closes, and names it (`_dropped`)."""
        state = self.sheet_state
        if not isinstance(state, dict):
            return
        had = any(number in (state.get(name) or {})
                  for name in ("offsets", "sources"))
        if number in (state.get("offsets") or {}):
            self._dropped.add(number)
        for name in ("offsets", "sources"):
            section = state.get(name)
            if isinstance(section, dict):
                section.pop(number, None)
        if had:
            self._store_sheet_state(state)

    def remember_arrangement(self, result, answer: bool = True) -> None:
        """Record how this photograph is arranged, and show it: a pass as it
        arrives (`_arrange`), and nothing else -- a turn records alone
        (`_record_arrangement`) and its caller redraws once."""
        self._record_arrangement(result, answer=answer)
        self._show(result)
        self._redraw_strip()

    def _record_arrangement(self, result, answer: bool = True) -> None:
        """Record how this photograph is arranged, however it was decided,
        and nothing more: no pass is shown and the filmstrip is not drawn.

        A turn in the contact sheet came through `remember_arrangement`,
        which showed the frame turned and drew the strip: "rotate all" on
        seventeen frames showed each of them in turn, dropped the full view
        each time and queued a read of each one's entry, and left the preview
        on the last -- a turn of frame 4 put frame 4 in front of whatever was
        being looked at (GUI2-24). The callers redraw once (`_reshow`).

        ``answer`` False leaves the photograph's own answer as it is and
        only cuts the older passes down: see `_arrange`.
        """
        key = picture_of(result)
        if key is not None and answer:
            self.orientations[key] = (result.rotation, result.flipped)
        # Surveyed frames are exempt. The contact sheet displays these arrays
        # and the adjuster zooms into them, so decimating one in place would
        # quietly halve the picture the operator is deciding on -- and later,
        # the reference his decision is checked against. A no-op at 300 dpi
        # (431 px is already under the limit); it bites at 600 and 900.
        surveyed = {id(r) for r in self.survey}
        for old in self.results[:-WORKING_COPIES]:
            if id(old) in surveyed:
                continue
            if old.image is not None and max(old.image.shape[:2]) > ARCHIVE_MAX_SIDE:
                old.image = preview.downscale(old.image, ARCHIVE_MAX_SIDE)

    def _visible(self) -> list:
        return [r for r in self.results if not r.hidden]

    def _show(self, result) -> None:
        self.current = result
        self._full = None
        self._full_seq = None
        self._levels = []
        self._levels_seq = None
        self._rail = None
        self._rail_seq = None
        self._view = [0.0, 0.0]
        # Read the scan's own pixels straight away rather than waiting for a
        # zoom to ask for them: what is on screen is then the scan at every
        # size, and the reduced copy is only what fills the gap while 142 MB
        # comes off disk.
        if result.entry is not None and result.image is not None:
            self._load_full(result)
        available = (preview.channels_available(result.image)
                     if result.image is not None else ("RGB",))
        for name, button in self.channel_buttons.items():
            button.configure(state="normal" if name in available else "disabled")
        # A picture whose export was chosen by its view shows that view again,
        # so what is on screen is what Save As will write (`_remember_view`).
        chosen = self._view_of(getattr(result, "export_mix", None))
        if chosen is not None and chosen in available:
            self.v_channel.set(chosen)
        if self.v_channel.get() not in available:
            self.v_channel.set("RGB")
        self.v_caption.set(self._caption_for(result))
        self._measure_histogram()
        self._schedule_redraw()

    def _caption_for(self, result) -> str:
        """The line under the picture for one pass: its label, how it is
        arranged, how the roll held it and what else is known of it. Its own
        so a turn can say it again without showing the pass anew
        (`_reshow`)."""
        marks = result.registration or {}
        extra = ("   \u00b7   " + _arrangement(result)
                 if result.rotation or result.flipped else "")
        # Appended, not assigned. It used to overwrite, and because a roll
        # frame always carries an offset the arrangement note above was
        # discarded on every one of them -- which is exactly the note that
        # says a frame was filed sideways.
        # The offset that used to print here came from `framing.registration`,
        # the whole-picture detector whose own docstring records it reading
        # +-0.00 on every real prescan. It sat immediately beside the
        # ensemble's number from `_aim_note` -- two contradictory figures on
        # one line, with nothing saying they came from different detectors.
        # `shortfall` is a different measurement and still means something.
        if marks.get("shortfall_mm") is not None:
            extra += (f"   ·   short by "
                      f"{say_units(marks['shortfall_mm'], signed=False)}")
        extra += _aim_note(marks)
        # How the roll held it -- the badge on its thumbnail, in words.
        held = hold_note(hold_of(result))
        if held:
            extra += f"   ·   {held}"
        shading = (result.meta or {}).get("shading")
        if shading and shading.get("clipped"):
            extra += f"   ·   {shading['clipped']} clipped -- lower the exposure"
        if getattr(result, "supersedes", None):
            extra += "   ·   replaced its prescan"
        extra += read_note((result.meta or {}).get("read_direction"))
        if getattr(result, "error", None):
            extra += f"   ·   {result.error}"
        return result.label + extra

    def _reshow(self, *results) -> None:
        """Redraw the filmstrip, and the big picture if it is one of these.

        A turn made in the contact sheet used to reach the thumbnail and stop
        there, so the preview behind it went on showing the old arrangement
        until the frame was clicked again -- the window disagreeing with itself
        about a decision the operator had just made.

        Never by showing it anew (`_show`): that dropped the full view and
        read the entry again, 142 MB at 3600 dpi, to draw the pixels it held
        another way up -- they are kept as the scanner sent them and turned
        as they are drawn. Only the picture on screen is drawn again, from
        its corner, and its caption said again; a frame turned that is not on
        screen stays off it.
        """
        self._redraw_strip()
        if self.current is not None and any(r is self.current for r in results):
            self._view = [0.0, 0.0]
            self.v_caption.set(self._caption_for(self.current))
            self._schedule_redraw()

    def _redraw_strip(self) -> None:
        self.strip.delete("all")
        self._thumbs = []
        x = 6
        here = None                          # where the selected frame ended up
        for r in self._visible():
            if r.image is None:
                continue
            arr = preview.render(
                preview.fit(preview.orient(r.image, r.rotation, r.flipped),
                            _px(THUMB_H * 2), _px(THUMB_H)),
                "RGB", self.v_invert.get(),
                cuts=(preview.channel_levels(r.levels, "RGB")
                      if getattr(r, "levels", None) is not None else None))
            photo = tk.PhotoImage(data=preview.to_ppm(arr))
            self._thumbs.append(photo)
            tag = f"r{r.seq}"
            self.strip.create_image(x, 6, image=photo, anchor="nw", tags=tag)
            if r is self.current:
                here = (x, x + photo.width())
                self.strip.create_rectangle(
                    x - 2, 4, x + photo.width() + 1, 8 + photo.height(),
                    outline="#e8b64c", width=2)
            filing = getattr(r, "filing", None)
            if filing is not None:
                # Red for a pass the library does not hold, blue for a copy
                # it can make again: see `_filing`.
                self.strip.create_rectangle(
                    x + 1, 7, x + photo.width() - 2, 5 + photo.height(),
                    outline=(FILING_FAILED if filing == NOT_FILED
                             else COPY_UNWRITTEN), width=2,
                    tags=("failed" if filing == NOT_FILED else "unwritten",
                          tag))
            level = hold_level(hold_of(r))
            if level is not None:
                # A frame the roll scanned off its position -- never a walk's
                # prescan (`hold_of`): its top right corner in the badge's
                # colour, under the thumbnail's own tag so a click there still
                # shows the frame -- whose caption then says what happened
                # (`hold_note`).
                corner = _px(14)
                end = x + photo.width()
                self.strip.create_polygon(
                    end - corner, 6, end, 6, end, 6 + corner,
                    fill=HOLD_BADGE[level][0], outline="",
                    tags=("hold", f"hold-{level}", tag))
            self.strip.tag_bind(tag, "<Button-1>",
                                lambda _e, s=r.seq: self._show_seq(s))
            x += photo.width() + 8
        self.strip.configure(scrollregion=(0, 0, x, _px(THUMB_H + 12)))
        self._keep_in_strip(here, x)

    def _keep_in_strip(self, span, total: int) -> None:
        """Scroll only as far as it takes to have the selected frame in view.

        This used to be `xview_moveto(1.0)` -- jump to the end, on every
        redraw. A redraw happens when a pass is *selected* as well as when one
        arrives, so on a strip longer than the window, clicking the first
        frame showed you the last one: the picture changed to the frame you
        asked for and the strip scrolled away from it, leaving the highlight
        off screen and no sign of what had been chosen.

        Following the newest still falls out of this, because a pass that has
        just arrived is the selected one and it is off the right-hand end.
        """
        if span is None or total <= 0:
            return
        # The scrollregion was set a moment ago and Tk works out what that
        # means for the view at idle, so asking before then reads the old one.
        self.strip.update_idletasks()
        width = max(1, self.strip.winfo_width())
        if total <= width:
            self.strip.xview_moveto(0.0)
            return
        left = self.strip.canvasx(0)
        start, end = span
        if start < left:
            self.strip.xview_moveto(max(0.0, (start - 6) / total))
        elif end > left + width:
            self.strip.xview_moveto(min(1.0, (end + 6 - width) / total))

    def _show_seq(self, seq: int) -> None:
        for r in self.results:
            if r.seq == seq:
                self._show(r)
                self._redraw_strip()
                return

    def _at(self, event: tk.Event):
        """The result under a click on the filmstrip, or None."""
        for item in self.strip.find_overlapping(
                self.strip.canvasx(event.x) - 1, event.y - 1,
                self.strip.canvasx(event.x) + 1, event.y + 1):
            for tag in self.strip.gettags(item):
                if tag.startswith("r"):
                    try:
                        seq = int(tag[1:])
                    except ValueError:
                        continue
                    for r in self.results:
                        if r.seq == seq:
                            return r
        return None

    def on_strip_menu(self, event: tk.Event) -> None:
        target = self._at(event)
        if target is None:
            return
        self._show_seq(target.seq)
        self._fill_result_menu(target)
        self.menu.tk_popup(event.x_root, event.y_root)

    def on_canvas_menu(self, event: tk.Event) -> str:
        """The filmstrip's menu, over the picture it is actually about.

        One menu, filled by one method, so the two cannot drift apart -- a
        second copy of this list would be wrong the first time an item was
        added to either.

        `_drag` is cleared because Control-click is still Button-1 as far as
        `<B1-Motion>` is concerned, and a menu left open over a live drag pans
        the picture underneath it. "break" stops the same click reaching
        `on_press`, which in aim mode moves film.
        """
        self._drag = None
        if self.current is None:
            return "break"
        self._fill_result_menu(self.current)
        self.menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def accelerator(self, action_id: str) -> str:
        """This action's key, in the form a Tk menu wants beside an item.

        `accelerator_text`, not `describe`: Tk parses what it is given and
        draws the glyphs itself, so it wants "Command+R" and not "⌘R". Given
        the second it finds no modifier name it knows, takes the whole string
        as a key equivalent, and draws the first character only -- which is
        what put a lone ⌘ in the menu with no letter beside it.

        Empty rather than a dash when there is no key: a menu is a list of
        things you can do, and an em dash in the accelerator column reads as a
        key you cannot make out rather than as the absence of one. The editor
        is the place that has to show "no key", and it does.

        Read fresh every time a menu is filled, so a rebind shows up on the
        next right-click without anything having to be told about it.
        """
        return shortcuts.accelerator_text(self.keys.get(action_id, ""))

    def _fill_result_menu(self, target) -> None:
        """Everything that can be done to one pass, on `self.menu`.

        Each item carries its key. The menu is how anybody finds out these
        exist -- nobody reads a shortcut list first -- so an item without its
        key on it is a key nobody will ever learn.
        """
        self.menu.delete(0, "end")
        self.menu.add_command(label="Save as ...",
                              accelerator=self.accelerator("save_as"),
                              command=lambda r=target: self.on_save_as(r))
        shown = sum(1 for r in self.results if not r.hidden)
        self.menu.add_command(
            label=f"Save all {shown} passes ..." if shown > 1 else "Save all ...",
            state="normal" if shown > 1 else "disabled",
            command=self.on_save_all)
        for label, degrees, action_id in (
                ("Rotate right 90\u00b0", 90, "rotate_right"),
                ("Rotate left 90\u00b0", 270, "rotate_left"),
                ("Rotate 180\u00b0", 180, "rotate_180")):
            self.menu.add_command(
                label=label, accelerator=self.accelerator(action_id),
                command=lambda r=target, d=degrees: self.on_rotate(r, d))
        if target.rotation:
            self.menu.add_command(
                label=f"Straighten (now {target.rotation}\u00b0)",
                accelerator=self.accelerator("straighten"),
                command=lambda r=target: self.on_rotate(r, -r.rotation))
        self.menu.add_command(
            label="Unflip" if target.flipped else "Flip left-right",
            accelerator=self.accelerator("flip"),
            command=lambda r=target: self.on_flip(r))
        if target.supersedes:
            self.menu.add_separator()
            self.menu.add_command(label="Show prescan",
                                  accelerator=self.accelerator("show_prescan"),
                                  command=lambda r=target: self.on_show_prescan(r))
        self.menu.add_separator()
        self.menu.add_command(label="Delete",
                              accelerator=self.accelerator("delete_pass"),
                              command=lambda r=target: self.on_delete(r))

    def on_save_as(self, result) -> None:
        # The dialog starts on whatever the output folder is set to, so picking
        # JPEG once covers both places -- but the type chosen *in* the dialog
        # wins for this one file, because that is the more specific answer.
        end = export.suffix_for(self.session.out_format)
        name = f"{result.label.replace(' ', '_').replace('·', '')}{end}"
        path = filedialog.asksaveasfilename(
            parent=self.root, defaultextension=end, initialfile=name,
            filetypes=save_as_types(self.session.out_format))
        if not path:
            return
        # On a writer thread, as Save all is, and for both of its reasons.
        # Here on the UI thread, re-correcting a 3600 dpi frame froze the
        # window for seconds; and a failure -- a full or pulled disk, an entry
        # that would not load -- went to Tk's stderr handler, so nothing was
        # written, nothing was said, and the operator took it as saved.
        quality = jpeg_quality(self.v_jpegq.get())
        mono, channel = self._mono_for(result)
        target = Path(path)
        # The dialog asked about the one name typed. A JPEG brings files of
        # its own (`export.outputs`) -- `<stem>.dng` for its infrared, and
        # `<stem>.tif` in its place without Pillow -- and an earlier save's
        # were replaced, or left beside a picture they no longer belong to,
        # without a word: a pass with no infrared saved over x.jpg left the
        # earlier pass's x.dng there, reading as one scan with it.
        try:
            others = [q for q in export.outputs(target)[1:] if q.exists()]
        except ValueError:
            others = []                  # no format at all: said on writing
        exact = True
        #: The earlier save's DNG, and which files it and the picture were.
        companion: tuple[Path, tuple[int, int, int] | None,
                         tuple[int, int, int] | None] | None = None
        if others:
            try:
                alternative: Path | None = unclaimed_delivery(target)
            except FileExistsError:
                alternative = None
            dng = export.infrared_path(target)
            answer = messagebox.askyesnocancel(
                "Save as",
                f"{', '.join(o.name for o in others)} "
                f"{is_are(len(others))} beside {target.name} already, from "
                "an earlier save, and would read as one scan with this one."
                f"\n\nYes saves as {target.name} anyway"
                + (f", and replaces or removes {dng.name}" if dng in others
                   else "")
                + ". "
                + (f"No saves as {alternative.name} instead. "
                   if alternative is not None else "")
                + "Cancel saves nothing.",
                default=messagebox.NO, parent=self.root)
            if answer is None or (answer is False and alternative is None):
                return
            if answer is False:
                target, exact = alternative, False
            elif dng in others:
                companion = (dng, _identity(dng), _identity(target))
        name = target.name

        def run() -> None:
            claimed: Path | None = None
            try:
                # The name confirmed, as it is; refused only while a write
                # in flight in this window holds it (`export.claim`).
                claimed = export.claim(target, exact=exact)
                said = self._deliver_one(result, claimed, quality, mono,
                                         channel)
                if companion is not None and \
                        _identity(companion[0]) == companion[1] and \
                        _identity(target) not in (None, companion[2]):
                    # Not rewritten, so not this pass's: an earlier save's
                    # infrared beside a picture it no longer belongs to.
                    # Only once the new file is in place, and only the DNG:
                    # a TIFF of that name is only ever replaced. And only
                    # where the picture itself was replaced: a JPEG written
                    # as a TIFF for want of Pillow leaves the earlier save's
                    # JPEG standing, and this is still its infrared.
                    stale = companion[0].name
                    try:
                        companion[0].unlink(missing_ok=True)
                    except OSError as exc:
                        said += (f" -- {stale}, left by an earlier save, "
                                 f"could not be removed ({exc}); it does "
                                 "not belong with this one")
                    else:
                        said += (f" -- removed {stale}, left by an earlier "
                                 "save: it does not belong with this one")
            except Exception as exc:                     # noqa: BLE001
                # Said in the window and asked to be seen, as a Save As the
                # operator is waiting on: the log line alone scrolls past.
                self._saves.put(("failed", name, str(exc)))
                return
            finally:
                if claimed is not None:
                    export.release([claimed])
            if said:
                self._saves.put(("line", f"saved {said}"))

        self._say(f"saving {name} ...")
        self._start_writing(run, "save-as")

    def _mono_for(self, result) -> tuple[bool, str]:
        """One channel or all for a delivered copy of this pass, and which.

        The pass's own film decides, as it did when the scan was delivered
        (`wants_mono`), not whatever film the window is set to now. Save As,
        Save all and Export read the window's controls, so a colour roll
        exported with the window on black and white came out as one grey
        plane with colour and infrared gone, and a black and white roll
        exported with it on negative as three channels, unlike its own
        frames.

        A roll's export items carry the roll's own answer. A pass whose film
        is the window's keeps the window's controls, where the operator may
        have chosen otherwise for that film; one of another film follows it.

        Ahead of all of those, a black-and-white picture's own view
        (`_remember_view`): the one it was left on in this window, found for
        a roll's Export through the library entry they share.
        """
        chosen = self._chosen_mix(result)
        if chosen is not None:
            return chosen
        own = getattr(result, "mono", None)
        if own is not None:
            return bool(own), getattr(result, "mono_channel", None) or MONO_CHANNEL
        film = (getattr(result, "meta", None) or {}).get("film")
        if film and film != self.v_film.get():
            return wants_mono(None, film), self.v_mono_channel.get()
        return self.v_mono.get(), self.v_mono_channel.get()

    def _chosen_mix(self, result) -> tuple[bool, str] | None:
        """The ``export_mix`` its view gave this picture, or a pass of the
        same library entry, in this window; None where none was chosen."""
        mix = getattr(result, "export_mix", None)
        if mix is not None:
            return mix
        entry = getattr(result, "entry", None)
        if entry is None:
            return None
        for r in self.results:
            if (getattr(r, "export_mix", None) is not None
                    and getattr(r, "entry", None) is not None
                    and Path(r.entry) == Path(entry)):
                return r.export_mix
        return None

    def on_save_all(self) -> None:
        """Every pass of this session into one folder, in one go.

        *Save as ...* is one dialog per pass, which is right for one and is
        thirty-eight of them after a roll.

        **On a thread, because it is not quick.** Each filed pass is re-read
        from its library entry and re-corrected at full resolution -- that is
        what makes it the same picture the operator saw rather than the reduced
        copy on screen -- and thirty-eight of those is minutes. Doing it on the
        UI thread would freeze the window for all of them.
        """
        passes = [r for r in self.results if not r.hidden
                  and (r.entry is not None or r.image is not None)]
        if not passes:
            messagebox.showinfo("Save all", "No passes to save yet.")
            return
        if self._saving:
            messagebox.showinfo(
                "Save all", "Still writing the last batch. It says so in the "
                "log as each file lands.")
            return
        folder = filedialog.askdirectory(
            parent=self.root, title="Save every pass into ...",
            initialdir=str(self.session.out_dir or Path.home()))
        if not folder:
            return
        fmt = self.session.out_format
        filed = sum(1 for r in passes if r.entry is not None)
        if not messagebox.askokcancel(
            "Save all",
            f"Write {counted(len(passes), 'pass', 'passes')} into "
            f"{Path(folder).name} as {fmt.upper()}.\n\n"
            + ((f"{filed} of them {is_are(filed)}" if len(passes) > 1
                else "It is")
               + " re-corrected from the library at full resolution, which "
               f"takes a moment{' each' if filed > 1 else ''}.\n\n"
               if filed else "")
            + (f"{len(passes) - filed} {is_are(len(passes) - filed)} the "
               "reduced previews on screen, "
               "because their full-resolution pixels are not filed yet -- the "
               "log says which.\n\n" if len(passes) - filed else "")
            + "Nothing already there is overwritten; a clashing name gets the "
              "next free one.\n\nStart?",
        ):
            return

        # Read here, on the UI thread, and handed over. A worker that reached
        # back into a Tk variable would work until the day it did not.
        quality = jpeg_quality(self.v_jpegq.get())
        # Each pass's own, decided here for the same reason; see `_mono_for`.
        monos = [self._mono_for(result) for result in passes]
        out = Path(folder)
        self._saving = True
        self._say(f"saving {counted(len(passes), 'pass', 'passes')} into "
                  f"{out} ...")

        def run() -> None:
            written = 0
            for result, (mono, channel) in zip(passes, monos):
                path: Path | None = None
                try:
                    # Claimed, not only found free: see `export.claim`.
                    path = export.claim(out / batch_name(result, fmt))
                    said = self._deliver_one(result, path, quality, mono,
                                             channel)
                except Exception as exc:                 # noqa: BLE001
                    # One pass that cannot be written costs that pass. The same
                    # line `FrameWriter` takes about a frame it cannot file.
                    self._saves.put(("line", f"could not save "
                                             f"{result.label}: {exc}"))
                    continue
                finally:
                    if path is not None:
                        export.release([path])
                if said:
                    written += 1
                    self._saves.put(("line", f"saved {said}"))
            self._saves.put(("done", written, len(passes)))

        self._start_writing(run, "save-all")

    def _deliver_one(self, result, path, quality: int, mono: bool,
                     mono_channel: str) -> str:
        """Write one pass to `path`, corrected, and say what was written.

        The one place a delivered file is made from a pass, so *Save as ...* and
        *Save all ...* cannot disagree about what "the picture" means. They did
        not yet, and a second copy of this would be wrong the first time either
        was changed.

        Safe to call off the UI thread: it touches no widget. Everything it
        needs is passed in, which is why `quality`, `mono` and `mono_channel`
        are arguments rather than read from the controls here -- reading a Tk
        variable from a worker is exactly the kind of thing that works until it
        does not.
        """
        if result.entry and (result.entry / "scan.tif").exists():
            # Corrected, always. The entry holds raw pixels and the reference
            # beside them; what leaves here is what the operator saw. This used
            # to copy scan.tif straight through when nothing was turned, which
            # is now an uncorrected file -- so it is re-written every time, and
            # the copy is gone deliberately rather than by oversight.
            full, entry_record = library.corrected(result.entry)
            full = preview.orient(full, result.rotation, result.flipped)
            dropped = infrared_left_out(full) if mono else ""
            if mono:
                full = to_monochrome(full, mono_channel)
            # The pass's own resolution, which the output folder's copy of it
            # carries (`FrameWriter`). Written without it, a 3600 dpi frame
            # said 72 dpi or nothing -- about a metre and a half wide to
            # anything that sizes a picture by it -- and a JPEG always said
            # the editor's default.
            dpi = ((entry_record.get("scan") or {}).get("resolution_dpi")
                   or (result.meta or {}).get("resolution_dpi"))
            note = export.write(path, full, quality=quality,
                                resolution=int(dpi) if dpi else None)
            how = entry_record.get("corrected")
            return (f"{Path(path).name} at full resolution"
                    + (f", turned {result.rotation}\u00b0"
                       if result.rotation else "")
                    + (", one channel" if mono else "")
                    + ("" if how == "applied" else f" ({how})")
                    + (f" -- {note}" if note else "")
                    + (f" -- {dropped}" if dropped else ""))
        if result.image is not None:
            turned = preview.orient(result.image, result.rotation,
                                    result.flipped)
            note = export.write(
                path,
                to_monochrome(turned, mono_channel) if mono else turned,
                # No resolution: a reduced copy at the scan's dpi would
                # misstate its size as surely as 72 does.
                quality=quality)
            return (f"{Path(path).name} -- reduced preview, the "
                    "full-resolution file is not filed yet"
                    + (f" -- {note}" if note else ""))
        return ""

    def on_rotate(self, result, degrees: int) -> None:
        """Turn this pass, and everything scanned after it.

        The carry-over is the point: rotating a prescan is how you say which
        way up the film is, and the scan that follows should not need telling
        again. It reaches the files written from here on -- the output folder's
        copy and a roll's own TIFF -- but never the library entry, whose pixels
        have to keep matching the raw bytes filed beside them.
        """
        result.rotation = (result.rotation + degrees) % 360
        self._carry(result, _arrangement(result))

    def on_flip(self, result) -> None:
        """Mirror this pass left to right, and everything scanned after it.

        Not a fourth angle: a strip loaded the other way up comes off this
        scanner reading backwards, and no amount of turning fixes that. It
        carries over exactly as a turn does, for the same reason -- which way
        round the film went in does not change between one frame and the next.
        """
        result.flipped = not result.flipped
        self._carry(result, _arrangement(result))

    def _carry(self, result, said: str) -> None:
        """Make this pass's arrangement the one new passes and files follow.

        The carry-over is the point: arranging a prescan is how you say how the
        film went in, and the scan that follows should not need telling again.
        It reaches the files written from here on -- the output folder's copy
        and a roll's own TIFF -- but never the library entry, whose pixels have
        to keep matching the raw bytes filed beside them.
        """
        self.rotation = result.rotation
        self.flip = result.flipped
        # One change, not two: a roll filing a frame between a turn and a
        # mirror set one after the other gave it the new turn and the old
        # mirror (`ScanSession.arrange`).
        self.session.arrange(result.rotation, result.flipped)
        self._record_arrangement(result)
        # Anything it stands in for follows it, so showing the prescan again
        # does not undo what was just decided.
        stood_for = None
        if result.supersedes:
            for r in self.results:
                if r.seq == result.supersedes:
                    r.rotation, r.flipped = result.rotation, result.flipped
                    stood_for = r
        # And the sheet, where either is a frame it shows: its own records
        # laid the old turn back over the next time it opened.
        self._turned_on_sheet(result, stood_for)
        # A commission writes its frames the way it was commissioned, and
        # shows them so (`_written_as`): this reaches the passes after it.
        roll, running = self._roll_in_hand()
        which = "running now" if running else "waiting to start"
        held = (f" -- but not the roll {which}, which writes and shows each "
                "frame the way it was commissioned"
                if roll is not None and roll.approved else "")
        self._say(f"{result.label}: {said} -- new scans and the files written "
                  "for them follow this; the library entry keeps the scanner's "
                  "own orientation" + held)
        # Once: the strip and, where it is the picture on screen, the picture
        # -- drawn again rather than shown anew, which read its entry again
        # (`_reshow`). A pass turned from its thumbnail is shown, as before.
        if result is self.current:
            self._reshow(result)
        else:
            self._view = [0.0, 0.0]
            self._show(result)
            self._redraw_strip()

    def _turned_on_sheet(self, *results) -> None:
        """Walked prescans among ``results``, turned outside the sheet,
        turned in its records too: the open sheet's, else what it is kept
        holding (`sheet_state`), filed as it would file it.

        A walked prescan in the filmstrip is the sheet's own frame, so a
        turn there changed the frame's result and the roll it is commissioned
        into -- and nothing the sheet keeps. Its cell was left drawn the old
        way, and the next time the sheet opened, its kept turns were laid
        back over the result without a word.
        """
        walked = [r for r in results if r is not None and r.number
                  and any(r is s for s in self.survey)]
        if not walked:
            return
        sheet = self.sheet
        if sheet is not None and sheet.alive():
            for result in walked:
                sheet.turned_elsewhere(result)
            return
        state = self._recall_sheet_state()
        for result in walked:
            state["rotations"][int(result.number)] = int(result.rotation) % 360
            state["flips"][int(result.number)] = bool(result.flipped)
        self._store_sheet_state(state)

    def _measure_histogram(self) -> None:
        """Re-read the panel against whatever is on screen now.

        Where the values actually sit, which the preview cannot show: the
        picture on the canvas is stretched so a negative can be judged by eye,
        and a stretch puts the brightest pixel at white whether it was against
        the ceiling or merely near it.

        Measured on a thread. `clipping` counts every pixel rather than
        sampling, which on a full 3600 dpi frame is about a third of a second
        -- and a window that locks up for that long each time you click along
        the filmstrip is its own kind of unhelpful.

        Called twice for one pass, deliberately: once on the working copy as
        soon as it is shown, and again when the scan's own pixels arrive,
        because a reduced copy understates how much is at the rail.

        **What is at 0 and at full scale is the sensor's**, wherever the entry
        is on disk to say. The correction multiplies each column by its own
        gain, and that moves the rail: in the bright middle of the lamp a
        sample the sensor railed comes back near 52000, counted as neither at
        nor near full, and at the edges a gain above one clamps samples the
        sensor never railed. So the table measured on corrected pixels missed
        real clipping and reported clipping that was not there -- the physics
        the bracket merge judged by, before it was archived in
        docs/multi-exposure/. The curve stays the
        corrected picture's: it is where the values sit in what is delivered.
        """
        result = self.current
        if result is None or result.image is None:
            self.histogram.nothing()
            return
        pixels, source = self._finest_pixels(result)
        # Infrared is not an exposure -- see `rgb_only`.
        pixels = rgb_only(pixels)
        rail = (self._rail if self._rail_seq == result.seq
                and result is self.current else None)
        if rail is not None:
            source += "; 0 and full as the sensor read them"
        # A plain counter, not the result's seq: one pass is measured twice,
        # so a token that only said *which* pass would let the coarse answer
        # land after the fine one and quietly replace it.
        self._histogram_token += 1
        token = self._histogram_token
        self.histogram.waiting(source)

        def work():
            try:
                counts = preview.histogram(pixels)
                clipped = rail if rail is not None else preview.clipping(pixels)
            except Exception as exc:                     # noqa: BLE001
                self._measured.put((token, None, None, str(exc)))
                return
            self._measured.put((token, counts, clipped, None))

        threading.Thread(target=work, daemon=True).start()

    def _finest_pixels(self, result):
        """The best pixels available for `result`, and what to call them."""
        if (self._levels_seq == result.seq and self._levels
                and result is self.current):
            factor, array = self._levels[-1]
            return array, f"the scan's own {array.shape[1]}x{array.shape[0]} pixels"
        return (result.image,
                f"a {result.image.shape[1]}x{result.image.shape[0]} copy, "
                "not the scan itself")

    def on_show_prescan(self, result) -> None:
        for r in self.results:
            if r.seq == result.supersedes:
                r.hidden = False
                result.supersedes = None
                self._show_seq(r.seq)
                self._redraw_strip()
                return

    def on_delete(self, result) -> None:
        """Take a pass out of this session -- and, asked twice, its library
        entry into the library's trash, where it stays whole until the trash
        is emptied by hand (Stefan's call, 2026-10-09).

        The entry is the result's, or where the session says the pass was
        filed (`ScanSession.filing`): the window hears of it a tick or two
        after the writer has it, and a Delete pressed in between offered
        only to drop the pass, and the entry was filed afterwards with
        nothing said. While it is still being written that is said, and the
        entry kept: Delete again, a moment later, moves it too.

        Only an entry of the library this session files into, by its name,
        directly in it (`_own_entry`), as the trash takes it. A reopened
        roll's frames carry the entry their walk filed, and `make run-sheet`
        opens a real walk -- so under the demo this was a real entry, raw
        bytes and all, one "No" away from rmtree under the one mode promised
        to touch nothing real. Not a demo branch: the window only ever takes
        an entry from the library it files into, and a demo files into its
        own. Outside the demo that is every entry it shows, bar a roll
        opened from another library, which this leaves to that library too.
        """
        state, filed = self.session.filing(result.seq)
        given = result.entry or filed
        entry = self._own_entry(given)
        question = f"Remove {result.label} from this session?"
        if given is not None and entry is None and Path(given).exists():
            question += (f"\n\nIts library entry {Path(given).name} is not "
                         "in this session's library, and is left where it "
                         "is.")
        elif entry is None and state == "owed":
            # Never queued for later: nothing acts after the question has
            # gone that the operator did not just confirm.
            if not messagebox.askokcancel(
                    "Delete", question + "\n\nIts library entry is still "
                    "being written. OK removes it from this session and "
                    "keeps that entry; Cancel, and press Delete again in a "
                    "moment to move the entry to the trash as well.",
                    default=messagebox.CANCEL, parent=self.root):
                return
            self._drop(result)
            return
        elif entry is None and getattr(result, "filing", None) == NOT_FILED:
            question += ("\n\nIt was not filed in the library -- the log says "
                         "why" + ("; with debug filing on, its spooled copy "
                                  "may still be filed when the scanner closes"
                                  if self.session.debug_filing else "")
                         + ".")
        if entry is not None:
            if not self._ask_to_trash(result, entry, question):
                return
        elif not messagebox.askokcancel("Delete", question, parent=self.root):
            return
        self._drop(result)

    def _drop(self, result) -> None:
        """Take a pass out of this session: the strip, and the view."""
        # Anything it was standing in for comes back rather than vanishing too.
        if result.supersedes:
            for r in self.results:
                if r.seq == result.supersedes:
                    r.hidden = False
        self.results = [r for r in self.results if r.seq != result.seq]
        if self.current is result:
            visible = self._visible()
            self.current = visible[-1] if visible else None
            if self.current:
                self._show(self.current)
            else:
                self.v_caption.set("nothing scanned yet")
                self._schedule_redraw()
        self._redraw_strip()

    def _own_entry(self, given) -> Path | None:
        """The entry ``given`` names, where it is one of this session's
        library: by its name, directly in `session.root`, as `library.trash`
        takes it -- an entry of a library nested inside, or of another one,
        is not, and neither is anything holding no record. A path written
        relative to wherever another window ran is found by its name; one
        that is there and is another folder is not this one."""
        root = self.session.root
        if given is None or not root:
            return None
        named = Path(given)
        mine = Path(root) / PureWindowsPath(str(given)).name
        if not (mine / "scan.json").is_file():
            return None
        try:
            if named.exists() and named.resolve() != mine.resolve():
                return None
        except OSError:
            return None
        return mine

    def _ask_to_trash(self, result, entry: Path, question: str) -> bool:
        """`on_delete`'s two questions for a pass with an entry here, and the
        move. True to take the pass out of the session -- its entry kept, or
        moved -- and False to leave everything as it was.

        Two questions still (Stefan's call, 2026-10-09: the guard stays in
        the change that adds the safety net). Keeping the entry is the
        answer that needs nothing but a press: the question used to be "Keep
        the library entry?", under a title of "Delete", and read by its title
        "No" -- then the irreversible answer -- was the natural one.

        A slide's infrared pass goes with its colour pass, in one delete
        (`library.infrared_partner`): left behind, it was an entry nothing
        named, and its picture lost its infrared once the colour pass came
        back without it.
        """
        record = library._read_record(entry) or {}
        partner = library.infrared_partner(entry, record)
        partner = self._own_entry(partner) if partner is not None else None
        batch = [entry] + ([partner] if partner is not None else [])
        refusal = self.session.still_filing(batch)
        if refusal is not None:
            messagebox.showinfo("Delete", refusal[0].upper() + refusal[1:]
                                + ".", parent=self.root)
            return False
        question += (f"\n\nIts library entry {entry.name} holds the raw "
                     "bytes, which cannot be recovered without rescanning.")
        if partner is not None:
            question += (f" Its infrared pass, {partner.name}, goes with it: "
                         "the picture is made from both.")
        if result.seq < 0:
            question += ("\n\nThis frame was reopened from a roll: the entry "
                         "is the one its walk filed, not a copy made for this "
                         "window.")
        roll_said = "".join(f"\n\n{line}"
                            for line in self._frames_said(entry, record))
        also = messagebox.askyesnocancel(
            "Delete", question + "\n\nMove its library entry to the library's "
            "trash as well? It stays whole there until the trash is emptied."
            + roll_said + "\n\nNo removes it from this session and keeps the "
            "entry.", default=messagebox.NO, parent=self.root)
        if also is None:
            return False
        if not also:
            return True
        if self.closing:
            # Its compaction may be in that entry as the scanner closes.
            messagebox.showinfo(
                "Delete", "The window is closing, and the library's last "
                "entries are being finished. The entry is kept; delete it "
                "next time.", parent=self.root)
            return False
        size = sum(library._size(p) for p in batch)
        named = " and ".join(p.name for p in batch)
        if not messagebox.askokcancel(
                "Move the library entry to the trash",
                f"Move {named} into {Path(self.session.root) / library.TRASH}?"
                f"\n\nIts {human_size(size)} are not freed until the trash is "
                "emptied (tools/library.py empty-trash --write); until then "
                "tools/library.py restore brings it back.",
                icon=messagebox.WARNING, default=messagebox.CANCEL,
                parent=self.root):
            return False
        return self._trash_entries(batch, result)

    def _frames_said(self, entry: Path, record: dict) -> list[str]:
        """What moving ``entry`` to the trash means for the rolls, for the
        Delete question: each frame whose roll.json names it as that frame's
        entry counts as not scanned (`ScanSession.frames_naming`, the frames
        `trash_entries` marks) -- and an entry that is only an earlier take,
        the frame scanned again since, is said as one: the roll keeps its
        later take, and marks nothing.

        From the rolls' records, not from the entry's own roll membership:
        that says which frame it was filed as, and promised "counts as not
        scanned" of a frame the roll had since taken again, which stayed
        scanned and whose frameNN.tif was the later take's.
        """
        said = []
        rolls_trash = _folder_key(Path(self.session.rolls) / library.TRASH)
        for path, _entry, numbers in self.session.frames_naming([entry]):
            folder = path.parent
            # In the trash: a roll's own folder, or a strip of a roll there
            # -- whole, or on its own.
            trashed = rolls_trash in (_folder_key(folder.parent.parent),
                                      _folder_key(folder.parent.parent.parent))
            # Its records, through the session's manifest where it writes
            # this one: the numbers they carry, which hold an extra the
            # index lost, and whether each frame's copy was written.
            progress = self._progress_now(path)
            records = {r.get("number"): r for r in progress.get("frames")
                       or () if isinstance(r, dict)}
            for number in numbers:
                named = self._roll_frame_named(path, number, progress)
                named = named[0].upper() + named[1:]
                line = (f"{named}, a roll in the rolls' trash, then counts as "
                        "not scanned once that roll is restored, and is "
                        "offered again when it is finished" if trashed else
                        f"{named} then counts as not scanned, and is offered "
                        "again when the roll is finished")
                own = f"frame{number:02d}.tif"
                if (folder / own).exists():
                    # This take's own copy only where its record says it
                    # was written, or says nothing either way: one whose
                    # copy was not written left an earlier take's there.
                    line += (f"; the {own} in its folder is an earlier "
                             "take's" if copy_written(records.get(number)
                                                      or {}, own) is False
                             else f"; {own} stays in its folder")
                said.append(line + ".")
        if said:
            return said
        member = (record.get("extra") or {}).get("roll_membership") or {}
        if not (isinstance(member, dict) and member.get("kind") == "frame"
                and isinstance(member.get("number"), int)
                and member.get("folder")):
            return said
        number = member["number"]
        path = Path(str(member["folder"])) / "roll.json"
        try:
            progress = read_manifest(path)
        except (OSError, ValueError):
            return said
        now = next((r for r in progress.get("frames") or ()
                    if isinstance(r, dict)
                    and str(r.get("number")) == str(number)), None)
        later = (PureWindowsPath(str(now.get("entry"))).name
                 if now and now.get("entry") else None)
        if later and later != entry.name:
            named = self._roll_frame_named(path, number, progress)
            said.append(f"It is an earlier take of {named}: the roll keeps "
                        f"its later one, {later}, and still counts that "
                        "frame as scanned.")
        return said

    def _progress_now(self, path: Path) -> dict:
        """A roll folder's roll.json at ``path`` as it stands: through the
        session's manifest where it writes it, read otherwise; ``{}`` where
        it cannot be read."""
        manifest = self.session.live_manifest(path)
        snapshot = manifest.snapshot() if manifest is not None else None
        if snapshot is not None:
            return snapshot
        try:
            return read_manifest(path)
        except (OSError, ValueError):
            return {}

    def _roll_frame_named(self, path: Path, number: int,
                          progress: dict | None = None) -> str:
        """Frame ``number`` -- its place -- of the roll folder whose roll.json
        is ``path``, as every line says it: a strip's by its number across
        the roll with its place beside it (`session.frame_said`), by the
        number its record carries (``progress``, read when not given), which
        holds an extra the index lost; one from before strips by its folder.
        A bare ``strip-02`` names no roll."""
        folder = path.parent
        strip = place_of(folder, strict=False)
        if strip is None:
            # By either separator: recorded on Windows, it has backslashes.
            return f"frame {number} of {PureWindowsPath(str(folder)).name}"
        if progress is None:
            progress = self._progress_now(path)
        given = record_numbers(progress)
        return frame_said(shown_number(strip, number, given.get(number, 0))
                          or number, strip.strip, number, of=strip.label)

    def _trash_entries(self, batch: list[Path], result) -> bool:
        """Move ``batch`` -- a pass's entry, and a slide's infrared pass --
        into the library's trash, the roll frames each was marked first
        (`ScanSession.trash_entries`), and say where it went and how to
        bring it back. True once the pass's own entry has gone.

        From the window's thread, even while the scanner works: a rename on
        the library's own disk, done at once. It used to be refused then --
        an `rmtree` raced the writer filing beside it -- and a Delete pressed
        mid-roll was "try again later" for hours.
        """
        entry = batch[0]
        try:
            done = self.session.trash_entries(
                batch, by=self._who, why=f"Delete of {result.label}")
        except StillFiling as exc:
            said = str(exc)
            messagebox.showinfo("Delete", said[0].upper() + said[1:] + ".",
                                parent=self.root)
            return False
        except locks.Refused as exc:
            messagebox.showinfo(
                "Delete", f"A roll that names {entry.name} is in use by "
                f"{exc.who}, and its record cannot be marked meanwhile. Try "
                "again once it has finished.\n\nNothing was moved.",
                parent=self.root)
            return False
        except (OSError, ValueError) as exc:
            self._say(f"could not move {entry.name} to the trash: {exc}; it "
                      "is left whole, and nothing was moved")
            return False
        moved = done.trashed
        for name, why in moved.left.items():
            self._say(f"{name} is left whole in the library: {why}")
        if moved.batch is not None:
            self._say(f"moved {', '.join(moved.moved)} to the trash "
                      f"({moved.batch}); "
                      f"`{self._restore_command(moved.batch.name)}` brings "
                      f"{'it' if len(moved.moved) == 1 else 'them'} back")
        # After the move is said, and only the frames whose entry went.
        sheet = self._sheet_roll
        for path, number in done.frames:
            if sheet is not None and _folder_key(path.parent) == \
                    _folder_key(sheet):
                self._sheet_done.discard(number)
            self._say(f"{self._roll_frame_named(path, number)} counts as "
                      "not scanned now; finishing the roll offers it again")
        return entry.name in moved.moved

    # -- drawing, zoom and pan --------------------------------------------

    def _source(self):
        """The picture, in the coordinates the zoom and the view are measured in.

        Always the working copy. Everything about where the picture sits is
        expressed against this one array, so reading the full-resolution pixels
        cannot move anything -- it only changes what is sampled, never what the
        numbers mean.
        """
        r = self.current
        if r is None or r.image is None:
            return None
        return preview.orient(r.image, r.rotation, r.flipped)

    def _finest(self) -> float:
        """How much finer the scan is than the working copy the view uses.

        So that the percentage means what it says: 100% is one scanned pixel
        per screen pixel, not one pixel of the reduced copy the window happens
        to be drawing from. On a 3600 dpi frame those differ by four.
        """
        r = self.current
        if r is None or r.image is None:
            return 1.0
        scanned = (r.meta or {}).get("width")
        if not scanned:
            return 1.0
        turned = r.rotation % 180 != 0
        across = r.image.shape[0] if turned else r.image.shape[1]
        return max(1.0, float(scanned) / max(1, across))

    def _pixels(self, scale: float):
        """What to sample, and how much finer it is than the picture.

        The coarsest array that still holds the detail being asked for. Going
        straight from the working copy to the full scan meant that just past
        the point where the copy runs out, a 3400-pixel-wide strip had to be
        decimated out of 142 MB for a 900-pixel view -- 30 ms against the
        copy's 4, a step you could feel exactly where the two met. A level in
        between keeps the strip read to about twice what is drawn, whatever the
        zoom, and costs 11.
        """
        r = self.current
        if r is None or r.image is None:
            return None, 1.0
        if self._levels_seq == r.seq and self._levels:
            # By the scale actually being drawn, not by the zoom: at fit the
            # zoom is zero, and choosing by it upscaled the reduced copy on any
            # pane wider than the copy -- soft exactly where the whole picture
            # is on show.
            for factor, array in self._levels:
                if factor >= scale:
                    return preview.orient(array, r.rotation, r.flipped), factor
            finest, array = self._levels[-1]
            return preview.orient(array, r.rotation, r.flipped), finest
        return preview.orient(r.image, r.rotation, r.flipped), 1.0

    def _load_full(self, r) -> None:
        """Read the entry's own pixels, off the UI thread.

        A reduced copy cannot show grain or shadow noise, so the picture on
        screen should be the scan itself wherever it can be. At 3600 dpi that
        is 142 MB, which would freeze the window for seconds if it were read
        here.

        **One read at a time.** Each is a correction of the whole scan, with a
        float copy per channel on the way -- 1.5-2 GB at 7200 dpi RGBI -- and a
        read started for every pass clicked past in the filmstrip ran them all
        at once, beside a scanner held open mid-roll: several gigabytes, swap,
        or the process killed with a read in flight. A pass shown while one is
        reading waits its turn, and only the last one shown is read next.
        """
        if self._loading == r.seq or self._levels_seq == r.seq:
            return
        if self._loading is not None:
            self._load_next = r
            return
        self._loading = r.seq
        self._load_next = None

        def work(entry: Path, seq: int) -> None:
            image = problem = how = rail = None
            try:
                # Read once. The rail as the sensor met it, for
                # `_measure_histogram`, is counted on the stored pixels before
                # they are corrected; reading them a second time inside
                # `library.corrected` doubled the wait for this view -- at
                # 7200 dpi, two reads of some 570 MB with the device open.
                raw, record = library.load(entry)
                rail = preview.clipping(rgb_only(raw))
                # Corrected, not raw: the library stores what the scanner sent
                # and the correction beside it, and this is the full-resolution
                # view an operator asked to look at. How that went comes back
                # with it: an entry that could not be corrected is handed back
                # raw, and was shown as "the scan's own pixels" without a word.
                image, record = library.correct(raw, record)
                del raw
                # A slide's RGB pass with its RGBI pass's infrared laid on,
                # as `library.corrected` gives it to every export.
                image, record = library.with_infrared(entry, image, record)
                how = record.get("corrected")
                if how == "already":
                    # A legacy entry, filed corrected: its stored pixels are
                    # not what the sensor read, and the caption says they are.
                    rail = None
            except Exception as exc:                     # noqa: BLE001
                problem = f"could not read {entry.name}: {exc}"
            # Through a queue, never by calling Tk. `after()` from another
            # thread is not safe, and wrapping it in a try/except turned a
            # visible failure into a silent one: the read finished, the call
            # back never arrived, and the full-resolution view simply never
            # appeared with nothing anywhere to say why.
            self._reads.put((seq, image, problem, how, rail))

        reader = threading.Thread(target=work, args=(r.entry, r.seq),
                                  daemon=True, name="full-resolution")
        # Registered, so the session's compaction at close waits for it
        # (`_wait_for_readers`).
        self._reading = [t for t in self._reading if t.is_alive()] + [reader]
        reader.start()

    def _loaded(self, seq: int, image, how: str | None = None,
                rail=None) -> None:
        """The scan's own pixels are in; `how` is `library.corrected`'s word,
        and `rail` what was at the rail as the sensor read it."""
        self._loading = None
        waiting, self._load_next = self._load_next, None
        if waiting is not None and waiting is self.current:
            self._load_full(waiting)
        if self.current is None or self.current.seq != seq or image is None:
            return
        self._rail, self._rail_seq = rail, seq
        # Nothing is adjusted: the zoom and the view are measured against the
        # working copy, so the big array arriving changes what is sampled and
        # not what any of the numbers mean.
        self._full, self._full_seq = image, seq
        # The reduced copy is the coarsest level, so a view that does not need
        # the scan's own pixels still costs what it always did.
        self._levels = [(1.0, self.current.image)] + preview.pyramid(
            image, image.shape[1] / max(1, self.current.image.shape[1]))
        self._levels_seq = seq
        self._say(f"{self.current.label}: now showing the scan's own "
                  f"{image.shape[1]}x{image.shape[0]} pixels"
                  + ("" if how in (None, "applied", "already") else
                     f" -- UNCORRECTED ({how}): the striping and fall-off in "
                     "them are the sensor's, and so is what the histogram "
                     "says"))
        # The levels are deliberately not re-measured -- they stay the working
        # copy's, so a 1:1 look is the same picture as the fit it came from.
        # The histogram is, because it is a measurement rather than a look, and
        # a reduced copy understates how much is at the rail.
        self._measure_histogram()
        self._redraw()

    def _set_zoom(self, zoom: float) -> None:
        """Fit, or a scale about the middle of what is on screen."""
        src = self._source()
        if zoom > 0 and src is not None:
            w, h = self._picture_area()
            middle = self._source_at(w / 2, h / 2)
            self._view = [middle[0] - (w / 2) / zoom, middle[1] - (h / 2) / zoom]
        self._zoom = zoom
        self._schedule_redraw()

    def _zoom_by(self, factor: float, moving: bool = False,
                 anchor: tuple[float, float] | None = None) -> None:
        """Scale about `anchor`, keeping whatever is under it where it is.

        Anchoring is the whole feel of a zoom. Without it the picture scales
        about the middle of the canvas, so the thing you were looking at slides
        away exactly when you lean in on it -- which is what "it does not zoom
        correctly" is: the arithmetic was right and the pivot was wrong.

        Fit is the floor. Below it the picture only shrinks into the middle of
        an empty canvas, which is not a view of anything.
        """
        src = self._source()
        if src is None:
            return
        w, h = self._picture_area()
        fit = min(w / src.shape[1], h / src.shape[0])

        focus = self._source_at(*anchor) if anchor is not None else None
        target = (self._zoom if self._zoom > 0 else fit) * factor

        if target <= fit:
            self._zoom = 0.0                             # back to fit, and stop
            self._schedule_redraw(moving=moving)
            return

        target = min(_MAX_ZOOM * self._finest(), target)
        if focus is None:
            focus = self._source_at(w / 2, h / 2)
            anchor = (w / 2, h / 2)
        # One line, and it is exact at every scale and in every direction: the
        # picture point under the pointer is the point the corner is measured
        # from.
        self._view = [focus[0] - anchor[0] / target,
                      focus[1] - anchor[1] / target]
        self._zoom = target
        self._schedule_redraw(moving=moving)

    def _picture_area(self) -> tuple[int, int]:
        """The canvas, less the corner the histogram is standing in.

        The panel floats over the canvas, so without this a picture is laid
        out underneath it and the top right of every frame sits behind a
        chart. Moved and re-fitted rather than clipped, because "do not
        overlap" for a picture that is centred means giving it a smaller
        space to be centred in.

        The whole height of that column is given up, not just the panel's own
        rows. The alternative -- reserve it while the picture is tall enough
        to reach the panel, release it when it is not -- makes the picture
        jump sideways part way through a zoom.
        """
        w = max(1, self.canvas.winfo_width())
        h = max(1, self.canvas.winfo_height())
        # Never more than half of it: a pane this narrow is better overlapped
        # than given a picture too small to judge anything by.
        return max(1, w - min(self.histogram.footprint(), w // 2)), h

    def _geometry(self, src, w: int, h: int):
        """Where the picture sits on the canvas: `(scale, x0, y0, width, height)`.

        Worked out from the zoom and offset as they are now, not from the last
        frame drawn. Several throttled zoom steps can pass between redraws, and
        pivoting on a stale frame let the anchor creep -- ten pixels over a
        dozen steps, which is small and is still the picture sliding under a
        finger that is meant to be holding it still.
        """
        tall, wide = src.shape[0], src.shape[1]
        if self._zoom <= 0:
            scale = min(w / wide, h / tall)
            view = [-(w / scale - wide) / 2, -(h / scale - tall) / 2]
        else:
            scale = self._zoom
            view = self._clamped_view(scale, w, h, wide, tall)

        # The corner of the canvas sits at `view` in the picture; negative
        # means the picture starts inside the canvas and there is a margin.
        x0, y0 = max(0.0, view[0]), max(0.0, view[1])
        left, top = max(0.0, -view[0] * scale), max(0.0, -view[1] * scale)
        out_w = max(1, min(int(w - left), int(round((wide - x0) * scale))))
        out_h = max(1, min(int(h - top), int(round((tall - y0) * scale))))
        return scale, x0, y0, out_w, out_h, left, top

    def _clamped_view(self, scale, w, h, wide, tall):
        """The view, kept far enough on the picture to still be looking at it.

        Deliberately not "the whole picture must stay visible". Insisting on
        that meant a picture only a little smaller than the canvas had to slide
        as it grew -- there was room to show all of it, so it was moved to show
        all of it, and a point held under the pointer moved with it. That band
        runs from fit to about 105%, which is exactly where the sliding was
        noticed. Zooming into the top of something is a request to let the
        bottom go.
        """
        vx, vy = self._view
        span_x, span_y = w / scale, h / scale
        vx = min(max(vx, -span_x * _OFF_CANVAS), wide - span_x * (1 - _OFF_CANVAS))
        vy = min(max(vy, -span_y * _OFF_CANVAS), tall - span_y * (1 - _OFF_CANVAS))
        return [vx, vy]

    def _source_at(self, x: float, y: float):
        """The picture coordinates under a point on the canvas, clamped.

        Unlike `_to_source` this does not refuse a point outside the drawn
        image: a pointer can sit in the margin beside a picture that does not
        fill the canvas, and a zoom there should still pivot somewhere sensible
        rather than not pivot at all.
        """
        src = self._source()
        if src is None:
            return None
        w, h = self._picture_area()
        scale, x0, y0, dw, dh, left, top = self._geometry(src, w, h)
        ix = min(max(x - left, 0.0), float(dw))
        iy = min(max(y - top, 0.0), float(dh))
        return x0 + ix / scale, y0 + iy / scale

    def _touchpad_over_picture(self, dx: int, dy: int) -> None:
        """Zoom by a trackpad swipe, smoothly and by however far it travelled.

        Continuous rather than notched. Spending one 1.15x notch per 60 px of
        travel meant five full swipes to double the size, which reads as the
        gesture doing nothing at all -- and reasonably so. A pixel of travel is
        worth a fixed proportion instead, so a short swipe is a small change
        and a long one is a large change, which is what the hand expects.
        """
        if dx and not dy and self._zoom > 0:
            self._view[0] += dx / self._zoom
            self._schedule_redraw(moving=True)
            return
        if dy:
            self._zoom_by(math.exp(dy * _ZOOM_PER_PIXEL), moving=True,
                          anchor=self._pointer)

    def _wheel_over_picture(self, amount: int, sideways: bool) -> None:
        if sideways and self._zoom > 0:
            self._view[0] += amount * 40 / self._zoom
            self._schedule_redraw(moving=True)
            return
        self._zoom_by((1 / _ZOOM_PER_NOTCH) ** amount if amount > 0
                      else _ZOOM_PER_NOTCH ** -amount,
                      anchor=self._pointer)

    def _schedule_redraw(self, moving: bool = False) -> None:
        """Draw now if it is time, and if not, make sure one is coming.

        Throttled, not debounced. It used to cancel the pending redraw and
        reschedule on every event, which sounds like the same thing and is not:
        a trackpad sends events continuously through a gesture and for most of a
        second of momentum afterwards, so the redraw was pushed back by every
        one of them and the picture did not move until the whole gesture had
        stopped. That was the half-second.

        `moving` says a hand is on it. Those frames are drawn coarse and fast,
        and a sharp one follows once the movement stops -- the only moment the
        detail is any use is the moment you are looking rather than moving.
        """
        if moving:
            if self._settle_job is not None:
                self.root.after_cancel(self._settle_job)
            self._settle_job = self._later(_SETTLE_MS, self._settle)
        since = (time.monotonic() - self._drawn_at) * 1000
        if since >= _FRAME_MS:
            self._redraw(quick=moving)
        elif self._redraw_job is None:
            self._redraw_job = self._later(
                max(1, int(_FRAME_MS - since)),
                lambda: self._redraw(quick=moving))

    def _settle(self) -> None:
        self._settle_job = None
        self._redraw()

    def _cuts(self):
        """The current picture's levels for the channel on show."""
        r = self.current
        if r is None or getattr(r, "levels", None) is None:
            return None
        try:
            return preview.channel_levels(r.levels, self.v_channel.get())
        except (IndexError, KeyError):
            return None

    def _redraw_all(self) -> None:
        self._redraw()
        self._redraw_strip()

    def _redraw(self, quick: bool = False) -> None:
        self._redraw_job = None
        self._drawn_at = time.monotonic()
        self.canvas.delete("note")
        self._shown = None
        src = self._source()
        w, h = self._picture_area()
        if src is None:
            self.canvas.delete("picture")
            self._item = None
            self.canvas.create_text(w // 2, h // 2, fill="#666", tags="note",
                                    text="nothing scanned yet")
            return

        # Sampled at whatever scale is asked for. Decimating and replicating by
        # whole numbers -- which is what this replaced -- can only show 50%,
        # 100%, 200%, so a smooth zoom reached the screen as jumps between them.
        scale, x0, y0, out_w, out_h, left, top = self._geometry(src, w, h)
        # A moving frame is drawn at a fraction of the size and enlarged by
        # Tk, which is far cheaper than sampling and rendering every pixel: both
        # of those costs, and building the image Tk shows, scale with the count.
        coarse = _GESTURE_FACTOR if quick and out_w > 2 * _GESTURE_FACTOR else 1
        # A coarse frame draws a third of the pixels, so it needs a third of
        # the detail: asking for the level the sharp frame would use meant
        # gathering from a finer array than anything on screen could show.
        pixels, detail = self._pixels(scale / coarse)
        if pixels is None:
            return
        # `detail` is how many of the sampled array's pixels make up one of the
        # picture's, so the start is that many times further in and a step
        # across the output covers that many more of them -- the scale is
        # divided by it, not multiplied. Multiplying sampled a region sixteen
        # times too small and blew it up, and because every coordinate stayed
        # consistent with every other, it looked right to everything but an eye.
        arr = preview.sample(pixels, scale / detail / coarse,
                             x0 * detail, y0 * detail,
                             max(1, out_w // coarse), max(1, out_h // coarse))
        try:
            rgb = preview.render(arr, self.v_channel.get(), self.v_invert.get(),
                                 cuts=self._cuts())
        except ValueError as exc:
            self.canvas.create_text(w // 2, h // 2, fill="#888", tags="note",
                                    text=str(exc))
            return
        photo = self._paint(rgb, coarse)
        self._place(photo, left, top)
        drawn_w, drawn_h = self._photo_size
        self._shown = (drawn_w, drawn_h, scale, x0, y0, left, top)
        self.v_zoomtext.set("fit" if self._zoom <= 0
                            else f"{scale / self._finest() * 100:.0f}%")
        if self._zoom > 0 and (rgb.shape[1] > w or rgb.shape[0] > h):
            self.canvas.configure(cursor="fleur")        # there is room to drag
        else:
            self.canvas.configure(cursor="")
        if self.v_aim.get() and self.current.kind == "prescan":
            dw, left = self._shown[0], self._shown[5]
            for x in (int(left), int(left + dw)):
                self.canvas.create_line(x, 0, x, h, fill="#e8b64c", dash=(4, 4),
                                        tags="note")
            self.canvas.create_line(w // 2, 0, w // 2, h, fill="#555",
                                    dash=(2, 6), tags="note")
            self.canvas.create_text(
                w // 2, 12, fill="#e8b64c", tags="note",
                text="click an edge of the picture -- it moves to the nearer "
                     "side of the aperture")

    def _paint(self, rgb, coarse: int) -> tk.PhotoImage:
        """The rendered pixels, in a Tk image, reusing the one from last time.

        Building a fresh `PhotoImage` every frame and handing it to a fresh
        canvas item was two thirds of the cost of a moving frame -- 6.5 ms in
        `create_image` alone, because binding an image Tk has not seen before
        makes it do the work again from scratch. Writing into an image it
        already knows, at a size it already is, costs a fraction of that.
        """
        ppm = preview.to_ppm(rgb)
        tall, wide = rgb.shape[0], rgb.shape[1]
        if coarse > 1:
            self._small, self._small_size = _sized(
                self._small, self._small_size, wide, tall)
            self._small.put(ppm)
            self._photo, self._photo_size = _sized(
                self._photo, self._photo_size, wide * coarse, tall * coarse)
            # Tk enlarges in place, into an image that already exists, rather
            # than `zoom()` which returns a new one every time.
            self.root.call(str(self._photo), "copy", str(self._small),
                           "-zoom", coarse, coarse)
        else:
            self._photo, self._photo_size = _sized(
                self._photo, self._photo_size, wide, tall)
            self._photo.put(ppm)
        return self._photo

    def _place(self, photo: tk.PhotoImage, left: float, top: float) -> None:
        """Show `photo` at `(left, top)`, keeping the one canvas item."""
        if self._item is None or photo is not self._item_photo:
            self.canvas.delete("picture")
            self._item = self.canvas.create_image(
                left, top, anchor="nw", image=photo, tags="picture")
            self._item_photo = photo
        else:
            self.canvas.coords(self._item, left, top)

    def _to_source(self, event: tk.Event):
        """Canvas coordinates to source-image pixels, or None if off-image."""
        if self._shown is None:
            return None
        dw, dh, scale, x0, y0, left, top = self._shown
        ix = event.x - left
        iy = event.y - top
        if not (0 <= ix < dw and 0 <= iy < dh):
            return None
        return x0 + ix / scale, y0 + iy / scale

    def on_hover(self, event: tk.Event) -> None:
        """The pixel under the pointer, in the readout beside the zoom.

        Off the picture clears it rather than holding the last value, which
        would be a number describing somewhere the pointer no longer is.
        """
        where = self._to_source(event)
        if where is None:
            self.v_readout.set("")
            return
        sampled = self._pixel_at(*where)
        if sampled is None:
            self.v_readout.set("")
            return
        x, y, values, exact = sampled
        self.v_readout.set(pixel_readout(x, y, values, exact))

    def _pixel_at(self, sx: float, sy: float):
        """`(x, y, values, exact)` at a source coordinate, or None.

        Read from the scan's own pixels when they are loaded for this pass, and
        from the working copy otherwise. Orienting the full array is free --
        `preview.orient` is `rot90` and `flip`, both views -- so this costs no
        copy of a 142 MB frame on every mouse move.
        """
        r = self.current
        work = self._source()
        if r is None or work is None:
            return None
        array = work
        exact = False
        if self._full is not None and self._full_seq == r.seq:
            array = preview.orient(self._full, r.rotation, r.flipped)
            exact = True
        # The working copy's coordinates are what the view is measured in, so a
        # finer array is indexed by the ratio between them rather than by the
        # zoom, which does not know about either.
        py = int(sy * array.shape[0] / max(work.shape[0], 1))
        px = int(sx * array.shape[1] / max(work.shape[1], 1))
        py = max(0, min(array.shape[0] - 1, py))
        px = max(0, min(array.shape[1] - 1, px))
        pixel = array[py, px]
        values = (pixel,) if array.ndim == 2 else tuple(pixel)
        return px, py, values, exact

    def on_press(self, event: tk.Event) -> None:
        if self.v_aim.get() and self.current is not None \
                and self.current.kind == "prescan":
            if self.current.seq != self._aim_from:
                # A distance measured on one picture of the film, applied to
                # the film where it is now: an older frame's prescan, a
                # walk's, a reopened roll's from another day -- or the one
                # just aimed from, which a second click applied twice.
                messagebox.showinfo(
                    "Aim",
                    "This prescan no longer shows where the film is: it is "
                    "another frame's, or the film has moved since it was "
                    "taken. Prescan again and aim on that.", parent=self.root)
                return
            self._aim(event)
            return
        self._drag = (event.x, event.y, list(self._view))

    def on_double_click(self, event: tk.Event) -> str:
        """Fit and 1:1, the shortcut people try before finding the buttons."""
        if self._zoom <= 0:
            self._pointer = (event.x, event.y)
            # Through the same pivot as a gesture, so the point clicked is the
            # point that stays put.
            src = self._source()
            if src is not None:
                w, h = self._picture_area()
                fit = min(w / src.shape[1], h / src.shape[0])
                want = self._finest()
                self._zoom_by(want / fit if fit else 1.0, anchor=self._pointer)
        else:
            self._set_zoom(0.0)
        return "break"

    def on_drag(self, event: tk.Event) -> None:
        if self._drag is None or self._zoom <= 0:
            return
        sx, sy, start = self._drag
        self._view = [start[0] - (event.x - sx) / self._zoom,
                      start[1] - (event.y - sy) / self._zoom]
        self._schedule_redraw(moving=True)

    def _aim(self, event: tk.Event) -> None:
        """Move the film so the clicked point becomes the centre of the frame.

        The prescan covers the whole transport window, so a fraction across the
        image is a fraction across the aperture, and the aperture is a measured
        width. What the transport can actually deliver is a different question,
        and one the dialog answers before anything moves.
        """
        where = self._to_source(event)
        if where is None:
            return
        src = self._source()
        # The film moves along the unrotated x axis however the picture is
        # turned on screen, so the click comes back through the rotation before
        # it means a distance. Without this, aiming on a frame turned 90 would
        # drive the transport from the wrong axis entirely.
        flat_x, _flat_y = preview.unorient_point(
            where[0], where[1], src.shape,
            self.current.rotation, self.current.flipped)
        width = (src.shape[1] if self.current.rotation % 180 == 0
                 else src.shape[0])
        fraction = min(1.0, max(0.0, flat_x / max(1, width)))
        want = aim_millimetres(fraction)
        side = "left" if fraction < 0.5 else "right"
        if abs(want) < FINE_STEP_MM:
            messagebox.showinfo(
                "Aim",
                f"That point is {say_units(want, signed=False)} from the {side} edge of the "
                f"aperture, and the smallest move the transport can make is "
                f"{say_units(FINE_STEP_MM, signed=False)}.\n\nIt is already as close as the "
                "hardware can put it.", parent=self.root)
            return
        if abs(want) > MAX_TRAVEL_MM:
            messagebox.showinfo(
                "Aim",
                f"That point is {say_units(want, signed=False)} from the {side} edge, which "
                f"is further than one command moves the film "
                f"({say_units(MAX_TRAVEL_MM, signed=False)}), and a fine "
                "adjustment is one command.\n\nClick nearer the edge you want "
                "it to reach, or use the slide buttons.", parent=self.root)
            return
        # The planner's own count, not a copy of its arithmetic.
        steps = len(plan_nudges(want)) or 1
        way = "forward" if want > 0 else "back"
        if self.v_reverse.get():
            way = "back" if want > 0 else "forward"
        if not messagebox.askokcancel(
            "Aim",
            f"Move the film {say_units(want, signed=False)} {way} "
            f"({say_command(want)}), so that point sits at the "
            f"{side} edge of the aperture?\n\n"
            f"{steps} sub-frame move{'s' if steps != 1 else ''}, about "
            f"{steps * 1.1:.0f} s.\n\nThe frame counter will not see this, so "
            "prescan again to check it landed. If it goes the wrong way, tick "
            "'reverse the direction' and try again.", parent=self.root,
        ):
            return
        self.on_nudge(1 if want > 0 else -1, millimetres=abs(want))


# ---------------------------------------------------------------------------


def aim_millimetres(fraction: float) -> float:
    """How far to move the film so the point at `fraction` reaches its own edge.

    Clicking is for putting an edge of the picture against an edge of the
    aperture: a point on the left half goes to the left edge, one on the right
    half to the right edge. Aiming at the centre instead was the wrong tool --
    what you can see and want to place is the border of the frame, not its
    middle, and asking for the middle made every click ask for a move far
    larger than one command could deliver.

    A prescan covers the whole transport window, so a fraction across the image
    is a fraction across the aperture, and the aperture is a measured 36.49 mm.
    Positive means the film travels in the +x direction; which way that is
    physically is the operator's to discover, and the "reverse the direction"
    tick is there for when it is not what they expected.
    """
    target = 0.0 if fraction < 0.5 else 1.0
    return -(fraction - target) * APERTURE_MM


#: Sequence numbers for reopened frames: negative, so never a live pass's --
#: the session counts those up from 1 -- and never the same twice. They were
#: ``-number``, so two reopened rolls, or one opened twice, gave two frames
#: one number: the full-resolution view could show the other roll's pixels,
#: and Delete removed both.
_REOPENED_SEQ = itertools.count(-1, -1)


def read_survey(folder, say=None, library_root=None) -> dict:
    """A walked strip, read back off disk so it need not be walked again.

    A survey costs four minutes of transport and is the thing the contact
    sheet is built from, but until now it died with the window -- `survey.json`
    has been written since rolls existed and never once read. Closing the app
    between walking a strip and deciding on it meant walking it again.

    Returns everything the sheet and a commissioned scan need: the frames as
    `Result` objects, the `start_at` the walk used, the prescan resolution it
    used, any positions and orientations already approved for it, and how a
    roll from it held the frames it scanned (`roll_holds`).

    **The prescans are un-rotated on the way in.** `prescanNN.tif` is written
    turned the way the screen had it, and a reference has to be the film's own
    orientation or it will not correlate against a fresh pass. `rotation` is
    carried on the result instead, which is exactly how a live pass behaves.

    What un-rotates each is the turn it was written with: the frame record's
    own pair (`prescan_rotation`, `prescan_flipped`, through
    `session.prescan_arrangement`), which every walk now records -- two walks
    merged into one survey need not have been made the same way up, and a turn
    made while a walk ran reaches the prescans written after it. The
    manifest's single `rotation` is the start of the walk's, and answers only
    for a walk older than those records. A per-frame turn set on the sheet is
    recorded in `approved.json` and never applied to a `prescanNN.tif`; those
    come back as `rotations`, which the sheet lays over the results
    afterwards.

    ``say`` hears anything the renumbering of an old walk, or of the roll
    beside it, could not settle; see `session.renumbered`.

    ``library_root`` is where the walk filed its prescans: each result's
    entry is the one `approved.json` names, and otherwise the one the walk
    recorded (`session.walked_prescan_entries`). Without it a walk reopened
    before anything was approved gave its references no entry, and the
    approvals made from them named none. The one `approved.json` names only
    where it is an entry of that library, by its id: it outranked the walk's
    own after it had gone to the trash, and as written it is a path relative
    to wherever the window ran. A restore before the next approval brings it
    back; an approval writes the reference the frame has then. The same
    library says which frames count as scanned (`session.scanned_frames`).

    And never over the walk's own: where the walk's record names another
    prescan than the one `approved.json` names -- the frame walked again
    since its position was set -- the walk's is the reference, as it is the
    picture the hold measures against, and the position set on the other is
    dropped with its source; its turn and mirror stay (FOUND-1). Compared
    by id: `approved.json` writes ``library\\<id>`` and the walk the bare
    id, so as strings no two of 324 agreed. An `approved.json` that names
    another walk than this folder's (`walk_stamp`) is not read at all.

    ``decided`` is every frame `approved.json` records, with when it was
    commissioned where it says (`approved_times`), and ``filed`` when each
    frame the roll scanned was last filed (`filed_times`), for
    `merge_sheet`.
    """
    folder = Path(folder)
    # Two manifests, one directory, and both matter. `survey.json` is the walk
    # -- it is the only one with `prescanNN.tif` beside it, so it is the only
    # one a contact sheet can be built from. `roll.json` is what was then
    # scanned, and on a roll that died part-way it is the record of how far it
    # got. Neither is required: a roll commissioned without a walk has no
    # survey, and a walk nobody acted on has no roll.
    survey_path = folder / "survey.json"
    roll_path = folder / "roll.json"
    if not survey_path.exists() and not roll_path.exists():
        raise ValueError("no survey.json and no roll.json")
    # A file that does not parse is read from the version kept beside it, or
    # refused by name -- never taken for an empty roll.
    manifest = read_manifest(survey_path if survey_path.exists()
                             else roll_path, say=say)
    progress = read_manifest(roll_path, say=say)
    # Onto the strip's numbering, whatever each file was written with. A walk
    # made before frame numbers were places on the strip counted from wherever
    # it started -- rolls/2026-09-23 calls the frame on the counter's 5 its
    # frame 1 -- and a roll commissioned from it now goes to frames by the
    # counter. Mapped by each frame's recorded transport position, never by
    # its number -- by its own run's shift only where the position is no
    # place on a strip, or two positions give one number and the run says
    # which, or in one walk a frame was moved onto its place
    # (`session.renumbered`) -- and the decisions filed against those
    # numbers with the walk's shift, having none of their own.
    shift = legacy_shift(manifest) or 0
    decided = approved_legacy(manifest, progress)
    manifest = renumbered(manifest, say=say)
    # The roll is said as well: a stale 72 in it, or two scans of one place,
    # is what the operator needs before scanning more into it -- and each of
    # the three session roll.json files under `rolls/` has a walk beside it,
    # checked 2026-09-23. Only when there is one, though; alone, the
    # roll.json was the manifest and has been said.
    progress = renumbered(progress, fallback=shift,
                          say=say if survey_path.exists() else None)
    # Merged, because `tools/scan_roll.py` writes these only inside `settings`
    # and this reader wanted them at the top level. See `manifest_settings`:
    # the one that matters is `prescan_resolution`, and reading it as None
    # silently costs every correction in a commissioned roll.
    settings = manifest_settings(manifest, progress)
    turn = int(manifest.get("rotation") or 0)
    mirrored = bool(manifest.get("flipped"))

    # What approved.json says, unless it was decided on another walk than
    # the one here: both name their walk (`walk_stamp`), and positions and
    # turns set on one strip's prescans name nothing on another's. Read as
    # files without ids always were. `read_approved` itself is left as it
    # is: the tools and Export read files handed over by name.
    walk_id = manifest.get("walk_id") if survey_path.exists() else None
    theirs = approved_walk(folder)
    if (isinstance(walk_id, str) and walk_id and isinstance(theirs, str)
            and theirs and theirs != walk_id):
        if say is not None:
            say(f"approved.json in {folder.name} was decided on another walk; "
                "its positions and turns are not used here, and the file is "
                "left as it is")
        offsets, rotations, flips, entries, sources = {}, {}, {}, {}, {}
        when_decided: dict[int, str | None] = {}
    else:
        offsets, rotations, flips, entries, sources = read_approved(
            folder, legacy=decided, say=say)
        when_decided = approved_times(folder, legacy=decided)

    results = []
    # A strip of a roll of strips: its place in the roll, for the numbers
    # its frames are shown by -- from its own records' copy of its claim
    # where the roll's index cannot be read, for showing only.
    strip = place_of(folder, strict=False)
    # The walk's own records, as the roll tool's `--approved` reads them, so
    # the two cannot key one folder two ways. A stray prescan an earlier walk
    # left in the folder is not in them.
    walked = walked_prescans(folder, manifest, say=say)
    # A frame walked again after its position was set (FOUND-1): its record
    # names the prescan it was walked as now, and approved.json the one the
    # position was set on. By id on both sides -- approved.json writes
    # "library\\<id>", the walk the bare id -- and where they differ the
    # walk's is the reference the hold uses, so the position measured on
    # the other one is dropped. Its turn and mirror are about the picture,
    # and stay. Outranked by approved.json as it was, the frame reopened
    # showing the new prescan and held to the old one's position.
    stale = []
    for number, _path, record in walked:
        own, named = record.get("prescan_entry"), entries.get(number)
        if (own and named and PureWindowsPath(str(own)).name
                != PureWindowsPath(str(named)).name):
            stale.append(number)
            entries.pop(number, None)
            offsets.pop(number, None)
            sources.pop(number, None)
    if stale and say is not None:
        say(f"{'frame' if len(stale) == 1 else 'frames'} "
            f"{number_spans(stale)} of {folder.name} "
            f"{'was' if len(stale) == 1 else 'were'} walked again after "
            f"{'its' if len(stale) == 1 else 'their'} position was set: the "
            "position is dropped, the turn is kept")
    if library_root is not None:
        approved_ones = {}
        for number, named in entries.items():
            found = Path(library_root) / PureWindowsPath(str(named)).name
            if (found / "scan.json").is_file():
                approved_ones[number] = found
        entries = {**walked_prescan_entries(
            folder, [(n, r) for n, _p, r in walked], library_root,
            walk=manifest, roll=progress),
            **approved_ones}
    stood_in: dict[int, str] = {}
    for number, path, record in walked:
        # Each file on its own: one that cannot be read, or is no longer
        # the file its walk wrote, is made again from the walk's own entry
        # for it or left off, and said -- it used to refuse the whole walk
        # (`session.read_walk_prescan`). In the film's own orientation. A
        # picture made from an entry has that entry for its reference, so
        # an approval made from it names the picture it was made on.
        image = read_walk_prescan(manifest, number, path, record,
                                  library_root=library_root, say=say,
                                  stood_in=stood_in)
        if image is None:
            continue
        if number in stood_in and library_root is not None:
            entries[number] = Path(library_root) / stood_in[number]
        # Each file as it was written. A walk that added to another may have
        # been made after "rotate all", or turned in the window while it ran,
        # so its frames need not share the manifest's pair; a record from
        # before that was recorded has only it.
        own, own_flip = prescan_arrangement(manifest, record)
        # A strip's frame by its number across the roll, as the walk filed
        # it (``roll_number``), its place on the strip beside it.
        given = record.get("roll_number")
        rolled = (shown_number(strip, number,
                               given if isinstance(given, int) else 0)
                  if strip is not None else None)
        result = Result(
            seq=next(_REOPENED_SEQ),         # see `_REOPENED_SEQ`
            kind="prescan",
            label=(f"{frame_said(rolled or number, strip.strip, number)}"
                   if strip is not None and rolled
                   else f"frame {number}") + " (reopened)",
            image=image,
            meta={"resolution_dpi": settings.get("prescan_resolution")},
            entry=Path(entries[number]) if number in entries else None,
            registration=record.get("registration") or {},
            position=record.get("transport_position"),
            number=number,
            roll_number=rolled or 0,
        )
        result.hidden = False
        result.supersedes = None
        # A walk's prescan: never marked as a frame a roll left off its
        # position, whatever hold its record carries (`hold_of`).
        result.walked = True
        result.rotation = own
        result.flipped = own_flip
        result.levels = (preview.levels(result.image)
                         if result.image is not None else None)
        results.append(result)

    # What is left of it, as every reader of a roll reads it: the frames
    # asked for and not done, and the rest of the strip no run reached.
    left = roll_left(manifest, progress, library_root,
                     ends_at=strip.ends_at if strip is not None else None)
    return {
        "results": results,
        "start_at": int(settings.get("start_at") or 1),
        "prescan_resolution": settings.get("prescan_resolution"),
        "film": settings.get("film"),
        "offsets": offsets,
        "rotations": rotations,
        "flips": flips,
        "sources": sources,
        "roll": manifest.get("roll") or folder.name,
        "rotation": turn,
        "flipped": mirrored,
        # What the roll got through, and what it was told to do. Empty for a
        # walk nobody acted on, which is what makes "reopen" and "continue"
        # one flow rather than two. Less a frame whose entry went to the
        # trash, which finishing the roll offers again.
        "scanned": scanned_frames(progress, library_root),
        # And how it held each frame it scanned: the sheet marks the ones it
        # left off their positions.
        "holds": roll_holds(progress),
        "settings": restored_settings(manifest, progress, settings),
        "wanted": left.wanted,
        # Where the rest of the strip starts that no run reached -- a run to
        # the end of the strip stopped part way -- or None (`roll_left`).
        "rest_from": left.rest_from,
        # Every frame approved.json records, placed or not, with when it was
        # commissioned where the file says: what a reopened sheet weighs
        # against the decisions made on it since (`merge_sheet`).
        "decided": when_decided,
        # And when each frame the roll scanned was last filed, which says
        # whether a tick on a done frame was made since (`merge_sheet`).
        "filed": filed_times(progress),
        # Which walk this is (`walk_stamp`), where it says.
        "walk_id": walk_id if isinstance(walk_id, str) else None,
        # Its place in its roll of strips, for showing its numbers; None
        # for a roll from before strips.
        "strip": strip,
    }


def only_copies_in(folder, numbers) -> list[int]:
    """Of ``numbers``, the frames whose walk record in ``folder`` names a
    prescan no library entry holds -- which a walk added to keeps beside its
    new one (`session.RollManifest.only_copies`); none where the walk
    cannot be read."""
    try:
        walk = read_manifest(Path(folder) / "survey.json")
    except (OSError, ValueError, TypeError):
        return []
    wanted = {int(n) for n in numbers}
    out = set()
    for record in walk.get("frames") or ():
        try:
            number = int(record["number"])
        except (KeyError, TypeError, ValueError):
            continue
        if number in wanted and any(
                record.get(key) and not record.get(f"{key}_entry")
                for key in ("prescan", "prescan_before")):
            out.add(number)
    return sorted(out)


def approved_walk(folder) -> str | None:
    """The walk a roll folder's approved.json was decided on, where it says
    (`ScannerGui._write_approved`); None for one from before it said, none
    at all, or one that cannot be read -- `read_approved` says that."""
    try:
        said = read_manifest(Path(folder) / "approved.json").get("walk_id")
    except (OSError, ValueError):
        return None
    return said if isinstance(said, str) and said else None


def approved_times(folder, legacy: int = 0) -> dict[int, str | None]:
    """Every frame a roll folder's approved.json records, placed or not, by
    its number on the strip -- moved by ``legacy`` as `read_approved` moves
    it -- with when it was commissioned (``decided_utc``), or None for one
    from before that was written. Empty where there is no file or it cannot
    be read: `read_approved` has said so."""
    try:
        stored = read_manifest(Path(folder) / "approved.json")
    except (OSError, ValueError):
        return {}
    shift = 0 if stored.get("numbering") == NUMBERING else legacy
    out: dict[int, str | None] = {}
    for record in stored.get("frames") or ():
        try:
            number = int(record["number"]) + shift
        except (KeyError, TypeError, ValueError):
            continue
        when = record.get("decided_utc")
        out[number] = when if isinstance(when, str) and when else None
    return out


def filed_times(progress: dict) -> dict[int, str]:
    """When each frame of a roll's manifest (``progress``, roll.json as
    `read_survey` reads it) last had a take filed: the latest ``filed_utc``
    its ``takes`` record (`RollManifest.record`). A frame from before takes
    were timed has none here, and is weighed as one filed after anything
    (`merge_sheet`)."""
    out: dict[int, str] = {}
    for record in progress.get("frames") or ():
        try:
            number = int(record["number"])
        except (KeyError, TypeError, ValueError):
            continue
        times = [t.get("filed_utc") for t in record.get("takes") or ()
                 if isinstance(t, dict)]
        times = [t for t in times if isinstance(t, str) and t]
        if times:
            out[number] = max(times)
    return out


def merge_sheet(approved: dict, stored: dict | None, done=()
                ) -> tuple[dict, int, int]:
    """What a reopened sheet comes back holding, frame by frame: the
    commission's word from approved.json (``approved``, `read_survey`'s
    answer) or the decisions made on the sheet since (``stored``, the raw
    state the window filed, `ScannerGui._stored_sheet`). Returns the state,
    and how many frames came from each.

    A frame approved.json records is its, unless the stored state was saved
    later than that frame was commissioned; every frame it does not record
    is the stored state's. All of a frame's fields -- position, who set it,
    turn, mirror -- come from the one side, so a turn is never paired with
    another decision's position. Ticks are the stored state's, and so are
    the sheet's options.

    Less the frames already scanned (``done``), whose stored tick is the
    commission's, left behind -- unless the state was saved after that
    frame's latest take was filed (``approved["filed"]``, `filed_times`).
    The window takes the commission's tick off a frame as it is filed
    (`ScannerGui._mark_filed`), so a done frame ticked in a state saved
    later was ticked again on purpose, to be scanned again; dropped with
    the rest, it came back unticked after a restart, though it stayed
    ticked for as long as the window did. Saved in the same second, or
    either time unknown, the tick is the commission's and goes.

    Once a roll was commissioned, the stored state was never read again
    (P22-a): turns, ticks and positions decided after a partial commission,
    on frames it had not named, came back as nothing after a restart.
    Files from before either time was written give approved.json its frames,
    as before, and only add the frames it never named.
    """
    mine = ScannerGui._clean_sheet_state(stored)
    saved = (stored or {}).get("saved_utc")
    saved = saved if isinstance(saved, str) and saved else None
    decided = approved.get("decided") or {}
    state: dict[str, dict] = {"ticks": {}, "offsets": {}, "rotations": {},
                              "flips": {}, "sources": {},
                              "options": dict(mine["options"])}
    from_approved = since = 0
    numbers = set(decided)
    for name in ("offsets", "rotations", "flips", "sources"):
        numbers |= set(mine[name])
    for number in sorted(numbers):
        when = decided.get(number)
        theirs = (number in decided
                  and (saved is None or when is None or saved <= when))
        side = ({"offsets": approved.get("offsets") or {},
                 "rotations": approved.get("rotations") or {},
                 "flips": approved.get("flips") or {},
                 "sources": approved.get("sources") or {}}
                if theirs else mine)
        for name in ("offsets", "rotations", "flips", "sources"):
            if number in side[name]:
                state[name][number] = side[name][number]
        if theirs:
            from_approved += 1
        else:
            since += 1
    done = {int(n) for n in done}
    filed = approved.get("filed") or {}
    state["ticks"] = {
        n: tick for n, tick in mine["ticks"].items()
        if n not in done or (saved is not None and filed.get(n) is not None
                             and saved > filed[n])}
    return state, from_approved, since


def json_ready(value):
    """``value`` as plain JSON types: a numpy number as its Python one.

    A detector's note is built from numpy arithmetic, and `json.dumps` takes
    a numpy float but refuses a numpy int -- which, inside `_write_approved`,
    would cost the whole file rather than the one field.
    """
    return json.loads(json.dumps(value, default=lambda o: (
        o.item() if hasattr(o, "item") else str(o))))


def _mono_choice(value) -> str:
    """A channel the chooser offers, or ValueError, which `restorable` drops.

    A manifest is a file anyone can edit. Put back as it stood, "I" or a typo
    sat in a read-only chooser that cannot show it, and every one-channel
    copy of the next roll was refused by `to_monochrome`.
    """
    if value not in MONO_CHOICES:
        raise ValueError(f"{value!r} is not one of {list(MONO_CHOICES)}")
    return str(value)


#: How a roll manifest's `settings` block maps onto the window's controls.
#: Only the ones a resume should put back: `mono` is missing deliberately,
#: because `_sync_film` derives it from the film type and restoring it would be
#: overwritten a moment later by something that looks like it disagreed.
#: `reverse_hold`, which older manifests carry, is missing too: it was the
#: hand-move tick handed to the hold loop, and putting it back from a roll
#: would set a control that no longer reaches a roll at all.
RESTORABLE = {
    "resolution": ("dpi", str),
    "prescan_resolution": ("predpi", str),
    "infrared": ("ir", bool),
    "fast_infrared": ("fast_ir", bool),
    "film": ("film", str),
    "meter": ("meter", str),
    "mono_channel": ("mono_channel", _mono_choice),
    "correct": ("correct", bool),
    "start_at": ("startat", str),
}


def batch_name(result, fmt: str) -> str:
    """What one pass is called when a whole session is saved at once.

    Shaped like `ScanSession._out_name`, and for its reason: NegPy reads these
    next, so what the file *is* leads and nothing sorts by when it was scanned.
    The roll name is not available here -- a `Result` does not carry one -- so
    the kind and the frame number stand in for it.

    `_ir` names a pass that *was* infrared even where the format cannot carry
    the plane, which is the same choice `_out_name` makes: it says what was
    scanned, and the JPEG's own note says what arrived. So does the film's
    process, ``c41``, ``e6``, ``bw`` or ``k14``, where the pass says its film.
    """
    meta = result.meta or {}
    dpi = meta.get("resolution_dpi") or 0
    process = film_process(meta.get("film"))
    film = f"_{process}" if process else ""
    channels = meta.get("channels") or len(meta.get("channel_order") or "")
    ir = "_ir" if channels and int(channels) >= 4 else ""
    # A pass whose full-resolution pixels are not filed yet is written from
    # the reduced copy on screen, and under the scan's full dpi it later
    # passed for the real delivery. The name says what it is.
    entry = getattr(result, "entry", ...)
    if entry is not ... and not (entry and (Path(entry) / "scan.tif").exists()):
        ir += "_preview"
    end = export.suffix_for(fmt)
    kind = _safe(result.kind or "scan")
    # By its number across its roll of strips where it has one -- what its
    # label and the session's delivered name carry -- and its place
    # otherwise: frame08, not strip 2's frame02 beside strip 1's.
    number = getattr(result, "roll_number", 0) or result.number
    if number:
        return f"{kind}{int(number):02d}{film}_{dpi}dpi{ir}{end}"
    return f"{kind}_{abs(int(result.seq)):03d}{film}_{dpi}dpi{ir}{end}"


#: What befell each part of a pass, as its caption and progress line say it:
#: by `Filing.part` and outcome. In this order, the pass itself first.
_FILING_WORDS = {
    "": {NOT_FILED: "not filed in the library",
         COPY_FAILED: "a delivered copy was not written"},
    "infrared pass": {
        NOT_FILED: "its infrared pass was not filed in the library",
        COPY_FAILED: "a copy of its infrared pass was not written"},
    # A slide's picture is made of its two passes and filed in neither, so
    # "not filed" says nothing of it: its copy is what failed, and red says
    # that its infrared pass is not in the library to make it again from.
    "picture": {NOT_FILED: "its delivered picture was not written",
                COPY_FAILED: "its delivered picture was not written"},
    "prescan before its aim": {
        NOT_FILED: "its prescan from before the aim was not filed in the "
                   "library",
        COPY_FAILED: "the copy of its prescan from before the aim was not "
                     "written"},
}


def filing_words(marks: dict[str, str]) -> str:
    """One pass's marks in words: ``part -> outcome``, worst kept."""
    said = []
    for part in [*_FILING_WORDS, *sorted(set(marks) - set(_FILING_WORDS))]:
        outcome = marks.get(part)
        if outcome is None:
            continue
        words = _FILING_WORDS.get(part)
        said.append(words[outcome] if words and outcome in words
                    else f"its {part}: {outcome}")
    return "; ".join(said)


def unclaimed_delivery(wanted: Path) -> Path:
    """`wanted`, or the next free name beside it, counting what it brings.

    Save all and Export promise that nothing already there is overwritten,
    and `_unclaimed` checked the one name asked for. A JPEG can leave more:
    `<stem>.dng` beside it for an infrared pass, or `<stem>.tif` in its place
    where Pillow is missing (`export.write`). Either was written over without
    a word -- a second Save all as JPEG into a folder of TIFFs replaced them
    all. Raises past the last name tried, which costs that one file and says
    so.

    `export.unclaimed`, which reads `export.outputs` rather than retyping its
    suffix rule here, and passes over a name a write in flight has claimed.
    What writes takes its name with `export.claim`; this only offers one.
    """
    return export.unclaimed(wanted)


def _identity(path: Path) -> tuple[int, int, int] | None:
    """Which file is at ``path`` now, or None for none: enough to tell it
    from one renamed over it since (`export._whole`)."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_ino, stat.st_mtime_ns, stat.st_size


def pixel_readout(x: int, y: int, values, exact: bool = True) -> str:
    """One pixel, in the scan's own numbers.

    The histogram answers "is anything at the rail" over the whole frame. This
    answers "what is *this*", which is the question once something specific
    looks suspect -- a highlight that may or may not be blown, two patches that
    may or may not differ. Neither answers the other.

    `exact` is False when the numbers come from the reduced working copy rather
    than the scan itself, and then it says so: a working copy's pixels are
    resampled averages, and quietly presenting them as the scan's values would
    make this worse than nothing on the one question it exists for.
    """
    # Named by how many there are rather than from `_CHANNEL_NAMES`, which is
    # "RGB" and would silently drop the infrared plane -- the one channel this
    # window exists to show. A single plane is not called "R": a monochrome
    # delivery is green by measurement, and naming it red would be a lie about
    # which one survived.
    names = {1: ("value",), 3: ("R", "G", "B"), 4: ("R", "G", "B", "I")}.get(
        len(values), tuple(str(i) for i in range(len(values))))
    named = "  ".join(f"{name} {int(value)}" for name, value
                      in zip(names, values))
    return f"{int(x)},{int(y)}   {named}" + ("" if exact else "   (approx)")


def restored_settings(manifest: dict, progress: dict, layered: dict) -> dict:
    """The settings a reopened roll puts back (`restorable`): its latest
    run's, or its walk's where it has run nothing yet.

    "Aim each frame" excepted where the latest run has nothing to say of it:
    a commission from the contact sheet, or `tools/scan_roll.py --approved`,
    holds every frame, so none could be aimed, and it records the tick as
    not applicable (`session.aiming_of`, GUI2-10). There the walk's answer
    stands -- ``layered``, the files read as one (`manifest_settings`).
    Recorded as False, it unticked "aim each frame" for every walk reopened
    after a commission.
    """
    out = dict(progress.get("settings") or manifest.get("settings") or {})
    if out.get("correct") is None and layered.get("correct") is not None:
        out["correct"] = layered["correct"]
    return out


def restorable(settings: dict) -> dict:
    """The window controls a roll's stored settings should put back.

    Keyed by control name, already the type that control's variable wants.

    **A key that is absent is left alone rather than defaulted.** A manifest
    written before a setting existed -- and `fast_infrared` is younger than the
    rolls already on disk -- must not silently assert that setting's default
    over whatever the window holds. Absent means "this roll has nothing to say
    about it", which is not the same as "off".
    """
    out: dict = {}
    for key, (control, kind) in RESTORABLE.items():
        value = settings.get(key)
        if value is None:
            # An alias, not a default: `tools/scan_roll.py` calls the scan
            # resolution `dpi`. Absent under both names still means "this roll
            # has nothing to say about it", which the guard above preserves.
            value = settings.get(SETTING_ALIASES.get(key))
        if value is None:
            continue
        try:
            out[control] = kind(value)
        except (TypeError, ValueError):
            continue
    # The last-frame box from the count a roll recorded, since the panel asks
    # for a range and a `Roll` carries a start and a count. Recorded as null
    # is a roll that ran to the end of the strip, which an empty box says;
    # not recorded at all is left alone, as above.
    if "frames" in settings:
        try:
            first = int(settings.get("start_at") or 1)
            count = settings["frames"]
            out["last"] = "" if count is None else str(first + int(count) - 1)
        except (TypeError, ValueError):
            pass
    return out


def roll_exports(summary: dict) -> list:
    """One item per frame of this roll that can be exported, in frame order.

    An item carries what `_deliver_one` and `batch_name` need and nothing else:
    the library entry to re-correct from, the arrangement that roll decided on,
    and enough meta to name the file. Built here rather than in the window so
    the join -- frame to entry to orientation -- is testable without Tk.

    Frames with no library entry are left out. Export re-corrects from the
    entries, so a frame without one cannot be exported at all, and a caller
    that wants to say so asks `export_skipped`.

    Each item's ``number`` is what its file is named by -- its number across
    its roll of strips, frame01 to frame36 with no two alike -- and
    ``place`` its place in ``folder``, its strip's. A roll of strips is its
    strips', in strip order.
    """
    from types import SimpleNamespace

    if summary.get("kind") == "roll":
        return [item for strip in summary.get("strips") or ()
                if strip.get("state") == "here"
                for item in roll_exports(strip)]
    folder = summary["folder"]
    numbers = summary.get("numbers") if summary.get("kind") == "strip" \
        else None
    _, rotations, flips, _, _ = read_approved(folder)
    settings = summary.get("settings") or {}
    turn = int(settings.get("rotation") or 0)
    mirrored = bool(settings.get("flipped"))
    arranged = summary.get("arranged") or {}
    # The roll's last run, for an entry whose own record cannot be read. The
    # command-line tool calls the resolution `dpi`, and read as `resolution`
    # alone its rolls were exported as "0dpi".
    # One channel or all, as the roll's own frames were written; None where
    # the roll does not say its film, and the window's controls decide.
    film = summary.get("film") or settings.get("film")
    fallback = {"resolution_dpi": summary.get("resolution")
                or settings.get("resolution")
                or settings.get(SETTING_ALIASES["resolution"]) or 0,
                "channels": 4 if settings.get("infrared") else 3,
                "film": film}
    mono = wants_mono(settings.get("mono"), film) if film else None
    out = []
    for number in sorted(summary.get("entries") or {}):
        entry = Path(summary["entries"][number])
        # What its frameNN.tif was written with, where the roll recorded it:
        # a turn made in the window while the roll ran reached the frames
        # written after it, and the roll's one `rotation` is the one it began
        # with. Otherwise the frame's own decision where it made one, the
        # roll's where not -- the precedence `_orientation_for` uses on the
        # way out.
        own = arranged.get(number)
        rolled = given_number(summary, number) or number
        out.append(SimpleNamespace(
            entry=entry,
            rotation=own[0] if own else rotations.get(number, turn),
            flipped=own[1] if own else flips.get(number, mirrored),
            image=None,
            kind="frame",
            number=rolled,
            roll_number=rolled if numbers is not None else 0,
            place=number,
            folder=Path(folder),
            seq=-rolled,
            meta=_scanned_as(entry, fallback),
            mono=mono,
            mono_channel=settings.get("mono_channel") or MONO_CHANNEL,
        ))
    return out


def export_skipped(summary: dict, items) -> list[int]:
    """The frames of a roll that were scanned and are left out of its
    Export -- no library entry left -- by the numbers their files would
    have had: across the roll, for a roll of strips or a strip of one."""
    if summary.get("kind") == "roll":
        return sorted(n for strip in summary.get("strips") or ()
                      if strip.get("state") == "here"
                      for n in export_skipped(strip, items))
    folder = _folder_key(summary["folder"])
    exported = {getattr(item, "place", item.number) for item in items
                if _folder_key(getattr(item, "folder", summary["folder"]))
                == folder}
    places = sorted(set(summary.get("done") or ()) - exported)
    return sorted((given_number(summary, p) or p) for p in places)


def _scanned_as(entry, fallback: dict) -> dict:
    """The resolution and channel count an entry's own record gives.

    What an exported file is named by. The roll's `settings` describe its
    last run only -- a roll resumed at 3600 dpi RGBI after twelve frames at
    1800 dpi RGB says 3600 and infrared for all of them -- and the entry is
    the pass the file is re-corrected from.
    """
    try:
        scan = json.loads((Path(entry) / "scan.json").read_text(
            encoding="utf-8")).get("scan") or {}
    except (OSError, ValueError, AttributeError):
        return dict(fallback)
    return {"resolution_dpi": scan.get("resolution_dpi")
            or fallback["resolution_dpi"],
            "channels": scan.get("channels") or fallback["channels"],
            "film": scan.get("film") or fallback.get("film")}


def duplicate_name(folder) -> Path:
    """The next free `<name>-2`, `-3`, ... beside `folder`.

    Same idea as `session._unclaimed` for files: a duplicate must never land on
    something that is already there, and the roll it would land on is somebody's
    scan.
    """
    folder = Path(folder)
    for suffix in range(2, 1000):
        candidate = folder.with_name(f"{folder.name}-{suffix}")
        if not candidate.exists():
            return candidate
    raise ValueError(f"no free name beside {folder.name}")


#: A roll's own records, which Duplicate copies last: a copy holding them is
#: a roll (`roll_summary`), so they go in only once everything they name has.
ROLL_RECORDS = ("approved.json", "roll.json", "survey.json")


class CopyStopped(Exception):
    """A Duplicate stopped by its Stop button, or by Quit."""


class CopyFailed(Exception):
    """A file Duplicate could not copy. Not an OSError, so `copytree` stops
    at it: it collects those and goes on to the next file, and a full disk
    then failed every file of the roll in turn before it said so."""


def _record_dirs(source: Path) -> list[Path]:
    """The folders of a roll that hold its records: the roll itself from
    before strips, and each strip of a roll of strips (`library.strips_in`).
    Each is copied as a roll folder always was, its records last and its
    `unfiled/` left out."""
    if library.roll_kind(source) == "strips":
        return library.strips_in(source)
    return [source]


def _records_of(source: Path) -> list[str]:
    """The records Duplicate writes last into its copy of ``source``, as
    paths inside it and in the order they go in: each folder's own, the
    walk last (`ROLL_RECORDS`), strip by strip after every file -- and a
    roll of strips' index last of all, which is what makes the copy a roll
    of strips (`library.roll_kind`). The marker lists these
    (`reserve_copy`), and a copy holding them all finished
    (`library._copy_finished`)."""
    out: list[str] = []
    for folder in _record_dirs(source):
        inside = "" if folder == source else f"{folder.name}/"
        out += [inside + name for name in ROLL_RECORDS
                if (folder / name).is_file()]
    if (source / library.STRIPS_INDEX).is_file():
        out.append(library.STRIPS_INDEX)
    return out


def _left_out_of_a_copy(source: Path, folder: str, names: list[str],
                        dirs: list[Path] | None = None) -> set[str]:
    """What `copy_roll` copies through `copytree` without: the records,
    which it writes last; the passes kept in `unfiled/`; a marker; and
    whatever a write beside left (``.<name>...part``), which is no file of
    the roll's. ``dirs`` are the roll's folders of records (`_record_dirs`),
    each a strip's in a roll of strips -- asked of the roll once where the
    caller has them."""
    left = {n for n in names if n.startswith(".") and ".part" in n}
    here = Path(folder)
    if here == source:
        left |= {n for n in names
                 if n in (library.INCOMPLETE, library.STRIPS_INDEX)}
    if any(here == d for d in (_record_dirs(source) if dirs is None
                               else dirs)):
        left |= {n for n in names if n in ROLL_RECORDS or n == UNFILED}
    return left


def _files_to_copy(source: Path) -> list[Path]:
    """Every file `copy_roll` copies through `copytree`, as it will meet them."""
    found: list[Path] = []
    dirs_of = _record_dirs(source)
    for here, dirs, names in os.walk(source):
        left = _left_out_of_a_copy(source, here, [*dirs, *names], dirs_of)
        dirs[:] = [d for d in dirs if d not in left]
        found += [Path(here) / n for n in names if n not in left]
    return found


def roll_copy_bytes(source) -> int:
    """What Duplicate writes for this roll: what `copy_roll` copies. The
    browser's size counts the folder's own files only, and a roll's
    `unfiled/` is never copied."""
    source = Path(source)
    total = 0
    for path in [*_files_to_copy(source),
                 *(source / name for name in _records_of(source))]:
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def unfiled_kept(folder) -> list[Path]:
    """The passes kept in this roll's `unfiled/` -- refused by the library,
    filed beside the roll's copies instead (`session.keep_unfiled`) -- and
    for a roll of strips, in each strip's."""
    found: list[Path] = []
    for here in _record_dirs(Path(folder)):
        try:
            found += [p for p in (here / UNFILED).iterdir() if p.is_dir()]
        except OSError:
            continue
    return sorted(found)


def reserve_copy(source, target, who: str = "the window") -> None:
    """Make Duplicate's target, and mark it `INCOMPLETE`, before anything
    is copied into it.

    Made by `mkdir`, which fails where anything is there: the name is
    Duplicate's from that moment, and a second one picks the next. The
    marker says what was being copied, and from where, for whoever finds
    it: a copy a kill or a power cut cut short keeps it, lists as no roll
    (`roll_summary`), and `tools/library.py verify --rolls` names it.
    Raises OSError, with nothing left behind, where either cannot be made.

    It lists the records the copy will end with, which `copy_roll` writes
    last: a target holding them all finished, and only its marker was left
    (`library._copy_finished`) -- which `verify` would otherwise have said
    to delete with the copy, after the window had called it whole.
    """
    target, source = Path(target), Path(source)
    # Paths inside the copy: a roll of strips' are its strips' and its index.
    records = _records_of(source)
    target.mkdir()
    try:
        library._write_atomic(target / library.INCOMPLETE, json.dumps({
            "incomplete": "this roll folder was being copied by Duplicate "
                          "and did not finish",
            "from": str(source.resolve()),
            "records": records,
            "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "what": who, "pid": os.getpid()}, indent=2) + "\n")
    except BaseException:
        remove_copy(target)
        raise


def remove_copy(target) -> bool:
    """Take away a Duplicate's target that did not finish, its marker last.

    What makes the folder a roll goes first -- its records, the last
    written first -- then everything else, each as patient with a holder
    as `library._unmark` is; the marker and the folder only once nothing
    else is left. `rmtree(ignore_errors=True)` went on past a file it could
    not delete, and NTFS lists names case-insensitively, so `INCOMPLETE`
    went before `prescan*`, `roll.json` and `survey.json`: a record a
    reader held outlived the marker, and the half copy listed as a roll
    with frames gone. Kept marked, it lists as none (`roll_summary`), and
    `verify` names it as a Duplicate that did not finish.

    Not renamed aside first: Windows refuses to rename a folder while any
    file inside is held (`copy_roll`), which is the case this is for.
    True where the folder has gone.
    """
    target = Path(target)

    def patiently(remove, path: Path) -> None:
        for wait in (*library.REPLACE_RETRY_S, None):
            try:
                remove(path)
                return
            except FileNotFoundError:
                return
            except PermissionError:
                if wait is None:
                    return
                time.sleep(wait)
            except OSError:
                return

    # A roll of strips' index first, which makes the copy one; then each
    # strip's records; then a roll's own from before strips.
    patiently(os.unlink, target / library.STRIPS_INDEX)
    try:
        strips = [p for p in target.iterdir()
                  if library.strip_number(p.name) is not None and p.is_dir()]
    except OSError:
        strips = []
    for folder in [*strips, target]:
        for name in reversed(ROLL_RECORDS):
            patiently(os.unlink, folder / name)
    try:
        children = [p for p in target.iterdir()
                    if p.name != library.INCOMPLETE]
    except OSError:
        children = []
    for child in children:
        # Never raises: it runs where a copy has failed, and on its thread
        # an error here would leave the dialog up with nothing to end it.
        try:
            folder = child.is_dir() and not child.is_symlink()
        except OSError:
            folder = False
        patiently(shutil.rmtree if folder else os.unlink, child)
    try:
        if any(p.name != library.INCOMPLETE for p in target.iterdir()):
            return False
        library._unmark(target)
        target.rmdir()
    except OSError:
        return not os.path.lexists(target)
    return True


def copy_roll(source, target, stop: threading.Event | None = None,
              progress=None) -> OSError | None:
    """Copy a roll folder into ``target``, which `reserve_copy` made.

    Every file but the roll's records, the passes kept in `unfiled/` and
    any write beside a kill left, each synced as it is copied (`copytree`,
    by `copy2`, times kept). Then the records -- `approved.json`,
    `roll.json`, `survey.json` last -- each beside and renamed in whole,
    and then the marker goes. Raises `CopyStopped` once ``stop`` is set,
    between files, `CopyFailed` at the first file that will not copy, and
    whatever writing a record raises; the caller removes the target.
    ``progress(done, total)`` is told bytes copied as it goes.

    Returns None, or why the marker would not go -- held by a reader past
    `library._unmark`'s waits. The copy is whole then, and only listed as
    a roll once the marker is deleted: kept, rather than removed with
    everything a long copy wrote.

    A copy, not a staging folder renamed into place: on Windows a folder
    cannot be renamed while any process holds a file inside it open
    (measured here, WinError 5), and a viewer showing one frame is enough.
    """
    source, target = Path(source), Path(target)
    total = roll_copy_bytes(source)
    done = 0

    def tell() -> None:
        if progress is not None:
            progress(done, total)

    def stopped() -> None:
        if stop is not None and stop.is_set():
            raise CopyStopped("stopped")

    def checked_copy(src, dst):
        nonlocal done
        stopped()
        try:
            shutil.copy2(src, dst)
        except OSError as exc:
            raise CopyFailed(f"{Path(src).name}: {exc}") from exc
        library._sync(Path(dst))
        try:
            done += Path(src).stat().st_size
        except OSError:
            pass
        tell()
        return dst

    tell()
    dirs = _record_dirs(source)
    shutil.copytree(source, target, dirs_exist_ok=True,
                    ignore=lambda here, names: _left_out_of_a_copy(
                        source, here, names, dirs),
                    copy_function=checked_copy)
    # Each folder's records after every file, a roll of strips' index last
    # of all (`_records_of`): a copy is a roll only once they are in.
    for name in _records_of(source):
        stopped()
        record = target / name
        storage.make_within(target, record.parent)
        temp = record.with_name(f".{record.name}.part")
        try:
            shutil.copy2(source / name, temp)
            library._sync(temp)
            library._replace(temp, record)
        finally:
            library._discard(temp)
        done += record.stat().st_size
        tell()
    try:
        library._unmark(target)
    except OSError as exc:
        return exc
    return None


def roll_entry_index(library_root) -> dict:
    """Every library entry a roll could name, gathered in one pass.

    One glob for the whole library rather than one per roll: there are two
    hundred entries and a dozen rolls, and asking the question per roll turns a
    listing into a quadratic one. `roll_entries` makes the join from this.

    Three ways in, from surest to weakest:

    - ``"ids"``: every entry by its directory name, for the entry a roll's own
      `roll.json` names for each frame it filed (`session.RollManifest`).
    - ``"folders"``: scanned frames by the roll folder their
      `roll_membership` records, then by frame number.
    - ``"names"``: frames that record no folder -- filed before entries said
      where they belonged, found by `film.frame`, which `ScanSession._file`
      sets to ``"{roll}-{NN}"`` -- by roll name, then number.

    Within the last two, the newest entry of a number wins (entry ids start
    with their UTC time, and the glob is sorted).

    The roll *name* is not an identity. A duplicate keeps its original's name,
    and so do a renamed folder and a name typed again after a Delete, so a
    join on the name alone exported one roll's rescans as another's frames.

    `library.entries()` cannot be used here: it returns the records and throws
    away the folder each came from, which is the only part this needs.
    """
    out: dict = {"ids": {}, "folders": {}, "names": {}}
    root = Path(library_root)
    if not root.is_dir():
        return out
    for record_path in sorted(root.glob("*/scan.json")):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        entry = record_path.parent
        out["ids"][entry.name] = entry
        member = (record.get("extra") or {}).get("roll_membership")
        if isinstance(member, dict):
            # Said by the entry itself. Only a scanned frame is the frame:
            # a walk's prescan shares its roll and number, and Export used to
            # deliver the 300 dpi prescan as the frame at full resolution.
            if member.get("kind") != "frame":
                continue
            roll, number = str(member.get("roll") or ""), member.get("number")
            if not isinstance(number, int):
                continue
            if member.get("folder"):
                out["folders"].setdefault(
                    _folder_key(member["folder"]), {})[number] = entry
            elif roll:
                out["names"].setdefault(roll, {})[number] = entry
            continue
        # Filed before entries said so: parse the label, and leave out what
        # is tagged a prescan for the same reason as above. `tools/scan_roll.py`
        # wrote `<roll>/<NN>`, which is read too.
        if "prescan" in (record.get("tags") or ()):
            continue
        frame = str(((record.get("film") or {}).get("frame") or "")).strip()
        roll, _, number = frame.rpartition("/" if "/" in frame else "-")
        if not roll or not number.isdigit():
            continue
        out["names"].setdefault(roll, {})[int(number)] = entry
    return out


def _folder_key(folder) -> str:
    """A roll folder as one string, however it was written down.

    `roll_membership` records the folder as the session had it -- relative to
    where the window ran, or absolute -- and the browser lists it however
    `--rolls` was given. Resolved and case-folded where the filesystem folds
    case, so the two meet: Windows by `normcase`, and macOS, whose disks
    fold case too, where `normcase` does nothing.
    """
    key = os.path.normcase(str(Path(folder).resolve()))
    return key.casefold() if sys.platform == "darwin" else key


def roll_entries(summary: dict, index: dict) -> dict[int, Path]:
    """Which library entry is each finished frame of this roll.

    The roll's own word first: `roll.json` names the entry it filed for each
    frame, and a duplicate or a renamed folder carries that record with it. A
    frame it names is that entry or, if the entry has since been deleted,
    none -- never another roll's frame of the same number. Only a record
    written before entries were named -- one with no ``entry`` key at all,
    ``legacy`` in the summary -- is joined on the folder its entries record,
    and only one older than that on its name. Decided record by record: a
    roll resumed with modern records among old ones keeps every old frame's
    entry, and a modern frame done with no entry joins nothing (P21-r1).

    Only frames the roll says are done: a name typed again after a Delete
    starts a new roll in the same folder, and the old roll's frames 7-12 are
    not frames of a new roll that scanned 1-6.
    """
    recorded = summary.get("recorded") or {}
    legacy = set(summary.get("legacy") or ())
    by_folder = index.get("folders", {}).get(_folder_key(summary["folder"]), {})
    by_name = index.get("names", {}).get(str(summary.get("roll") or ""), {})
    ids = index.get("ids", {})
    out: dict[int, Path] = {}
    for number in summary.get("done") or ():
        if number in recorded:
            # The last part on either separator: `RollManifest` records
            # `str(entry)`, backslashes on Windows, which a POSIX `Path` reads
            # as one name -- a roll scanned there and opened here on a shared
            # disk found none of its entries and exported nothing.
            entry = ids.get(PureWindowsPath(recorded[number]).name)
        elif number in legacy:
            entry = by_folder.get(number) or by_name.get(number)
        else:
            entry = None
        if entry is not None:
            out[number] = entry
    return out


def folder_created(folder) -> float:
    """When this roll was made, as a timestamp.

    The **folder name wins when it parses as a date**, and that is the point
    rather than a fallback: a roll duplicated or copied to another disk gets a
    fresh birthtime while its name still says when the film was scanned. The
    name is the roll's own answer; the filesystem's is about the copy.
    """
    folder = Path(folder)
    try:
        stamp = time.mktime(time.strptime(folder.name[:10], "%Y-%m-%d"))
    except (ValueError, OverflowError):
        stamp = 0.0
    try:
        status = folder.stat()
    except OSError:
        return stamp
    from_disk = float(getattr(status, "st_birthtime", 0) or status.st_ctime or 0)
    return stamp or from_disk


def roll_summary(folder, entries: dict | None = None,
                 library_root=None) -> dict | None:
    """What a roll folder holds, without reading a single pixel.

    This is what the browser lists from, so it has to be cheap: a roll
    directory can hold 38 frames at 142 MB each, and opening them to find out
    whether the roll is finished would make the list unusable. Everything here
    comes from the two small JSON files.

    ``None`` for a directory that is not a roll at all, so the caller can scan
    a folder of them without filtering first -- nor for a dot folder, the
    rolls' own trash among them.

    ``library_root`` says which frames whose entry went to the trash are
    back (`session.scanned_frames`); those not back are not done, and are
    listed apart (``"trashed"``).
    """
    folder = Path(folder)
    if folder.name.startswith("."):
        return None
    survey_path, roll_path = folder / "survey.json", folder / "roll.json"
    if not survey_path.exists() and not roll_path.exists():
        return None
    if (folder / library.INCOMPLETE).exists():
        # A Duplicate being copied, or one a kill cut short (`copy_roll`):
        # not a roll until its marker goes. Listed, the half was opened,
        # rescanned into and exported as if it were one.
        return None
    try:
        # The version kept beside a file that does not parse, rather than the
        # roll dropping out of the list; see `session.read_manifest`.
        manifest = read_manifest(survey_path if survey_path.exists()
                                 else roll_path)
        progress = read_manifest(roll_path)
    except (OSError, ValueError):
        return None
    # The same numbering `read_survey` puts on them, so the list and the sheet
    # it opens cannot disagree about which frames are left.
    progress = renumbered(progress, fallback=legacy_shift(manifest) or 0)
    manifest = renumbered(manifest)

    settings = progress.get("settings") or manifest.get("settings") or {}
    # What is left, as the sheet and `tools/scan_roll.py` read it: the rest
    # of the strip a run to its end never reached among it, so a roll stopped
    # part way no longer reads as finished. A strip's stub past "Strip ends
    # here" is no frame of the film.
    place = place_of(folder, strict=False)
    left = roll_left(manifest, progress, library_root,
                     ends_at=place.ends_at if place is not None else None)
    wanted, done = left.wanted, set(left.done)
    walked = set()
    if survey_path.exists():
        for record in manifest.get("frames") or ():
            try:
                walked.add(int(record["number"]))
            except (KeyError, TypeError, ValueError):
                continue
    # A strip's frames by the number each record carries across its roll,
    # the scan's over the walk's: one copy of the three, and the one that
    # holds an extra whose write to the roll's index was refused, or every
    # extra where the index cannot be read (`session.StripPlace.unsaved`).
    # Worked out from the index alone, such a frame was exported under the
    # number of the next strip's first frame.
    given = {**(record_numbers(manifest) if survey_path.exists() else {}),
             **record_numbers(progress)}
    # How each scanned frame's file was arranged, where the roll said, and
    # which library entry the roll filed for it (`roll_entries`) -- or, for
    # a record from before entries were named, that it names none.
    arranged, recorded, legacy, trashed = {}, {}, [], []
    for record in progress.get("frames") or ():
        try:
            number = int(record["number"])
            if record.get("rotation") is not None:
                arranged[number] = (int(record["rotation"]) % 360,
                                    bool(record.get("flipped")))
        except (KeyError, TypeError, ValueError):
            continue
        # Finished as `scanned_frames` reads it: `rolls/scan600*` name their
        # entries with no `done` at all, and were read as naming none.
        if not frame_finished(record):
            continue
        if record.get("entry"):
            recorded[number] = str(record["entry"])
            if entry_trashed(record, library_root):
                trashed.append(number)
        elif "entry" not in record:
            # Only code from before entries were named writes a record with
            # no `entry` key at all (`RollManifest._apply` always writes it):
            # the one kind joined by folder or name (P21-r1).
            legacy.append(number)
    # `stat` only, no pixels: a roll directory can hold 38 frames at 142 MB, and
    # the whole point of this function is that listing a shelf of them is cheap.
    sizes, newest = 0, 0.0
    for child in folder.iterdir():
        try:
            status = child.stat()
        except OSError:
            continue
        if child.is_file():
            sizes += status.st_size
        newest = max(newest, float(status.st_mtime))
    filed = dict(entries or {})
    return {
        "folder": folder,
        "created": folder_created(folder),
        #: When it was last scanned into, which is not when it was created.
        "modified": newest,
        "size": sizes,
        #: Which frames still have a library entry. Export re-corrects from
        #: those, so a roll without them cannot be exported and should say so in
        #: the list rather than at the end of a failed export.
        "entries": {n: p for n, p in filed.items()},
        "roll": progress.get("roll") or manifest.get("roll") or folder.name,
        "walked": survey_path.exists(),
        #: The frames the walk took, which is not `wanted`: a sheet asks for
        #: some of them, and a roll scanned into the folder adds its own.
        "walked_frames": sorted(walked),
        "scanned": roll_path.exists(),
        # A sheet needs the walk's own prescans; a roll commissioned without a
        # walk can still be resumed, just not looked at first.
        "has_sheet": any(folder.glob("prescan*.tif")),
        "settings": settings,
        "resolution": manifest_settings(manifest, progress).get("resolution"),
        "film": settings.get("film") or progress.get("film")
        or manifest.get("film"),
        "infrared": settings.get("infrared", progress.get("infrared",
                                                          manifest.get("infrared"))),
        "wanted": wanted,
        "done": sorted(done),
        "remaining": left.remaining,
        #: Where the rest of the strip starts that no run reached -- a run
        #: to its end that stopped -- or None; and where a run found the
        #: strip's end (`session.roll_left`).
        "rest_from": left.rest_from,
        "strip_end": left.strip_end,
        "arranged": arranged,
        #: The entry `roll.json` says it filed for each done frame, by number.
        "recorded": recorded,
        #: The done frames whose record is from before entries were named:
        #: the only ones `roll_entries` joins by folder or name.
        "legacy": sorted(legacy),
        #: Frames scanned whose entry went to the trash and is not back: not
        #: done, and said in the browser (`roll_cells`).
        "trashed": sorted(trashed),
        #: A strip's frames by the number their records carry across its
        #: roll, by place (`given_number`).
        "given": given,
    }


def record_numbers(manifest: dict) -> dict[int, int]:
    """The number across its roll each record of a strip's survey.json or
    roll.json carries (``roll_number``), by place: the copy of a frame's
    number that outlives an index write refused, or an index unread."""
    out: dict[int, int] = {}
    for record in manifest.get("frames") or ():
        if not isinstance(record, dict):
            continue
        given = record.get("roll_number")
        try:
            place = int(record["number"])
        except (KeyError, TypeError, ValueError):
            continue
        if isinstance(given, int) and not isinstance(given, bool) \
                and given > 0:
            out[place] = given
    return out


def given_number(summary: dict, place: int) -> int | None:
    """A frame of a row by its number across its roll, as its files and
    labels carry it: the number its record gives (``given``), else its
    place's in the strip's range (`shown_number`) -- None for a place past
    a closed range that no record numbers. Its place for a roll from before
    strips, and for a roll of strips' own row, which has no places."""
    numbers = (summary.get("numbers") if summary.get("kind") == "strip"
               else None)
    if numbers is None:
        return int(place)
    return shown_number(numbers, place,
                        (summary.get("given") or {}).get(int(place), 0))


def same_folder(a, b) -> bool:
    """Whether ``a`` and ``b`` name one folder on disk -- one spelling of
    the other's name in another case, on a disk that folds case -- by the
    file id both report (`os.path.samestat`). Never where the disk reports
    none: some network and FAT volumes give 0 for every file, and every
    name would be the same folder."""
    try:
        first, second = os.stat(a), os.stat(b)
    except (OSError, ValueError):
        return False
    return bool(first.st_ino and second.st_ino
                and os.path.samestat(first, second))


def _quoted_path(value) -> str:
    """A path as it would be typed, quoted where a shell would split it --
    double quotes, which bash, PowerShell and cmd.exe all read alike."""
    text = str(value)
    return f'"{text}"' if not text or any(c.isspace() for c in text) else text


def roll_losses(summary: dict, library_root=None) -> dict:
    """What a roll folder holds that nothing else does: what goes for good
    once a Delete has moved it into the trash and the trash is emptied.

    From the disk and the library, not from what the folder's name or the
    browser's summary suggest -- every list here was got wrong once:

    - ``before``: each `prescan*-before.tif` no walk record says was filed,
      or whose entry is not in the library. Both front ends file one now,
      and every such file was counted as existing nowhere else.
    - ``prescans``: walk prescans the library holds no entry for
      (`walked_prescan_entries`) -- a walk from before they were filed --
      and each such earlier one a walk added to kept beside the one that
      replaced it (P18-b), by frame number.
    - ``unfiled``: each pass the library refused, kept in `unfiled/` with
      its raw bytes (`session.keep_unfiled`), which the question never
      named and whose size it left out. Emptying the trash leaves these
      unless told otherwise (`library.empty_trash`), so the question says
      them apart.
    - ``last``: frames the roll says are done whose entry is gone, their
      `frameNN.tif` the last copy.
    - ``trashed``: those whose entry is in the library's trash: kept until
      it is emptied, and gone with it.
    - ``bytes``: the folder and everything below it.

    A roll of strips (``kind`` "roll"): summed over the strips it holds,
    each frame by its number across the roll, each file by its strip --
    ``strip-02/prescan03-before.tif``. A strip's own by its number too.
    """
    if summary.get("kind") == "roll":
        out: dict = {"before": [], "prescans": [], "unfiled": [], "last": [],
                     "trashed": [], "bytes": library._size(summary["folder"])}
        for strip in summary.get("strips") or ():
            if strip.get("state") != "here":
                continue
            lost = roll_losses(strip, library_root)
            out["before"] += [f"{Path(strip['folder']).name}/{name}"
                              for name in lost["before"]]
            out["unfiled"] += lost["unfiled"]
            for key in ("prescans", "last", "trashed"):
                out[key] += lost[key]
        for key in ("prescans", "last", "trashed"):
            out[key] = sorted(out[key])
        return out
    lost = _folder_losses(summary, library_root)
    if summary.get("kind") == "strip":
        # By the number each record gives, and a place no record numbers
        # by its place rather than dropped: the question counts what goes.
        for key in ("prescans", "last", "trashed"):
            lost[key] = sorted((given_number(summary, p) or p)
                               for p in lost[key])
    return lost


def _folder_losses(summary: dict, library_root=None) -> dict:
    """`roll_losses` for one folder of records: a roll from before strips,
    or one strip, by its places."""
    folder = Path(summary["folder"])
    root = Path(library_root) if library_root else None

    def in_library(name) -> bool:
        return (root is not None and bool(name)
                and (root / PureWindowsPath(str(name)).name / "scan.json")
                .is_file())

    try:
        walk = read_manifest(folder / "survey.json")
    except ValueError:
        walk = {}
    try:
        progress = read_manifest(folder / "roll.json")
    except ValueError:
        progress = {}
    filed_before = {str(r.get("prescan_before")): r.get("prescan_before_entry")
                    for r in walk.get("frames") or ()
                    if isinstance(r, dict) and r.get("prescan_before")}
    before = sorted(p.name for p in folder.glob("prescan*-before.tif")
                    if not in_library(filed_before.get(p.name)))
    walked = walked_prescans(folder, walk) if walk else []
    joined = (walked_prescan_entries(folder, [(n, r) for n, _p, r in walked],
                                     root, walk=walk, roll=progress)
              if root is not None else {})
    prescans = [n for n, _p, _r in walked if n not in joined]
    # And each earlier prescan a walk added to kept beside the new one
    # because no entry holds it (P18-b): listed among its frame's takes
    # with a file and no entry, the only copy there is.
    for record in walk.get("frames") or ():
        if not isinstance(record, dict):
            continue
        for take in record.get("takes") or ():
            if (isinstance(take, dict) and take.get("file")
                    and not take.get("entry")
                    and Path(str(take["file"])).name == str(take["file"])
                    and (folder / str(take["file"])).is_file()):
                try:
                    prescans.append(int(record["number"]))
                except (KeyError, TypeError, ValueError):
                    continue
    prescans.sort()
    unfiled = sorted(p.parent for p in (folder / UNFILED).glob("*/scan.json"))
    last, trashed = [], []
    entries = summary.get("entries") or {}
    in_trash = ({n for batch in library.trash_batches(root)
                 for n in batch.names} if root is not None else set())
    for record in progress.get("frames") or ():
        # Finished as `scanned_frames` reads it, a roll tool's record from
        # before `done` included (P21-r1).
        if not isinstance(record, dict) or not frame_finished(record):
            continue
        try:
            number = int(record["number"])
        except (KeyError, TypeError, ValueError):
            continue
        named = record.get("entry")
        if in_library(named) or (not named and number in entries):
            continue
        own = f"frame{number:02d}.tif"
        if named and PureWindowsPath(str(named)).name in in_trash:
            trashed.append(number)
        elif (copy_written(record, own) is not False
              and (folder / own).exists()):
            # This take's own copy: one whose record says it was not
            # written left an earlier take's file there, which is no copy
            # of the entry gone (PLAT-11).
            last.append(number)
    return {"before": before, "prescans": prescans, "unfiled": unfiled,
            "last": sorted(last), "trashed": sorted(trashed),
            "bytes": library._size(folder)}


def copy_written(record: dict, name: str) -> bool | None:
    """Whether a roll frame's record says its own copy, ``name``
    (``frameNN.tif``), was written: True, False, or None where it says
    neither -- a record from before the window said it (PLAT-11), whose
    readers go on taking the file on the disk for it. Said once the writer
    has answered (`session.Filing.copy_said`)."""
    if record.get("file_error"):
        return False
    if record.get("file"):
        return str(record["file"]) == name
    return None


def without_entry(summary: dict) -> list[int]:
    """The frames of a roll that were scanned and have no entry now: each
    whose entry went to the trash, and -- where the browser has looked
    (`rolls_on_disk`) -- each done whose entry has gone some other way.
    The browser marked a roll only when it had no entries at all, so one
    frame deleted among thirty-six showed nothing."""
    gone = set(summary.get("trashed") or ())
    if summary.get("looked"):
        gone |= set(summary.get("done") or ()) - set(summary.get("entries")
                                                     or {})
    return sorted(gone)


def rolls_on_disk(root, library_root=None) -> list[dict]:
    """Every roll under `root`, newest first, as `roll_summary` describes them.

    Sorted by the directory name because that is the date the session wrote --
    `rolls/2026-09-14` -- which orders correctly as text and does not move when
    a file inside is touched, as an mtime would. The table re-sorts it anyway;
    this is the order it opens on.

    `library_root` is where the entries are looked for, once for the whole
    listing. Without it the rolls still list, and none of them knows whether it
    can be exported.

    Never a dot folder: the rolls' trash is one (`library.TRASH`), and a
    roll deleted is not a roll to open.

    Two kinds of row (``kind``): ``"folder"``, a roll from before strips,
    as it always was; and ``"roll"``, a roll of strips (`strips_summary`),
    whose ``strips`` are its strips' rows, ``"strip"`` each.
    """
    root = Path(root)
    if not root.is_dir():
        return []
    index = roll_entry_index(library_root) if library_root else {}
    out = []
    trash = library.trash_batches(root)
    for folder in sorted(root.iterdir(), key=lambda p: p.name, reverse=True):
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        if library.roll_kind(folder) == "strips":
            summary = strips_summary(folder, library_root, index, trash)
            if summary is not None:
                out.append(summary)
            continue
        summary = roll_summary(folder, library_root=library_root)
        if summary is None:
            continue
        summary["kind"] = "folder"
        summary["entries"] = roll_entries(summary, index) if index else {}
        summary["looked"] = bool(index)
        out.append(summary)
    return out


def _strip_skeleton(folder: Path, label: str) -> dict:
    """A strip's row with nothing read from it: one in the rolls' trash,
    gone, or claimed and never walked -- the keys every reader of a row
    asks for, each saying nothing."""
    return {"kind": "strip", "folder": folder, "created": 0.0,
            "modified": 0.0, "size": 0, "entries": {}, "roll": label,
            "walked": False, "walked_frames": [], "scanned": False,
            "has_sheet": False, "settings": {}, "resolution": None,
            "film": None, "infrared": None, "wanted": [], "done": [],
            "remaining": [], "rest_from": None, "strip_end": None,
            "arranged": {}, "recorded": {}, "legacy": [],
            "trashed": [], "given": {}, "looked": False}


def _rolled(strip: dict, places) -> list[int]:
    """A strip row's places, by their numbers across its roll
    (`given_number`); a place not numbered yet -- past a closed range,
    never scanned -- is left out."""
    return sorted(n for n in (given_number(strip, p) for p in places)
                  if n is not None)


def strip_numbers_said(strip: dict) -> str:
    """A strip row's numbers: ``13-18`` once closed, and while open the
    numbers its walk and its frames have reached -- ``19-22`` -- or ``from
    19`` where they have reached none."""
    numbers = strip.get("numbers")
    if numbers is None:
        return "?"
    if numbers.last is not None:
        return numbers.numbers()
    places = set(strip.get("walked_frames") or ()) | set(strip.get("wanted")
                                                           or ())
    places |= set(strip.get("done") or ())
    if numbers.ends_at is not None:
        places = {p for p in places if p <= numbers.ends_at} | {numbers.ends_at}
    if not places:
        return numbers.numbers()
    high = numbers.first + max(places) - 1
    return (str(numbers.first) if high == numbers.first
            else f"{numbers.first}-{high}")


def strips_summary(roll, library_root=None, index: dict | None = None,
                   trash=None) -> dict | None:
    """A roll of strips as the browser lists it: one row for the roll, its
    strips' rows under it, by number (`roll_summary` of each, numbered
    across the roll).

    Each strip the roll's index lists has a row, here or not -- its
    ``state`` "here", "in the trash" (a strip deleted on its own, in the
    rolls' trash by a delete of this roll -- not of another film's that
    had its name), or "missing" -- and so does a
    strip folder here that the index does not list, holding records. An
    index that cannot be read is said (``unreadable``), and each strip is
    listed from the copy of its claim its own records carry.

    The roll's frames are its strips', by their numbers across it: what is
    wanted, done and left, and which have an entry -- so "only unfinished"
    shows a roll while any of its strips has frames left. Its resolution,
    channels and film are its last strip's that says them.

    None for a Duplicate cut short (`library.INCOMPLETE`), as for a roll
    from before strips."""
    roll = Path(roll)
    if (roll / library.INCOMPLETE).exists():
        return None
    try:
        record = read_index(roll)
        unreadable = None
    except ValueError as exc:
        record, unreadable = {}, str(exc)
    label = record.get("roll") if isinstance(record.get("roll"), str) \
        and record.get("roll") else roll.name
    listed = listed_strips(record)
    trash = library.trash_batches(roll.parent) if trash is None else trash
    here = {f.name.casefold(): f for f in library.strips_in(roll)}
    rows: list[dict] = []
    seen: set[str] = set()
    for entry in listed:
        name = str(entry.get("folder") or "")
        folder = roll / name
        seen.add(name.casefold())
        rows.append(_strip_row(folder, label, entry, library_root, index,
                               trash, roll, record=record))
    for key, folder in here.items():
        if key in seen:
            continue
        block = library.strip_block(folder) or {}
        rows.append(_strip_row(folder, label, {
            "strip": block.get("number") or library.strip_number(folder.name),
            "first": block.get("first") or 1, "last": None},
            library_root, index, trash, roll, listed=False, record=record))
    rows.sort(key=lambda s: (int(s.get("strip") or 0), Path(s["folder"]).name))
    present = [s for s in rows if s["state"] == "here"]
    wanted = sorted(n for s in present for n in _rolled(s, s["wanted"]))
    done = sorted(n for s in present for n in _rolled(s, s["done"]))
    entries = {}
    for s in present:
        for place, entry in (s.get("entries") or {}).items():
            number = given_number(s, place)
            if number is not None:
                entries[number] = entry
    settled = [s for s in present if s.get("resolution")]
    last = settled[-1] if settled else (present[-1] if present else None)
    #: The strips whose rest no run reached, by number: what keeps the roll
    #: unfinished with no frame left to name.
    rests = [int(s.get("strip") or 0) for s in present
             if s.get("rest_from") is not None]
    return {
        "kind": "roll", "folder": roll, "roll": label, "strips": rows,
        "unreadable": unreadable,
        "created": folder_created(roll),
        "modified": max((s["modified"] for s in present), default=0.0),
        "size": sum(s["size"] for s in present),
        "entries": entries,
        "walked": any(s["walked"] for s in present),
        "scanned": any(s["scanned"] for s in present),
        "has_sheet": any(s["has_sheet"] for s in present),
        "settings": dict(last["settings"]) if last is not None else {},
        "resolution": last["resolution"] if last is not None else None,
        "film": last["film"] if last is not None else None,
        "infrared": last["infrared"] if last is not None else None,
        "wanted": wanted, "done": done,
        "remaining": [n for n in wanted if n not in done],
        # Counted up from its strips, as the frames are: a roll is
        # unfinished while any strip has a rest no run reached.
        "rests": rests,
        "rest_from": None,
        "trashed": sorted(n for s in present
                          for n in _rolled(s, s["trashed"])),
        "looked": bool(index),
    }


def _strip_row(folder: Path, label: str, entry: dict, library_root, index,
               trash, roll: Path, listed: bool = True,
               record: dict | None = None) -> dict:
    """One strip's row in its roll's (`strips_summary`). ``record`` is the
    roll's own index, as read for the listing.

    In the trash by the newest delete of this roll that holds it: a delete
    of another film's strip, its roll's name given to this one since, is
    none of this roll's (`library.strip_delete_is_of`)."""
    state = "here"
    try:
        there = folder.is_dir()
    except OSError:
        there = False
    deleted = None
    if not there:
        state = "missing"
        deleted = next((b for b in reversed(list(trash or ()))
                        if library.strip_delete_is_of(b, roll, record)
                        and any(n.casefold() == folder.name.casefold()
                                for n in b.names)), None)
        if deleted is not None:
            state = "in the trash"
    summary = (roll_summary(folder, library_root=library_root)
               if state == "here" else None)
    if summary is None:
        summary = _strip_skeleton(folder, label)
    else:
        summary["entries"] = roll_entries(summary, index) if index else {}
        summary["looked"] = bool(index)
    number = entry.get("strip")
    first = entry.get("first")
    last = entry.get("last")
    ends = entry.get("ends_at")
    summary.update(
        kind="strip", state=state, roll_folder=roll, listed=listed,
        strip=int(number) if isinstance(number, int) else 0,
        first=int(first) if isinstance(first, int) else 1,
        last=last if isinstance(last, int) else None,
        batch=deleted.name if deleted is not None else None)
    summary["numbers"] = (place_of(folder, strict=False) if state == "here"
                          else StripPlace(roll, label, summary["strip"],
                                          folder, summary["first"],
                                          summary["last"],
                                          ends if isinstance(ends, int)
                                          else None))
    if summary["numbers"] is None:
        summary["numbers"] = StripPlace(roll, label, summary["strip"], folder,
                                        summary["first"], summary["last"])
    summary["roll"] = label
    return summary


def row_said(summary: dict) -> str:
    """A browser row as a line names it: its folder, and a strip by its
    roll -- ``Gold200 (strip 2)`` -- never a bare ``strip-02``."""
    if summary.get("kind") == "strip":
        return f"{summary.get('roll')} (strip {summary.get('strip')})"
    return Path(summary["folder"]).name


def roll_rows(rolls: list[dict]) -> list[dict]:
    """Every row a selection can name -- each roll, and each strip of a
    roll of strips -- for finding a strip's roll."""
    out = []
    for summary in rolls:
        out.append(summary)
        out += summary.get("strips") or []
    return out


def opened_at(summary: dict, stored: dict) -> float | None:
    """When a row was last opened, from the settings file's ``rolls``
    (`ScannerGui._note_roll_opened`): a roll from before strips and a strip
    by `session.roll_key`, and a roll of strips by the newest of its own and
    its strips' (``Gold200/...``)."""
    if not isinstance(stored, dict):
        return None
    folder = Path(summary["folder"])
    if summary.get("kind") == "strip":
        key = f"{folder.parent.name}/{folder.name}"
        return (stored.get(key) or {}).get("opened")
    if summary.get("kind") == "roll":
        prefix = folder.name + "/"
        times = [(value or {}).get("opened") for key, value in stored.items()
                 if (key == folder.name or key.startswith(prefix))
                 and isinstance(value, dict)]
        times = [t for t in times if isinstance(t, (int, float))]
        return max(times) if times else None
    return (stored.get(folder.name) or {}).get("opened")


def unfinished(summary: dict) -> bool:
    """Whether a browser row has anything left: a frame it asks for and has
    not done, or a rest of the strip no run reached -- its own, or for a
    roll of strips any strip's (`session.roll_left`). One test for its cell,
    its colour, "only unfinished" and which strip Open opens."""
    return bool(summary.get("remaining")
                or summary.get("rest_from") is not None
                or summary.get("rests"))


#: What the table can be ordered by, and how. Each is a key function over a
#: summary; the browser sorts with these rather than on the rendered text, so
#: "2 of 10" does not sort before "2 of 4" and 900 MB does not sort before 1 GB.
SORT_KEYS = {
    "roll": lambda r: str(r["roll"]).lower(),
    # A rest of the strip no run reached is left too: one more, unnumbered.
    "frames": lambda r: (len(r["remaining"]) + bool(
        r.get("rest_from") is not None or r.get("rests")), -len(r["wanted"])),
    "dpi": lambda r: int(r["resolution"] or 0),
    "film": lambda r: str(r["film"] or ""),
    "created": lambda r: r["created"],
    "opened": lambda r: r.get("opened") or 0.0,
    "size": lambda r: r["size"],
}


def roll_cells(summary: dict) -> tuple:
    """One table row: what this roll is and how far it got, column by column.

    Kept out of the widget so the rendering is testable and so the sort keys in
    `SORT_KEYS` can order on the *values* rather than on these strings -- "2 of
    10" sorts before "2 of 4" as text, and 900 MB before 1 GB.

    A roll of strips reads ``Gold200 · 6 strips``, its frames counted across
    them, and each strip under it ``strip 5 · 25-30`` -- by the numbers its
    frames have across the roll -- or that it is in the rolls' trash.
    """
    kind = summary.get("kind")
    if kind == "strip" and summary.get("state") != "here":
        where = ("in the rolls' trash" if summary.get("state")
                 == "in the trash" else "not here")
        return (f"strip {summary.get('strip')} · "
                f"{strip_numbers_said(summary)}", where, "--", "--", "--",
                when(summary.get("opened")), "--")
    wanted, done = len(summary["wanted"]), len(summary["done"])
    rest = summary.get("rest_from")
    rests = summary.get("rests") or []
    if not summary["scanned"]:
        frames = f"walked, {wanted}"
    elif wanted and not unfinished(summary):
        # Finished by what is left, never by a count: done frames outside
        # what the roll asks for read "finished, 3" while frame 8 was left,
        # and the "only unfinished" tick, which reads what is left, showed
        # it (RES-n6).
        frames = f"finished, {done}"
    elif wanted:
        # Of the frames it asks for: a frame taken outside them is no step
        # towards them, and "4 of 3" said nothing.
        got = len(set(summary["done"]) & set(summary["wanted"]))
        frames = f"{got} of {wanted}"
    else:
        frames = f"{done} scanned"
    if rest is not None:
        frames += (f", strip from {given_number(summary, rest) or rest} not "
                   "reached")
    elif rests:
        frames += (f", strip{'s' if len(rests) > 1 else ''} "
                   f"{_and_joined(rests)} not scanned to "
                   f"{'their' if len(rests) > 1 else 'its'} end")
    gone = len(without_entry(summary))
    if gone:
        frames += f" ({gone} without an entry)"
    depth = str(summary["resolution"] or "--")
    if summary["resolution"]:
        depth += " RGBI" if summary["infrared"] else " RGB"
    # The **folder** name leads, not the manifest's roll name, because the
    # folder is what is unique, what Finder shows and what Rename changes. A
    # duplicate keeps the original's roll name inside its manifest, so two rows
    # read identically without this -- which is exactly when you need to tell
    # them apart. The roll's own name follows when the two differ.
    folder = Path(summary["folder"]).name
    named = (folder if folder == summary["roll"]
             else f"{folder}  ({summary['roll']})")
    if kind == "roll":
        strips = summary.get("strips") or []
        named += f" · {counted(len(strips), 'strip')}"
        if summary.get("unreadable"):
            named += " -- strips.json cannot be read"
    elif kind == "strip":
        named = f"strip {summary.get('strip')} · {strip_numbers_said(summary)}"
        if not summary.get("listed", True):
            named += " -- not in strips.json"
    return (
        named,
        frames,
        depth,
        str(summary["film"] or "--"),
        when(summary["created"]),
        when(summary.get("opened")),
        human_size(summary["size"]),
    )


def matching(rolls: list[dict], text: str = "",
             unfinished_only: bool = False) -> list[dict]:
    """The rolls a search box and a tick leave showing.

    Case-insensitive, and across the roll's name *and* its film, because "the
    slide one" is as likely a way to look for a roll as its date is.
    """
    wanted = (text or "").strip().lower()
    out = []
    for roll in rolls:
        if unfinished_only and not unfinished(roll):
            continue
        if wanted and wanted not in (f"{roll['roll']} {roll['film'] or ''}"
                                     .lower()):
            continue
        out.append(roll)
    return out


def human_size(count: int) -> str:
    """Bytes as something a person can compare at a glance."""
    for unit, scale in (("GB", 1e9), ("MB", 1e6), ("kB", 1e3)):
        if count >= scale:
            return f"{count / scale:.1f} {unit}"
    return f"{count} B"


def when(stamp: float | None) -> str:
    """A timestamp as a date, or a dash. Never the epoch, which reads as 1970."""
    if not stamp:
        return "--"
    return time.strftime("%Y-%m-%d", time.localtime(stamp))


def carry_walk(source, target, rolls=None, who: str = "the window"
               ) -> list[str]:
    """Copy the walk in ``source`` into ``target``, when ``target`` has none.

    For a walk added to a sheet opened from outside this session's rolls:
    the walk goes into the folder of the same name here (`_roll_folder`),
    and the session carries forward the `survey.json` it finds *there* --
    which was none, so the new one listed only the frames just walked while
    the sheet, `approved.json` and `roll.json` covered the strip, and the
    folder reopened as a partial sheet. The source is read, never written.

    What is copied is what the walk's records name, not whatever the folder
    holds (see `walked_prescans`), and `survey.json` last, as it was read --
    so a target with a survey has the prescans it lists. Returns the names
    copied, empty when there was nothing to do. Raises OSError or
    ValueError, naming the file, when the walk cannot be read or copied.

    ``target`` is made below ``rolls`` -- its parent where none is given --
    which may have its own folder made and never the ones above it
    (`storage`): a rolls folder on a drive that has gone is not made again
    on C:.

    Both held while it copies (`rps7200.locks`), as ``who``: the target as
    written into, the source as shown -- so neither is written, renamed or
    deleted by another program part way. Raises `locks.Refused`, an
    OSError, where one is.
    """
    source, target = Path(source), Path(target)
    if (target / "survey.json").exists() or not (source / "survey.json").exists():
        return []
    try:
        if source.resolve() == target.resolve():
            return []
    except OSError:
        pass
    # Each by its roll's folder where it is a strip (`library.lock_folder`).
    with locks.take(locks.ROLL, library.lock_folder(target), exclusive=True,
                    what=who), \
            locks.take(locks.ROLL_VIEW, library.lock_folder(source),
                       exclusive=False, what=who):
        manifest = read_manifest(source / "survey.json")
        names: list[str] = []
        for record in manifest.get("frames") or ():
            for key in ("prescan", "prescan_before"):
                name = record.get(key)
                if (isinstance(name, str) and name and Path(name).name == name
                        and (source / name).is_file() and name not in names):
                    names.append(name)
        storage.make_within(target.parent if rolls is None else rolls, target)
        for name in names:
            # Beside, on the disk, then renamed in, as every walk prescan is
            # written (`export._whole`): copied straight to its name, a copy
            # cut short -- a kill, a full disk, a power cut -- sat under the
            # name survey.json lists, and is read back as a hold's reference.
            temp = target / f".{name}.part"
            try:
                shutil.copyfile(source / name, temp)
                library._sync(temp)
                library._replace(temp, target / name)
            finally:
                library._discard(temp)
        write_manifest(target / "survey.json", manifest)
    return [*names, "survey.json"]


def same_walk(source, target) -> bool:
    """Whether ``target`` holds the walk ``source`` holds, as `carry_walk`
    would have copied it: the same walk by its id where both have one
    (`walk_stamp`) -- its writer's late answers may have rewritten either
    file since -- and otherwise the same survey.json, read."""
    try:
        theirs = read_manifest(Path(target) / "survey.json")
        mine = read_manifest(Path(source) / "survey.json")
    except ValueError:
        return False
    if not theirs:
        return False
    ids = (theirs.get("walk_id"), mine.get("walk_id"))
    if all(isinstance(i, str) and i for i in ids):
        return ids[0] == ids[1]
    return theirs == mine


class _Unknown:
    """What `walk_stamp` says of a walk it could not read."""

    def __repr__(self) -> str:
        return "UNKNOWN"


#: `walk_stamp`'s answer for a survey.json there and not readable now --
#: held by a writer for a moment, or damaged. Neither "no walk" nor a walk:
#: nothing is filed under it, and nothing found by it.
UNKNOWN = _Unknown()


def walk_stamp(folder) -> str | int | None | _Unknown:
    """Which walk a roll folder holds: its survey.json's ``walk_id``.

    Written once, as the walk starts, and kept by a walk added to it
    (`session.ScanSession._roll`), so a sheet's decisions filed under it
    are found again for the same walk -- after a restart, or in a copy --
    and for no other. It was the file's modification time, and the writer's
    late answers rewrite the file after the walk has ended: decisions filed
    between the two were keyed to a time no file had any more (P22-b).

    A walk from before ids is still known by that time, in nanoseconds, so
    its decisions filed with it are found as they were. None for a folder
    with no walk; `UNKNOWN` for a survey.json that cannot be read now. Read
    as it is, never from the copy kept beside it: that is another walk's.
    """
    try:
        path = Path(folder) / "survey.json"
    except TypeError:
        return None
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        return UNKNOWN
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        return UNKNOWN
    if not isinstance(data, dict):
        return UNKNOWN
    walk_id = data.get("walk_id")
    if isinstance(walk_id, str) and walk_id:
        return walk_id
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return UNKNOWN


def set_aside_sheet(sheets: dict, key: str, walk: str | int | None) -> None:
    """Keep the sheet decisions under ``key`` from being written over by
    another walk's: moved to ``<key>@<their walk>``, where `_stored_sheet`
    finds them by their walk alone.

    For a walk not yet commissioned they are the only record, and the name
    they are filed under is free for the next walk once the folder is in
    the trash -- and for a Rename. Decisions that say no walk are dropped:
    they are found by none (`_stored_sheet`).
    """
    old = sheets.get(key)
    if not isinstance(old, dict):
        return
    theirs = old.get("walk")
    if theirs is not None and theirs != walk:
        sheets[f"{key}@{theirs}"] = old


def folder_note(folder, dry: bool, library_root=None) -> str:
    """Where a roll from the Roll button goes, and what is there already.

    Said in the question that starts it, because the folder decides what is
    replaced: a walk into a folder that holds one replaces its survey.json and
    overwrites its prescans frame by frame, and a roll into one adds to its
    roll.json and overwrites its frame files. With the date as every unnamed
    roll's folder that happened to each strip of a day with no word; the name
    is new for each roll now, so this only ever warns about a name he typed.

    Its scanned frames counted against the library at ``library_root``, as
    `done_in` counts them a few lines further into the same question: with
    none, a frame whose entry `restore` had brought back counted as not
    scanned here and as scanned there (`session.scanned_frames`).

    A strip of a roll of strips is named as one, with the numbers its roll
    gave it: a roll into it scans places on the strip, and its files are
    labelled by those numbers. Another film goes into a new roll, which New
    roll starts.
    """
    folder = Path(folder)
    where = f"Into {folder.parent.name}/{folder.name}"
    strip = place_of(folder, strict=False)
    if strip is not None:
        where += (f": strip {strip.strip} of {strip.label}, frames "
                  f"{strip.numbers()}")
    another = ("Press New roll if this is another film." if strip is not None
               else "Clear the roll box for a new roll if this is another "
                    "strip.")
    summary = (roll_summary(folder, library_root=library_root)
               if folder.is_dir() else None)
    if summary is None:
        return f"{where}, a new roll." if strip is None else f"{where}."
    held = []
    if summary["walked"]:
        # The walk's own frames. It said `wanted` -- the frames asked for --
        # so a walk of 1 to 6 whose sheet asked for 2 and 4 was "a walk of
        # frames 2, 4".
        walked = summary.get("walked_frames")
        held.append(f"a walk of frames {number_spans(walked)}"
                    if walked else "a walk")
    if summary["scanned"]:
        done = len(summary["done"])
        held.append(f"{done} scanned frame{'s' if done != 1 else ''}")
    already = f"{where}, which already holds {' and '.join(held) or 'files'}."
    if dry:
        return (f"{already} This walk replaces its walk -- the old survey is "
                "kept beside it as survey.json.bak, and its prescans are "
                "overwritten frame by frame. Clear the roll box for a new "
                "roll if this is another strip.")
    return (f"{already} This roll adds its frames to it, and a frame scanned "
            f"again replaces its file. {another}")


# -- a roll of strips, as the Roll button places a walk or a roll in it -----
#
# Stefan's call, 2026-10-10: a film is one roll folder, and the strips cut
# from it are walked in order, one by one, into strip-01, strip-02 ... inside
# it -- each exactly what a roll folder was before -- numbered across the
# roll (`session`'s "A roll of strips"). The box names the roll, never a
# strip; the Roll button's walk goes to the roll's next strip, or starts a
# new roll. The 103 folders from before strips are one strip each: a plain
# roll resumes one as it always did, and a walk never adds a strip to it.


#: What `ScannerGui._skip_done` says of a strip with every frame scanned that
#: was walked to its end: a plain roll for it is the next strip instead.
NEXT_STRIP = "next strip"


class ForeignStrip(ValueError):
    """A strip of a roll of strips shown from outside this window's rolls:
    scanned, walked and added to in its own roll alone, never carried into
    a folder here (P18-d). Its message says where to open it."""


class RollRun(NamedTuple):
    """A roll the session started, as the window keeps it once the roll has
    ended (`_mark_filed`): its folder (`Roll.out`, None where it named only
    a roll), and whether it was a walk. Not the job, which holds a
    commission's prescans."""

    out: Path | None
    dry: bool

    @classmethod
    def of(cls, job: Roll) -> "RollRun":
        return cls(Path(job.out) if job.out else None, bool(job.dry_run))


class RollTarget(NamedTuple):
    """Where the Roll button's walk or roll goes (`roll_target`).

    ``kind`` is one of:

    - ``"new roll"``: strip 1 of a roll not made yet, ``plan`` saying it;
    - ``"next strip"``: the next strip of a roll of strips -- ``plan``, and
      ``offer`` beside it, whose ``again`` is a last strip whose walk did
      not reach its end, which the window offers to add to first;
    - ``"strip"``: a plain roll into a strip there is, ``folder``;
    - ``"legacy"``: a plain roll into a roll from before strips, resumed.

    ``folder`` is what `Roll.out` gets -- the roll's own for a strip the
    run claims, the strip's or the old roll's otherwise -- ``roll`` the
    roll folder whose name the box shows, ``note`` what the question says
    of it, and ``ours`` whether the box then shows that name as the
    window's own rather than as one he typed (`_typed_roll_name`).
    """

    kind: str
    folder: Path
    roll: Path
    plan: StripPlan | None = None
    offer: StripOffer | None = None
    note: str = ""
    ours: bool = False

    @property
    def new_strip(self) -> bool:
        """Whether the run claims a strip (`Roll.new_strip`)."""
        return self.plan is not None


class Reopened(NamedTuple):
    """A strip the Roll button opened to finish it (`ScannerGui._roll_into`)
    -- ``folder`` -- and what that put back into the controls, said
    (``restored``): the press is asked again with them."""

    folder: Path
    restored: str


class Finish(NamedTuple):
    """Which frames a plain roll from the Roll button takes, once the frames
    a roll has done are left out (`ScannerGui._skip_done`): its first frame
    and count, the frames chosen among them (``only``), where it goes on to
    the end of the strip after those (``rest_from``, `session.Roll`), and
    what the question says of it (``note``)."""

    only: tuple[int, ...] | None
    note: str
    rest_from: int | None
    start_at: int
    frames: int | None


def taken_at(settings: dict, other: dict | None = None) -> str:
    """A roll's settings as a question says them -- ``1800 dpi RGB`` -- and
    its film, metering and tied infrared where ``other`` differs in them:
    what decides the pixels (`session.settings_differ`)."""
    said = (f"{settings.get('resolution')} dpi "
            f"{'RGBI' if settings.get('infrared') else 'RGB'}")
    other = other or {}

    def differs(key: str) -> bool:
        """Said by both, and not alike: a roll that does not say its
        metering holds a run to none (`session.settings_differ`)."""
        return (settings.get(key) is not None and other.get(key) is not None
                and settings.get(key) != other.get(key))

    if differs("film"):
        said += f", {settings.get('film')} film"
    if differs("meter"):
        said += f", metered {settings.get('meter')}"
    if (other and settings.get("infrared") and other.get("infrared")
            and bool(settings.get("fast_infrared"))
            != bool(other.get("fast_infrared"))):
        said += (", infrared tied to the resolution"
                 if settings.get("fast_infrared") else ", infrared untied")
    return said


#: What `session.settings_differ` compares, as the controls that set it
#: are labelled -- in the window and on the contact sheet alike.
SETTING_CONTROLS = (("resolution", "scan dpi"), ("infrared", "infrared (RGBI)"),
                    ("fast_infrared", "infrared at scan resolution"),
                    ("film", "film"), ("meter", "meter"))


def settings_to_set(was: dict, now: dict) -> list[str]:
    """What to set for a run asking for ``now`` to ask for what a roll's
    frames were taken at (``was``): each control whose setting differs, as
    it is labelled, with the value -- ``scan dpi 3600``, ``infrared (RGBI)
    on`` -- compared as `session.settings_differ` compares them."""
    out = []
    for key, label in SETTING_CONTROLS:
        value = was.get(key)
        if value is None:
            continue
        if key == "fast_infrared":
            # Only for an infrared roll: an RGB roll's flag governs nothing.
            if (not was.get("infrared")
                    or bool(value) == bool(now.get("fast_infrared"))):
                continue
        elif key not in now or value == now[key]:
            continue
        said = ("on" if value else "off") if isinstance(value, bool) else value
        out.append(f"{label} {said}")
    return out


#: The controls a roll's settings put back (`restorable`), as the panels
#: label them, for saying which opening a strip moved.
ROLL_CONTROLS = {
    "dpi": "scan dpi", "predpi": "prescan dpi", "ir": "infrared (RGBI)",
    "fast_ir": "infrared at scan resolution", "film": "film",
    "meter": "meter", "mono_channel": "channel",
    "correct": "aim each frame while prescanning",
    "startat": "first frame", "last": "last frame",
}


def controls_said(name: str, before: dict, after: dict) -> str:
    """What opening the strip ``name`` put back into the controls: each
    that moved, as its panel labels it, with what it reads now. Said in the
    Roll button's question once it has opened one, where it used to change
    them without a word and the question showed the old ones."""
    def value(v) -> str:
        if isinstance(v, bool):
            return "on" if v else "off"
        return str(v) if str(v) != "" else "empty"

    moved = [f"{label} {value(after.get(key))}"
             for key, label in ROLL_CONTROLS.items()
             if before.get(key) != after.get(key)]
    if not moved:
        return f"{name} is open; its settings are the window's already."
    return (f"{name} is open, with its own settings back in the window: "
            f"{', '.join(moved)}.")


def _ordinal(n: int) -> str:
    """``1st``, ``2nd``, ``3rd``, ``4th`` ... ``11th``, ``21st``."""
    n = int(n)
    suffix = ("th" if 10 <= n % 100 <= 20
              else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th"))
    return f"{n}{suffix}"


def extras_said(strip: StripPlace | None, start_at: int, frames: int | None,
                live=None, *, scan: bool = False) -> str:
    """What a walk or a roll into ``strip`` from ``start_at`` -- ``frames``
    of them, or to the end of the strip -- does to the roll's numbers past
    the strip's own, said before it runs; "" where it reaches none.

    A strip whose numbers are fixed (``last``, once the next strip was
    claimed) gives a frame past them the number after the roll's last,
    given once and kept for good (`session.StripPlace.number`). Only the
    per-frame line said so, once the number was used up. A walk past
    "Strip ends here" takes none: those places keep the number they have.
    A scan (``scan``) of a frame named past it does -- the end was set too
    early -- and one to the end of the strip is refused.

    The first such number closes the roll's open last strip, where that is
    another, at the frames it had -- or numbers it on, where it recorded
    none -- and that is said too."""
    if strip is None or strip.last is None:
        return ""
    if strip.ends_at is not None and frames is None:
        return ""
    if not strip.could_give(start_at, frames, None, scan=scan):
        return ""
    span = strip.last - strip.first + 1
    try:
        after = highest_given(strip.roll_folder, live=live) + 1
    except ValueError:
        after = None
    said = (f"Strip {strip.strip} has numbers {strip.numbers()}. A frame past "
            f"its {_ordinal(span)} gets a number after the roll's last"
            + (f", from {after}" if after is not None else "")
            + " -- numbers already given never change.")
    following = open_last_strip(strip.roll_folder, live,
                                leave_out=strip.strip)
    if following is None:
        return said
    if following.count > 0:
        return (said + f" The first of them closes strip {following.strip}, "
                f"the roll's last, at {following.numbers()}: a frame of it "
                "walked later is numbered after the roll's last too.")
    return (said + f" Strip {following.strip}, the roll's last, has nothing "
            "recorded yet, so it is numbered on after them.")


def roll_target(rolls, typed: str, *, own: str | None = None, dry: bool,
                current=None, library_root=None) -> RollTarget:
    """Where a walk (``dry``) or a roll from the Roll button goes, for the
    roll box reading ``typed``: one rule for its question, its hand-over and
    the Roll panel's line.

    - An empty box: a new roll, by a dated name (`session.new_roll_name`).
    - A roll of strips: its next strip (`session.plan_next`) -- or, for a
      plain roll, the strip of it that is ``current`` in the window (the
      sheet's, or the one opened), finished as a roll always was.
    - A name with no folder yet, or an empty one: a new roll of that name.
    - A roll from before strips: resumed by a plain roll, as it always was.
      A walk adds no strip to it: with the window's own name in the box
      (``own``), a roll it opened, a new dated roll, as before; typed, the
      first of ``<name>-2``, ``-3`` ... that is a roll of strips or not
      there yet, and the note says why. The window then shows that name as
      its own, so the next walk goes on with it.
    - A folder holding what is no roll's: the same ``-2``, for either.

    Read-only. Raises ValueError where a roll's index cannot be read, and
    `session.RollFolderInUse` for a strip's own folder typed as a roll.
    """
    rolls = Path(rolls)
    typed = (typed or "").strip()
    what = "walk" if dry else "roll"
    if not typed:
        return _new_roll(rolls / new_roll_name(rolls), ours=True)
    folder = roll_dir(rolls, typed)
    kind = library.roll_kind(folder)
    mine = bool(own) and typed == own
    if kind == "strips":
        if not dry and current is not None:
            strip = Path(current)
            if (library.strip_number(strip.name) is not None
                    and _folder_key(library.lock_folder(strip))
                    == _folder_key(folder)):
                return RollTarget("strip", strip, folder,
                                  note=folder_note(strip, False, library_root),
                                  ours=mine)
        return _next_strip(folder, dry=dry, ours=mine)
    if kind == "new":
        return _new_roll(folder, ours=mine)
    if kind == "legacy":
        if not dry:
            return RollTarget("legacy", folder, folder,
                              note=folder_note(folder, False, library_root),
                              ours=mine)
        if mine:
            # As before: a fresh walk with an opened roll's own name in the
            # box is another strip of film, and gets a roll of its own.
            return _new_roll(rolls / new_roll_name(rolls), ours=True)
        why = (f"rolls/{folder.name} is one strip from before rolls held "
               "strips and takes no second strip")
    else:
        why = f"rolls/{folder.name} holds files that are no roll's"
    for n in range(2, 1000):
        candidate = folder.with_name(f"{folder.name}-{n}")
        there = library.roll_kind(candidate)
        if there == "new":
            target = _new_roll(candidate, ours=True)
            return target._replace(note=(
                f"{why}: this {what} starts a new roll, "
                f"rolls/{candidate.name}. {target.note}"))
        if there == "strips":
            target = _next_strip(candidate, dry=dry, ours=True)
            return target._replace(note=(
                f"{why}: this {what} goes into rolls/{candidate.name}, a roll "
                f"of strips already. {target.note}"))
    raise ValueError(f"no free name beside {folder.name} in {rolls}")


def _new_roll(folder: Path, *, ours: bool) -> RollTarget:
    """Strip 1 of a roll not made yet, in ``folder``."""
    offer = plan_next(folder)
    plan = offer.next
    return RollTarget(
        "new roll", folder, folder, plan, offer,
        note=(f"Into {folder.name}/{plan.folder.name}: strip 1 of a new roll, "
              f"{folder.name}. Its frames are numbered from 1; the next strip "
              "walked under this name is strip 2, numbered on from this "
              "one's last frame."),
        ours=ours)


def _next_strip(roll: Path, *, dry: bool, ours: bool) -> RollTarget:
    """The next strip of the roll of strips ``roll``."""
    offer = plan_next(roll)
    return RollTarget("next strip", roll, roll, offer.next, offer,
                      note=next_strip_said(offer.next, dry=dry), ours=ours)


def next_strip_said(plan: StripPlan, *, dry: bool) -> str:
    """The strip ``plan`` claims, as the question that starts it says it:
    its folder, its numbers, what claiming it does to the strip before it,
    and the full roll's warning."""
    roll = plan.roll_folder
    into = f"{roll.name}/{plan.folder.name}"
    if plan.reuse:
        said = (f"Into {into}: strip {plan.number} of {plan.label} again, "
                f"numbered from {plan.first} -- nothing was recorded under it.")
    elif dry:
        said = (f"Into {into}: strip {plan.number} of {plan.label}, numbered "
                f"from {plan.first}.")
    else:
        said = (f"Scan strip {plan.number} of {plan.label} (new), numbered "
                f"from {plan.first}, without walking it first -- into {into}.")
    if plan.closes is not None:
        said += " " + closing_said(plan.closes, plan)
    for warning in plan.warnings:
        said += "\n\n" + warning
    return said


def closing_said(closes: StripCount, plan: StripPlan) -> str:
    """What claiming ``plan`` does to the open strip before it: closed at
    the frames it had, its numbers fixed for good."""
    if closes.complete:
        return (f"Strip {closes.strip} keeps its "
                f"{counted(closes.count, 'frame')} ({closes.numbers()}), "
                f"{closes.how}.")
    return (f"Strip {closes.strip}'s {closes.how}, so it counts as "
            f"{counted(closes.count, 'frame')} ({closes.numbers()}) and strip "
            f"{plan.number} is numbered from {plan.first}; a frame of strip "
            f"{closes.strip} walked later gets a number after the roll's "
            "last.")


def strip_folder(roll, number: int) -> Path | None:
    """Strip ``number`` of the roll of strips ``roll``, by the folder its
    index lists it in; None where it lists none, or cannot be read."""
    for listed in library.indexed_strips(roll) or ():
        if listed.get("strip") == number and listed.get("folder"):
            return Path(roll) / str(listed["folder"])
    return None


def strip_line(rolls, typed: str, *, own: str | None = None,
               last_roll: str | None = None) -> tuple[str, str | None]:
    """The Roll panel's line -- what a walk from the Roll button would be --
    and the roll its Continue button would put back, or None for none.

    Continue is offered with the box empty, for the roll of strips walked
    last, or given a strip by a roll scanned without a walk (``last_roll``,
    kept in the settings file) -- never one only opened: the box itself is
    not kept across a restart, so nothing is filed under yesterday's roll
    unless he presses it (Stefan's call, 2026-10-10)."""
    rolls = Path(rolls)
    typed = (typed or "").strip()
    if not typed:
        line = "a new roll: frames from 1"
        folder = rolls / last_roll if last_roll else None
        if folder is None or library.roll_kind(folder) != "strips":
            return line, None
        try:
            offer = plan_next(folder)
        except ValueError:
            return line, None
        said = offer.said
        head = f"{offer.next.label}: "
        said = said[len(head):] if said.startswith(head) else said
        return f"a new roll -- or Continue {folder.name}: {said}", folder.name
    try:
        target = roll_target(rolls, typed, own=own, dry=True)
    except ValueError as exc:
        return f"{typed}: {exc}", None
    folder = roll_dir(rolls, typed)
    if library.roll_kind(folder) == "legacy":
        # A dated name is the second the walk starts, not this one: named
        # only where it is a name, `<name>-2`.
        dated = bool(own) and typed == own
        return (f"{folder.name}: one strip from before rolls held strips -- a "
                "walk starts a new roll"
                + ("" if dated else f", {target.roll.name}")), None
    if _folder_key(target.roll) != _folder_key(folder):
        return (f"{folder.name} is no roll of strips -- a walk goes into "
                f"{target.roll.name}"), None
    if target.kind == "new roll":
        return f"{target.roll.name}: a new roll, frames from 1", None
    assert target.offer is not None
    return target.offer.said, None


def shown_number(strip: StripPlace | None, place: int, given: int = 0, *,
                 scan: bool = False) -> int | None:
    """The number frame ``place`` of a strip has across its roll, as the
    sheet shows it: the one its record carries (``given``), else its
    place's inside the strip's range or past "Strip ends here", else an
    extra it was given. None for a place past a closed range not numbered
    yet: it is given the number after the roll's last when it is scanned
    (`session.StripPlace.number`) -- as is one past "Strip ends here" on a
    closed strip, for a scan (``scan``). The place itself for a roll from
    before strips. Never gives a number: shown only."""
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


def ticked_again_said(strip: StripPlace | None, places) -> str:
    """Frames already scanned that a reopened sheet comes back ticked, as
    `merge_sheet` keeps a tick made after a frame was filed: ``frame 2 is
    ticked again, to be scanned again``."""
    places = sorted(int(p) for p in places)
    one = len(places) == 1
    return (f"{'frame' if one else 'frames'} {places_said(strip, places)} "
            f"{'is' if one else 'are'} ticked again, to be scanned again")


def strip_to_open(roll, library_root=None) -> Path | None:
    """The strip of a roll of strips that opening the roll opens: the first
    with frames left to scan, by number, and otherwise the last that holds a
    walk or a roll; None where none here does."""
    held = [s for s in library.strips_in(roll)
            if roll_summary(s, library_root=library_root) is not None]
    for strip in held:
        summary = roll_summary(strip, library_root=library_root)
        if summary is not None and unfinished(summary):
            return strip
    return held[-1] if held else None


def sheet_title(strip: StripPlace | None, places) -> str:
    """The contact sheet's title: "Contact sheet", and for a strip of a roll
    of strips which, with the numbers its frames have across the roll."""
    if strip is None:
        return "Contact sheet"
    # Not the stub past "Strip ends here": no frame of the film.
    numbers = [n for n in (shown_number(strip, p) for p in places
                           if p and not strip.past_its_end(p))
               if n is not None]
    span = (f" ({'frame' if len(numbers) == 1 else 'frames'} "
            f"{number_spans(numbers)})" if numbers else "")
    return f"Contact sheet -- {strip.label}, strip {strip.strip}{span}"


def done_in(folder, library_root=None, live=None) -> set[int]:
    """The frames a roll folder's roll.json says are scanned, on the strip.

    Empty for a folder with none, one that cannot be read, or one numbered
    before its numbers were places on the strip -- `read_survey` renumbers
    those, and a set in the wrong numbering would skip the wrong frames.

    Less each whose entry went to the trash and is not back in the library
    at ``library_root`` (`session.scanned_frames`): finishing the roll
    offers it again.

    ``live`` is the session's `live_manifest`: the roll as the session holds
    it, ahead of the file while frames are still being filed, and each
    frame still awaiting its filing (`pending_in`) counted with the done.
    Read from the file alone, the last frame of a roll that had just ended
    -- filed plain after it, while the next press was made -- read as not
    done, and was neither skipped nor named, but scanned again (RES-n8).
    """
    progress = held_now(Path(folder) / "roll.json", live)
    if progress.get("numbering") != NUMBERING:
        return set()
    return scanned_frames(progress, library_root) | pending_in(folder, live)


def held_now(path, live=None) -> dict:
    """A roll folder's survey.json or roll.json as it stands: through the
    session's manifest of it (``live``, its `live_manifest`) where that is
    ahead of the file, and read otherwise; ``{}`` where there is none, or it
    cannot be read."""
    manifest = live(Path(path)) if live is not None else None
    held = manifest.snapshot() if manifest is not None else None
    if held is not None:
        return held
    try:
        return read_manifest(path)
    except ValueError:
        return {}


def pending_in(folder, live=None) -> set[int]:
    """The frames of a roll the session is still filing: recorded, and
    awaiting the writer's answer (`RollManifest.awaits`). Neither taken
    again nor passed over in silence: a question names them, and the roll
    asked for leaves them out. Empty without the session's ``live``."""
    if live is None:
        return set()
    manifest = live(Path(folder) / "roll.json")
    if manifest is None:
        return set()
    held = manifest.snapshot()
    if held is None:
        return set()
    out = set()
    for record in held.get("frames") or ():
        try:
            number = int(record["number"])
        except (KeyError, TypeError, ValueError):
            continue
        if manifest.awaits(number):
            out.add(number)
    return out


def roll_holds(manifest: dict) -> dict[int, dict]:
    """How a roll held each frame it scanned, from its manifest, by frame number.

    The record is the hold's own (`_hold_to_approved`), filed under the
    frame's ``registration.approved`` -- the latest attempt's, since a frame
    scanned again replaces its record. None for a frame that errored: it was
    not scanned, and is offered again (`scanned_frames`). What the contact
    sheet's badges are drawn from.
    """
    out: dict[int, dict] = {}
    for record in manifest.get("frames") or ():
        try:
            number = int(record["number"])
        except (KeyError, TypeError, ValueError):
            continue
        marks = record.get("registration")
        held = marks.get("approved") if isinstance(marks, dict) else None
        if record.get("error") or not isinstance(held, dict) or not held.get("outcome"):
            continue
        out[number] = held
    return out


def holds_in(folder, kept=None) -> dict[int, dict]:
    """``kept`` -- how a roll held its frames when it was opened -- under
    what ``folder``'s roll.json says now.

    The roll.json decides for every frame it has a record of: the hold of
    that frame's latest take (`roll_holds`), or none where the take was not
    held or failed. The session keeps one record per frame, so the older
    take's hold is gone from the file and a reopened roll shows no badge for
    it; laid over ``kept`` instead, a frame taken again unheld went on saying
    "N units out" until the window was closed. ``kept`` answers for the
    frames the file does not record, and for all of them where it cannot be
    read on the strip's numbering, as `done_in` reads it.
    """
    out = dict(kept or {})
    try:
        progress = read_manifest(Path(folder) / "roll.json")
    except ValueError:
        return out
    if progress.get("numbering") != NUMBERING:
        return out
    for record in progress.get("frames") or ():
        try:
            out.pop(int(record["number"]), None)
        except (KeyError, TypeError, ValueError):
            continue
    out.update(roll_holds(progress))
    return out


def snap_offset(millimetres: float) -> float:
    """The nearest position the transport can actually reach.

    A number finer than the hardware is a lie. The reachable set starts at one
    SLIDE command and steps by param, so there is nothing at all between zero
    and `FINE_STEP_MM` -- showing an operator "+1.3 units" invites him to aim at
    a place that does not exist. Clamped to what one command moves, which is
    `MAX_TRAVEL_MM`, so the planner is never asked for a distance it would
    refuse.
    """
    want = float(millimetres)
    if not math.isfinite(want):
        # `min(M, nan)` is M, so a NaN -- a hand-edited file, a detector that
        # measured nothing -- became the largest forward move there is.
        return 0.0
    want = max(-MAX_TRAVEL_MM, min(MAX_TRAVEL_MM, want))
    sign = -1.0 if want < 0 else 1.0
    try:
        plan = plan_nudges(want)
    except ValueError:
        plan = []
    # The result has to be re-plannable, or the adjuster stores a number the
    # mover would later refuse. The largest command travels slightly more
    # than the nominal maximum, so the top of the range can snap to a value
    # just past what the planner accepts back. Drop a step until it survives
    # the round trip.
    while plan:
        value = sign * abs(sum(plan))
        try:
            plan_nudges(value)
        except ValueError:
            plan = plan[:-1]
            continue
        return value
    return 0.0


#: What one press of an arrow moves, as the operator may choose. Each rung is
#: one integer `param`, so each is exactly one command -- no rung can surprise
#: him with a chain, and the label is what that command travels.
#:
#: param 1 is the finest move that exists: `param 0` was sent on 2026-09-22,
#: five times, and is accepted and does nothing, so there is no rung beneath
#: this one. param 20 is where `docs/protocol.md` section 5 says the law begins
#: to bend, and it became a single command when the cap went to 87.
#:
#: "finest" is not a distance at all: it walks to the next position the
#: transport can reach, which is not a constant -- the lattice is 2.84 units
#: off zero (`param 1`, with the command's ramp) and 1.0 everywhere above it.
ADJUST_PARAMS = {"small": 3, "medium": 8, "large": 20}
ADJUST_STEPS = ("finest",) + tuple(
    f"{name} ({units_for_param(param):.1f} units)"
    for name, param in ADJUST_PARAMS.items())


def step_millimetres(choice: str) -> float:
    """The chosen step as a distance, or 0.0 meaning "the next one along".

    Looked up by name rather than parsed out of the label. The label now leads
    with a word, and the parser this replaces read the first token as a number
    -- which would have returned 0.0 for every rung, and 0.0 is "finest", so
    every step would have quietly become the smallest one.
    """
    name = str(choice).split()[0] if str(choice).strip() else ""
    param = ADJUST_PARAMS.get(name)
    if param is None:
        return 0.0
    return MM_PER_UNIT * param + MM_PER_COMMAND


def fine_preview(text: str) -> str:
    """What the typed fine adjustment would actually send, in words.

    The operator types a distance and the transport delivers the nearest
    command to it; the difference between those two is exactly what the window
    never showed him. Built on the planner rather than on a copy of its
    arithmetic, so the preview and the move cannot disagree.

    Module level and free of Tk on purpose: it is the part that has to be
    right, and that is where this file keeps such things.
    """
    values = _numbers(text)
    if not values:
        return (f"{units(FINE_STEP_MM):.1f}-{units(MAX_TRAVEL_MM):.0f}"
                if str(text).strip() else "")
    millimetres = abs(values[0]) * MM_PER_UNIT
    if deliverable_mm(millimetres) == 0:
        return f"under {units(FINE_STEP_MM):.1f} -- the film would not move"
    try:
        plan = plan_nudges(millimetres)
    except ValueError:
        return f"past {units(MAX_TRAVEL_MM):.0f} -- use the slide buttons"
    if millimetres > MAX_TRAVEL_MM:
        return f"past {units(MAX_TRAVEL_MM):.0f} -- use the slide buttons"
    sent = sum(plan)
    said = say_command(sent)
    short = units(sent - millimetres)
    if abs(short) >= 0.05:
        said += f", {short:+.1f} off"
    return said


def step_offset(current: float, direction: int, step_mm: float = 0.0) -> float:
    """Where one press of an arrow should put the frame.

    `step_mm` of zero means the finest move there is: the adjacent position on
    the transport's own lattice. That is not a fixed distance and cannot be
    written as one. Off zero the first reachable place is one whole SLIDE
    command away -- `FINE_STEP_MM`, and nothing exists below it, because
    `param 0` was sent to the scanner on 2026-09-22 and is accepted and does
    nothing -- while above that the positions are `STEP_MM` apart, since a
    command's distance grows by one param at a time. Adding a constant and
    snapping gets this wrong at both ends: the first step's distance steps over
    two thirds of the reachable positions, and one param's rounds to nothing at
    all and the frame never moves.

    So the finest step is found rather than computed -- probe outward until
    the snapped answer changes. It is a handful of arithmetic per keypress and
    it cannot disagree with `snap_offset` about what is reachable, which a
    second copy of the lattice would eventually do.
    """
    here = snap_offset(current)
    if step_mm > 0:
        return snap_offset(here + direction * step_mm)
    probe = FINEST_PROBE_MM
    want = here
    # Enough to cross the widest gap in the lattice -- the first command off
    # zero -- several times over.
    for _ in range(64):
        want += direction * probe
        if abs(want) > MAX_TRAVEL_MM:
            break
        landed = snap_offset(want)
        if abs(landed - here) > 1e-9:
            return landed
    return here


def picture_of(result) -> tuple | None:
    """What photograph a pass is of, as far as its arrangement is concerned.

    A roll numbers its frames, and that is the best answer: it survives the
    film being moved and put back. Otherwise the transport position is what
    there is -- it is already how `_add_result` decides that a scan stands in
    for a prescan, so orientation keyed the same way cannot disagree with it.

    None for a pass belonging to no identifiable picture, which is not an
    error: it simply follows whatever was last set.
    """
    if getattr(result, "number", 0):
        return ("frame", result.number)
    position = getattr(result, "position", None)
    if position is not None:
        return ("at", position)
    return None


def _snap_proposals(offsets: dict, notes: dict) -> tuple[dict, dict]:
    """The detector's positions, each on a place the film can actually reach.

    Snapped here rather than where the records are built, so that every
    reader of `offsets` sees a position the film can actually reach. The
    caption used to show the raw proposal and the commission used to deliver
    the snapped one, so a frame captioned as moving could be delivered as no
    move at all -- and five other readers carried numbers that do not exist.
    """
    notes = {int(n): dict(v) for n, v in notes.items()}
    out = {}
    for number, value in offsets.items():
        landed = snap_offset(value)
        if landed:
            out[int(number)] = landed
        else:
            # Below one command. Not the same as unreadable: the detector saw
            # it and it is already as close as the transport can put it, which
            # is what the driver calls `in_place`. Keep the note, drop the move.
            note = dict(notes.get(int(number)) or {})
            note["in_place"] = True
            notes[int(number)] = note
    return out, notes


def _merge_kept(out: dict, notes: dict, kept: dict, remembered=None) -> tuple[dict, dict]:
    """His positions over the detector's: ``out`` and ``notes`` changed in place.

    A kept position whose recorded source is the machine's is dropped rather
    than kept, so today's reading replaces it: every number on the sheet that
    is not his is today's detector's. His is left exactly as he left it -- the
    sheet is where he corrects the detector, so overwriting what he set would
    undo the correction it exists to collect. `remembered` says who decided
    each kept position; a kept offset it says nothing about is his, which is
    what an offset meant before the sheet proposed any.
    """
    known = remembered or {}
    # His "as surveyed" as well, which a sheet saved before zeros were kept
    # recorded as his with no position at all.
    theirs = {int(n) for n, source in known.items() if source == "operator"}
    kept = dict(kept)
    for n in theirs:
        if all(int(k) != n for k in kept):
            kept[n] = 0.0
    for number, value in kept.items():
        n = int(number)
        fresh = notes.get(n) or {}
        if known.get(n) in MACHINE_SOURCES:
            # A machine position surviving a reopen is read again rather than
            # replayed: a remembered number is only as good as the detector
            # that made it, and one an older detector left in the settings or
            # in `approved.json` would otherwise hide today's reading --
            # its position on the sheet and its edge lines both.
            continue
        out[n] = value                      # his, over anything measured here
        # His position, and still the detector's reading of the frame: the
        # edge lines are drawn whoever decided where the frame goes.
        notes[n] = {"source": "operator", "reason": "you set this one",
                    "edges": fresh.get("edges"), "width": fresh.get("width")}
    return out, notes


#: The detector's words for how it read a frame, as they appear in a caption.
#: `tools/frame_edges.propose_centred` produces these -- the same words
#: `framing.propose_offsets` used, so a sheet saved by either reads the same.
MACHINE_SOURCES = ("measured", "unconfirmed", "neighbours")


def sheet_counts(state: dict) -> dict[str, int]:
    """How many of a sheet's frames he decided something about, as the log
    says it: ``positioned``, ``turned`` and ``flipped``.

    Decisions, not entries. Every frame commissioned is recorded with its
    turn and mirror, zeros and falses included -- zero is a decision there
    (`_ContactSheet.rotations`) -- and the sheet keeps the detector's
    proposals beside his positions; counted by entry, a walk nobody had
    touched reopened "6 already turned, 6 flipped" and "kept 4 positioned"
    (B2-T1). Positioned by `_merge_kept`'s own rule, so the count and the
    captions saying "you set this one" agree: a position whose source is no
    detector's, and his "as surveyed", which is a source with no position.
    """
    offsets = state.get("offsets") or {}
    sources = {int(n): str(s) for n, s in (state.get("sources") or {}).items()}
    mine = {int(n) for n in offsets if sources.get(int(n)) not in MACHINE_SOURCES}
    mine |= {n for n, source in sources.items()
             if source == "operator" and all(int(k) != n for k in offsets)}
    return {
        "positioned": len(mine),
        "turned": sum(1 for t in (state.get("rotations") or {}).values()
                      if int(t) % 360),
        "flipped": sum(1 for f in (state.get("flips") or {}).values() if f),
    }


def counts_said(counts: dict[str, int], kinds=("positioned", "turned",
                                               "flipped")) -> str:
    """`sheet_counts`, said: ``2 positioned, 1 turned`` -- the kinds with
    none left out, "" where there are none at all."""
    return ", ".join(f"{counts[kind]} {kind}" for kind in kinds
                     if counts.get(kind))


def frame_ends(read: dict | None) -> tuple[float, float, bool] | None:
    """Both ends of the frame in its prescan's columns: ``(left, right, inferred)``.

    Where the detector read only one edge, the other is placed the measured
    frame width away from it (`frame_edges.frame_columns`, 435.6 columns at
    300 dpi) and ``inferred`` is True. None where no edge was read.
    """
    if not read or not read.get("width"):
        return None
    width = int(read["width"])
    edges = read.get("edges") or {}

    def at(side):
        e = edges.get(side) or {}
        return float(e["x"]) if e.get("state") == "edge" and e.get("x") is not None else None

    left, right = at("left"), at("right")
    frame = frame_edges.frame_columns(width)
    if left is not None and right is not None:
        return left, right, False
    if left is not None:
        return left, left + frame, True
    if right is not None:
        return right - frame, right, True
    return None


def frame_overhang(read: dict | None, offset_mm: float) -> tuple[float, float] | None:
    """How much picture lies past each orange guide once the film has moved.

    ``(left, right)`` in units. Positive is picture beyond the scan's edge,
    which the scan will not take; negative is unexposed base showing inside
    it. The guides are where the scan cuts (`framing.scan_window`), not the
    prescan's own edges: a scan takes a little more of the transport than the
    428 columns a prescan comes back with, and more of it on the right. So a
    frame centred on its prescan reads about +2.8 on the left and +1.0 on the
    right (`centred_overhang`) -- the red line sits just outside the orange one
    on purpose. The shift is the big view's own: ``offset_mm`` over the
    aperture, in the prescan's columns, as `measure_shift_mm` checks it.
    """
    ends = frame_ends(read)
    if ends is None:
        return None
    left, right, _ = ends
    width = int(read["width"])
    shift = offset_mm / APERTURE_MM * width
    per = units_per_column(width)
    cut_left, cut_right = scan_window(width)
    return ((cut_left - (left + shift)) * per, (right + shift - cut_right) * per)


def scan_reach_units(width: float = PRESCAN_COLUMNS) -> tuple[float, float]:
    """How far a scan takes past each edge of a prescan this wide, in units."""
    cut_left, cut_right = scan_window(width)
    per = units_per_column(width)
    return -cut_left * per, (cut_right - width) * per


def centred_overhang(width: float = PRESCAN_COLUMNS) -> tuple[float, float]:
    """What a frame centred on its prescan has past each of the scan's edges, in units.

    Worked out rather than typed, because the help text says it and the
    guides it describes have moved once already: at the prescan's own edges a
    centred frame overhung both by about 3 units. The frame is
    `FRAME_WIDTH_UNITS` wide and the scan's window about 4 units narrower, and
    that window is not centred on the prescan -- so the frame overhangs the
    left guide by about 2.8 units and the right by about 1.0.
    """
    per = units_per_column(width)
    beyond = (FRAME_WIDTH_UNITS / per - width) / 2.0      # columns, each side
    cut_left, cut_right = scan_window(width)
    return (cut_left + beyond) * per, (width + beyond - cut_right) * per


def overhang_note(overhang: tuple[float, float] | None) -> str:
    """The big view's word on where the frame's ends sit against the guides."""
    if overhang is None:
        return ""

    def one(side: str, value: float) -> str:
        if value >= 0:
            return f"{value:.1f} past the {side}"
        return f"{-value:.1f} of base showing on the {side}"

    left, right = overhang
    return f"   \u00b7   picture {one('left', left)}, {one('right', right)} (units)"


def read_note(read: dict | None) -> str:
    """The caption's word on which way the carriage read a pass, when it matters.

    Nothing for an ordinary top-down pass. A pass read bottom-up was turned
    upright in the decode, and says so rather than silently -- it is the case
    that used to arrive upside down. An unknown one is shown as it came.
    """
    state = (read or {}).get("direction")
    if state == "reversed":
        return "   ·   read bottom-up, turned upright"
    if state == "unknown":
        return "   ·   which way it was read is unknown; shown as it came"
    return ""


def frame_status(done: int, total: int | None) -> str:
    """The header's middle while a roll runs: the frame it is on, of how many.

    Counted within this roll -- frame 1 is the first it takes, wherever on the
    strip that is -- the way the roll line under the picture counts.
    """
    if total:
        return f"frame {min(done + 1, total)} of {total}"
    return f"frame {done + 1}"


def edges_label(progress) -> str:
    """What sits beside the frame-edge light: how many frames it has read.

    ``36/36`` with the light still blue is the walk read once and being read
    again against its whole self, which it can only be once it has ended.
    """
    if progress.state == frame_edges.IDLE:
        return "frame edges"
    if progress.state == frame_edges.SKIPPED:
        return f"frame edges: not read on {progress.film}"
    said = f"frame edges {progress.done}/{progress.total}"
    if progress.state == frame_edges.FAILED:
        said += " -- failed on some"
    return said


def proposals_said(notes: dict) -> str:
    """One log line for what the detector made of a walk, by source."""
    counted = ", ".join(
        f"{n} {label}" for label, n in (
            (label, sum(1 for v in notes.values() if v.get("source") == source))
            for source, label in (("measured", "measured"),
                                  ("unconfirmed", "unconfirmed"),
                                  ("neighbours", "from neighbours"),
                                  ("none", "not placed")))
        if n)
    if not counted:
        return ""
    return f"frame edges read on {len(notes)} frame(s): {counted}"


def axis_mark(fraction: float, degrees: int = 0, flipped: bool = False) -> tuple[str, float]:
    """Where a point along the transport axis lands on a thumbnail turned this way.

    ``fraction`` is how far across the prescan *as scanned* the point is: 0 its
    left edge, 1 its right. Returns ``("x", f)`` for a vertical line ``f`` of the
    way across the thumbnail, or ``("y", f)`` for a horizontal one ``f`` of the
    way down -- which is what a quarter turn makes of it. The same order as
    `preview.orient`: mirrored first, then turned clockwise, so a line drawn
    over a turned cell sits on the pixels it was measured on.
    """
    f = 1.0 - fraction if flipped else fraction
    turn = int(degrees) % 360
    if turn == 0:
        return "x", f
    if turn == 90:
        return "y", f
    if turn == 180:
        return "x", 1.0 - f
    return "y", 1.0 - f


#: The frame-edge detector's line on a thumbnail: red, and dotted so the base
#: strip it marks still shows between the dots.
EDGE_RGB = (224, 48, 42)
EDGE_DOT, EDGE_GAP, EDGE_PX = 4, 3, 2


def paint_edges(arr: np.ndarray, read: dict | None, degrees: int = 0,
                flipped: bool = False) -> np.ndarray:
    """A thumbnail with the detector's edges painted in, as red dotted lines.

    ``arr`` is the thumbnail as drawn -- already turned and mirrored -- and
    ``read`` the detector's note for the frame (``edges`` per side, ``width``
    of the prescan it read them on). Painted into the pixels rather than laid
    over them, so the lines are there wherever the thumbnail is, in whichever
    way the cell is turned (`axis_mark`). Sides where the picture runs to the
    border draw nothing.
    """
    if not read or not read.get("width"):
        return arr
    out = arr.copy()
    h, w = out.shape[:2]
    cols = float(read["width"])
    for side in ("left", "right"):
        edge = ((read.get("edges") or {}).get(side)) or {}
        if edge.get("state") != "edge" or edge.get("x") is None:
            continue
        axis, f = axis_mark(float(edge["x"]) / cols, degrees, flipped)
        if axis == "x":
            c = int(np.clip(round(f * w) - EDGE_PX // 2, 0, w - EDGE_PX))
            for y in range(0, h, EDGE_DOT + EDGE_GAP):
                out[y:y + EDGE_DOT, c:c + EDGE_PX] = EDGE_RGB
        else:
            r = int(np.clip(round(f * h) - EDGE_PX // 2, 0, h - EDGE_PX))
            for x in range(0, w, EDGE_DOT + EDGE_GAP):
                out[r:r + EDGE_PX, x:x + EDGE_DOT] = EDGE_RGB
    return out


def frame_caption(offset, source, done=False, contrast=0.0, read=False):
    """One cell's line under the picture, and the colour to write it in.

    Module level and free of Tk because it is the part that has to be right,
    and because it had no tests at all while carrying four separate mistakes.

    `done` wins the colour. On a resumed roll the marker that has to survive is
    the one that stops three hours of transport being spent twice -- and since
    the sheet began proposing a position for every frame it can read, almost
    every scanned frame had an offset, so almost every one lost its marker. The
    position still prints beside it, because a frame re-ticked through the
    documented escape hatch needs to show where it is going.

    `read` says the detector saw the frame and found it already in place. That
    is not the same as nothing being able to read it, and the driver makes the
    same distinction -- so dropping an unreachable proposal must not also throw
    away the fact that it was measured.
    """
    if source in MACHINE_SOURCES:
        said = f"{say_units(offset)} ({source})" if offset else f"in place ({source})"
    elif offset:
        said = f"moved {say_units(offset)}"
    elif source == "operator":
        # His "as surveyed". It read "contrast 0.42", the same as a frame
        # nobody had touched, so the one decision that keeps the detector off
        # a frame could not be seen on the sheet.
        said = "as walked (yours)"
    elif read:
        said = "in place"
    else:
        said = ""
    if done:
        return (f"scanned - {said}" if said else "scanned"), "DONE"
    if said:
        return said, "CHOSEN"
    return f"contrast {contrast:.2f}", "GREY"


def _aim_note(marks: dict) -> str:
    """What the walk did about this frame's position, in the operator's words.

    Corrected frames say so, refused ones say why. The distinction is the
    point: a detector that cannot see a frame and one that has checked it are
    the same silence otherwise, and telling them apart is what `gap_edges`
    made impossible by answering "registered" for both.
    """
    fix = (marks or {}).get("correction")
    if not fix:
        return ""
    outcome = fix.get("outcome")
    aimed = fix.get("decision_mm")
    if outcome == "held" and aimed is not None:
        return f"   ·   aimed {say_units(aimed)}"
    if outcome == "in_place":
        return "   ·   in place"
    if outcome == "dry_run" and aimed is not None:
        return f"   ·   would aim {say_units(aimed)}"
    # Every way the loop that delivers an aim can end short of it: the hold's
    # own outcomes, since an aim is delivered by the hold (`_aim_frame`). One
    # missing here fell to the line below and said "not aimed" and no more,
    # because only a refusal carries a reason.
    if outcome in ("not_converged", "abandoned", "budget", "stopped",
                   "no_effect", "diverging", "would_reverse", "unverified",
                   "wrong_way"):
        return f"   ·   not aimed ({outcome.replace('_', ' ')})"
    reason = (fix.get("reason") or "").split(";")[0].split(" -- ")[0]
    return f"   ·   not aimed{f': {reason}' if reason else ''}"


#: The two ways a roll can leave a frame off the position it was held to,
#: as the filmstrip and the contact sheet mark them -- Stefan's choice on
#: 2026-10-05: a badge on the frame, and a list at the end of the roll.
#: `MISSED` is a frame the hold's last look measured, and found away from the
#: position; `UNVERIFIED` one no look could place -- the match refused,
#: holding off for the rest of the roll, or the hold cut short. Ids without a
#: space, because they are also canvas tags.
MISSED, UNVERIFIED = "missed", "unverified"

#: Their badges, ``(fill, ink)``: the window's amber for a miss it can put a
#: number on, and its red for one it cannot. Small, in a corner: the frames
#: are filed and usable, and this says which to look at first.
HOLD_BADGE = {MISSED: ("#e8b64c", "#1e1e1e"), UNVERIFIED: ("#e0605a", "#1e1e1e")}


def hold_of(result) -> dict | None:
    """The roll's hold record for this pass (`_hold_to_approved`), if one ran.

    None for a walk's prescan (``walked``), held or not. What is marked is a
    frame a roll scanned, which is all the contact sheet's badges know
    (`roll_holds`). A walk `tools/scan_roll.py` held -- ``--dry-run`` with
    approved positions -- comes back from its survey.json with each hold, and
    the filmstrip marked those prescans "not placed", as if a roll had missed
    them, while the sheet built from the same pictures marked none.
    """
    if getattr(result, "walked", False):
        return None
    held = (getattr(result, "registration", None) or {}).get("approved")
    return held if isinstance(held, dict) and held.get("outcome") else None


def hold_out_mm(held: dict | None) -> float | None:
    """How far from its position the hold's last look found the frame, or None.

    The record's own ``residual_mm``, else its target less where the frame
    ended up. None where nothing measured where it ended up.
    """
    if not held:
        return None
    if held.get("residual_mm") is not None:
        return float(held["residual_mm"])
    if held.get("final_mm") is not None and held.get("target_mm") is not None:
        return float(held["target_mm"]) - float(held["final_mm"])
    return None


def hold_level(held: dict | None) -> str | None:
    """`MISSED` or `UNVERIFIED` for a frame the hold did not place, else None.

    None too where there is no record: a roll with no approved positions, a
    single scan, and a walk's prescan, held or not (`hold_of`).
    """
    if not held or held.get("outcome") in (None, "held"):
        return None
    return MISSED if hold_out_mm(held) is not None else UNVERIFIED


def hold_words(held: dict) -> str:
    """What a hold did, for a person: `framing.say_hold`'s words, as the
    tools say them.

    A hold a failed pass broke into keeps the step it had reached, ``move``,
    which `framing.HOLD_SAID` has words for now. The window turned it into
    ``stopped`` -- "was interrupted", a Stop's words, though a Stop no
    longer cuts a hold short -- while `tools/scan_roll.py` printed the bare
    code.
    """
    return say_hold(held.get("outcome"))


def hold_note(held: dict | None) -> str:
    """The caption's word on how a frame was held: '' where it was not."""
    if not held:
        return ""
    if held.get("outcome") == "held":
        moves = int(held.get("moves") or 0)
        return (f"held after {counted(moves, 'move')}" if moves
                else "held where it arrived")
    said = f"not placed: {hold_words(held)}"
    out = hold_out_mm(held)
    if out is not None:
        said += f" ({say_units(out, signed=False)} out)"
    return said


def hold_badge(held: dict | None) -> str:
    """The few words on a frame's badge, '' for a frame that needs none."""
    level = hold_level(held)
    if level == MISSED:
        return f"{abs(units(hold_out_mm(held) or 0.0)):.0f} units out"
    if level == UNVERIFIED:
        return "not verified"
    return ""


def _arrangement(result) -> str:
    """How a pass is arranged, in words, for a caption or a line in the log.

    One phrasing, so the caption over the picture and the log line underneath
    it cannot describe the same frame two different ways.
    """
    parts = [f"{result.rotation}\u00b0"] if result.rotation else []
    if getattr(result, "flipped", False):
        parts.append("flipped")
    return ", ".join(parts) or "as the scanner sent it"


def approved_from_sheet(frames, ticks, offsets, sources=None) -> tuple:
    """The `Approved` records for the ticked frames, in frame order.

    Every ticked frame gets one, including those left at zero: an untouched
    frame still carries "leave it where I saw it, and here is the picture I saw"
    -- which is what lets the scan check it rather than assume. `frames` is the
    surveyed results, `ticks` the numbers chosen, `offsets` the adjustments
    made, keyed by frame number.

    The orientation is read off each result rather than passed in, because
    `result.rotation` is the one place that always knows it: a frame turned in
    the sheet and a frame merely following the session default both carry it,
    and both have to reach the file. A separate dictionary of turns could only
    describe the first kind, and an absent entry would then mean "follow the
    session" -- which is wrong the moment the session default moves under a
    frame somebody deliberately straightened.

    A turn on an unticked frame goes nowhere, which is right: there is no file
    for it to reach. It is the same thing that happens to that frame's offset.

    `sources` says where each number came from -- the sheet's own per-frame
    note, keyed by frame number. Defaulted, so the records still build without
    it; a frame with no note is `none`, since nothing has decided it -- not
    `operator`, which is what an approval meant before the sheet pre-filled a
    position for every frame it could read, and which then claimed his
    decision for every frame the reader had not reached yet. It is
    carried so the driver's log can say `measured` where a detector decided,
    which is what `_hold_to_approved`'s `source` exists for.
    """
    picked = set(ticks)
    labels = sources or {}
    out = []
    for result in frames:
        number = getattr(result, "number", None)
        if number is None or number not in picked:
            continue
        out.append(Approved(
            number=number,
            offset_mm=snap_offset(offsets.get(number, 0.0)),
            rotation=int(getattr(result, "rotation", 0) or 0) % 360,
            flipped=bool(getattr(result, "flipped", False)),
            reference=getattr(result, "image", None),
            # str, not the Path the GUI carries: Approved declares a str,
            # and a Path here reaches json.dumps in _write_approved and
            # raises -- which used to take the whole commission down with it.
            reference_entry=str(getattr(result, "entry", "") or ""),
            # "none" for a frame nothing has placed: not read yet and not
            # touched. It was "operator", so a roll commissioned while the
            # edge light was still blue logged his decision for frames he
            # never touched -- and filed their zeros as his "as surveyed".
            source=(labels.get(number) or {}).get("source") or "none",
        ))
    return tuple(out)


#: What a frame number means, for the dialogs that send a reopened roll back
#: to the scanner. The one thing the transport cannot check for itself is how
#: the strip was put in, and only the operator can see that.
STRIP_NUMBERS = (
    "Frame numbers are places on the strip, counted by the transport from "
    "where the strip went in: frame 1 is where its counter reads 0, and it "
    "has been seen resetting to 0 as a strip goes in. The roll finds each "
    "frame by that counter, so it is right as long as the strip is in the "
    "way it was when it was walked.")


def position_label(position: int | None) -> str:
    """The transport readout: which frame of the strip the film is on.

    One-based, like every other frame number the window shows. It showed the
    counter itself until 2026-09-23, so "frame position: 10" sat beside a roll
    that called the same picture frame 11 -- two numbering schemes on one
    screen for one piece of film.
    """
    if position is None:
        return "film on frame ?"
    return f"film on frame {position + 1}"


def chosen_span(numbers) -> tuple[int, int]:
    """Where a roll of the ticked frames starts, and how many frames it covers.

    The numbers are places on the strip -- the survey's recorded transport
    positions plus one, for a walk made now and, through `read_survey`, for
    one made before numbers meant that -- so the first of them is the frame
    the film has to go to, and the roll ends with the last.
    """
    chosen = sorted(int(n) for n in numbers)
    return chosen[0], chosen[-1] - chosen[0] + 1


#: The highest frame number the Roll panel accepts. A 36-exposure roll cut into
#: strips never gets near it; it is a guard against a typo, not a strip length.
LAST_FRAME = 100


def frame_range(first: str, last: str) -> tuple[int, int | None]:
    """The Roll panel's two boxes as a roll's ``start_at`` and ``frames``.

    Both ends are included, so ``1`` to ``20`` is twenty frames. An empty first
    frame is the first of the strip; an empty last frame is the end of it, and
    comes back as ``frames=None``, which is how a `Roll` already says "until the
    strip runs out". Raises `ValueError` with the sentence to show.
    """
    first, last = str(first).strip(), str(last).strip()
    try:
        start = int(first) if first else 1
    except ValueError:
        raise ValueError("First frame has to be a whole number.") from None
    if not 1 <= start <= LAST_FRAME:
        raise ValueError(f"First frame {start} is outside 1-{LAST_FRAME}.")
    if not last:
        return start, None
    try:
        end = int(last)
    except ValueError:
        raise ValueError("Last frame has to be a whole number, or empty for "
                         "the end of the strip.") from None
    if not start <= end <= LAST_FRAME:
        raise ValueError(f"Last frame {end} is outside {start}-{LAST_FRAME}: "
                         "it cannot come before the first.")
    return start, end - start + 1


def range_words(start_at: int, frames: int | None) -> str:
    """A roll's range the way the Roll panel puts it, for a sentence."""
    if frames is None:
        return f"every frame from frame {start_at} to the end of the strip"
    if frames == 1:
        return f"frame {start_at}"
    return f"frames {start_at} to {start_at + frames - 1}"


def number_spans(numbers) -> str:
    """Frame numbers as runs, ``1-10, 12``, so a sheet of 36 fits a sentence."""
    runs: list[list[int]] = []
    for n in sorted({int(n) for n in numbers}):
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in runs)


def counted(n: int, noun: str, plural: str | None = None) -> str:
    """``1 frame``, ``2 frames``: a count and its noun, agreeing."""
    return f"{n} {noun if n == 1 else (plural or noun + 's')}"


def is_are(n: int) -> str:
    """The verb that agrees with a count: "1 is", "2 are"."""
    return "is" if n == 1 else "are"


def rewalked(walked, start_at: int, frames: int | None) -> list[int]:
    """Which frames already on a sheet a walk of this range would take again."""
    end = None if frames is None else start_at + frames - 1
    return sorted(int(n) for n in walked
                  if int(n) >= start_at and (end is None or int(n) <= end))


def roll_estimate(per: float, frames: int | None, move_s: float) -> str:
    """The Roll dialog's time, for a count of frames or for the whole strip.

    With no count the roll runs until the strip does, and nothing here knows
    how long the strip is -- so it says the pace rather than a total. It used
    to multiply by six while the sentence above it said "every frame to the
    end of the strip": a figure for a strip of six, on any strip.
    """
    if frames:
        return f"Roughly {_duration(per * frames + move_s)}."
    getting_there = (f", plus about {_duration(move_s)} to reach the first "
                     "frame" if move_s else "")
    return (f"Roughly {_duration(per)} a frame{getting_there}, for as many "
            "frames as the strip holds -- there is no count to add up.")


def seek_note(here: int | None, start_at: int) -> tuple[str, float]:
    """What a roll will do to the film before its first frame, and its cost.

    ``here`` is the transport's counter as the window last heard it, 0-based;
    ``start_at`` the frame the roll starts on, 1-based. The roll asks the
    transport again itself before moving, so this is a forecast, and it says
    so when it cannot know.

    Only the forward moves are costed, at the measured `FORWARD_FRAME_S`. A
    move backwards has never been timed, and saying so is better than an
    estimate that looks measured and is not.

    A frame no strip has is refused by the seek before it reads or moves
    anything, so it is forecast as that -- nothing moves and it costs nothing
    -- rather than as a wind that never happens. The refusal itself stays the
    seek's; this only stops the forecast contradicting it.
    """
    if not plausible(start_at - 1):
        return (f"Frame {start_at} is past the {LAST_PLAUSIBLE_POSITION + 1} "
                "frames a strip is taken to have, so the roll will refuse it "
                "before it moves the film: nothing moves, nothing is scanned, "
                "and it costs nothing.", 0.0)
    if here is None:
        return ("The window has not heard where the film is, so the roll "
                f"asks the transport first and goes to frame {start_at} from "
                "there -- or refuses, and scans nothing, if it cannot tell.",
                0.0)
    target = start_at - 1
    # "Last said", because the scanner's own keys move the film without a word
    # over USB; the roll reads the counter again before it moves anything.
    if here == target:
        return (f"The transport last said the film is on frame {start_at}, so "
                "nothing moves before the first frame.", 0.0)
    if here > target:
        back = here - target
        return (f"The transport last said the film is on frame {here + 1}, so "
                f"it winds back {back} frame{'s' if back != 1 else ''} to "
                f"frame {start_at} first. A move backwards has never been "
                "timed, so that is not in the figure below.", 0.0)
    ahead = target - here
    cost = ahead * FORWARD_FRAME_S
    return (f"The transport last said the film is on frame {here + 1}, so it "
            f"advances {ahead} frame{'s' if ahead != 1 else ''} to frame "
            f"{start_at} first -- about {cost:.0f} s, at the "
            f"{FORWARD_FRAME_S:.1f} s a frame measured forward.", cost)


def _age(hours: float) -> str:
    if hours < 1:
        return f"{int(hours * 60)} minutes"
    if hours < 48:
        return f"{hours:.0f} hours"
    return f"{hours / 24:.0f} days"


def jpeg_quality(value) -> int:
    """A quality the encoder will accept, whatever the box contains.

    The spinbox hands back a string and an operator can type in it, so this is
    the one place that decides what "" or "abc" or 5000 mean. Clamped rather
    than refused: a scan must never be lost to a typo in a quality field.
    """
    try:
        wanted = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return export.DEFAULT_QUALITY
    return max(60, min(100, wanted))


def save_as_types(fmt: str) -> list[tuple[str, str]]:
    """The save dialog's file types, with the current output format first.

    Order is the whole point: the first entry is what the dialog opens on, so
    an operator working in JPEG is not asked to pick it again every time.
    """
    types = {"tiff": ("TIFF", "*.tif"), "jpeg": ("JPEG", "*.jpg")}
    first = types.get(fmt, types["tiff"])
    return [first] + [t for key, t in types.items() if t != first]


def dpi_or(text: str, fallback: int = 1800) -> int:
    """The resolution box as a number, or a sane one while it is being typed."""
    try:
        return int(str(text).strip())
    except ValueError:
        return fallback


def infrared_cost_note(resolution: int, fast: bool) -> str:
    """What the infrared plane costs at this resolution, tied or untied.

    Both numbers, always, because the choice is only meaningful as a comparison
    -- and above about 3700 dpi there is no choice left to make, which the line
    says outright rather than leaving an operator to notice that the two
    figures have converged.
    """
    tied = estimate_seconds(resolution, True, True)
    untied = estimate_seconds(resolution, True, False)
    now, other = (tied, untied) if fast else (untied, tied)
    note = (f"about {_duration(now)} a pass at {resolution} dpi; "
            f"{_duration(other)} "
            f"{'untied' if fast else 'tied to the resolution'}")
    if resolution >= INFRARED_TIE_CROSSOVER_DPI:
        # Keyed on the measured crossover rather than on the two estimates
        # agreeing, because they do not: the untied branch keeps older anchors
        # that read long above 1800 dpi. See `estimate_seconds`.
        note += (f" -- little in it past ~{INFRARED_TIE_CROSSOVER_DPI} dpi, "
                 f"where the lines cost more than the floor did")
    return note


def output_note(fmt: str) -> str:
    """What the label under the output folder says for this format."""
    if fmt == "jpeg":
        return ("A JPEG of every scan is written here as it lands, on top of "
                "the library entry. It is the same picture as the TIFF at 8 "
                "bits -- still a negative. An infrared scan leaves a DNG "
                "beside it holding the plane a JPEG has no room for. A roll's "
                "own files stay TIFF. Leave empty for the library only.")
    return ("A TIFF of every scan is written here as it lands, on top of the "
            "library entry. Leave empty for the library only.")


def stop_label(job) -> str:
    """What the stop button should say while `job` is running.

    "Stop" cannot mean "now" on this device: a pass in flight always finishes,
    because an abandoned read is what costs a power cycle. So the button has to
    say which of the two things it will do, and be right about it for the whole
    run -- it once read the progress label, which the line counter overwrites a
    second in, and so relabelled itself mid-roll.

    Judged by the job itself where the window has it: every `Roll`, a walk
    included, stops after the frame in flight. Read from its words, the
    label missed twice -- a roll given its frames by number ("scanning N
    chosen frames"), and then every roll of strips, described as "strip 2 of
    <name>", which said "roll" only when its name did: the 2026-10-12 tour
    passed because its roll was called "tour-roll". The words remain the
    fallback for a caller that has only them.
    """
    if isinstance(job, Roll):
        roll = True
    elif job is None or isinstance(job, str):
        text = job or ""
        roll = any(w in text for w in ("roll", "chosen frame", "strip"))
    else:
        # Any other job is one pass, a calibration or a move. Read as words,
        # its settings were: a Scan whose film notes said "strolling" or
        # "strip" promised the end of a frame it does not have.
        roll = False
    return "Stop after this frame" if roll else "Stop (finishes this pass)"


#: What one pixel of trackpad travel is worth, as a proportion. 150 px of
#: swiping is then about 2.5x, which is roughly what a hand expects from the
#: gesture; the notched version this replaced wanted 300 px to reach 2x and
#: read as doing nothing.
_ZOOM_PER_PIXEL = 0.006
#: What one wheel notch is worth, for an actual mouse.
_ZOOM_PER_NOTCH = 1.25
#: As close as the picture can be brought, in scanned pixels per screen pixel.
#: At sixteen a single scanned pixel is a block you could put a finger on, which
#: is the right size for looking at a speck of dust and about the end of what
#: there is to see.
_MAX_ZOOM = 16.0
#: How much of the canvas may be empty before the view is pulled back. Some
#: slack is what lets a zoom hold its anchor near an edge; too much and the
#: picture can be pushed out of sight altogether.
_OFF_CANVAS = 0.75


def _bind_optional(widget: tk.Misc, sequence: str, handler, *,
                   everywhere: bool = False) -> bool:
    """Bind `sequence` if this Tk knows it, and say whether it took.

    `<TouchpadScroll>` arrived in Tk 8.7. Windows and most Linux distributions
    ship 8.6, where binding it raises TclError -- and it raised during
    `_build`, so the window never reached the screen at all. That one line is
    what made this driver macOS-only in practice.

    Nothing is lost where it does not exist: the wheel sequences bound beside
    it carry those machines, because a trackpad off Tk 9 sends <MouseWheel>
    like everything else.
    """
    bind = widget.bind_all if everywhere else widget.bind
    try:
        bind(sequence, handler, add="+")
        return True
    except tk.TclError:
        return False


def _touchpad_deltas(event: tk.Event) -> tuple[int, int]:
    """Unpack a <TouchpadScroll> delta into pixels, as tk::PreciseScrollDeltas.

    Tk packs both axes into one integer: x in the high half, y in the low half
    sign-extended. Tk 9 on macOS sends these instead of <MouseWheel> for a
    trackpad, which is why binding the wheel alone left two fingers dead.
    """
    packed = int(getattr(event, "delta", 0) or 0)
    dx = packed >> 16
    low = packed & 0xFFFF
    dy = low if low < 0x8000 else low - 0x10000
    return dx, dy


def _scroll_pixels(widget: tk.Misc, dx: int, dy: int) -> None:
    """Scroll by pixels, the way tk::ScrollByPixels does.

    Canvas and Text take `moveto` fractions, not pixel counts -- `yview_scroll`
    has no "pixels" unit on a canvas -- so the pixels become a fraction of the
    widget.
    """
    if dy:
        height = max(1.0, float(widget.winfo_height()))
        widget.yview_moveto(widget.yview()[0] - dy / height)
    if dx:
        width = max(1.0, float(widget.winfo_width()))
        widget.xview_moveto(widget.xview()[0] - dx / width)


#: One mouse notch, in the units Windows reports.
_WHEEL_NOTCH = 120


class _WheelCarry:
    """What is left of a Windows wheel delta that was not yet worth a line.

    Windows reports a *fraction* of 120 and says so in its own documentation;
    how big the number looks decides nothing. A mouse notch sends exactly 120,
    a Precision Touchpad sends 12 or 24 at a time for a smooth gesture.

    So the fraction has to be carried. Rounding each small delta up to a line
    scrolls about twelve times too far for the finger that moved it; rounding
    each one down to zero means the picture never moves at all.
    """

    def __init__(self) -> None:
        self.value = 0

    def lines(self, delta: int) -> int:
        """Whole lines this delta completes. A positive delta scrolls up."""
        self.value += delta
        sign = -1 if self.value < 0 else 1
        whole, left = divmod(abs(self.value), _WHEEL_NOTCH)
        self.value = sign * left
        return -sign * whole


_wheel_carry = _WheelCarry()


def _wheel_amount(event: tk.Event, platform: str = sys.platform,
                  carry: _WheelCarry | None = None) -> tuple[int, bool]:
    """How far to scroll, and whether sideways, from any platform's event.

    X11 sends Button-4/5 with no delta at all. A macOS trackpad sends small
    numbers -- single digits, and occasionally a zero for a movement too small
    to matter -- so there anything small is already a line count, which is what
    makes two fingers feel like two fingers rather than one notch per gesture.
    Windows is the other rule entirely; see `_WheelCarry`.

    `platform` and `carry` are arguments so that both rules can be driven from
    a test on one machine. The rules disagree, and a rule that only its own
    platform ever exercises is one nobody notices breaking.
    """
    number = getattr(event, "num", 0)
    if number in (4, 5):
        return (-1 if number == 4 else 1), False
    delta = int(getattr(event, "delta", 0) or 0)
    if delta == 0:
        return 0, False
    sideways = bool(event.state & 0x0001)
    if platform == "win32":
        return (carry or _wheel_carry).lines(delta), sideways
    size = abs(delta)
    step = size if size < 20 else max(1, size // _WHEEL_NOTCH)
    return (-step if delta > 0 else step), sideways


#: The colour each channel is drawn in. Infrared is grey because it is a
#: measurement rather than a colour, the same reason its preview is not tinted.
#: One ink per visible plane. There is no fourth: see `rgb_only`.
_CHANNEL_INK = ("#e0605a", "#5ab86a", "#5a8fe0")

#: The channel names a histogram row can carry, in the order the planes sit.
_CHANNEL_NAMES = "RGB"


def rgb_only(pixels):
    """The visible planes of a pass, which is all a histogram is about.

    Infrared is not a colour and not an exposure. It is a dust measurement:
    its plane holds where the film is opaque to 940 nm, so "how much of it is
    against full scale" is not a question about whether this frame was exposed
    well, and the answer moves with the film base rather than with anything
    the operator can change. On traditional black and white it holds the
    picture over again, at +0.97 correlation with green -- a fourth curve
    tracing the third, on a chart read at a glance.

    Dropped before the measurement rather than after it, so a full 3600 dpi
    frame costs three passes over the pixels and not four.
    """
    if pixels.ndim == 3 and pixels.shape[2] > len(_CHANNEL_NAMES):
        return pixels[..., :len(_CHANNEL_NAMES)]
    return pixels


class _HistogramPanel:
    """Where the current pass's values sit, unstretched, always on screen.

    A panel in the corner of the picture rather than a window opened from a
    menu. What it answers -- is this against the ceiling? -- is not an
    occasional question but the one being asked continuously while an exposure
    is being judged, and a reading you have to go and ask for is a reading
    nobody takes. Blue reaches the rail first on this scanner and does it
    without looking any different on a stretched preview, which is the whole
    reason these numbers exist.

    Small, and over the picture rather than beside it: the width belongs to
    the scan. It follows whatever is on screen, and upgrades itself from the
    working copy to the scan's own pixels when those arrive, because a reduced
    copy understates how much is at the rail.
    """

    WIDTH, HEIGHT = 244, 74
    BACK = "#141414"
    EDGE = "#333333"
    MARGIN = 12                              # from the corner, and from the picture

    def __init__(self, parent: tk.Misc):
        # Scaled per instance rather than in the class body: there is no Tk
        # root when that runs, so `_px` would have no display to measure. It
        # has to cover the drawing coordinates below as well as the canvas --
        # they are the same two numbers, and scaling only one would put the
        # chart outside its own frame.
        self.WIDTH, self.HEIGHT = _px(self.WIDTH), _px(self.HEIGHT)
        self.frame = tk.Frame(parent, background=self.BACK,
                              highlightthickness=1, highlightbackground=self.EDGE)
        self.v_source = tk.StringVar(value="nothing scanned yet")
        # One line, clipped rather than wrapped: this is a caption, and three
        # lines of it made the panel taller than the chart it describes.
        tk.Label(self.frame, textvariable=self.v_source, background=self.BACK,
                 foreground="#777", font=_font(9), anchor="w",
                 width=1).pack(fill="x", padx=8, pady=(4, 0))
        self.canvas = tk.Canvas(self.frame, width=self.WIDTH, height=self.HEIGHT,
                                background=self.BACK, highlightthickness=0)
        self.canvas.pack(padx=8, pady=(3, 0))
        self.table = tk.Frame(self.frame, background=self.BACK)
        self.table.pack(fill="x", padx=8, pady=(2, 6))
        # Built once and rewritten, not destroyed and rebuilt: this runs on
        # every pass the operator clicks through, and a panel that discards
        # and recreates a dozen widgets each time flickers doing it.
        self._cells: dict[tuple[int, int], tk.Label] = {}
        for column, text in enumerate(("", "at 0", "at full", "near full")):
            tk.Label(self.table, text=text, background=self.BACK,
                     foreground="#666", font=_font(9)).grid(
                row=0, column=column, sticky="e", padx=(0, 6))
        for row, name in enumerate(_CHANNEL_NAMES, start=1):
            tk.Label(self.table, text=name, background=self.BACK,
                     foreground=_CHANNEL_INK[row - 1],
                     font=_font(9)).grid(row=row, column=0,
                                                     sticky="w", padx=(0, 6))
            for column in range(1, 4):
                cell = tk.Label(self.table, text="--", background=self.BACK,
                                foreground="#999", font=_font(9))
                cell.grid(row=row, column=column, sticky="e", padx=(0, 6))
                self._cells[(row, column)] = cell

    def place(self) -> None:
        """Top right of the picture, out of the way of the toolbar below it."""
        self.frame.place(relx=1.0, x=-self.MARGIN, y=self.MARGIN, anchor="ne")

    def footprint(self) -> int:
        """How much of the canvas's width this is standing on.

        Its own width, the gap to the edge, and the same gap again so the
        picture stops short of it rather than up against it.

        `winfo_reqwidth`, not `winfo_width`: the requested width is right
        before the panel has been mapped, and the first picture is laid out
        before that has happened.
        """
        return self.frame.winfo_reqwidth() + self.MARGIN * 2

    # -- what it is showing ------------------------------------------------

    def waiting(self, source: str) -> None:
        self.v_source.set(source)
        self._note("measuring ...")

    def nothing(self) -> None:
        self.v_source.set("nothing scanned yet")
        self._note("")

    def failed(self) -> None:
        self._note("could not measure")

    def _note(self, text: str) -> None:
        self.canvas.delete("all")
        self._blank()
        if text:
            self.canvas.create_text(self.WIDTH // 2, self.HEIGHT // 2,
                                    fill="#777", text=text)

    def _blank(self) -> None:
        for cell in self._cells.values():
            cell.configure(text="--", foreground="#999")

    def show(self, counts, clipped) -> None:
        self.v_source.set(self.v_source.get())
        self.canvas.delete("all")
        import numpy as np

        # Square root, not log: it lifts a small population into view without
        # the misleading flatness log gives a histogram with an empty tail.
        shown = np.sqrt(counts.astype(float))
        tallest = max(1.0, float(shown.max()))
        bins = counts.shape[1]
        for edge in (0.25, 0.5, 0.75):
            x = 1 + edge * (self.WIDTH - 2)
            self.canvas.create_line(x, 0, x, self.HEIGHT, fill="#2a2a2a")
        for channel in range(min(counts.shape[0], len(_CHANNEL_INK))):
            points = []
            for b in range(bins):
                x = 1 + b * (self.WIDTH - 2) / max(1, bins - 1)
                y = self.HEIGHT - 1 - shown[channel][b] / tallest * (self.HEIGHT - 6)
                points.extend((x, y))
            self.canvas.create_line(*points, fill=_CHANNEL_INK[channel], width=1)
        self.canvas.create_text(3, self.HEIGHT - 7, anchor="w", fill="#555",
                                text="0", font=_font(9))
        self.canvas.create_text(self.WIDTH - 3, self.HEIGHT - 7, anchor="e",
                                fill="#555", text="full scale",
                                font=_font(9))

        self._blank()
        for channel in range(min(clipped.shape[0], len(_CHANNEL_NAMES))):
            for column, fraction in enumerate(clipped[channel], start=1):
                cell = self._cells.get((channel + 1, column))
                if cell is None:
                    continue
                # A tenth of a percent of a frame is thousands of pixels, so
                # anything that rounds to zero is said to be zero rather than
                # shown as a very small number that invites squinting.
                cell.configure(
                    text="--" if fraction == 0 else f"{fraction * 100:.2f}%",
                    foreground="#e0605a" if fraction > 0.001 else "#999")


class _ShortcutSettings:
    """Every key in the window, and what it does, changeable and restorable.

    Its own window like the contact sheet: it is opened to change one thing and
    closed again, and it wants the room to show three windows' worth of keys at
    once. A change binds immediately -- there is no Apply -- because the thing
    being edited is what the keyboard does, and trying a key is how anyone
    checks they got the one they meant.
    """

    def __init__(self, gui):
        self.gui = gui
        self.keys = dict(gui.keys)
        self._capturing: str | None = None   # the action id waiting for a key
        self._buttons: dict[str, tk.Widget] = {}

        self.top = tk.Toplevel(gui.root)
        self.top.title("Shortcuts")
        self.top.transient(gui.root)
        self.top.geometry(_geometry(620, 760))

        outer = ttk.Frame(self.top, padding=(12, 10))
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, font=_font(13, bold=True),
                  text="Shortcuts").pack(anchor="w")
        ttk.Label(
            outer, foreground="#777", justify="left", wraplength=580,
            text=("Click a key to change it, then press the one you want. The "
                  "same key can be used in different windows -- the arrows walk "
                  "the filmstrip here, move the selection in the contact sheet, "
                  "and step the frame's planned position in the position "
                  "window.\n\n"
                  "Prescan, Scan and Roll have keys, and each asks before it "
                  "starts: they cost minutes of the scanner, and a slip on the "
                  "keyboard is not a decision to spend them. Nothing "
                  "calibrates or moves film from a key.")
        ).pack(anchor="w", pady=(2, 10))

        host = ttk.Frame(outer)
        host.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(host, highlightthickness=0, borderwidth=0)
        bar = ttk.Scrollbar(host, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        table = ttk.Frame(self.canvas, padding=(0, 4))
        window = self.canvas.create_window((0, 0), window=table, anchor="nw")
        table.bind("<Configure>", lambda _e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(window, width=e.width))
        table.columnconfigure(0, weight=1)

        row = 0
        for scope in shortcuts.SCOPES:
            ttk.Label(table, text=shortcuts.SCOPE_NAMES[scope],
                      font=_font(11, bold=True)).grid(
                row=row, column=0, sticky="w", pady=(12, 2))
            row += 1
            for action in shortcuts.ACTIONS:
                if action.scope == scope:
                    self._row(table, action, row)
                    row += 1

        foot = ttk.Frame(outer)
        foot.pack(fill="x", pady=(10, 0))
        ttk.Button(foot, text="Restore all defaults",
                   command=self._restore_all).pack(side="left")
        self.v_note = tk.StringVar()
        ttk.Label(foot, textvariable=self.v_note, foreground="#e0605a").pack(
            side="left", padx=10)
        ttk.Button(foot, text="Close", command=self.top.destroy).pack(side="right")

        gui._scrolls(self.canvas,
                     lambda amount, _s: self.canvas.yview_scroll(amount, "units"),
                     precise=lambda dx, dy: _scroll_pixels(self.canvas, dx, dy))
        self.top.bind("<Escape>", self._escape)

    def _row(self, table, action, row: int) -> None:
        line = ttk.Frame(table)
        line.grid(row=row, column=0, sticky="ew", pady=1)
        line.columnconfigure(0, weight=1)
        ttk.Label(line, text=action.label).grid(row=0, column=0, sticky="w")
        button = ttk.Button(line, width=12,
                            command=lambda a=action.id: self._capture(a))
        button.grid(row=0, column=1, padx=(8, 2))
        self._buttons[action.id] = button
        ttk.Button(line, text="\u00d7", width=2,
                   command=lambda a=action.id: self._set(a, "")).grid(
            row=0, column=2, padx=1)
        ttk.Button(line, text="\u21ba", width=2,
                   command=lambda d=action.default, a=action.id: self._set(a, d)
                   ).grid(row=0, column=3, padx=1)
        self._show(action.id)

    # -- changing one ------------------------------------------------------

    def _show(self, action_id: str) -> None:
        button = self._buttons.get(action_id)
        if button is None:
            return
        if self._capturing == action_id:
            button.configure(text="press a key")
            return
        button.configure(text=shortcuts.describe(self.keys.get(action_id, "")))

    def _capture(self, action_id: str) -> None:
        """Wait for the next key press and give it to this action."""
        was, self._capturing = self._capturing, action_id
        if was:
            self._show(was)
        self._show(action_id)
        self.v_note.set("")
        self.top.bind("<KeyPress>", self._captured)
        self.top.focus_set()

    def _captured(self, event) -> str:
        action_id, self._capturing = self._capturing, None
        self.top.unbind("<KeyPress>")
        if action_id is None:
            return "break"
        if event.keysym == "Escape":
            self._show(action_id)        # cancelled, nothing changed
            return "break"
        sequence = shortcuts.sequence_for(event.keysym, int(event.state))
        if sequence is None:
            self.v_note.set("That is only a modifier -- it could never fire.")
            self._show(action_id)
            return "break"
        self._set(action_id, sequence)
        return "break"

    def _escape(self, _event=None) -> str | None:
        """Cancel a capture if one is running, otherwise close the window."""
        if self._capturing is not None:
            waiting, self._capturing = self._capturing, None
            self.top.unbind("<KeyPress>")
            self._show(waiting)
            return "break"
        self.top.destroy()
        return "break"

    def _set(self, action_id: str, sequence: str) -> None:
        """Give one action a key, refusing a clash inside the same window."""
        scope = shortcuts.scope_of(action_id)
        if sequence:
            # In one spelling: a hand-edited `<Control-s>` holds the key a
            # captured `<Control-Key-s>` is, as far as Tk is concerned.
            taken = shortcuts.in_scope(self.keys, scope)
            held = {shortcuts.canonical(key): owner
                    for key, owner in taken.items()}.get(
                        shortcuts.canonical(sequence))
            if held and held != action_id:
                other = shortcuts.action(held)
                self.v_note.set(
                    f"{shortcuts.describe(sequence)} already does "
                    f"\u201c{other.label}\u201d in {shortcuts.SCOPE_NAMES[scope].lower()}.")
                self._show(action_id)
                return
        self.keys[action_id] = sequence
        self.v_note.set("")
        self._show(action_id)
        self.gui.set_keys(self.keys)

    def _restore_all(self) -> None:
        if not messagebox.askokcancel(
            "Shortcuts", "Put every key back to what it ships as?",
            parent=self.top,
        ):
            return
        self.keys = shortcuts.defaults()
        self.v_note.set("")
        for action_id in self.keys:
            self._show(action_id)
        self.gui.set_keys(self.keys)

    def rebind(self) -> None:
        """The window changed the keys under us -- show what they are now."""
        self.keys = dict(self.gui.keys)
        for action_id in self.keys:
            self._show(action_id)

    def alive(self) -> bool:
        try:
            return bool(self.top.winfo_exists())
        except tk.TclError:
            return False


class _FrameAdjuster:
    """One surveyed frame, big, with its position in the aperture set by hand.

    The contact sheet says *whether* to scan a frame. This says *where it sits*
    when the scan happens -- and that number is the operator's, not a
    measurement. Every automatic registration detector built for this scanner
    has been confidently wrong on some frames, and on real film the level-based
    one abstains on 97% of prescans and reports +-0.00 mm for everything. A
    picture somebody looked at and accepted is a different kind of evidence.

    **Nothing moves while this is open.** The number is an intent, recorded
    against the frame, applied only when the scan is commissioned.

    The whole frame stays visible at about 2x rather than offering zoom and
    pan. Drag has to mean one thing, and here it means the offset; a frame you
    can only see a third of is also the wrong tool for judging where its edges
    sit in the aperture. The prescan is the finest pass a surveyed frame has,
    so 2x is most of what there is anyway.
    """

    #: 704 tall since the help line above the picture wraps: about four lines
    #: more than the one it was cut down to, so the picture keeps its size.
    WIDTH, HEIGHT = 900, 704
    GUIDE = "#e8b64c"                        # the sheet's amber, reused
    #: The frame-edge detector's reading: red like the sheet's edge marks,
    #: dotted so the pixels under it stay visible, and heavier than the
    #: guides -- Stefan: "a red dotted line and a bit bigger".
    EDGE_DASH, EDGE_WIDTH = (4, 4), 3
    #: The frame's other end, where only one edge could be read: the measured
    #: frame width away from it. Lighter and finer, because it is worked out
    #: rather than seen.
    FAR_EDGE, FAR_DASH, FAR_WIDTH = "#f2a19b", (2, 6), 2

    def __init__(self, sheet, gui, index: int):
        # Per instance, not in the class body: no Tk root exists there. These
        # size the window and the insets computed from it further down, so
        # both move together.
        self.WIDTH, self.HEIGHT = _px(self.WIDTH), _px(self.HEIGHT)
        self.sheet = sheet
        self.gui = gui
        self.index = index
        self._photo = None
        self._drag_from: float | None = None
        self._drag_base = 0.0

        # A child of the sheet's window, so it closes with the sheet however
        # the sheet is closed. A child of the main window, it outlived the
        # sheet, and every position set in it afterwards went into a sheet
        # that no longer existed -- redrawn here as if accepted, and lost.
        self.top = tk.Toplevel(sheet.top)
        self.top.title("Frame position")
        self.top.transient(sheet.top)
        self.top.geometry(f"{self.WIDTH}x{self.HEIGHT}")

        outer = ttk.Frame(self.top, padding=10)
        outer.pack(fill="both", expand=True)

        self.v_title = tk.StringVar()
        ttk.Label(outer, textvariable=self.v_title,
                  font=_font(12, bold=True)).pack(anchor="w")
        # The figures from the constants, not typed: this said the dashed
        # lines were "exactly what the scan takes" while they stood at the
        # prescan's edges, which the scan reaches past (`scan_reach_units`).
        reach_left, reach_right = scan_reach_units()
        past_left, past_right = centred_overhang()
        # Wrapped to the window. It never was: one line some 5000 pixels long
        # in a 900-pixel window, so from 2026-09-15 everything after its first
        # sentence -- where the guides are, what Centre and Reset do -- was cut
        # off unread. Short enough now to be read, and the window is taller by
        # what the wrapped lines take, so the picture keeps its size. The last
        # sentence was dropped in that rewrite and is back: the picture here
        # is the prescan as it came (`_source`), so a frame the sheet shows
        # turned upright or flipped opens on its side or mirrored, and
        # nothing else in this window says why.
        ttk.Label(
            outer, foreground="#777", wraplength=self.WIDTH - _px(24),
            justify="left",
            text=("Drag the picture, or use the arrow keys, to set where the "
                  "film should sit; \u201cfinest\u201d steps to the next "
                  "position the transport can reach. The dashed lines are "
                  "where the scan cuts: a little wider than the prescan, "
                  f"about {reach_left:.1f} units past its left edge and "
                  f"{reach_right:.1f} past its right, and nothing beyond them "
                  "is scanned. The red dotted line is the picture's edge as "
                  "the detector reads it, the lighter one the frame's other "
                  "end, a frame's width away. A frame is a little wider than "
                  "what the scan takes, so centred it overhangs the left guide "
                  f"by about {past_left:.0f} units and the right by about "
                  f"{past_right:.0f}. Centre puts it back as walked, Reset "
                  "where the detector puts it; Return keeps it and moves to "
                  "the next frame. Shown as the film sits in the transport, "
                  "not turned or flipped as the sheet shows it.")
        ).pack(anchor="w", pady=(0, 6))

        self.canvas = tk.Canvas(outer, background="#1e1e1e",
                                highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Configure>", lambda _e: self._draw())

        row = ttk.Frame(outer)
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="\u25c0", width=3,
                   command=lambda: self._step(-1)).pack(side="left")
        ttk.Button(row, text="\u25b6", width=3,
                   command=lambda: self._step(1)).pack(side="left", padx=(2, 8))
        ttk.Button(row, text="Centre", command=self._centre).pack(side="left")
        ttk.Button(row, text="Reset", command=self._reset).pack(side="left",
                                                                 padx=(4, 0))
        ttk.Label(row, text="step").pack(side="left", padx=(12, 4))
        ttk.Combobox(row, textvariable=gui.v_adjuststep, width=8,
                     state="readonly", values=list(ADJUST_STEPS)).pack(side="left")
        self.v_read = tk.StringVar()
        ttk.Label(row, textvariable=self.v_read,
                  font=_font(11)).pack(side="left", padx=12)

        self.v_tick = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="Scan this frame", variable=self.v_tick,
                        command=self._tick_changed).pack(side="right")

        nav = ttk.Frame(outer)
        nav.pack(fill="x", pady=(8, 0))
        ttk.Button(nav, text="\u25c0 Previous frame",
                   command=lambda: self._go(-1)).pack(side="left")
        ttk.Button(nav, text="Next frame \u25b6",
                   command=lambda: self._go(1)).pack(side="left", padx=6)
        ttk.Button(nav, text="Show in preview",
                   command=self._show_in_preview).pack(side="left", padx=6)
        ttk.Button(nav, text="Done", command=self.top.destroy).pack(side="right")

        self.rebind()
        self.canvas.focus_set()
        self._load()

    # -- the keyboard ------------------------------------------------------

    def _actions(self) -> dict:
        return {
            "adjust_left": lambda: self._step(-1),
            "adjust_right": lambda: self._step(1),
            "adjust_accept": self._accept,
            "adjust_previous": lambda: self._go(-1),
            "adjust_next": lambda: self._go(1),
            "adjust_centre": self._centre,
            "adjust_toggle": self._toggle_tick,
            "adjust_close": self.top.destroy,
        }

    def rebind(self) -> None:
        for sequence in getattr(self, "_bound", []):
            try:
                self.top.unbind(sequence)
            except tk.TclError:
                pass
        self._bound = []
        actions = self._actions()
        scoped = shortcuts.in_scope(self.gui.keys, "adjuster")
        for sequence, action_id in scoped.items():
            run = actions.get(action_id)
            if run is not None:
                bound = self.gui._bind_key(self.top, sequence, action_id, run,
                                           taken=scoped)
                if bound:
                    self._bound.append(bound)

    def _accept(self) -> None:
        """Keep this frame and move on to the next one.

        The offset is already recorded -- `_set` writes it into the sheet as
        the picture is dragged -- so there is nothing here to save. What this
        does is say yes: tick it for scanning, and show the next frame. That
        is the whole of the job this window exists for, done one key at a time
        rather than one mouse round trip at a time.

        Not a frame already scanned: a resumed roll opens those unticked so
        they are not scanned twice, and reviewing the strip with Return,
        Return, ... ticked every one of them again.

        The last frame closes the window, because there is nowhere further to
        go and leaving it open invites a press that does nothing.
        """
        if self.number not in self.sheet.done:
            self.v_tick.set(True)
            self._tick_changed()
        if self.index >= len(self.sheet.frames) - 1:
            self.gui._say("that was the last frame of the strip")
            self.top.destroy()
            return
        self._go(1)

    def _toggle_tick(self) -> None:
        self.v_tick.set(not self.v_tick.get())
        self._tick_changed()

    # -- the frame on show -------------------------------------------------

    @property
    def result(self):
        return self.sheet.frames[self.index]

    @property
    def number(self) -> int:
        return self.result.number

    def _load(self) -> None:
        self.v_tick.set(self.sheet.ticks[self.number].get())
        self._refresh()

    def _go(self, by: int) -> None:
        self.index = max(0, min(len(self.sheet.frames) - 1, self.index + by))
        self._load()

    def _show_in_preview(self) -> None:
        self.gui._show_seq(self.result.seq)

    def _tick_changed(self) -> None:
        self.sheet.ticks[self.number].set(self.v_tick.get())
        self.sheet._changed()

    # -- the offset --------------------------------------------------------

    @property
    def offset(self) -> float:
        return self.sheet.offsets.get(self.number, 0.0)

    def _set(self, millimetres: float) -> None:
        """Put this frame where he just put it, and record that it was him.

        The note matters as much as the number. `proposals` was written once,
        when the sheet opened, and never again -- so a frame he dragged from
        the detector's suggestion to his own went on wearing the detector's
        badge, and the confirm dialog went on counting it as measured. It is
        his the moment he moves it.
        """
        # Zero is kept, not dropped: "as walked" is his answer as much as any
        # other, and an absent entry is where the detector's goes. Dropped,
        # it survived only as long as this sheet did -- reopened, the detector's
        # number came back in its place and the roll moved the frame.
        self.sheet.offsets[self.number] = snap_offset(millimetres)
        self.sheet.proposals[self.number] = {"source": "operator",
                                             "reason": "you set this one"}
        self._refresh()
        self.sheet._refresh_caption(self.number)

    def _step(self, direction: int) -> None:
        """One step. Drag is coarse; this is how a frame is landed.

        "finest" walks to the next position the transport can reach, which is
        the smallest move there is. What this replaced added a flat first-step
        distance and snapped, and that distance is not the lattice's spacing --
        it is where the lattice starts. Above it the positions are one param
        apart, so the arrows were stepping over two out of every three places
        the film could actually be put.
        """
        self._set(step_offset(self.offset, direction,
                              step_millimetres(self.gui.v_adjuststep.get())))

    def _centre(self) -> None:
        self._set(0.0)

    def _reset(self) -> None:
        """This frame back where the detector puts it; the others keep theirs."""
        self.sheet.reset(self.number)
        self._refresh()
        self.gui._say(f"{self.sheet._frame_said(self.number)}: position "
                      "reset to the detector's")

    # -- drawing -----------------------------------------------------------

    def _source(self):
        """The prescan, unrotated. Left on screen is then left on the film.

        The sheet only ever holds frames that have an image, so there is no
        empty case to guard.
        """
        return self.result.image

    def _size(self) -> tuple[int, int]:
        """The canvas, falling back to its requested size before it is mapped.

        `winfo_width` is 1 until Tk has laid the widget out, so the first draw
        would otherwise scale the picture against a one-pixel canvas -- and a
        drag measured against that maps a few pixels of hand movement onto the
        whole travel range.
        """
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if width <= 1:
            width = max(self.canvas.winfo_reqwidth(), self.WIDTH - 40)
        if height <= 1:
            height = max(self.canvas.winfo_reqheight(), self.HEIGHT - 160)
        return max(1, width), max(1, height)

    def _scale(self, source) -> float:
        width, height = self._size()
        return min(width / max(source.shape[1], 1),
                   height / max(source.shape[0], 1))

    def _refresh(self) -> None:
        moves = len(plan_nudges(self.offset)) if self.offset else 0
        seconds = moves * 1.1
        # By its number across the roll, its place on the strip beside it
        # wherever the two differ.
        shown = self.sheet.shown_number(self.number)
        strip = self.sheet.numbers
        self.v_title.set(
            f"Frame {self.number} of {len(self.sheet.frames)}"
            if strip is None or shown == self.number else
            f"Frame {shown if shown is not None else '?'} ({self.number} of "
            f"{len(self.sheet.frames)} on strip {strip.strip})")
        if not self.offset:
            said = "as surveyed"
        else:
            said = (f"{say_units(self.offset)}   \u00b7   {moves} "
                    f"move{'s' if moves != 1 else ''}   \u00b7   "
                    f"about {seconds:.0f} s")
        self.v_read.set(said + overhang_note(
            frame_overhang(self.sheet.edges.get(self.number), self.offset)))
        self._draw()

    def _draw(self) -> None:
        if not self.canvas.winfo_exists():
            return
        source = self._source()
        scale = self._scale(source)
        width = max(1, int(source.shape[1] * scale))
        height = max(1, int(source.shape[0] * scale))

        arr = preview.render(
            preview.sample(source, scale, 0.0, 0.0, width, height),
            "RGB", self.gui.v_invert.get(),
            cuts=(preview.channel_levels(self.result.levels, "RGB")
                  if getattr(self.result, "levels", None) is not None else None))
        self._photo = tk.PhotoImage(data=preview.to_ppm(arr))

        canvas_width, canvas_height = self._size()
        left = (canvas_width - width) // 2
        top = (canvas_height - height) // 2
        # The guides stay put and the picture moves against them -- which is
        # what the film will actually do. They are where the scan cuts, which
        # is not the prescan's own edges: a scan takes a little more of the
        # transport than the 428 columns a prescan comes back with, and more
        # of it on the right (`framing.SCAN_WINDOW_COLUMNS`). Drawn at the
        # prescan's edges, they called picture the scan would take "past the
        # aperture" and the frame's red line sat off them by the difference.
        shift = int(round(self.offset / APERTURE_MM * width))
        per_column = width / float(source.shape[1])
        cut_left, cut_right = scan_window(source.shape[1])

        self.canvas.delete("all")
        self.canvas.create_image(left + shift, top, image=self._photo,
                                 anchor="nw")
        for x in (left + cut_left * per_column, left + cut_right * per_column):
            self.canvas.create_line(x, top, x, top + height,
                                    fill=self.GUIDE, dash=(4, 3), width=2)
        if shift:
            # Where the picture was walked, at the prescan's own extent, so a
            # move can be seen against where it started.
            self.canvas.create_rectangle(
                left, top, left + width, top + height,
                outline="#6a6a6a", dash=(2, 4))
        self._draw_edges(left + shift, top, width, height)

    def _draw_edges(self, x0: int, top: int, width: int, height: int) -> None:
        """The detector's edges, on the picture as it is drawn at ``x0``.

        On the film, not on the aperture: the line moves with the picture, so
        with the proposed move applied it shows where the edge lands against
        the guides -- just outside them, for a frame wider than what the scan
        takes. Tilted when the reading was, from its top row to its bottom row.
        """
        read = self.sheet.edges.get(self.number)
        if not read or not read.get("width"):
            return
        per_column = width / float(read["width"])
        for side in ("left", "right"):
            edge = (read.get("edges") or {}).get(side) or {}
            if edge.get("state") != "edge" or edge.get("x") is None:
                continue
            x = float(edge["x"])
            x_top = float(edge["x_top"]) if edge.get("x_top") is not None else x
            x_bottom = float(edge["x_bottom"]) if edge.get("x_bottom") is not None else x
            self.canvas.create_line(
                x0 + x_top * per_column, top, x0 + x_bottom * per_column, top + height,
                fill=self.sheet.EDGE_LINE, dash=self.EDGE_DASH, width=self.EDGE_WIDTH)
        ends = frame_ends(read)
        if ends is not None and ends[2]:
            # Only one end was read: the other is a frame's width from it.
            read_left = ((read.get("edges") or {}).get("left") or {}).get("state") == "edge"
            far = ends[1] if read_left else ends[0]
            self.canvas.create_line(
                x0 + far * per_column, top, x0 + far * per_column, top + height,
                fill=self.FAR_EDGE, dash=self.FAR_DASH, width=self.FAR_WIDTH)

    # -- dragging ----------------------------------------------------------

    def _press(self, event) -> None:
        self._drag_from = event.x
        self._drag_base = self.offset

    def _drag(self, event) -> None:
        if self._drag_from is None:
            return
        source = self._source()
        width = max(1, int(source.shape[1] * self._scale(source)))
        moved = (event.x - self._drag_from) / width * APERTURE_MM
        self._set(self._drag_base + moved)

    def _release(self, _event) -> None:
        self._drag_from = None

    def alive(self) -> bool:
        try:
            return bool(self.top.winfo_exists())
        except tk.TclError:
            return False


class _RollBrowser:
    """The rolls on disk as a table: sortable, filterable, and actionable.

    A table rather than a folder picker, and the reason is the whole feature:
    what decides which roll to act on is how far it got, when it was last
    opened and how much disk it is holding, and `filedialog.askdirectory`
    shows directory names. A roll directory is named by its date.

    Everything it lists comes from two small JSON files and `stat`, never from
    pixels -- a roll can hold 38 frames at 142 MB and a listing that opened them
    would be unusable.
    """

    #: `(key, heading, width, anchor)`. The key is both the column id and the
    #: name in `SORT_KEYS`, so a heading click needs no translation table.
    COLUMNS = (
        ("roll", "Roll", 155, "w"),
        ("frames", "Frames", 95, "w"),
        ("dpi", "Resolution", 100, "w"),
        ("film", "Film", 80, "w"),
        ("created", "Created", 95, "w"),
        ("opened", "Last opened", 95, "w"),
        ("size", "Size", 80, "e"),
    )
    UNFINISHED = "#a8761f"                   # the sheet's amber, legible on white
    ORPHANED = "#8a3b3b"                     # nothing left in the library

    def __init__(self, gui, rolls):
        self.gui = gui
        self.rolls = list(rolls)
        self.shown: list[dict] = []
        #: Column and direction. Opens on newest first, which is what anybody
        #: coming here for "the roll I was just doing" wants.
        self._sort = ("created", True)

        self.top = tk.Toplevel(gui.root)
        self.top.title("Rolls")
        self.top.geometry(_geometry(940, 520))
        self.top.transient(gui.root)
        outer = ttk.Frame(self.top, padding=10)
        outer.pack(fill="both", expand=True)

        head = ttk.Frame(outer)
        head.pack(fill="x", pady=(0, 8))
        ttk.Label(head, text="find").pack(side="left")
        self.v_find = tk.StringVar()
        find = ttk.Entry(head, textvariable=self.v_find, width=24)
        find.pack(side="left", padx=(6, 12))
        find.bind("<KeyRelease>", lambda _e: self._fill())
        self.v_unfinished = tk.BooleanVar(value=False)
        ttk.Checkbutton(head, text="only unfinished",
                        variable=self.v_unfinished,
                        command=self._fill).pack(side="left")
        self.v_count = tk.StringVar()
        ttk.Label(head, textvariable=self.v_count,
                  foreground="#777").pack(side="right")

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True)
        # A tree: a roll of strips is a row with its strips under it, and a
        # roll from before strips a row with none. The first column is the
        # tree's own, narrow, for the mark that opens a roll.
        self.table = ttk.Treeview(
            body, columns=[key for key, *_ in self.COLUMNS],
            show="tree headings", selectmode="extended")
        self.table.column("#0", width=_px(24), stretch=False)
        #: Each row's summary, by its item id: ``r3`` a roll, ``r3s1`` a
        #: strip of it.
        self._rows: dict[str, dict] = {}
        for key, heading, width, anchor in self.COLUMNS:
            self.table.heading(key, text=heading,
                               command=lambda k=key: self._sort_by(k))
            self.table.column(key, width=width, anchor=anchor,
                              stretch=(key == "roll"))
        bar = ttk.Scrollbar(body, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.table.pack(side="left", fill="both", expand=True)
        self.table.tag_configure("unfinished", foreground=self.UNFINISHED)
        self.table.tag_configure("orphaned", foreground=self.ORPHANED)
        self.table.bind("<Double-Button-1>", lambda _e: self._open())
        self.table.bind("<Return>", lambda _e: self._open())
        for sequence in MENU_EVENTS:
            self.table.bind(sequence, self._menu)
        self.top.bind("<Escape>", lambda _e: self.top.destroy())

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="Close",
                   command=self.top.destroy).pack(side="right")
        for text, call in (("Open", self._open),
                           ("Export ...", self._export),
                           ("Duplicate", self._duplicate),
                           ("Delete", self._delete)):
            ttk.Button(buttons, text=text,
                       command=call).pack(side="right", padx=(0, 6))
        self.v_note = tk.StringVar()
        ttk.Label(buttons, textvariable=self.v_note, foreground="#777",
                  wraplength=420, justify="left").pack(side="left")

        self._fill()
        self._say_trash()

    #: What the note under the table always says.
    NOTE = "Unfinished rolls are marked. Opening one offers to finish it."

    def _say_trash(self) -> None:
        """The note, with what the two trashes hold beside it: a Delete here
        moves a roll there, and its gigabytes stay on the disk until it is
        emptied -- which this window never does, with the scanner open."""
        parts = []
        rolls = self.gui.session.rolls
        for place, one, many in ((rolls, "roll", "rolls"),
                                 (self.gui.session.root, "entry", "entries")):
            if place is None:
                continue
            _batches, items, size, _oldest = library.trash_summary(place)
            if items:
                # A strip deleted on its own is no roll: counted apart, as
                # `library.trash_said` counts it in the log.
                strips = (sum(len(b.names)
                              for b in library.trash_batches(place)
                              if b.kind == "strips") if place is rolls else 0)
                counted = [f"{n} {one if n == 1 else many}"
                           for n in (items - strips,) if n]
                counted += [f"{n} strip{'' if n == 1 else 's'}"
                            for n in (strips,) if n]
                parts.append(f"{' and '.join(counted)}, {size / 1e9:.1f} GB")
        self.v_note.set(self.NOTE + (
            "\ntrash: " + "; ".join(parts) + " -- tools/library.py "
            "empty-trash --write, with no window open, frees it"
            if parts else ""))

    # -- what is showing ---------------------------------------------------

    def _fill(self) -> None:
        """Apply the filter and the sort, and redraw the rows: each roll of
        strips with its strips under it, by number, open."""
        rows = matching(self.rolls, self.v_find.get(),
                        self.v_unfinished.get())
        key, descending = self._sort
        rows.sort(key=SORT_KEYS.get(key, SORT_KEYS["created"]),
                  reverse=descending)
        self.shown = rows
        self._rows = {}
        self.table.delete(*self.table.get_children())
        for index, summary in enumerate(rows):
            iid = f"r{index}"
            self._rows[iid] = summary
            self.table.insert("", "end", iid=iid, values=roll_cells(summary),
                              tags=self._tags(summary), open=True)
            for number, strip in enumerate(summary.get("strips") or ()):
                # "Only unfinished" per strip: the roll shows while any of
                # its strips has frames left, and only those strips under it.
                if self.v_unfinished.get() and not unfinished(strip):
                    continue
                child = f"{iid}s{number}"
                self._rows[child] = strip
                self.table.insert(iid, "end", iid=child,
                                  values=roll_cells(strip),
                                  tags=self._tags(strip))
        left = sum(1 for r in rows if unfinished(r))
        self.v_count.set(f"{len(rows)} of {len(self.rolls)} shown"
                         + (f", {left} unfinished" if left else ""))

    @staticmethod
    def _tags(summary: dict) -> tuple:
        """How a row is marked: unfinished, or scanned with nothing left in
        the library to re-correct from -- Export cannot work on it, said in
        the list rather than at the end of a failed export."""
        tags = []
        if unfinished(summary):
            tags.append("unfinished")
        if summary["done"] and not summary["entries"]:
            tags.append("orphaned")
        return tuple(tags)

    def _sort_by(self, key: str) -> None:
        current, descending = self._sort
        self._sort = (key, not descending if key == current else key != "roll")
        self._fill()

    def _reload(self) -> None:
        """Re-read the shelf. After anything that changed it on disk."""
        self.rolls = rolls_on_disk(self.gui.session.rolls,
                                   self.gui.session.root)
        stored = self.gui.remembered.get("rolls") or {}
        for summary in roll_rows(self.rolls):
            summary["opened"] = opened_at(summary, stored)
        self._fill()
        self._say_trash()

    def _selected(self) -> list[dict]:
        """The rows picked, each a roll's or a strip's summary (``kind``),
        in the order shown."""
        return [self._rows[iid] for iid in self.table.selection()
                if iid in self._rows]

    def _one(self, what: str):
        picked = self._selected()
        if len(picked) != 1:
            messagebox.showinfo(what, f"Pick one roll to {what.lower()}.")
            return None
        return picked[0]

    @staticmethod
    def _whole(picked: list[dict]) -> list[dict]:
        """A selection with a roll and strips of it in it, as the roll alone:
        what is done to the roll is done to each of its strips."""
        rolls = {_folder_key(s["folder"]) for s in picked
                 if s.get("kind") == "roll"}
        return [s for s in picked
                if not (s.get("kind") == "strip"
                        and _folder_key(s["roll_folder"]) in rolls)]

    # -- the actions -------------------------------------------------------

    def _menu(self, event: tk.Event) -> str:
        row = self.table.identify_row(event.y)
        if row and row not in self.table.selection():
            self.table.selection_set(row)
        menu = tk.Menu(self.top, tearoff=0)
        menu.add_command(label="Open", command=self._open)
        menu.add_command(label="Export ...", command=self._export)
        menu.add_separator()
        menu.add_command(label="Duplicate", command=self._duplicate)
        menu.add_command(label="Rename ...", command=self._rename)
        menu.add_command(label="Show in file manager", command=self._reveal)
        menu.add_separator()
        menu.add_command(label="Delete", command=self._delete)
        menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def _open(self) -> None:
        summary = self._one("Open")
        if summary is None:
            return
        if summary.get("kind") == "strip" and summary.get("state") != "here":
            # Said, and the browser left open: a strip in the trash comes
            # back to its place in its roll by `restore`, and no other way.
            messagebox.showinfo("Open", self.gui._strip_away_said(summary),
                                parent=self.top)
            return
        self.top.destroy()
        self.gui.open_roll(summary["folder"])

    def _export(self) -> None:
        picked = self._whole(self._selected())
        # A strip in the rolls' trash, or gone, said as Open says it: it
        # reached the export, which found no frame and said none "has a
        # library entry left" and that its files were still in its folder
        # -- neither true of a strip in the trash.
        away = [s for s in picked
                if s.get("kind") == "strip" and s.get("state") != "here"]
        for strip in away:
            messagebox.showinfo(
                "Export", self.gui._strip_away_said(
                    strip, then="it can be exported then")
                + "\n\nNothing of it was exported.", parent=self.top)
        picked = [s for s in picked if not any(s is a for a in away)]
        if not picked:
            if not away:
                messagebox.showinfo("Export",
                                    "Pick at least one roll to export.")
            return
        self.gui.on_export_rolls(picked)

    def _duplicate(self) -> None:
        summary = self._one("Duplicate")
        if summary is None:
            return
        if summary.get("kind") == "strip":
            # In words, not greyed: a copy of one strip would number nothing,
            # its numbers being its roll's.
            roll = Path(summary["roll_folder"]).name
            messagebox.showinfo(
                "Duplicate", "Duplicate copies a whole roll. Pick "
                f"{roll}'s row to copy it with all its strips.\n\nNothing was "
                "copied.", parent=self.top)
            return
        self.gui.on_duplicate_roll(summary)
        self._reload()

    def _rename(self) -> None:
        summary = self._one("Rename")
        if summary is not None:
            self.gui.on_rename_roll(summary)
            self._reload()

    def _reveal(self) -> None:
        summary = self._one("Show")
        if summary is not None:
            self.gui.on_reveal_roll(summary)

    def _delete(self) -> None:
        picked = self._whole(self._selected())
        if not picked:
            messagebox.showinfo("Delete", "Pick at least one roll to delete.")
            return
        self.gui.on_delete_rolls(picked)
        self._reload()

    def alive(self) -> bool:
        try:
            return bool(self.top.winfo_exists())
        except tk.TclError:
            return False


class _ContactSheet:
    """The whole surveyed strip at once, with a tick against each picture.

    This is where a roll is decided. Seventeen frames at 3600 dpi RGBI is three
    hours and 2.4 GB, and a strip with four keepers on it should not cost the
    same as one with seventeen -- but until there was something to look at, the
    only choice on offer was all of them or none of them. The walk that fills
    this took four minutes.

    Its own window, like the histogram: it wants the room, and it is looked at
    twice a roll rather than continuously.
    """

    CELL = 210                               # the longest side of a thumbnail
    COLUMNS = 4

    #: What the sheet can decide about the scan itself. Exactly the fields a
    #: `Roll` job carries, and deliberately no more: exposure and shading are
    #: session-wide rather than per-roll, so a copy of them here would either
    #: do nothing to the roll or quietly change the main window's next single
    #: scan, and offering a control that does neither of the things it looks
    #: like it does is worse than not offering it.
    #:
    #: `mono` is absent for the same reason it is absent from `PANEL_CONTROLS`:
    #: it follows the film type rather than being chosen, and a second control
    #: disagreeing with the one above it helps nobody.
    #:
    #: Data rather than a walk of the widget tree, so what the sheet sends can
    #: be checked against what a `Roll` accepts without opening a window.
    #:
    #: "Nudge registration between frames" is gone by that rule (GUI2-10,
    #: Stefan's call, 2026-10-10): every frame ticked here is held to its
    #: position, and a frame with one is never aimed, so it acted on no
    #: frame of any roll it was offered for. A stored sheet's ``correct`` is
    #: dropped as it is read (`_clean_sheet_state`).
    OPTIONS = ("dpi", "predpi", "film", "meter", "ir", "fast_ir")
    CHOSEN = "#e8b64c"                       # the filmstrip's amber, reused
    SKIPPED = "#7a3b3b"                      # unmistakably not amber
    SELECTED = "#ffffff"                     # the keyboard's place, not a tick
    DONE = "#5b7a5b"                         # already scanned: neither of those
    #: Where the frame-edge detector says the picture ends and unexposed base
    #: begins -- red, as in the study's contact sheets, and thin: it is drawn
    #: on the picture so Stefan can judge the reading before the film moves.
    EDGE_LINE = "#e0302a"
    #: The coloured line itself: thin, because it is a marker and not a mount.
    RING = 2
    #: The white gap between that line and the picture. The line used to sit
    #: hard against the thumbnail, where it read as an edge artefact of the
    #: picture rather than as something drawn around it. Standing it off gives
    #: the frame a border to be, the way a mounted print has one.
    MOUNT = 6

    def __init__(self, gui, frames, offsets=None, proposals=None,
                 rotations=None, flips=None,
                 done=None, ticks=None, options=None,
                 readings=None, generation=None, holds=None, numbers=None):
        self.gui = gui
        #: The strip's place in its roll of strips (`session.StripPlace`),
        #: for the numbers its frames have across the roll -- what their
        #: labels and delivered files carry -- or None for a roll from
        #: before strips, whose numbers are their places. Ticks, positions
        #: and turns are by place either way, as `Roll.only` takes them.
        self.numbers: StripPlace | None = numbers
        #: Each cell's tick, by place, for its text as the numbers change.
        self._tick_buttons: dict[int, ttk.Checkbutton] = {}
        #: Which of the frame-edge reader's walks this sheet shows, so an
        #: answer about another walk is never taken for this one's.
        self.generation = generation
        #: The detector's latest answer per frame, snapped -- ``(offset or
        #: None, note)`` -- kept apart from `offsets` so a frame he positioned
        #: can be put back where the detector puts it (`reset`).
        self.detected: dict[int, tuple[float | None, dict]] = {}
        self.frames = [r for r in frames if r.image is not None]
        # A frame that was walked but cannot be shown is not a cosmetic
        # problem: it cannot be ticked, so it silently does not get scanned.
        # It used to drop out with nothing said anywhere.
        dropped = [getattr(r, "number", "?") for r in frames
                   if r.image is None]
        if dropped:
            gui._say(f"contact sheet: {len(dropped)} walked frame(s) have no "
                     f"picture and are not shown -- {dropped}. They cannot be "
                     f"ticked, so they will not be scanned.")
        self.ticks: dict[int, tk.BooleanVar] = {}
        #: Ticks this sheet was reopened with, by frame number. Absent means
        #: "decide from `done`", which is the fresh-sheet rule. Kept separate
        #: from `self.ticks` because those are Tk variables and only exist once
        #: the cells are built.
        self._initial_ticks: dict[int, bool] = {int(n): bool(v) for n, v
                                                in dict(ticks or {}).items()}
        #: Scan options this sheet was reopened with. Anything absent falls
        #: back to the main window's control, which is what a sheet opened for
        #: the first time uses for all of them.
        self._initial_options: dict = dict(options or {})
        self.v_options: dict = {}
        #: Frames this roll has already scanned, from `roll.json`. They open
        #: unticked and say so, because a resumed roll should *finish* rather
        #: than start again -- three hours of transport is exactly what a
        #: resume exists to not spend twice. Ticking one anyway rescans it,
        #: which is the right escape hatch for a frame that came out wrong.
        self.done: set[int] = {int(n) for n in (done or ())}
        #: How the roll held each frame it scanned -- the hold's own record,
        #: by frame number (`roll_holds`). A frame it left off its position
        #: carries a badge saying so (`_badge`), because the list at the end
        #: of the roll is read once, and this is where the frames are looked
        #: at again when the roll is.
        self.holds: dict[int, dict] = {int(n): dict(v) for n, v
                                       in dict(holds or {}).items()
                                       if isinstance(v, dict)}
        #: Where each frame should sit, in mm, relative to where it was
        #: surveyed. Absent means no decision: as surveyed until the detector
        #: says otherwise. A zero is his "as surveyed" (`_FrameAdjuster._set`)
        #: and its note says so; the detector's in-place frames are absent.
        self.offsets: dict[int, float] = dict(offsets or {})
        #: Where each proposed offset came from, so a caption can say whether
        #: a number was measured, read by one detector and uncorroborated, or
        #: predicted from the frames either side. A proposal the operator
        #: cannot tell from a guess is one he has to check by hand anyway,
        #: which is the work this exists to save.
        self.proposals: dict[int, dict] = dict(proposals or {})
        #: The detector's reading of each frame's edges, kept apart from
        #: `proposals` because adjusting a frame by hand replaces its note --
        #: and the prescan still shows where its edges were read.
        self.edges: dict[int, dict] = {
            int(n): {"edges": note["edges"], "width": note.get("width")}
            for n, note in self.proposals.items()
            if isinstance(note, dict) and note.get("edges")}
        #: Which way up each frame has been *decided* to be, in degrees
        #: clockwise. Per frame because a strip is not one orientation: a
        #: portrait among landscapes is ordinary, and the session's single
        #: `rotation` can only get one of them right.
        #:
        #: Absolute, and **zero is a decision** -- unlike `offsets`, where an
        #: explicit zero and an absent entry mean the same thing. They cannot
        #: mean the same thing here: "rotate all" moves the session default, so
        #: a frame deliberately straightened afterwards would fall back to that
        #: default and be scanned sideways. It did, on the first strip this was
        #: driven on.
        self.rotations: dict[int, int] = {int(n): int(t) % 360 for n, t
                                          in dict(rotations or {}).items()}
        #: And whether each reads left to right, on the same terms: absolute,
        #: and an explicit False is a decision. A strip can go in the other way
        #: up, and that is not a fourth angle.
        self.flips: dict[int, bool] = {int(n): bool(v) for n, v
                                       in dict(flips or {}).items()}
        # A reopened survey arrives with the manifest's one arrangement on every
        # result; a frame decided individually overrides it, so the cell is
        # drawn the way it was left rather than the way the roll was.
        for result in self.frames:
            if result.number in self.rotations:
                result.rotation = self.rotations[result.number]
            if result.number in self.flips:
                result.flipped = self.flips[result.number]
        # Keyed by frame number rather than appended, because a cell is now
        # re-rendered when it is turned and a list would grow a PhotoImage per
        # rotation while holding every superseded one alive.
        self._photos: dict[int, tk.PhotoImage] = {}
        self._pictures: dict[int, tk.Label] = {}
        self._rings: dict[int, tk.Frame] = {}
        self._captions: dict[int, ttk.Label] = {}
        self._skips: dict[int, ttk.Label] = {}
        self._badges: dict[int, tk.Label] = {}
        self._adjuster = None
        #: Which cell the keyboard is on. Distinct from the tick: a frame can
        #: be selected and not scanned, or scanned and not selected, and the
        #: sheet had no notion of "this one" at all before there were keys.
        self.selected = 0
        self._bound: list[str] = []
        # What the reader had when the sheet opened; the rest arrives later
        # through the same door.
        if readings is not None:
            self.take_readings(*readings)

        self.top = tk.Toplevel(gui.root)
        # Which strip of which roll, with the numbers its frames have across
        # the roll: two numberings are on screen, and this says which one
        # the ticks use.
        self.top.title(sheet_title(self.numbers,
                                   [r.number for r in self.frames]))
        # What the gap inside the ring is filled with. Taken from the window
        # rather than named, so the border reads as space around the print on
        # whatever theme this is running under, rather than as a white
        # rectangle on a grey sheet.
        self.MOUNT_BG = self.top.cget("background")
        self.top.transient(gui.root)
        self.top.geometry(_geometry(980, 720))
        # Its own menu, parented on this window, so closing the sheet takes it
        # with it rather than leaving one attached to the main window.
        self.menu = tk.Menu(self.top, tearoff=0)

        outer = ttk.Frame(self.top, padding=(10, 8))
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, font=_font(12, bold=True),
                  text=f"{counted(len(self.frames), 'frame')} walked"
                  ).pack(anchor="w")
        # Two headers, because half of the usual one is about a scan that
        # cannot happen here, and a window that describes something it will not
        # do is the fault this sheet exists to avoid.
        said = ("Click a picture to tick it, double-click or press Return to "
                "set where the film should sit, right-click to arrange it. "
                "The arrow keys move between frames and Space ticks.")
        if gui.look_only:
            said = ("There is no film in the transport: this is a walk that "
                    "was stored earlier, and the positions under the frames "
                    "are measured from those pictures in the background -- "
                    "the frame edges count says how far that has got. "
                    + said + " Scanning is offered as it always is, and will "
                    "say there is no film when it reaches for it.")
        else:
            # Said as the roll does it. It claimed the automatic nudge still
            # covered the frames not adjusted -- every ticked frame is held to
            # its position, so it covered none, and its tick is gone (see
            # `OPTIONS`) -- and a rewind to the start of the strip, where the
            # roll goes to the first frame ticked, forward or back.
            said = ("Tick what is worth scanning. " + said + " A frame is "
                    "scanned the way you leave it here. Positions are used as "
                    "given -- nothing moves until you commission the scan. The "
                    "film goes to the first frame ticked, and every frame "
                    "nobody ticked after it costs its advance only.")
        ttk.Label(outer, foreground="#777", justify="left", wraplength=940,
                  text=said).pack(anchor="w", pady=(0, 8))

        # Canvas-with-a-frame-inside, the same shape as the options column:
        # Tk has no scrollable frame of its own.
        host = ttk.Frame(outer)
        host.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(host, highlightthickness=0, borderwidth=0)
        bar = ttk.Scrollbar(host, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        grid = ttk.Frame(self.canvas, padding=4)
        window = self.canvas.create_window((0, 0), window=grid, anchor="nw")
        grid.bind("<Configure>", lambda _e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(window, width=e.width))

        for column in range(self.COLUMNS):
            grid.columnconfigure(column, weight=1)
        for cell, result in enumerate(self.frames):
            self._cell(grid, result, cell // self.COLUMNS,
                       cell % self.COLUMNS, cell)

        self._build_options(outer)

        foot = ttk.Frame(outer)
        foot.pack(fill="x", pady=(8, 0))
        ttk.Button(foot, text="All", command=lambda: self._set_all(True)).pack(
            side="left")
        ttk.Button(foot, text="None", command=lambda: self._set_all(False)).pack(
            side="left", padx=4)
        self.v_count = tk.StringVar()
        ttk.Label(foot, textvariable=self.v_count, foreground="#777").pack(
            side="left", padx=10)
        ttk.Button(foot, text="Reset positions",
                   command=self.reset_all).pack(side="left", padx=(10, 4))
        # The main window's count, repeated here because the sheet usually
        # covers the window that carries it.
        self.v_edges = tk.StringVar(value=edges_label(gui.edge_watch.progress()))
        ttk.Label(foot, textvariable=self.v_edges, foreground="#777").pack(
            side="left", padx=10)
        ttk.Button(foot, text="Close", command=self._dismiss).pack(side="right")
        # The title bar's X as well: it is the way a window gets closed, and
        # routing only the button through `_dismiss` would keep the decisions
        # for one way out and drop them for the other.
        self.top.protocol("WM_DELETE_WINDOW", self._dismiss)
        self.b_scan = ttk.Button(foot, text="Scan chosen frames",
                                 command=self._scan)
        self.b_scan.pack(side="right", padx=6)

        # Bound after the cells exist: `_scrolls` walks the children it finds.
        gui._scrolls(self.canvas,
                     lambda amount, sideways: self.canvas.yview_scroll(amount, "units"),
                     precise=lambda dx, dy: _scroll_pixels(self.canvas, dx, dy))
        self.rebind()
        self.canvas.focus_set()
        self._changed()

    # -- what the roll will be scanned with --------------------------------

    def _build_options(self, parent) -> None:
        """The scan options this roll will use, set where it is commissioned.

        Pre-filled from the main window, and then its own: this is the sheet
        that decides the roll, so what is set here is what the roll is scanned
        with, and the main window keeps its values for single scans. Deciding
        "these four frames, at 3600, without infrared" across two windows, one
        of them behind this one, is how a roll gets scanned at the wrong
        resolution and nobody notices until it has cost the hours.
        """
        frame = ttk.Labelframe(parent, text="Scan the chosen frames with",
                               padding=(8, 4))
        frame.pack(fill="x", pady=(8, 0))

        top = ttk.Frame(frame)
        top.pack(fill="x")

        def pick(label, key, values, width, readonly=True):
            ttk.Label(top, text=label).pack(side="left")
            var = tk.StringVar(value=str(self._initial_options.get(
                key, getattr(self.gui, f"v_{key}").get())))
            widget = ttk.Combobox(
                top, textvariable=var, width=width, values=list(values),
                state="readonly" if readonly else "normal")
            widget.pack(side="left", padx=(4, 12))
            widget.bind("<<ComboboxSelected>>", lambda _e: self._changed())
            widget.bind("<KeyRelease>", lambda _e: self._changed())
            self.v_options[key] = var

        # dpi is typable rather than readonly, like the main window's: the
        # ladder is the useful set, not the only legal one.
        pick("scan dpi", "dpi", [str(d) for d in DPI_LADDER], 7, readonly=False)
        pick("prescan", "predpi", [str(d) for d in PRESCAN_LADDER], 6,
             readonly=False)
        pick("film", "film", FILM_TYPES, 11)
        pick("meter", "meter", METER_MODES, 8)

        bottom = ttk.Frame(frame)
        bottom.pack(fill="x", pady=(4, 0))
        for key, text in (("ir", "infrared (RGBI)"),
                          ("fast_ir", "infrared at scan resolution")):
            var = tk.BooleanVar(value=bool(self._initial_options.get(
                key, getattr(self.gui, f"v_{key}").get())))
            ttk.Checkbutton(bottom, text=text, variable=var,
                            command=self._changed).pack(side="left",
                                                        padx=(0, 14))
            self.v_options[key] = var

        self.l_options = ttk.Label(frame, foreground="#8a6d00", wraplength=900,
                                   justify="left")
        self.l_options.pack(anchor="w", pady=(4, 0))

    def scan_options(self) -> dict:
        """What this sheet says the roll should be scanned with."""
        return {key: var.get() for key, var in self.v_options.items()}

    def _options_differ(self) -> list[str]:
        """Which of them the main window would have done differently.

        Said out loud, because the sheet is opened on top of the window it was
        pre-filled from: a roll scanned at something other than what the panel
        behind it still shows is exactly the surprise this panel introduces,
        and the only defence against it is saying so before the dialog.
        """
        differ = []
        for key, var in self.v_options.items():
            mine, theirs = var.get(), getattr(self.gui, f"v_{key}").get()
            if str(mine) != str(theirs):
                differ.append(f"{key} {theirs} -> {mine}")
        return differ

    # -- the keyboard ------------------------------------------------------

    def _actions(self) -> dict:
        return {
            "sheet_left": lambda: self._move(-1),
            "sheet_right": lambda: self._move(1),
            "sheet_up": lambda: self._move(-self.COLUMNS),
            "sheet_down": lambda: self._move(self.COLUMNS),
            "sheet_toggle": lambda: self._on_selected(self._toggle),
            "sheet_adjust": lambda: self.adjust(self.selected),
            "sheet_all": lambda: self._set_all(True),
            "sheet_none": lambda: self._set_all(False),
            "sheet_rotate_right": lambda: self._on_selected(self._rotate, 90),
            "sheet_rotate_left": lambda: self._on_selected(self._rotate, 270),
            "sheet_rotate_180": lambda: self._on_selected(self._rotate, 180),
            "sheet_straighten": lambda: self._on_selected(self._straighten),
            "sheet_flip": lambda: self._on_selected(self._flip),
            "sheet_show": self._show_selected,
            # `_dismiss`, not `destroy`: Escape is a fourth way out of this
            # window and it used to throw away everything the other three
            # keep. A tick, a drag and a turn all died with it, silently.
            "sheet_close": self._dismiss,
        }

    def rebind(self) -> None:
        """Take the window's current keys, here and in the position window."""
        for sequence in self._bound:
            try:
                self.top.unbind(sequence)
            except tk.TclError:
                pass
        self._bound = []
        actions = self._actions()
        scoped = shortcuts.in_scope(self.gui.keys, "sheet")
        for sequence, action_id in scoped.items():
            run = actions.get(action_id)
            if run is not None:
                bound = self.gui._bind_key(self.top, sequence, action_id, run,
                                           taken=scoped)
                if bound:
                    self._bound.append(bound)
        if self._adjuster is not None and self._adjuster.alive():
            self._adjuster.rebind()

    def _on_selected(self, method, *args) -> None:
        if 0 <= self.selected < len(self.frames):
            method(self.frames[self.selected].number, *args)

    def _show_selected(self) -> None:
        if 0 <= self.selected < len(self.frames):
            self.gui._show_seq(self.frames[self.selected].seq)

    def _clicked(self, number: int, index: int) -> None:
        """A click both picks the frame and ticks it, as it always has."""
        self._select(index)
        self._toggle(number)

    def _double_clicked(self, number: int, index: int) -> None:
        """Set where the frame sits, leaving its tick as it was.

        The first press of a double-click is a click, so it has already
        toggled the tick by the time the second one gets here. That toggle is
        taken back: the position window loaded the flipped tick, and left with
        Done or Escape a frame ticked for the roll came out of it unticked and
        was silently not scanned.
        """
        self._toggle(number)
        self.adjust(index)

    def _move(self, by: int) -> None:
        self._select(self.selected + by)

    def _select(self, index: int) -> None:
        """Put the keyboard on one cell and make sure it can be seen."""
        if not self.frames:
            return
        self.selected = max(0, min(len(self.frames) - 1, index))
        self._paint_rings()
        self._scroll_to(self.selected)

    def _scroll_to(self, index: int) -> None:
        """Scroll only as far as it takes to have the selected cell in view.

        The same rule the filmstrip follows: a sheet that jumped somewhere on
        every keypress would lose the operator's place rather than keep it.
        """
        number = self.frames[index].number
        ring = self._rings.get(number)
        if ring is None:
            return
        self.canvas.update_idletasks()
        try:
            total = self.canvas.bbox("all")[3]
        except (TypeError, IndexError):
            return
        height = max(1, self.canvas.winfo_height())
        if total <= height:
            return
        top = ring.winfo_rooty() - self.canvas.winfo_rooty() + self.canvas.canvasy(0)
        bottom = top + ring.winfo_height()
        seen = self.canvas.canvasy(0)
        if top < seen:
            self.canvas.yview_moveto(max(0.0, (top - 8) / total))
        elif bottom > seen + height:
            self.canvas.yview_moveto(min(1.0, (bottom + 8 - height) / total))

    # -- one picture -------------------------------------------------------

    def _cell(self, grid, result, row: int, column: int, index: int) -> None:
        number = result.number
        # Everything ticked; untick the duds -- except on a resumed roll, where
        # what is already scanned starts unticked, and except where the sheet
        # is being reopened on a decision already made about this frame, which
        # outranks both defaults because somebody made it on purpose.
        var = tk.BooleanVar(
            value=self._initial_ticks.get(number, number not in self.done))
        self.ticks[number] = var

        cell = ttk.Frame(grid, padding=6)
        cell.grid(row=row, column=column, sticky="n")
        # Three nested frames, so the coloured line can stand off the picture:
        # the ring is the line, the mount is the white gap inside it, and the
        # picture sits in that. One frame with thick padding made a broad band
        # of colour instead of a border with room around the print.
        ring = tk.Frame(cell, background=self.CHOSEN,
                        padx=self.RING, pady=self.RING)
        ring.pack()
        self._rings[number] = ring
        mount = tk.Frame(ring, background=self.MOUNT_BG,
                         padx=self.MOUNT, pady=self.MOUNT)
        mount.pack()

        photo = self._render(result)
        picture = tk.Label(mount, image=photo, borderwidth=0)
        picture.pack()
        self._pictures[number] = picture
        picture.bind("<Button-1>",
                     lambda _e, n=number, i=index: self._clicked(n, i))
        picture.bind("<Double-Button-1>",
                     lambda _e, n=number, i=index: self._double_clicked(n, i))
        for seq in MENU_EVENTS:
            picture.bind(
                seq, lambda e, i=index: self.on_cell_menu(e, i))
        self._badge(mount, number, index)

        # By its number across the roll -- what its file and label carry --
        # and its place on the strip under it, in the caption, wherever the
        # two differ (`_refresh_caption`).
        tick = ttk.Checkbutton(cell, text=self._tick_text(result), variable=var,
                               command=self._changed)
        tick.pack(anchor="w", pady=(4, 0))
        self._tick_buttons[number] = tick
        caption = ttk.Label(cell, foreground="#777")
        caption.pack(anchor="w")
        caption.bind("<Button-1>", lambda _e, i=index: self.adjust(i))
        self._captions[number] = caption
        self._refresh_caption(number)
        skip = ttk.Label(cell, foreground="#e0605a", text="not scanning")
        self._skips[number] = skip        # packed by _changed when unticked
        short = (result.registration or {}).get("shortfall_mm") or 0.0
        if short > 0.85:
            # The same 0.85 mm the driver calls drift. Worth saying here: a
            # frame this far out has picture outside the aperture, and no
            # amount of scanning it brings that back.
            ttk.Label(cell, foreground="#e0605a",
                      text=f"drifted -- {say_units(short, signed=False)} "
                           "outside").pack(anchor="w")

    def shown_number(self, number: int) -> int | None:
        """Frame ``number`` of this strip -- its place -- by its number
        across the roll, as its walk filed it or its place gives it
        (`shown_number`); its place for a roll from before strips."""
        result = self._find(number)
        given = getattr(result, "roll_number", 0) if result is not None else 0
        return shown_number(self.numbers, number, given or 0)

    def _tick_text(self, result) -> str:
        """A cell's tick: ``Frame 8``, by its number across the roll -- and
        no number for the stub past "Strip ends here", which is no frame of
        the roll. It showed the number its walk prescan was filed with, the
        number the next strip's first frame then took (the 2026-10-12
        tour: two "Frame 12"s)."""
        strip = self.numbers
        if strip is not None and strip.past_its_end(result.number):
            return "Past the strip's end"
        shown = self.shown_number(result.number)
        return f"Frame {shown if shown is not None else '?'}"

    def _place_said(self, number: int) -> str:
        """The caption's first line: ``strip 2 · 2`` -- the frame's place --
        wherever its number across the roll is another, and that it lies
        past where "Strip ends here" put the strip's end; empty otherwise,
        so strip 1 and a roll from before strips read as they always did."""
        strip = self.numbers
        if strip is None:
            return ""
        past = strip.past_its_end(number)
        if self.shown_number(number) == number:
            return "past where the strip ends" if past else ""
        said = f"strip {strip.strip} · {number}"
        return said + " -- past where the strip ends" if past else said

    def renumber(self, strip: StripPlace | None) -> None:
        """Take the strip's place in its roll as it is now -- "Strip ends
        here" moved where it ends -- into the title, ticks and captions.
        A frame past the new end is unticked and said: a scan of it is
        refused (`session.past_strip_end`), being no frame of the film."""
        self.numbers = strip
        self.top.title(sheet_title(strip, [r.number for r in self.frames]))
        unticked = []
        for result in self.frames:
            number = result.number
            tick = self._tick_buttons.get(number)
            if tick is not None:
                tick.configure(text=self._tick_text(result))
            if (strip is not None and strip.past_its_end(number)
                    and self.ticks[number].get()):
                self.ticks[number].set(False)
                unticked.append(number)
            self._refresh_caption(number)
        if unticked:
            # `places_said` says them past its end, by their places.
            self.gui._say(f"unticked {places_said(strip, unticked)} -- no "
                          "frame of the film")
        self._changed()

    def _render(self, result) -> tk.PhotoImage:
        """This frame's thumbnail, the way up it is currently turned.

        Called to build a cell and again whenever one is rotated, so the two
        cannot disagree about how a cell is drawn. The photo is kept against
        the frame number because Tk drops an image nothing references.

        preview.sample, not preview.fit: fit decimates by whole integers only,
        so a 428 px prescan asked to fill a 210 px cell comes back 143 px wide
        -- a third of the space, and softer than it needs to be. sample scales
        fractionally and fills the cell.
        """
        turned = preview.orient(result.image, result.rotation, result.flipped)
        scale = min(self.CELL / max(turned.shape[1], 1),
                    self.CELL / max(turned.shape[0], 1))
        arr = preview.render(
            preview.sample(turned, scale, 0.0, 0.0,
                           max(1, int(turned.shape[1] * scale)),
                           max(1, int(turned.shape[0] * scale))),
            "RGB", self.gui.v_invert.get(),
            cuts=(preview.channel_levels(result.levels, "RGB")
                  if getattr(result, "levels", None) is not None else None))
        # The detector's edges, on the picture itself -- the only lines a cell
        # carries, so every line on the sheet is the detector's reading.
        arr = paint_edges(arr, self.edges.get(result.number), result.rotation,
                          result.flipped)
        photo = tk.PhotoImage(data=preview.to_ppm(arr))
        self._photos[result.number] = photo
        return photo

    def _badge(self, mount, number: int, index: int) -> None:
        """A frame the roll scanned off its position, said on the picture's corner.

        Two kinds, as the filmstrip marks them (`HOLD_BADGE`): how far out,
        where the hold's last look measured it, and "not verified" where no
        look could. Laid over the picture rather than painted into it, so it
        stays upright and in its corner however the frame is turned; a click
        on it does what a click on the picture does, so it is no dead spot.
        """
        held = self.holds.get(number)
        level = hold_level(held)
        if level is None:
            return
        background, ink = HOLD_BADGE[level]
        badge = tk.Label(mount, text=hold_badge(held), background=background,
                         foreground=ink, font=_font(9, bold=True),
                         padx=4, pady=0, borderwidth=0)
        badge.place(relx=1.0, rely=0.0, x=-self.MOUNT, y=self.MOUNT, anchor="ne")
        badge.bind("<Button-1>",
                   lambda _e, n=number, i=index: self._clicked(n, i))
        badge.bind("<Double-Button-1>",
                   lambda _e, n=number, i=index: self._double_clicked(n, i))
        self._badges[number] = badge

    def mark_done(self, number: int, held: dict | None) -> None:
        """Frame ``number`` was scanned and filed while this sheet was open
        (`ScannerGui._mark_filed`): done, unticked, its caption saying so,
        and its badge as the roll held it this time -- none where it was
        held, as `holds_in` reads the folder for a sheet opened later."""
        number = int(number)
        self.done.add(number)
        if held is None:
            self.holds.pop(number, None)
        else:
            self.holds[number] = dict(held)
        badge = self._badges.pop(number, None)
        if badge is not None:
            badge.destroy()
        picture = self._pictures.get(number)
        index = next((i for i, r in enumerate(self.frames)
                      if r.number == number), None)
        if picture is not None and index is not None:
            self._badge(picture.master, number, index)
        tick = self.ticks.get(number)
        if tick is not None:
            tick.set(False)
        self._refresh_caption(number)
        self._changed()

    def turned_elsewhere(self, result) -> None:
        """A frame of this sheet turned or mirrored outside it -- in the
        filmstrip, where its walked prescan is the same result -- recorded
        here as if turned here, and its cell drawn again. Only its result
        changed, and the sheet's own records laid the old turn back over it
        the next time it opened, without a word."""
        if not any(r is result for r in self.frames):
            return
        number = result.number
        self.rotations[number] = int(result.rotation) % 360
        self.flips[number] = bool(result.flipped)
        picture = self._pictures.get(number)
        if picture is not None:
            picture.configure(image=self._render(result))
        self.gui._keep_sheet_soon()

    # -- arranging ---------------------------------------------------------

    def on_cell_menu(self, event: tk.Event, index: int) -> str:
        """What can be done to one frame without leaving the sheet.

        Right-clicking selects the cell first. Without that the menu would
        offer "Rotate right, R" over one frame while R turned a different one
        -- the key acts on the selection and the menu on what was clicked, and
        the two saying different things about the same item is worse than
        either alone.
        """
        self._select(index)
        result = self.frames[index]
        number = result.number
        key = self.gui.accelerator
        self.menu.delete(0, "end")
        for label, degrees, action_id in (
                ("Rotate right 90°", 90, "sheet_rotate_right"),
                ("Rotate left 90°", 270, "sheet_rotate_left"),
                ("Rotate 180°", 180, "sheet_rotate_180")):
            self.menu.add_command(
                label=label, accelerator=key(action_id),
                command=lambda n=number, d=degrees: self._rotate(n, d))
        if result.rotation:
            self.menu.add_command(
                label=f"Straighten (now {result.rotation}°)",
                accelerator=key("sheet_straighten"),
                command=lambda n=number: self._straighten(n))
        self.menu.add_command(
            label="Unflip" if result.flipped else "Flip left-right",
            accelerator=key("sheet_flip"),
            command=lambda n=number: self._flip(n))
        self.menu.add_separator()
        self.menu.add_command(
            label="Rotate all right 90°",
            command=lambda: self._rotate_all(90))
        self.menu.add_command(
            label="Rotate all left 90°",
            command=lambda: self._rotate_all(270))
        self.menu.add_command(
            label=("Unflip all" if all(r.flipped for r in self.frames)
                   else "Flip all left-right"),
            command=self._flip_all)
        self.menu.add_separator()
        self.menu.add_checkbutton(
            label="Scan this frame", variable=self.ticks[number],
            accelerator=key("sheet_toggle"), command=self._changed)
        self.menu.add_command(label="Set position ...",
                              accelerator=key("sheet_adjust"),
                              command=lambda i=index: self.adjust(i))
        self.menu.add_command(
            label="Show in preview", accelerator=key("sheet_show"),
            command=lambda s=result.seq: self.gui._show_seq(s))
        # Where the strip ends, on its roll's open last strip only: before
        # the next strip fixes its numbers, a stub at its end -- a part
        # frame, a blank -- is left out of them. Refused in words where it
        # cannot be (`ScannerGui.on_strip_end`).
        strip = self.numbers
        if strip is not None and strip.last is None:
            self.menu.add_separator()
            self.menu.add_command(
                label="Strip ends here",
                command=lambda n=number: self.gui.on_strip_end(n))
            if strip.ends_at is not None:
                self.menu.add_command(
                    label="Count to the end of the strip",
                    command=lambda: self.gui.on_strip_end(None))
        self.menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def _orient(self, result, degrees: int = 0, flip: bool | None = None) -> str:
        """Record one frame's new arrangement and redraw its cell.

        Recorded against the number rather than applied to pixels: a prescan is
        the reference a scan is checked against, and it has to stay in the
        film's own orientation. `approved_from_sheet` carries this into the
        roll, where it reaches that frame's delivered file alone.

        Says nothing and leaves the filmstrip alone -- the callers below do
        that once each, so arranging seventeen frames costs one redraw and one
        line in the log rather than seventeen of both.
        """
        number = result.number
        result.rotation = (result.rotation + degrees) % 360
        # Set, not toggled: `flip` is the state wanted, None meaning "leave it".
        # A toggle would make "flip all" swap each frame instead of agreeing
        # them, so a half-mirrored strip came out still half-mirrored -- which
        # is the one thing an operator reaching for "all" is trying to fix.
        if flip is not None:
            result.flipped = flip
        # Recorded even when they come back to nothing: see `self.rotations`.
        self.rotations[number] = result.rotation
        self.flips[number] = result.flipped
        picture = self._pictures.get(number)
        if picture is not None:
            picture.configure(image=self._render(result))
        # The same photograph, not merely the same cell: the window remembers
        # how this picture is arranged, so the preview behind this sheet and
        # the scan taken later both agree with what was just decided here.
        # Recorded only: shown, it put this frame in front of whatever the
        # preview held, and "rotate all" read every frame's entry in turn.
        self.gui._record_arrangement(result)
        self.gui._keep_sheet_soon()
        return _arrangement(result)

    def _find(self, number: int):
        return next((r for r in self.frames if r.number == number), None)

    def _rotate(self, number: int, degrees: int) -> None:
        """Turn one frame. The others keep whatever they were."""
        self._one(number, degrees=degrees)

    def _straighten(self, number: int) -> None:
        """Take this frame's turn off, leaving any mirror alone."""
        result = self._find(number)
        if result is not None and result.rotation:
            self._one(number, degrees=-result.rotation)

    def _flip(self, number: int) -> None:
        """Mirror one frame, or put it back. The others keep what they were."""
        result = self._find(number)
        if result is not None:
            self._one(number, flip=not result.flipped)

    def _one(self, number: int, degrees: int = 0,
             flip: bool | None = None) -> None:
        result = self._find(number)
        if result is None:
            return
        said = self._orient(result, degrees, flip)
        # The same picture is in the filmstrip and in the preview behind this
        # window, and one frame shown three ways round is how an operator
        # loses track of how it will be scanned.
        self.gui._reshow(result)
        self.gui._say(f"{self._frame_said(number)}: {said} -- scanned this "
                      "way; the other frames are unchanged" + self._too_late())

    def _frame_said(self, number: int) -> str:
        """Frame ``number`` -- its place -- as the log says a frame of this
        sheet: by its number across the roll, its place beside it where the
        two differ (`session.frame_said`)."""
        strip = self.numbers
        shown = self.shown_number(number)
        if strip is None:
            return f"frame {number}"
        if shown is None:
            # Past a closed range and not numbered yet: its place alone.
            return f"frame ? (strip {strip.strip} · {number})"
        return frame_said(shown, strip.strip, number)

    def _too_late(self, every: bool = False) -> str:
        """What a turn made while a roll is in hand does not reach, said --
        ``every`` for one that turned every frame and moved what new scans
        follow (`_all`).

        The sheet stays open, and can be edited, while a roll runs: what is
        changed is for the next commission, and comes back after a restart
        (Stefan's call, 2026-10-10). A roll commissioned from it writes each
        frame the way it was commissioned, and its frames are shown that way
        as they arrive (`ScannerGui._written_as`), so a cell here can show a
        turn the frame just scanned does not have -- said here, as it is
        made. A roll from the Roll button turns every frame alike, as the
        window's last turn says, and "rotate all" moves that.
        """
        roll, running = self.gui._roll_in_hand()
        if roll is None:
            return ""
        which = ("the roll running now" if running
                 else "the roll waiting to start")
        if roll.approved:
            return (f" -- kept for the next commission: {which} writes and "
                    "shows each frame the way it was commissioned")
        if every:
            return f" -- and {which} follows it from its next frame"
        return (f" -- not for {which}, which turns every frame alike, as the "
                "filmstrip or \"rotate all\" last set")

    def _rotate_all(self, degrees: int) -> None:
        """Turn every frame, and make it the session's default too."""
        self._all(degrees=degrees)

    def _flip_all(self) -> None:
        """Agree every frame's mirror, and make it the session's default too.

        Mirrored unless they already all are, in which case this puts them
        back -- so the menu item that says "unflip all" does that, and pressing
        it twice lands where it started rather than somewhere new.
        """
        self._all(flip=not all(r.flipped for r in self.frames))

    def _all(self, degrees: int = 0, flip: bool | None = None) -> None:
        """Arrange every frame, and make it what everything else follows.

        A whole roll one way round is the ordinary case. If the operator says
        it here he should not have to say it again for everything scanned
        outside the sheet, so this sets the same carry-over the filmstrip's
        menu sets. Each frame still gets its own recorded arrangement, so one
        of them can be put back afterwards without disturbing the rest.
        """
        said = ""
        for result in self.frames:
            said = self._orient(result, degrees, flip)
        if self.frames:
            last = self.frames[-1]
            self.gui.rotation = last.rotation
            self.gui.flip = last.flipped
            self.gui.session.arrange(last.rotation, last.flipped)
        self.gui._reshow(*self.frames)
        self.gui._say(f"every frame: {said} -- and new scans follow this "
                      "until something says otherwise"
                      + self._too_late(every=True))

    # -- adjusting ---------------------------------------------------------

    def adjust(self, index: int) -> None:
        """Open the position adjuster on one frame.

        One window, reused: opening a second for every double-click would
        leave a trail of them all editing the same dictionary.
        """
        if self._adjuster is not None and self._adjuster.alive():
            self._adjuster.index = index
            self._adjuster._load()
            self._adjuster.top.lift()
            return
        self._adjuster = _FrameAdjuster(self, self.gui, index)

    def _refresh_caption(self, number: int) -> None:
        """The cell's line under the picture, and a position filed as it moves.

        When the operator has set a position, that is what the cell shows, in
        the sheet's amber -- it is his number and it is the one that will be
        acted on.

        A proposed position shows the same number and says where it came from,
        because the two are not worth the same and he is the one who decides
        which to trust:

          (measured)      two independent detectors agreed on it
          (unconfirmed)   one could read the frame and nothing corroborated it
          (neighbours)    nothing could read it; the strip's line spoke for it

        The offset this replaced read +-0.00 mm on every real prescan, because
        `film_bounds` abstains on all of them.

        The wording itself is `frame_caption`, which is testable without a
        window. This is the lookup around it.
        """
        # Every change of position comes through here; see `_keep_sheet_soon`.
        self.gui._keep_sheet_soon()
        caption = self._captions.get(number)
        if caption is None:
            return
        marks = next((r.registration or {} for r in self.frames
                      if r.number == number), {})
        note = self.proposals.get(number) or {}
        said, colour = frame_caption(
            self.offsets.get(number) or 0.0,
            note.get("source"),
            done=number in self.done,
            contrast=marks.get("contrast", 0),
            read=bool(note.get("in_place")),
        )
        place = self._place_said(number)
        if place:
            said = f"{place}\n{said}"
        caption.configure(text=said, foreground={
            "DONE": self.DONE, "CHOSEN": self.CHOSEN}.get(colour, "#777"))

    def adjusted(self) -> dict:
        """The positions set by hand, keyed by frame number."""
        return dict(self.offsets)

    # -- the detector's answers, as they arrive ----------------------------

    def take_readings(self, offsets: dict, notes: dict) -> None:
        """The frame-edge reader's newest answer for this walk.

        Arrives while the sheet is open -- first each frame as the walk
        delivered it, then again against the whole walk -- so a frame's
        position and edge lines can change under his eyes until the light
        goes green. Only the detector's: a position he set stands, and only
        its edge lines follow the reading.
        """
        snapped, notes = _snap_proposals(offsets, notes)
        changed = []
        for result in self.frames:
            number = result.number
            note = notes.get(number)
            if note is None:
                continue                          # not read yet
            answer = (snapped.get(number), note)
            if self.detected.get(number) == answer:
                continue
            self.detected[number] = answer
            edges = ({"edges": note["edges"], "width": note.get("width")}
                     if note.get("edges") else None)
            redraw = self.edges.get(number) != edges
            if edges is None:
                self.edges.pop(number, None)
            else:
                self.edges[number] = edges
            if (self.proposals.get(number) or {}).get("source") != "operator":
                self._apply_detected(number)
            picture = self._pictures.get(number)
            if redraw and picture is not None:
                picture.configure(image=self._render(result))
            self._refresh_caption(number)
            changed.append(number)
        if (changed and self._adjuster is not None and self._adjuster.alive()
                and self._adjuster.number in changed):
            self._adjuster._refresh()

    def _apply_detected(self, number: int) -> None:
        value, note = self.detected.get(number, (None, None))
        if value:
            self.offsets[number] = value
        else:
            self.offsets.pop(number, None)
        if note is not None:
            self.proposals[number] = dict(note)
        else:
            self.proposals.pop(number, None)

    def reset(self, number: int) -> None:
        """Put one frame back where the detector puts it, forgetting his position.

        Not `Centre`, which is a position of his own -- "as surveyed". A frame
        the reader has not got to yet goes back to as surveyed, and takes its
        reading when that arrives.
        """
        self._apply_detected(number)
        self._refresh_caption(number)

    def reset_all(self) -> None:
        """Every frame back where the detector puts it, after asking."""
        mine = sorted(n for n, v in self.proposals.items()
                      if (v or {}).get("source") == "operator")
        if mine and not messagebox.askyesno(
                "Reset positions",
                "Put every frame back where the frame-edge detector puts it?"
                f"\n\nThe position{'s' if len(mine) != 1 else ''} you set on "
                f"frame{'s' if len(mine) != 1 else ''} "
                f"{', '.join(str(n) for n in mine)} "
                f"{'are' if len(mine) != 1 else 'is'} dropped. Ticks and turns "
                "stay as they are.", parent=self.top):
            return
        for result in self.frames:
            self.reset(result.number)
        if self._adjuster is not None and self._adjuster.alive():
            self._adjuster._refresh()
        self.gui._say("contact sheet: every position is the detector's again"
                      + (f" -- dropped yours on {mine}" if mine else ""))

    def state(self) -> dict:
        """Every decision made here, in plain types, for reopening it.

        Ticks are part of it: "scan these four" is a decision like any other,
        and a sheet rebuilt without them comes back with the whole strip
        ticked, which is the opposite of what was decided.

        In none of the three per-frame maps is an explicit zero the same as an
        absent entry. `rotations` and `flips` because "rotate all" moves the
        session default and a frame straightened by hand would fall back to
        it and be scanned sideways -- that happened on the first strip this
        was driven on. `offsets` because an absent position is the
        detector's to fill, and his "as surveyed" was replaced by its number
        on the next reopen.
        """
        return {
            "ticks": {int(n): bool(v.get()) for n, v in self.ticks.items()},
            "offsets": {int(n): float(v) for n, v in self.offsets.items()},
            "rotations": {int(n): int(t) for n, t in self.rotations.items()},
            "flips": {int(n): bool(f) for n, f in self.flips.items()},
            # Who decided each position. Without it a reopened sheet handed
            # every offset back as `kept`, and `_merge_kept` stamps
            # `operator` over anything kept -- so closing the window and
            # opening it again relabelled every machine proposal as his, and
            # the confirm dialog then counted them as positions he had set.
            "sources": {int(n): str((v or {}).get("source") or "")
                        for n, v in self.proposals.items()
                        if (v or {}).get("source")},
            # Not keyed by frame: one set for the roll. Kept with the rest so
            # a sheet reopened for a strip comes back describing the same scan
            # it described when it was closed.
            "options": self.scan_options(),
        }

    def _dismiss(self) -> None:
        """Close, keeping what was decided.

        Every way out of this window goes through here -- the Close button,
        the title bar's X, and commissioning the scan -- because the window is
        destroyed on the way out and the decisions live in it. They used to
        die with it: closing the sheet and opening it again rebuilt it from
        the survey, and every position set by hand was gone with nothing said.
        """
        try:
            self.gui._store_sheet_state(self.state())
        except Exception as exc:                          # noqa: BLE001
            # Remembering is never allowed to stop the window closing. A sheet
            # that will not close is worse than one that forgets.
            self.gui._say(f"could not keep the contact sheet settings: {exc}")
        if self._adjuster is not None and self._adjuster.alive():
            self._adjuster.top.destroy()
        self.top.destroy()

    # -- picking -----------------------------------------------------------

    def _toggle(self, number: int) -> None:
        self.ticks[number].set(not self.ticks[number].get())
        self._changed()

    def _set_all(self, on: bool) -> None:
        for var in self.ticks.values():
            var.set(on)
        self._changed()

    def chosen(self) -> tuple[int, ...]:
        return tuple(sorted(n for n, var in self.ticks.items() if var.get()))

    def _paint_rings(self) -> None:
        """Ticked or not, and which one the keyboard is on.

        Two different questions on one cell, so two different marks: the ring's
        colour says whether it will be scanned, and the outline around it says
        where the keyboard is. A cell can be either without being the other.
        """
        chosen = ({self.frames[self.selected].number}
                  if 0 <= self.selected < len(self.frames) else set())
        for number, ring in self._rings.items():
            on = self.ticks[number].get()
            colour = self.CHOSEN if on else self.SKIPPED
            ring.configure(
                background=colour,
                highlightthickness=2,
                # Its own colour when it is not the selected one: invisible,
                # and the cell keeps the same size either way, so moving the
                # selection does not make the grid jump.
                highlightbackground=(self.SELECTED if number in chosen
                                     else colour))

    def _changed(self) -> None:
        # Ticks and options, filed as they change; see `_keep_sheet_soon`.
        self.gui._keep_sheet_soon()
        picked = self.chosen()
        self._paint_rings()
        for number in self._rings:
            on = self.ticks[number].get()
            # The ring alone is not enough to see. A prescan of a negative is
            # very dark -- measured across real surveys, mean 16 of 255 -- so
            # a dark ring around a nearly black picture reads as an empty
            # space, and a frame that had merely been clicked off looked like
            # a frame that had vanished. It cost two rolls: the operator saw
            # the first frame "disappear" and it simply went unscanned.
            label = self._skips.get(number)
            if label is not None:
                if on:
                    label.pack_forget()
                else:
                    label.pack(anchor="w")
        # Costed against this sheet's own options rather than the window's,
        # because those are what its button will ask for.
        options = self.scan_options() if self.v_options else {}
        try:
            dpi = int(str(options.get("dpi", "")).strip())
        except (TypeError, ValueError):
            dpi = None                       # mid-edit; fall back to the window
        per = self.gui._per_frame_seconds(
            dpi=dpi, ir=options.get("ir"), fast_ir=options.get("fast_ir"))
        self.v_count.set(
            f"{len(picked)} of {len(self.frames)} chosen"
            + (f"   ·   about {_duration(per * len(picked))}" if picked else ""))
        self.b_scan.configure(state="normal" if picked else "disabled")
        if self.v_options:
            differ = self._options_differ()
            self.l_options.configure(
                text=("Not what the main window is set to: "
                      + ", ".join(differ)) if differ else "")

    def _scan(self) -> None:
        picked = self.chosen()
        approved = approved_from_sheet(self.frames, picked, self.offsets,
                                       self.proposals)
        # Read before the window goes: these are Tk variables that live in it,
        # and `_dismiss` destroys it.
        options = self.scan_options()
        # Closed once the roll is handed over, not before the question. It
        # used to go first, so a Cancel, a busy scanner, a calibration still
        # to make or a refused setting left the sheet closed behind a message
        # telling him to "press this again".
        # And the detector's own reading of each frame, which is kept beside
        # a position he set (`_write_approved`): `proposals` holds his there.
        detected = {n: note for n, (_value, note) in self.detected.items()
                    if note is not None}
        if not self.gui.on_scan_chosen(picked, approved, options,
                                       readings=self.proposals,
                                       detected=detected):
            return
        if self._adjuster is not None and self._adjuster.alive():
            self._adjuster.top.destroy()
        self._dismiss()

    def alive(self) -> bool:
        try:
            return bool(self.top.winfo_exists())
        except tk.TclError:
            return False


def _within(path, root) -> bool:
    """Whether `path` is inside the folder `root`, both as the disk sees them."""
    if not root:
        return False
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
    except (OSError, ValueError):
        return False
    return True


def _descendants(widget):
    """Every widget under `widget`, depth first, including nested frames.

    `winfo_children` is one level, and the panels are rows inside rows -- a
    label and its combobox sit in a frame of their own so they line up.
    """
    for child in widget.winfo_children():
        yield child
        yield from _descendants(child)


def _sash_positions(pane) -> list[int]:
    """Where a paned window's dividers sit, however many it has.

    Tk gives no count, so they are read until one refuses -- a pane with two
    panels has one sash, and asking for a second raises rather than returning
    nothing.
    """
    positions: list[int] = []
    for index in range(8):
        try:
            positions.append(int(pane.sashpos(index)))
        except Exception:                                # noqa: BLE001
            break
    return positions


def _sized(photo, size, width: int, height: int):
    """`photo` if it is already this size, otherwise a new one that is.

    The size is remembered on this side rather than asked for: `width()` and
    `height()` are round trips into Tk, and four of them a frame is not free
    when a frame is aiming at sixteen milliseconds.
    """
    if photo is not None and size == (width, height):
        return photo, size
    made = tk.PhotoImage(width=max(1, width), height=max(1, height))
    return made, (width, height)


def _numbers(text: str) -> list[float] | None:
    """The numbers in `text`, or None if any of it is not one."""
    try:
        return [float(v) for v in text.replace(",", " ").split()]
    except ValueError:
        return None


def _duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 90:
        return f"{seconds} s"
    minutes, rest = divmod(seconds, 60)
    if minutes < 90:
        return f"{minutes}m {rest:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


#: Where a demo run writes. It reads the real library for pixels -- that is the
#: point of it -- but it must not file into it: a demo scan is not a scan, and
#: an afternoon of trying the window out would leave the library full of
#: entries whose raw bytes belong to some other photograph.
DEMO_ROOT = Path("demo")


def _claim_real_pixels() -> None:
    """Ask Windows not to stretch this window's bitmap.

    Without it a process is "DPI unaware": at 150% Windows draws the window at
    100% and scales the result up, so the whole thing is soft, and
    `winfo_fpixels` reports 96 whatever the display is really doing -- which
    would make `_px` a no-op exactly where it is needed. Must be called before
    the first Tk root exists.

    No-op anywhere else, and harmless if it fails: an unscaled window is worse
    than a scaled one but better than no window.
    """
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # system-DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()    # pre-8.1 fallback
        except Exception:
            pass


def main() -> int:
    use_utf8_stdout()
    _claim_real_pixels()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--demo", action="store_true",
                    help="drive the window from stored library entries, with "
                         "no scanner on the bus; writes under demo/")
    ap.add_argument("--library", default=None,
                    help="where scans are filed (default: library, or "
                         "demo/library with --demo, which refuses a folder "
                         "outside demo/)")
    ap.add_argument("--demo-source", default="library",
                    help="which library --demo shows pictures from; a roll "
                         "after the first also draws on every library beside "
                         "it, such as 'library 2'")
    ap.add_argument("--demo-entry", default=None,
                    help="a specific library entry for --demo to show; by "
                         "default the highest-resolution one that has both a "
                         "prescan and a scan of the same picture")
    ap.add_argument("--reference", default=None)
    ap.add_argument("--rolls", default=None,
                    help="where rolls and walks are kept (default: rolls, or "
                         "demo/rolls with --demo, which refuses a folder "
                         "outside demo/)")
    ap.add_argument("--out", default=None,
                    help="also write a TIFF of every scan here (with --demo, "
                         "a folder under demo/ only)")
    ap.add_argument("--settings", default=None,
                    help=f"where the window remembers its setup "
                         f"(default: {settings.DEFAULT_PATH}, or "
                         f"${settings.PATH_ENV}, or demo/gui-settings.json "
                         f"with --demo, which refuses a file outside demo/)")
    ap.add_argument("--open-roll", default=None, metavar="ROLL",
                    help="open this roll folder as soon as the window is up, "
                         "as a path or as a bare name under the rolls "
                         "directory. Its positions are proposed afresh from "
                         "the prescans, every launch.")
    ap.add_argument("--look-only", action="store_true",
                    help="there is no film in the transport. Every control "
                         "still works; anything that reaches for film says so. "
                         "Only with --demo")
    args = ap.parse_args()
    # Only the demo's stand-in can be told there is no film, so only it can
    # refuse for want of one. Given alone, the flag changed the sheet's words
    # and nothing else: it promised a refusal while the real scanner was
    # sought, moved, calibrated with an empty transport and scanned.
    if args.look_only and not args.demo:
        ap.error("--look-only needs --demo: it tells the stand-in scanner "
                 "there is no film, and the real one cannot be told that -- "
                 "it would be driven as usual while the window said scanning "
                 "would refuse")
    # What the demo writes is synthetic -- resampled pixels, bytes it encoded
    # itself, a made-up infrared plane, metering on pictures that ignore the
    # exposure -- and it is filed under ordinary ids that `make verify` excuses.
    # Pointed at the real library or rolls, it filed that in among the scans,
    # and a roll commissioned from a real walk wrote its approved.json and
    # frames back into that walk. So it writes under DEMO_ROOT or not at all
    # -- its calibration's cache too, which it leaves where the driver
    # leaves a reference; its settings, whose geometry and sheet decisions
    # went over the real window's; and the copies `--out` names, synthetic
    # pictures under the names real scans get. A folder chosen in the window
    # is the operator's own act, and is said instead (`_set_outdir`).
    if args.demo:
        for flag, value in (("--library", args.library),
                            ("--rolls", args.rolls),
                            ("--reference", args.reference),
                            ("--settings", args.settings),
                            ("--out", args.out)):
            if value is not None and not _within(value, DEMO_ROOT):
                ap.error(f"{flag} {value!r} with --demo: the demo files what "
                         f"it invents, and writes only under {DEMO_ROOT}/ so "
                         "none of it can land among real scans or over a "
                         "real calibration. Leave it out, or name a path "
                         f"under {DEMO_ROOT}/.")

    # A mistyped entry was accepted, logged as "showing" it, and the pictures
    # came from somewhere else.
    if args.demo_entry and not (Path(args.demo_entry) / "scan.json").is_file():
        ap.error(f"--demo-entry {args.demo_entry!r}: no library entry there "
                 "(a folder holding a scan.json)")

    home = DEMO_ROOT if args.demo else Path(".")

    # A demo keeps its own remembered geometry and sheet state. It used to
    # write them into the real file, which is the one thing DEMO_ROOT exists
    # to prevent everywhere else.
    settings_path = args.settings
    if settings_path is None and args.demo:
        settings_path = str(DEMO_ROOT / "gui-settings.json")

    # One window per library, rolls folder and settings file, taken before
    # anything is made, and before a session or a window exists
    # (`rps7200.locks`). A second one -- with `--library X` alone it still
    # shares the rolls and the settings -- could never scan, the scanner
    # taking one process, but it deleted, renamed and filed into the first's
    # rolls and library, and the last to quit wrote its settings and sheet
    # decisions over the other's. One line on stderr and no box: a box would
    # keep this window past the five seconds `make run` watches it for
    # (`tasks.start_apart`), and hide the reason.
    held = contextlib.ExitStack()
    # A lock that could not be taken for want of a lock folder is said in
    # the window's log once it has one, as well as here.
    lock_notes: list[str] = []
    refused = hold_window(
        held, "the demo window" if args.demo else "the window",
        library=args.library or str(home / "library"),
        rolls=args.rolls or str(home / "rolls"),
        settings_file=settings.path(settings_path), say=lock_notes.append)
    if refused is not None:
        held.close()
        print(refused, file=sys.stderr)
        return 1

    # The demo's own folder is the window's to make: everything it writes is
    # under it, and it is in no checkout until a demo has run. Its library,
    # rolls and calibration are then folders whose parent is there, which is
    # all a configured folder may have made (`storage`).
    if args.demo:
        DEMO_ROOT.mkdir(exist_ok=True)
    # A typed --out is made now, its own folder at most, never the ones
    # above it -- and refused at once where that cannot be, as a remembered
    # one that has gone is said at launch.
    if args.out:
        try:
            storage.make_root(args.out)
        except OSError as exc:
            ap.error(f"--out {args.out}: {exc}")

    # Resolved before a window exists, so a typo is a line of text rather than
    # a dialog behind a half-built window.
    #
    # A path is taken as given, which under --demo means a real walk in
    # `rolls/` and not `demo/rolls`. That is deliberate: the point of the demo
    # sheet is a strip that was actually walked. It is safe because opening a
    # roll only reads it -- a roll commissioned from it goes to
    # `_roll_folder`, which is always under `session.rolls`, and --demo pins
    # that under `demo/`, so nothing the window does afterwards can write
    # back into the walk it is showing.
    open_roll = None
    if args.open_roll:
        rolls_dir = Path(args.rolls) if args.rolls else home / "rolls"
        for candidate in (Path(args.open_roll), rolls_dir / args.open_roll):
            if candidate.is_dir():
                # In the folder's own spelling, however it was typed: the
                # roll box, the sheet's key and every check of which roll is
                # open take its name (PLAT-06) -- a strip's roll's too.
                open_roll = as_on_disk(candidate)
                if library.strip_number(open_roll.name) is not None:
                    open_roll = as_on_disk(open_roll.parent) / open_roll.name
                break
        else:
            ap.error(f"no roll folder at {args.open_roll!r}, and none called "
                     f"that under {rolls_dir}")
    session = ScanSession(
        root=args.library or str(home / "library"),
        reference=args.reference or str(home / "calibration" / "shading.npz"),
        rolls=args.rolls or str(home / "rolls"),
        out_dir=args.out,
    )
    # The roll's in-walk correction reads edges with the same detector as the
    # sheet; `rps7200` is handed it, never imports it.
    session.edge_reader = frame_edges.walk_reader
    if args.demo:
        from rps7200.demo import DemoScanner, libraries_beside
        source, entry = args.demo_source, args.demo_entry
        # A roll after the first draws other photographs, from every library
        # beside the one shown -- `library 2` beside `library` -- and the
        # likenesses that tell them apart are kept under demo/ between runs.
        libraries = libraries_beside(source)
        # `--look-only` is a fact about the film, so it goes to the thing that
        # would know. The backend refuses and the window reports it the way it
        # reports any other transport fault.
        session._open_scanner = lambda: DemoScanner(
            source, entry=entry, no_film=args.look_only,
            libraries=libraries, cache=home / "pictures.npz")

    root = tk.Tk()
    app = ScannerGui(root, session, demo=args.demo,
                     settings_path=settings_path, look_only=args.look_only,
                     open_roll=open_roll, logs=home / "logs",
                     notes=lock_notes)
    # Tk runs the handler between events, on this thread; it only schedules
    # the orderly quit, which then waits for the scanner thread as Quit does.
    # Not the session's halt itself: run between two bytecodes of this
    # thread, perhaps inside `submit` holding the session's jobs lock, the
    # halt would wait on that lock for good. A tick later it costs nothing:
    # the session stops at its next command or probe either way.
    interrupt = DeferredInterrupt(
        on_request=lambda: root.after(0, app.on_interrupt))

    def console_closed() -> None:
        # The halt at once, from Windows' own thread, whatever this one is
        # doing: of the 5 s Windows gives, a job taken meanwhile must not
        # start a pass, nor a slide its colour pass (`ScanSession.halt`).
        # The window's own quit follows on its thread, as Ctrl-C's does.
        session.halt(console_closed=True)
        root.after(0, app.on_interrupt, True)

    # Windows' console closing reaches no Python handler in time, and is
    # answered on a thread Windows makes; Python's Tcl there is threaded, so
    # `after` from that thread is handed to this one. About 5 s are all it
    # buys -- see `ConsoleClose`. Entered after `interrupt`, whose handlers
    # the C runtime calls, so that Windows reaches this one first.
    # Its line is kept in a file as well: the console it would have been
    # read in is the thing that closed, and the log pane goes with the
    # process a moment later.
    closed = ConsoleClose(
        on_close=console_closed,
        say=logged_to(home / "logs" / "console-closed.log"))
    with held, interrupt, closed, kept_hooks(getattr(app, "_kept", None)):
        root.mainloop()
    return 130 if interrupt.requested() or closed.closed() else 0


@contextlib.contextmanager
def kept_hooks(kept: KeptLog | None):
    """A thread that dies, and every Python warning, into ``kept`` as well.

    Each went to stderr alone, which only `make run` on Windows keeps: the
    filing thread ending (`FrameWriter`), `library.save`'s warning that an
    entry is filed but its index could not be rewritten. Both go where they
    went before, and into the window's kept log beside them. Process-wide,
    so installed by `main` alone -- never by a window a test builds -- and
    put back as it ends. None, a stand-in window's, installs nothing.
    """
    if kept is None:
        yield
        return
    before_thread = threading.excepthook
    before_warning = warnings.showwarning

    def thread_died(args) -> None:
        before_thread(args)
        name = getattr(args.thread, "name", None) or "a thread"
        kept.write(f"thread {name} ended on an exception:\n" + "".join(
            traceback.format_exception(args.exc_type, args.exc_value,
                                       args.exc_traceback)))

    def warned(message, category, filename, lineno, file=None,
               line=None) -> None:
        before_warning(message, category, filename, lineno, file, line)
        kept.write(f"warning: {category.__name__}: {message} "
                   f"({filename}:{lineno})")

    threading.excepthook = thread_died
    warnings.showwarning = warned
    try:
        yield
    finally:
        threading.excepthook = before_thread
        warnings.showwarning = before_warning


def hold_window(held: contextlib.ExitStack, who: str, *, library, rolls,
                settings_file, say: Callable[[str], None] | None = None
                ) -> str | None:
    """Hold this window's library, rolls folder and settings file as its
    own, and its library as in use, on ``held``; or say why not.

    Each of the three exclusively (`locks.WINDOW`): a second window that
    shares any is refused, naming the first. The library is held shared as
    well (`locks.LIBRARY`), which `compact --write`, the migrations,
    `duplicates --delete` and `file-spool` refuse beside -- for the window's
    whole life, scanner or none: maintenance is rare, and Stefan's to
    start. A lock that cannot be taken for any other reason is said, and
    the window opens unguarded, as every window did before.

    Said in one line, on stderr and to ``say`` -- the window's log pane
    (`main`). On stderr alone it never reached the operator: under `make
    run` on Windows that goes to a log file shown only for a window that
    ends within five seconds, and two windows could share a library with
    nothing on screen to say the guard was off.
    """
    notes: list[str] = []
    for label, path, kind, exclusive in (
            ("library", library, locks.WINDOW, True),
            ("rolls folder", rolls, locks.WINDOW, True),
            ("settings file", settings_file, locks.WINDOW, True),
            ("library", library, locks.LIBRARY, False)):
        try:
            taken = held.enter_context(locks.take(
                kind, path, exclusive=exclusive, what=who))
        except locks.Refused as exc:
            where = os.path.abspath(path)
            if kind == locks.LIBRARY:
                return (f"library {where} is being rewritten by {exc.who}; "
                        "start the window once it has finished")
            return (f"{label} {where} is already open in {exc.who}. One "
                    "window per library, rolls folder and settings file: "
                    "use that one, or quit it first.")
        if taken.note:
            notes.append(taken.note)
    if notes:
        line = ("; ".join(notes) + "; the window opens all the same, and a "
                "second window or a library rewrite (compact --write, "
                "migrations, file-spool) on these folders will not be "
                "refused")
        print(f"warning: {line}", file=sys.stderr)
        if say is not None:
            say(line)
    return None


if __name__ == "__main__":
    raise SystemExit(main())
