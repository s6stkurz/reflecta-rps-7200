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
import ctypes
import json
import math
import queue
import shutil
import subprocess
import sys
import time
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, simpledialog, ttk

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rps7200 import (                                     # noqa: E402
    export, library, preview, settings, shortcuts, tiff,
)
from rps7200.console import use_utf8_stdout
from rps7200.direct import (                              # noqa: E402
    FILM_BW,
    FILM_TYPES,
    INFRARED_IS_BLIND_TO,
    METER_MODES,
)
from rps7200.framing import FULL_FRAME, units_per_column  # noqa: E402
from tools import frame_edges                             # noqa: E402
from rps7200.library import FilmNotes                     # noqa: E402
from rps7200.mono import (                                 # noqa: E402
    MONO_AVERAGE,
    MONO_CHANNEL,
    MONO_CHOICES,
    to_monochrome,
)
from rps7200.protocol import (                             # noqa: E402
    COORD_PER_INCH,
    FILM_NEGATIVE,
    MM_PER_INCH,
    MM_PER_COMMAND,
    MM_PER_UNIT,
    say_command,
    say_units,
    units,
    units_for_param,
)
from rps7200.session import (                              # noqa: E402
    FINE_MAX_MM,
    FINE_MIN_MM,
    FORWARD_FRAME_S,
    INFRARED_TIE_CROSSOVER_DPI,
    LAST_PLAUSIBLE_POSITION,
    NUMBERING,
    Approved,
    Calibrate,
    Move,
    Prescan,
    Result,
    Roll,
    Scan,
    ScanSession,
    _safe,
    _unclaimed,
    deliverable_mm,
    estimate_seconds,
    legacy_shift,
    plan_nudges,
    plausible,
    renumbered,
    walked_prescans,
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

#: Film fields safe to carry over. `stock`, `process` and `tags` describe the
#: film and are the same all roll; `roll`, `frame`, `subject` and `notes`
#: describe one shot, and a stale value there would file today's scan under
#: yesterday's name.
REMEMBERED_FILM = ("stock", "process", "tags")

#: How many results keep a full-size working copy. Older ones are shrunk rather
#: than dropped, so every channel and the invert toggle keep working on them.
WORKING_COPIES = 12
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
#: How long to wait before opening a roll named on the command line. The window
#: is built inside `__init__`, which runs before `mainloop`, so the root is not
#: mapped yet: `open_roll` ends in a dialog whose parent would be an unmapped
#: window, and the sheet is a Toplevel sized against a geometry Tk has not
#: applied. Behind the 120 ms sash restore, so the panes are placed first.
OPEN_ROLL_MS = 250

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
                 open_roll=None):
        self.root = root
        self.session = session
        self.demo = demo
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
        self.remembered = settings.load(settings_path)
        self.results: list = []
        self.current = None
        self.busy = False
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
        self._loading = None
        self._redraw_job = None
        self._settle_job = None
        self._drawn_at = 0.0
        self._alive = True
        self._job = ""                       # what is running, for the stop label
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
        #: Which roll the sheet's decisions belong to. Set both by opening a
        #: roll and by finishing a walk -- a strip walked in this session has
        #: a folder too, and until the roll is commissioned its manifest holds
        #: no positions, so that folder is the only thing the decisions can be
        #: filed against. Kept apart from `_loaded_roll`, which answers the
        #: different question of which roll the browser should mark as open.
        self._sheet_roll = None
        self.browser = None                  # the rolls list, while it is open
        self._saving = False                 # a batch save is on a thread
        self._loaded_roll = None             # which roll folder is open, if any
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
        self._build()
        # Between the two, deliberately: this is the one moment the window holds
        # its shipped defaults and nothing a settings file has said.
        self._take_defaults()
        self._bind_control_menus()
        self._watch_controls()
        self._restore()
        self._refresh_panels()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
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
        if open_roll is not None:
            # Deferred through `_later` rather than called here, and rather
            # than `root.after`: `_later` registers in `self._pending`, so
            # closing the window inside the delay cancels it instead of firing
            # into a destroyed widget.
            self._later(OPEN_ROLL_MS, lambda: self.open_roll(open_roll))

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
        self.keys = shortcuts.resolve(stored if isinstance(stored, dict) else None)
        self.shortcut_overrides = shortcuts.overrides_from(self.keys)
        for key, value in self.remembered["film"].items():
            if key in REMEMBERED_FILM and key in self.fields:
                self.fields[key].set(str(value))
        # An explicit --out wins: it was typed for this run.
        if self.remembered["output"] and self.session.out_dir is None:
            self._set_outdir(str(self.remembered["output"]))
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

    def _remember(self) -> None:
        """Gather the setup and write it. Never allowed to stop the window."""
        try:
            controls = {key: getattr(self, f"v_{key}").get()
                        for key in REMEMBERED if hasattr(self, f"v_{key}")}
            film = {key: self.fields[key].get()
                    for key in REMEMBERED_FILM if key in self.fields}
            window = {"geometry": self.root.winfo_geometry()}
            for name, pane in (("outer", self._outer), ("right", self._right)):
                window[name] = _sash_positions(pane)
            settings.save({
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
            }, self._settings_path)
        except Exception as exc:                         # noqa: BLE001
            self._say(f"could not save the settings: {exc}")

    # -- the keyboard ------------------------------------------------------

    def _actions(self) -> dict:
        """What each action id does, for the main window.

        A table rather than a method per key, because the editor has to be able
        to say what every id is and the tests have to be able to check that
        none of them reaches the scanner. The two extra windows keep their own;
        see `_ContactSheet._actions` and `_FrameAdjuster._actions`.

        Everything here is viewing or arranging. Nothing starts a scan, moves
        film or calibrates -- `shortcuts.NEVER_BOUND` names those and a test
        holds the line. `stop` is here because `request_stop` finishes the pass
        already running rather than abandoning a read.
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
            # Only when there is something to stop. `submit` clears the flag,
            # so a stray press cannot reach the next job -- but the log is
            # evidence, and "finishing what is already running" with nothing
            # running is a line that will be read back one day and believed.
            "stop": lambda: self.on_stop() if self.busy else None,
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
        self._schedule_redraw()

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
        """
        if self.busy:
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
        for sequence, action_id in shortcuts.in_scope(self.keys, "window").items():
            run = actions.get(action_id)
            if run is None:
                continue
            self.root.bind(sequence, self._runner(run, sequence))
            self._bound.append(sequence)

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
        if not stored:
            return
        for key, value in stored.items():
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
        self.c_ir = ttk.Checkbutton(box, text="infrared (RGBI)",
                                    variable=self.v_ir,
                                    command=self._show_estimate)
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
                                      command=self.on_calibrate)
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
        ttk.Entry(box, textvariable=self.v_outdir).pack(fill="x")
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
        self.session.out_format = "jpeg" if jpeg else "tiff"
        quality = jpeg_quality(self.v_jpegq.get())
        self.session.jpeg_quality = quality
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
                                command=self._schedule_redraw)
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
        self.session.out_dir = Path(path) if path else None

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

    def on_calibrate(self, mode: str | None = None) -> None:
        self.session.submit(Calibrate(mode=mode or self.v_shading.get(),
                                      reference=self.session.reference))
        self.calibrated = True
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

    def ask_to_calibrate(self, parent=None) -> None:
        """Say that a scan needs a calibration, and offer to start one.

        The reference belongs to the power-on that measured it, so a session
        that scans before calibrating is a session whose corrections describe
        some other day's sensor. Not a modal: a modal here sits inside the event
        pump and stops it. Pressed twice, it raises the one already open.
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
        top.title("Calibrate first")
        top.transient(parent)
        top.resizable(False, False)
        frame = ttk.Frame(top, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, font=_font(13, bold=True),
                  text="Without a calibration no picture can be scanned"
                  ).pack(anchor="w")
        ttk.Label(
            frame, wraplength=430, justify="left", padding=(0, 8),
            text=("The scanner measures its own per-column response and hands "
                  "it back; this driver does the dividing. Without it the "
                  "vertical striping is simply left in.\n\n"
                  "The reference belongs to the power-on that measured it, so "
                  "it is done once per session -- and with the film loaded, "
                  "which is what the vendor software does. The calibration "
                  "frame is the lower part of the transport, which the film "
                  "does not cover, so the sensor is measured either way.\n\n"
                  "Nothing is scanned now. Press the scan button again once "
                  "the calibration has finished.")
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

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(14, 0))

        def choose(mode: str | None) -> None:
            if mode:
                self.v_shading.set(mode)
                self.on_calibrate(mode)          # closes this prompt
                return
            self._calibrate_prompt = None
            top.destroy()

        top.protocol("WM_DELETE_WINDOW", lambda: choose(None))
        ttk.Button(buttons, text="Not now",
                   command=lambda: choose(None)).pack(side="left")
        if cached.exists():
            ttk.Button(buttons, text="Use the cached one",
                       command=lambda: choose("reuse")).pack(side="right", padx=4)
        start = ttk.Button(buttons, text="Calibrate now",
                           command=lambda: choose("measure"))
        start.pack(side="right")
        start.focus_set()
        top.bind("<Return>", lambda _e: choose("measure"))
        top.bind("<Escape>", lambda _e: choose(None))
        top.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - top.winfo_width()) // 2
        y = parent.winfo_rooty() + 120
        top.geometry(f"+{max(0, x)}+{max(0, y)}")

    def on_prescan(self) -> None:
        if self._calibration_missing():
            return
        dpi = self._prescan_dpi()
        if dpi is None:
            return
        self.session.submit(Prescan(resolution=dpi, film=self.v_film.get(),
                                    notes=self._notes(),
                                    tags=self._tags()))

    def on_scan(self) -> None:
        if self._calibration_missing():
            return
        dpi, exposure = self._dpi(), self._exposure()
        if dpi is None or exposure is None:
            return
        self._pin_arrangement()
        self.session.submit(Scan(
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
            self.session.rotation = self.current.rotation
            self.session.flip = self.current.flipped

    def on_roll(self) -> None:
        if self._calibration_missing():
            return
        dpi, predpi = self._dpi(), self._prescan_dpi()
        if dpi is None or predpi is None:
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
        dry = self.v_dryrun.get()
        per = (23.0 if dry else
               estimate_seconds(dpi, self.v_ir.get(),
                                self.v_fast_ir.get()) + 70)
        move, move_s = seek_note(self._transport, start_at)
        # No figure for the frames when the seek will refuse the first: the
        # line above says it costs nothing, and "Roughly 4m 31s" under it
        # was the time of frames nobody would scan.
        cost = (f"{roll_estimate(per, frames, move_s)}\n\n"
                if plausible(start_at - 1) else "")
        question = (
            f"{'Walk' if dry else 'Scan'} {range_words(start_at, frames)}, "
            f"at {dpi} dpi"
            f"{' with infrared' if self.v_ir.get() and not dry else ''}.\n\n"
            f"{move}\n\n"
            f"{cost}")
        # One question either way: `on_roll` is the only confirmation the
        # `roll` key gets, and asking twice trains the habit of dismissing both.
        keep = False
        if dry and self._sheet_to_keep():
            answer = self._ask_keep_sheet(question, start_at, frames, predpi)
            if answer is None:
                return
            keep = answer
        elif not messagebox.askokcancel("Scan roll", question + "Start?"):
            return
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
            # Set again when the walk finishes and the folder is known. Cleared
            # here so a walk that fails partway cannot leave the next set of
            # decisions filed against the roll before it.
            self._sheet_roll = None
            self._kept_walk = set()
            self.edge_watch.begin(self._survey_film, expected=frames)
        if dry:
            self._rewalked = set()
            self._surveying = True
            self._survey_predpi = predpi
        # Starts the whole-roll estimate at the same rough figure the dialog
        # above just showed, so the number on screen does not jump the moment
        # scanning begins. `frames` is already "how many this run will do",
        # counted from `start_at` rather than added on top of it.
        # The button this handler is behind is disabled while busy, so this
        # cannot race a job that is still running.
        self._roll_wall_start = time.monotonic()
        self._roll_seeking = True
        self._roll_dry = dry
        self._roll_frames_total = frames
        self._roll_frames_done = 0
        self._roll_seconds_per_frame = per
        self._update_roll_eta()
        self.session.submit(Roll(
            frames=frames, start_at=start_at, resolution=dpi,
            prescan_resolution=predpi, infrared=self.v_ir.get(),
            fast_infrared=self.v_fast_ir.get(),
            film=self.v_film.get(), meter=self.v_meter.get(), dry_run=dry,
            correct=self.v_correct.get(),
            correct_dry_run=self.v_correct_dry.get(),
            mono=self.v_mono.get(),
            mono_channel=self.v_mono_channel.get(),
            # A kept walk goes into the folder the sheet came from, whatever
            # the roll box or the date says now -- a walk continued after
            # midnight would otherwise start a new roll named by the new day.
            name=(Path(self._sheet_roll).name if keep
                  else self.fields["roll"].get().strip()),
            out=str(self._sheet_roll) if keep else "",
            extend_walk=keep,
            notes=self._notes(), tags=self._tags(),
        ))

    def _sheet_to_keep(self) -> bool:
        """Whether there is a walked sheet a new walk could be added to."""
        return bool(self.survey) and self._sheet_roll is not None

    def _ask_keep_sheet(self, question: str, start_at: int,
                        frames: int | None, predpi: int) -> bool | None:
        """Ask whether this walk adds to the sheet there is: True, False or None.

        None is Cancel. A walk of 1 to 10 on a strip of 12 left two frames
        nobody has seen, and walking them used to throw the first ten away --
        ticks, positions and turns with them, on screen and in `survey.json`.

        Refused, with the reason, when the prescan resolution or the film
        differs from the walk being kept: those frames would be references at
        another resolution, or read by the detector as another film, beside
        frames that are not.
        """
        walked = [int(r.number) for r in self.survey if r.number]
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
                "OK starts a new sheet; the old one stays under Rolls ...")
            return False if started else None
        again = rewalked(walked, start_at, frames)
        note = ""
        if again:
            which = (f"Frame {again[0]} is" if len(again) == 1
                     else f"Frames {number_spans(again)} are")
            note = (f"\n\n{which} on it already: walked again, the new "
                    "prescans replace the old, and a position set on "
                    f"{'it' if len(again) == 1 else 'them'} is dropped -- it "
                    "was measured from the old one.")
        return messagebox.askyesnocancel(
            "Scan roll",
            question + have + " Keep it?\n\n"
            "Yes -- add what this walk finds to it. Its ticks, positions and "
            "turns stay.\n"
            "No -- start a new sheet. The old one stays under Rolls ...\n"
            "Cancel -- nothing moves." + note)

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
        if self.sheet is not None and self.sheet.alive():
            self.sheet.top.lift()
            self.sheet.top.focus_force()
            return
        kept = self._recall_sheet_state()
        # Said out loud, because a restored decision that is wrong is worse
        # than none: the operator has to be able to see that the sheet came
        # back holding something, and how much.
        restored = ", ".join(
            f"{len(kept[name])} {label}"
            for name, label in (("offsets", "positioned"),
                                ("rotations", "turned"),
                                ("flips", "flipped"))
            if kept[name])

        self._say(f"contact sheet: {len(self.survey)} walked "
                  f"{[getattr(r, 'number', '?') for r in self.survey]}"
                  + (f" -- kept {restored}" if restored else ""))
        self._open_sheet(kept["offsets"], kept.get("sources"),
                         rotations=kept["rotations"], flips=kept["flips"],
                         ticks=kept["ticks"], options=kept["options"],
                         done=self._sheet_done)

    def _open_sheet(self, offsets: dict, sources, **cells) -> None:
        """The sheet, now, on whatever the frame-edge reader has so far.

        It does not wait for the reader: the positions it has not read yet
        arrive through `_edges_changed` while the sheet is open, and the light
        in the header says how far it has got. His own positions (``offsets``
        whose ``sources`` say he set them) stand whatever it reads.
        """
        progress = self.edge_watch.progress()
        kept, notes = _merge_kept({}, {}, offsets, sources)
        self.sheet = _ContactSheet(self, self.survey, offsets=kept,
                                   proposals=notes,
                                   readings=(progress.offsets, progress.notes),
                                   generation=progress.generation, **cells)

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
        if self.busy:
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
        for summary in rolls:
            summary["opened"] = (
                stored.get(Path(summary["folder"]).name) or {}).get("opened")
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
        rolls[Path(folder).name] = {"opened": time.time()}
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
        return Path(self._sheet_roll).name if self._sheet_roll else None

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
                    out[name][int(key)] = cast(value)
                except (TypeError, ValueError):
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
        """Keep the sheet's decisions past the window that made them."""
        self.sheet_state = state
        key = self._sheet_key()
        if key is None:
            return
        sheets = self.remembered.setdefault("sheet", {})
        if not isinstance(sheets, dict):
            sheets = self.remembered["sheet"] = {}
        sheets[key] = state
        self._remember()

    def _recall_sheet_state(self) -> dict:
        """What the sheet should open holding.

        This session's copy first: it is the one the operator has been
        working in, and it is already in the right types. The settings file
        is the fallback for a window that has been restarted since.
        """
        if self.sheet_state:
            return self._clean_sheet_state(self.sheet_state)
        key = self._sheet_key()
        if key is None:
            return self._clean_sheet_state(None)
        return self._clean_sheet_state(
            (self.remembered.get("sheet") or {}).get(key))

    def _roll_is_busy(self, summaries, what: str) -> bool:
        """Refuse to touch a roll the scanner or the window is using."""
        if self.busy:
            messagebox.showinfo(
                what, "The scanner is working. Wait for it to finish -- a roll "
                "it is writing into is not one to move or remove.")
            return True
        loaded = self._loaded_roll and Path(self._loaded_roll).resolve()
        for summary in summaries:
            if loaded and Path(summary["folder"]).resolve() == loaded:
                messagebox.showinfo(
                    what, f"{summary['roll']} is the roll open in this window. "
                    "Open another, or restart, before changing it on disk.")
                return True
        return False

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
        if not total:
            messagebox.showinfo(
                "Export",
                "None of the frames in "
                + (summaries[0]["roll"] if len(summaries) == 1 else "those rolls")
                + " has a library entry left, so there is nothing to re-correct "
                  "from.\n\nThe roll's own frame files are still in its folder.")
            return
        missing = sum(len(s["done"]) - len(items) for s, items in plans)
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
            + (f"{missing} scanned frame(s) have no library entry left and are "
               f"skipped -- the log names them.\n\n" if missing > 0 else "")
            + "This takes a moment per frame. Nothing already there is "
              "overwritten.\n\nStart?",
        ):
            return

        quality = jpeg_quality(self.v_jpegq.get())
        mono, channel = self.v_mono.get(), self.v_mono_channel.get()
        out = Path(folder)
        self._saving = True
        self._say(f"exporting {total} frames into {out} ...")

        def run() -> None:
            written = 0
            for summary, items in plans:
                for item in items:
                    try:
                        path = _unclaimed(
                            out / f"{_safe(summary['roll'])}_"
                                  f"{batch_name(item, fmt)}")
                        said = self._deliver_one(item, path, quality, mono,
                                                 channel)
                    except Exception as exc:             # noqa: BLE001
                        self._saves.put(("line", f"could not export "
                                                 f"{summary['roll']} frame "
                                                 f"{item.number}: {exc}"))
                        continue
                    if said:
                        written += 1
                        self._saves.put(("line", f"exported {said}"))
            self._saves.put(("done", written, total))

        threading.Thread(target=run, daemon=True, name="export-rolls").start()

    def on_duplicate_roll(self, summary) -> None:
        """A second copy of the roll under the next free name.

        So a strip can be rescanned at different settings without losing the
        first scan *or* the approvals that went with it -- the positions and
        turns in `approved.json`, which are the one thing here that cannot be
        rebuilt from the library.
        """
        if self._roll_is_busy([summary], "Duplicate"):
            return
        source = Path(summary["folder"])
        try:
            target = duplicate_name(source)
        except ValueError as exc:
            messagebox.showerror("Duplicate", str(exc))
            return
        if not messagebox.askokcancel(
            "Duplicate",
            f"Copy {source.name} to {target.name} -- "
            f"{human_size(summary['size'])}.\n\nThe copy keeps the walk, the "
            "approvals and the frames already scanned, so the original is safe "
            "to rescan over.\n\nCopy?",
        ):
            return
        try:
            shutil.copytree(source, target)
        except OSError as exc:
            messagebox.showerror("Duplicate", f"Could not copy: {exc}")
            return
        self._say(f"duplicated {source.name} to {target.name}")

    def on_delete_rolls(self, summaries) -> None:
        """Remove roll folders. Never the library entries.

        Everything in a roll folder is re-derivable from the library **except
        `approved.json`** -- the frames and prescans can be rebuilt, the
        operator's own positions and turns cannot. That is what the question
        below says, because it is the only thing actually being risked.
        """
        if self._roll_is_busy(summaries, "Delete"):
            return
        names = ", ".join(s["roll"] for s in summaries)
        size = human_size(sum(s["size"] for s in summaries))
        decided = sum(1 for s in summaries
                      if any(read_approved(s["folder"])[1:3]))  # turns/flips
        if not messagebox.askokcancel(
            "Delete",
            f"Delete {len(summaries)} roll folder"
            f"{'s' if len(summaries) != 1 else ''} -- {names} -- and {size} "
            f"with them?\n\nThe library entries are NOT touched: the raw bytes "
            f"stay, and the frames can be rebuilt from them.\n\n"
            + (f"What does go for good is the positions and turns you set by "
               f"hand: {decided} of these has an approved.json, and that is the "
               f"one thing here the library cannot rebuild.\n\n"
               if decided else "")
            + "Delete?",
        ):
            return
        for summary in summaries:
            try:
                shutil.rmtree(summary["folder"])
                self._say(f"deleted roll folder {summary['roll']}")
            except OSError as exc:
                self._say(f"could not delete {summary['roll']}: {exc}")

    def on_rename_roll(self, summary) -> None:
        """Rename the folder. The manifests keep the roll's own name inside."""
        if self._roll_is_busy([summary], "Rename"):
            return
        source = Path(summary["folder"])
        wanted = simpledialog.askstring(
            "Rename", f"A new folder name for {source.name}:",
            initialvalue=source.name, parent=self.root)
        if not wanted or wanted.strip() == source.name:
            return
        target = source.with_name(_safe(wanted.strip()))
        if target.exists():
            messagebox.showerror("Rename", f"{target.name} is already there.")
            return
        try:
            source.rename(target)
        except OSError as exc:
            messagebox.showerror("Rename", f"Could not rename: {exc}")
            return
        self._say(f"renamed {source.name} to {target.name}")

    def on_reveal_roll(self, summary) -> None:
        """Show the folder in the platform's own file manager."""
        folder = str(summary["folder"])
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", folder], check=False)
            elif sys.platform.startswith("win"):
                subprocess.run(["explorer", folder], check=False)
            else:
                subprocess.run(["xdg-open", folder], check=False)
        except OSError as exc:
            self._say(f"could not open {folder}: {exc}")

    def open_roll(self, folder) -> None:
        """Load one roll folder: its walk, its decisions, and how far it got.

        The settings come back from the manifest rather than from
        `gui-settings.json`, and that distinction is the whole point of
        resuming: the window's own settings have moved on to other film, while
        the manifest still describes *this* roll. A year later it is the only
        thing that still does.
        """
        folder = Path(folder)
        self._note_roll_opened(folder)
        self._loaded_roll = folder
        try:
            out = read_survey(folder, say=self._say)
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror(
                "Open a roll",
                f"{folder.name} does not hold a roll this can read.\n\n"
                f"{exc}\n\nA roll folder has a survey.json or a roll.json, "
                "and the prescanNN.tif files beside it.")
            return

        done = out["scanned"]
        remaining = [n for n in out["wanted"] if n not in done]
        restored = self._restore_roll_settings(out["settings"])

        if not out["results"]:
            # A roll commissioned without a walk has no prescans, so there is
            # no sheet to show -- but it can still be finished, which is what
            # matters. Point the plain Roll button at the first frame left.
            if not remaining:
                messagebox.showinfo(
                    "Open a roll",
                    f"{out['roll']} has no prescans to show and nothing left "
                    "to scan.")
                return
            if restored:
                self.v_startat.set(str(remaining[0]))
            messagebox.showinfo(
                "Open a roll",
                f"{out['roll']} was scanned without walking the strip first, "
                f"so there is no contact sheet to show.\n\n"
                f"{len(done)} frames are done and {len(remaining)} are left. "
                f"Its settings are back and \"first frame\" is set to frame "
                f"{remaining[0]} -- put the strip in the way it went in "
                "before and press Roll, and the film is wound there first.\n\n"
                + STRIP_NUMBERS)
            self._say(f"reopened {out['roll']}: {len(done)} scanned, "
                      f"{len(remaining)} left, no sheet")
            return

        self.survey = out["results"]
        self._survey_predpi = out["prescan_resolution"]
        self._survey_film = out.get("film")
        for result in out["results"]:
            self.results.append(result)
        self.b_sheet.configure(state="normal")
        self._redraw_strip()
        self._say(f"reopened {out['roll']}: {len(out['results'])} frames"
                  + (f", {len(done)} already scanned" if done else "")
                  + (f", {len(out['offsets'])} with a position already set"
                     if out["offsets"] else "")
                  + (f", {len(out['rotations'])} already turned"
                     if out["rotations"] else "")
                  + (f", {sum(out['flips'].values())} flipped"
                     if any(out["flips"].values()) else "")
                  + (f", settings restored: {', '.join(sorted(restored))}"
                     if restored else ""))
        if self.sheet is not None and self.sheet.alive():
            # Destroyed rather than dismissed: `_loaded_roll` already names the
            # roll being opened, so keeping the old sheet's decisions here
            # would file the outgoing roll's positions under the incoming one.
            self.sheet.top.destroy()
        self._sheet_done = {int(n) for n in done}
        self._sheet_roll = folder
        # The manifest is the record for this roll, so it replaces whatever
        # the window was holding -- including another roll's decisions, which
        # are keyed by frame number and would otherwise be read as this one's.
        self.sheet_state = {
            "ticks": {}, "offsets": dict(out["offsets"]),
            "rotations": dict(out["rotations"]), "flips": dict(out["flips"]),
            # Carried, or the next sheet built from this state stamps every
            # one of them `operator`: `_propose_positions` reads a kept offset
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
        self._open_sheet(out["offsets"], out.get("sources"),
                         rotations=out["rotations"], flips=out["flips"],
                         done=done)
        # The film is almost certainly not where the walk left it, and that no
        # longer matters: the roll goes to each frame by the transport's own
        # counter. What does matter, and only Stefan can see it, is that the
        # strip is back in the way it went in, because that is where the
        # counter starts from. Said rather than guessed at, and the sentence
        # differs by what he is about to do.
        if done and remaining:
            messagebox.showinfo(
                "Continue this roll",
                f"{out['roll']}: {len(done)} of {len(out['wanted'])} frames "
                f"scanned, {len(remaining)} left "
                f"({', '.join(str(n) for n in remaining)}).\n\n"
                "Those are ticked in the sheet and the ones already done are "
                "not. Its settings are back. Put the strip in the way it went "
                "in before and press \"Scan chosen frames\" -- the transport "
                "goes to each frame itself, from wherever the film is.\n\n"
                + STRIP_NUMBERS + "\n\n"
                "It will calibrate again first, which is the right default "
                "rather than a limitation: a reference describes the sensor at "
                "the exposure and gain of the pass that measured it, and "
                "months later neither is the same. Set Calibrate to \"reuse\" "
                "before starting if you would rather load the saved one."
                + (f"\n\nRestored: {', '.join(sorted(restored))}."
                   if restored else ""))
        else:
            messagebox.showinfo(
                "Open a roll",
                f"{len(out['results'])} frames from {out['roll']}"
                + (", all of them already scanned" if done and not remaining
                   else "")
                + ".\n\n"
                + ("The positions are measured from these prescans every time "
                   "this opens, so the sheet shows what the frames say today "
                   "rather than what was recorded about them."
                   if self.look_only else STRIP_NUMBERS))

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
                       options=None) -> None:
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
        """
        if not numbers:
            return
        if self.busy:
            # The sheet stays open and readable while the scanner is working,
            # so this button is reachable mid-roll. Queueing a second roll
            # behind the first is not what anyone pressing it means.
            messagebox.showinfo(
                "Scan chosen frames",
                "The scanner is busy. Wait for it to finish, or stop it, then "
                "press this again -- the ticks stay where they are.")
            return
        # Over the sheet when the sheet asked, so the prompt is not hidden
        # behind the window it was pressed in.
        if self._calibration_missing(
                self.sheet.top if self.sheet is not None and self.sheet.alive()
                else None):
            return
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
                return
        else:
            dpi, predpi = self._dpi(), self._prescan_dpi()
            if dpi is None or predpi is None:
                return

        def chose(key, variable):
            """The sheet's value where it set one, else the window's."""
            return options[key] if options and key in options else variable.get()

        infrared = bool(chose("ir", self.v_ir))
        fast_ir = bool(chose("fast_ir", self.v_fast_ir))
        film = str(chose("film", self.v_film))
        meter = str(chose("meter", self.v_meter))
        correct = bool(chose("correct", self.v_correct))
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
        start_at, span = chosen_span(numbers)
        walked = len(self.survey)
        per = self._per_frame_seconds(dpi=dpi, ir=infrared, fast_ir=fast_ir)
        move, move_s = seek_note(self._transport, start_at)
        if not messagebox.askokcancel(
            "Scan chosen frames",
            f"Scan {len(numbers)} of the {walked} frames walked: "
            f"{', '.join(str(n) for n in numbers)}.\n\n"
            f"{move}\n\n"
            f"At {dpi} dpi{' with infrared' if infrared else ''}, {film}, "
            f"roughly {_duration(per * len(numbers) + move_s)}. The frames "
            "nobody ticked cost their advance only."
            + self._approved_note(approved, correct)
            + self._options_note(options) + self._edges_pending()
            + "\n\nStart?",
        ):
            return
        self._write_approved(approved)
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
        # Kept so the frames that come back are shown the way they were
        # written. Without it a roll returns pictures the filmstrip draws one
        # way up and the file on disk holds another.
        for record in approved:
            self.orientations[("frame", record.number)] = (
                record.rotation, bool(record.flipped))
        # The move is the roll's own, not a `Move` queued in front of it: a
        # `Move` reports a short rewind by returning a string, which the worker
        # logs before taking the next job -- so a rewind that got three of
        # fourteen was followed straight away by a roll scanning frames it had
        # mis-numbered. The roll refuses instead, and scans nothing.
        self.session.submit(Roll(
            frames=span, start_at=start_at, resolution=dpi,
            prescan_resolution=predpi, infrared=infrared,
            fast_infrared=fast_ir,
            film=film, meter=meter, dry_run=False,
            correct=correct, only=tuple(numbers),
            approved=tuple(approved), reverse_hold=self.v_reverse.get(),
            mono=mono,
            mono_channel=self.v_mono_channel.get(),
            name=self.fields["roll"].get().strip(),
            notes=self._notes(), tags=self._tags(),
        ))

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
                 "meter": "metering", "correct": "the automatic nudge"}
        differ = []
        for key, value in options.items():
            variable = getattr(self, f"v_{key}", None)
            if variable is not None and str(variable.get()) != str(value):
                differ.append(f"{named.get(key, key)} {value}")
        if not differ:
            return ""
        return ("\n\nSet on the contact sheet, not in the main window: "
                + ", ".join(differ) + ".")

    def _approved_note(self, approved, correct=None) -> str:
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
                ("operator", "you positioned"),
                ("measured", "two detectors agreed"),
                ("unconfirmed", "one detector, uncorroborated"),
                ("neighbours", "read from the frames either side"),
                ("none", "nothing could read"),
            ) if tally.get(name))
        note = (f"\n\n{len(carried)} frame"
                f"{'s' if len(carried) != 1 else ''} carry a position: {said}."
                "\n\nEach is used exactly as given.")
        # The automatic nudge is deliberately not mentioned. Every ticked frame
        # gets an `Approved`, including the ones left at zero, and the driver
        # takes the held branch for any frame that has one -- so `correct`
        # cannot act on a single frame of a commissioned roll. Saying it "stays
        # on for the frames you did not adjust" described something that never
        # happens. See TODO.md: the tick itself should go.
        return note

    def _write_approved(self, approved) -> None:
        """Record the positions beside the roll before anything is scanned.

        Written from here rather than by the scan thread because it is the
        operator's decision, made before the roll starts -- and it is the file
        that says what was asked for, whatever the roll then does about it.
        A kilobyte of JSON with the device idle between jobs.
        """
        if not approved:
            return
        try:
            name = _safe(self.fields["roll"].get().strip())
            folder = Path(self.session.rolls) / name
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "approved.json").write_text(json.dumps({
                "roll": name,
                # The numbers below are places on the strip; a file without
                # this was numbered the way its walk was. See `read_approved`.
                "numbering": NUMBERING,
                "frames": [{"number": a.number,
                            "offset_mm": round(a.offset_mm, 4),
                            "rotation": int(a.rotation),
                            "flipped": bool(a.flipped),
                            "reference_entry": str(a.reference_entry or ""),
                            "source": str(a.source or "operator")}
                           for a in approved],
            }, indent=2, default=str), encoding="utf-8")
        except Exception as exc:                          # noqa: BLE001
            # Broad on purpose. This file is a note about what was asked for;
            # the scan is the work. Losing the note must never cost the roll,
            # and it did once -- a Path where a str was expected raised inside
            # json.dumps, took the Tk callback with it, and the operator saw
            # a button that did nothing at all.
            self._say(f"could not write approved.json ({exc}); scanning anyway")
            return
        told = ", ".join(f"{a.number}:{say_units(a.offset_mm)}"
                         for a in approved if a.offset_mm) or "none moved"
        self._say(f"approved positions written to "
                  f"{folder / 'approved.json'} ({told})")

    def on_move_frames(self, frames: int) -> None:
        self.session.submit(Move(frames=frames))

    def on_nudge(self, direction: int, millimetres: float | None = None) -> None:
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
        self.session.submit(Move(millimetres=millimetres * direction))

    def on_stop(self) -> None:
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
        if self.busy and not messagebox.askokcancel(
            "Quit",
            "A scan is still running. Quitting waits for it to finish -- "
            "abandoning it is what wedges the scanner.\n\nWait and quit?",
        ):
            return
        self.closing = True
        self.v_state.set("closing ...")
        self._remember()
        self.session.shutdown()
        self._wait_to_quit()

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
        if self._session_closed or thread is None or not thread.is_alive():
            self._quit()
            return
        self._later(150, self._wait_to_quit)

    def _quit(self) -> None:
        self._alive = False
        self.edge_watch.close()
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
        if not self._alive:
            return
        version = self.edge_watch.version
        if version != self._edge_seen:
            self._edge_seen = version
            self._edges_changed(self.edge_watch.progress())
        while True:
            try:
                message = self._saves.get_nowait()
            except queue.Empty:
                break
            if message[0] == "line":
                self._say(message[1])
            else:
                self._saving = False
                _, written, total = message
                self._say(f"saved {written} of {total} passes"
                          + ("" if written == total
                             else " -- the rest are in the log above"))
        while True:
            try:
                seq, image, problem = self._reads.get_nowait()
            except queue.Empty:
                break
            if problem:
                self._say(problem)
            self._loaded(seq, image)
        while True:
            try:
                token, counts, clipped, problem = self._measured.get_nowait()
            except queue.Empty:
                break
            if token != self._histogram_token:
                continue          # a later measurement is already the answer
            if problem:
                self._say(f"could not measure: {problem}")
                self.histogram.failed()
            else:
                self.histogram.show(counts, clipped)
        for event in self.session.poll():
            self._handle(event)
            if not self._alive:
                return
        # Ticks the roll countdown between frames, not only when one lands --
        # cheap string formatting, and "left" that only moved at frame
        # boundaries would sit still for minutes at a time.
        self._update_roll_eta()
        self._later(POLL_MS, self._pump)

    def _handle(self, event) -> None:
        if event.kind == "log":
            self._say(event.text)
        elif event.kind == "state":
            self.v_state.set(event.text.splitlines()[0])
            if event.busy:
                self._job = event.text
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
            self._set_busy(event.busy)
        elif event.kind == "progress":
            self._progress(event.done, event.total)
        elif event.kind == "result":
            self._add_result(event.result)
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
        elif event.kind == "calibrated":
            self.calibrated = bool(event.done)
            self._sync_calibration()
        elif event.kind == "filed":
            for r in self.results:
                if r.seq == event.done:
                    r.entry = Path(event.text)
        elif event.kind == "finished":
            self.v_progress.set(f"{event.text} -- done")
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
        elif event.kind == "failed":
            # Reported in place, not in a modal: a modal sits inside this pump
            # and stops it, so one failed frame would freeze the window and the
            # rest of a roll would arrive unseen.
            self._say(event.text)
            self.v_progress.set(event.text)
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
            if not self.session.inquiry_text:
                messagebox.showerror("No scanner", event.text)
        elif event.kind == "closed":
            self._session_closed = True
            self._set_busy(False)
            self.v_state.set("scanner closed")
            if self.closing:
                self._quit()

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
        if self.survey and not self._kept_walk:
            self._sheet_roll = getattr(self.session, "last_roll_dir", None)
        # In strip order, whichever end the walk added to.
        self.survey.sort(key=lambda r: int(r.number or 0))
        again = sorted(self._rewalked)
        if again:
            # Measured from the prescan this walk has just replaced, so it no
            # longer says where anything is. Turns and ticks are about the
            # picture and stay.
            dropped = [n for n in again
                       if n in (self.sheet_state.get("offsets") or {})]
            for name in ("offsets", "sources"):
                section = self.sheet_state.get(name)
                if isinstance(section, dict):
                    for number in again:
                        section.pop(number, None)
            self._say(f"walked frames {number_spans(again)} again: their new "
                      "prescans replace the old"
                      + (f", and the positions set on {number_spans(dropped)} "
                         "are dropped" if dropped else ""))
            if dropped:
                self._store_sheet_state(self.sheet_state)
        self._kept_walk, self._rewalked = set(), set()
        self.b_sheet.configure(state="normal" if self.survey else "disabled")

    def _report_held(self) -> None:
        """Say, once and by name, which frames did not reach their position.

        A line in the log scrolls away -- six frames of a roll went out
        uncorrectable once because the warning was above the fold. This is the
        last thing seen when a roll ends, and it names frames rather than
        outcome codes, because "not_converged" is not what anyone needs to
        read at the end of an hour.
        """
        said = {
            "not_converged": "did not get close enough",
            "unverified": "could not be verified against your picture",
            "would_reverse": "overshot, and reversing was refused",
            "budget": "would have travelled further than allowed",
            "wrong_way": "moved the wrong way -- the direction is inverted",
            "stopped": "was interrupted",
            "off": "was left alone; holding had been switched off",
        }
        missed = []
        counted = set()
        for result in self.results:
            held = (result.registration or {}).get("approved")
            if not held or held.get("outcome") == "held":
                continue
            number = getattr(result, "number", None)
            # A frame's prescan and its scan share one registration dict, so
            # both carry the outcome and the frame would be named twice --
            # and counted twice, which made the number in the first line
            # disagree with the list under it.
            if number in counted:
                continue
            counted.add(number)
            why = said.get(held.get("outcome"), held.get("outcome", "?"))
            residual = held.get("residual_mm")
            short = (f" ({say_units(residual, signed=False)} out)"
                 if residual else "")
            missed.append(f"frame {number} {why}{short}")
        if not missed:
            return
        messagebox.showwarning(
            "Positions not reached",
            (f"{len(missed)} frames were" if len(missed) != 1
             else "1 frame was")
            + " scanned without reaching the position you approved:\n\n"
            + "\n".join(missed)
            + "\n\nThe scans are filed and usable; the frames above are the "
              "ones to look at first.")

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
        self.b_stop.configure(state="normal" if busy else "disabled",
                              text=stop_label(self._job))
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
                f"roll: {verb} {done} frames so far, "
                f"{_duration(elapsed)} elapsed, ~{_duration(per)}/frame"
                f"{measured}")

    def _say(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # -- results and the filmstrip ----------------------------------------

    def _add_result(self, result) -> None:
        result.hidden = False
        result.supersedes = None
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
        self._arrange(result, superseded)
        self.results.append(result)

    def _arrange(self, result, superseded=None) -> None:
        """How this pass should be shown, in order of what knows best.

        The prescan it replaces, first: you framed that picture and said which
        way up it was, and the scan of it is the same photograph. Then anything
        already said about this picture, which is what a turn in the contact
        sheet leaves behind. Then the last arrangement set anywhere, because a
        strip goes into the transport one way round and the frame after this
        one is almost certainly the same way up.
        """
        known = None
        if superseded is not None:
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
        # Whatever it turned out to be, the picture now has an answer, so the
        # next pass over it agrees with this one rather than with the session.
        self.remember_arrangement(result)

    def _into_survey(self, result) -> None:
        """Add a walked prescan to the survey, replacing a kept one of its frame.

        Only a frame the sheet already held is replaced: the walk adding to it
        took that frame again, and one frame is one cell. Anything else is
        appended, as every walk's prescans always were.
        """
        number = int(result.number)
        if number in self._kept_walk:
            for i, earlier in enumerate(self.survey):
                if earlier.number == number:
                    self.survey[i] = result
                    self._rewalked.add(number)
                    return
        self.survey.append(result)

    def remember_arrangement(self, result) -> None:
        """Record how this photograph is arranged, however it was decided."""
        key = picture_of(result)
        if key is not None:
            self.orientations[key] = (result.rotation, result.flipped)
        if self._surveying and result.kind == "prescan" and result.number:
            self._into_survey(result)
            self.edge_watch.add(result.number, result.image)
        # Through the same filter the session's readout reports use, so a
        # counter no strip can have never becomes a forecast either.
        if plausible(result.position):
            self._transport = result.position
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
        self._show(result)
        self._redraw_strip()

    def _visible(self) -> list:
        return [r for r in self.results if not r.hidden]

    def _show(self, result) -> None:
        self.current = result
        self._full = None
        self._full_seq = None
        self._levels = []
        self._levels_seq = None
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
        if self.v_channel.get() not in available:
            self.v_channel.set("RGB")
        marks = result.registration
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
        shading = (result.meta or {}).get("shading")
        if shading and shading.get("clipped"):
            extra += f"   ·   {shading['clipped']} clipped -- lower the exposure"
        if result.supersedes:
            extra += "   ·   replaced its prescan"
        extra += read_note((result.meta or {}).get("read_direction"))
        self.v_caption.set(result.label + extra)
        self._measure_histogram()
        self._schedule_redraw()

    def _reshow(self, *results) -> None:
        """Redraw the filmstrip, and the big picture if it is one of these.

        A turn made in the contact sheet used to reach the thumbnail and stop
        there, so the preview behind it went on showing the old arrangement
        until the frame was clicked again -- the window disagreeing with itself
        about a decision the operator had just made.
        """
        self._redraw_strip()
        if self.current is not None and any(r is self.current for r in results):
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
        said = self._deliver_one(result, path, jpeg_quality(self.v_jpegq.get()),
                                 self.v_mono.get(), self.v_mono_channel.get())
        if said:
            self._say(f"saved {said}")

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
            f"Write {len(passes)} passes into {Path(folder).name} as "
            f"{fmt.upper()}.\n\n"
            + (f"{filed} of them are re-corrected from their library entries at "
               f"full resolution, which takes a moment each.\n\n"
               if filed else "")
            + (f"{len(passes) - filed} are the reduced previews on screen, "
               "because their full-resolution pixels are not filed yet -- the "
               "log says which.\n\n" if len(passes) - filed else "")
            + "Nothing already there is overwritten; a clashing name gets the "
              "next free one.\n\nStart?",
        ):
            return

        # Read here, on the UI thread, and handed over. A worker that reached
        # back into a Tk variable would work until the day it did not.
        quality = jpeg_quality(self.v_jpegq.get())
        mono, channel = self.v_mono.get(), self.v_mono_channel.get()
        out = Path(folder)
        self._saving = True
        self._say(f"saving {len(passes)} passes into {out} ...")

        def run() -> None:
            written = 0
            for result in passes:
                try:
                    path = _unclaimed(out / batch_name(result, fmt))
                    said = self._deliver_one(result, path, quality, mono,
                                             channel)
                except Exception as exc:                 # noqa: BLE001
                    # One pass that cannot be written costs that pass. The same
                    # line `FrameWriter` takes about a frame it cannot file.
                    self._saves.put(("line", f"could not save "
                                             f"{result.label}: {exc}"))
                    continue
                if said:
                    written += 1
                    self._saves.put(("line", f"saved {said}"))
            self._saves.put(("done", written, len(passes)))

        threading.Thread(target=run, daemon=True,
                         name="save-all").start()

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
            if mono:
                full = to_monochrome(full, mono_channel)
            note = export.write(path, full, quality=quality)
            how = entry_record.get("corrected")
            return (f"{Path(path).name} at full resolution"
                    + (f", turned {result.rotation}\u00b0"
                       if result.rotation else "")
                    + (", one channel" if mono else "")
                    + ("" if how == "applied" else f" ({how})")
                    + (f" -- {note}" if note else ""))
        if result.image is not None:
            turned = preview.orient(result.image, result.rotation,
                                    result.flipped)
            note = export.write(
                path,
                to_monochrome(turned, mono_channel) if mono else turned,
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
        self.session.rotation = result.rotation
        self.session.flip = result.flipped
        self.remember_arrangement(result)
        # Anything it stands in for follows it, so showing the prescan again
        # does not undo what was just decided.
        if result.supersedes:
            for r in self.results:
                if r.seq == result.supersedes:
                    r.rotation, r.flipped = result.rotation, result.flipped
        self._say(f"{result.label}: {said} -- new scans and the files written "
                  "for them follow this; the library entry keeps the scanner's "
                  "own orientation")
        self._view = [0.0, 0.0]
        self._show(result)
        self._redraw_strip()

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
        """
        result = self.current
        if result is None or result.image is None:
            self.histogram.nothing()
            return
        pixels, source = self._finest_pixels(result)
        # Infrared is not an exposure -- see `rgb_only`.
        pixels = rgb_only(pixels)
        # A plain counter, not the result's seq: one pass is measured twice,
        # so a token that only said *which* pass would let the coarse answer
        # land after the fine one and quietly replace it.
        self._histogram_token += 1
        token = self._histogram_token
        self.histogram.waiting(source)

        def work():
            try:
                counts = preview.histogram(pixels)
                clipped = preview.clipping(pixels)
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
        entry = result.entry
        question = f"Remove {result.label} from this session?"
        if entry and entry.exists():
            question += (f"\n\nIts library entry {entry.name} holds the raw "
                         "bytes, which cannot be recovered without rescanning.")
            keep = messagebox.askyesnocancel(
                "Delete", question + "\n\nKeep the library entry?", parent=self.root)
            if keep is None:
                return
            if not keep:
                try:
                    shutil.rmtree(entry)
                    library.reindex(entry.parent)
                    self._say(f"deleted library entry {entry.name}")
                except OSError as exc:
                    self._say(f"could not delete {entry.name}: {exc}")
        elif not messagebox.askokcancel("Delete", question, parent=self.root):
            return
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
        """
        if self._loading == r.seq or self._levels_seq == r.seq:
            return
        self._loading = r.seq

        def work(entry: Path, seq: int) -> None:
            image = problem = None
            try:
                # Corrected, not raw: the library stores what the scanner sent
                # and the correction beside it, and this is the full-resolution
                # view an operator asked to look at.
                image, _ = library.corrected(entry)
            except Exception as exc:                     # noqa: BLE001
                problem = f"could not read {entry.name}: {exc}"
            # Through a queue, never by calling Tk. `after()` from another
            # thread is not safe, and wrapping it in a try/except turned a
            # visible failure into a silent one: the read finished, the call
            # back never arrived, and the full-resolution view simply never
            # appeared with nothing anywhere to say why.
            self._reads.put((seq, image, problem))

        threading.Thread(target=work, args=(r.entry, r.seq), daemon=True).start()

    def _loaded(self, seq: int, image) -> None:
        self._loading = None
        if self.current is None or self.current.seq != seq or image is None:
            return
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
                  f"{image.shape[1]}x{image.shape[0]} pixels")
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
                f"would take more than {MAX_FINE_STEPS} sub-frame moves. Past "
                "that the calibration goes sub-linear and the film would not "
                "travel what was asked for.\n\nClick nearer the edge you want "
                "it to reach, or use the slide buttons.", parent=self.root)
            return
        steps = max(1, -(-int(abs(want) * 1000) // int(MAX_FINE_MM * 1000)))
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
    `measure_shift_mm` reads it at about half the confidence: 93.5 falls to
    47.4 against a floor of 55, so **every frame reads `unverified` and nothing
    moves**. A roll that costs hours, delivers no correction, and says nothing.

    Read side rather than write side deliberately. Fixing `scan_roll` would
    help folders that do not exist yet; the eleven already on disk --
    `registration-D` through `registration-M` -- are the evidence this whole
    feature was built on, and only the reader recovers them.
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


def read_survey(folder, say=None) -> dict:
    """A walked strip, read back off disk so it need not be walked again.

    A survey costs four minutes of transport and is the thing the contact
    sheet is built from, but until now it died with the window -- `survey.json`
    has been written since rolls existed and never once read. Closing the app
    between walking a strip and deciding on it meant walking it again.

    Returns everything the sheet and a commissioned scan need: the frames as
    `Result` objects, the `start_at` the walk used, the prescan resolution it
    used, and any positions and orientations already approved for it.

    **The prescans are un-rotated on the way in.** `prescanNN.tif` is written
    turned the way the screen had it, and a reference has to be the film's own
    orientation or it will not correlate against a fresh pass. `rotation` is
    carried on the result instead, which is exactly how a live pass behaves.

    That is also why the manifest's single `rotation` is what un-rotates them:
    a per-frame turn is recorded in `approved.json` and never applied to a
    `prescanNN.tif`, so this arithmetic stays true however many frames were
    turned individually. The per-frame turns come back as `rotations`, which
    the sheet lays over the results afterwards. A walk's record can carry
    its prescan's own pair (`prescan_rotation`), and wins where it does: two
    walks merged into one survey need not have been made the same way up.

    ``say`` hears anything the renumbering of an old walk, or of the roll
    beside it, could not settle; see `session.renumbered`.
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
    manifest = json.loads(
        (survey_path if survey_path.exists()
         else roll_path).read_text(encoding="utf-8"))
    progress = (json.loads(roll_path.read_text(encoding="utf-8"))
                if roll_path.exists() else {})
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
    # `approved.json` was written when a roll was commissioned, and that roll
    # numbered its frames the way the file did -- so where the roll recorded
    # positions, its shift is the file's, even when a later walk into the
    # same folder has replaced the survey it was decided on.
    decided = legacy_shift(progress) if progress else None
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

    offsets, rotations, flips, entries, sources = read_approved(
        folder, legacy=shift if decided is None else decided)

    results = []
    # The walk's own records, as the roll tool's `--approved` reads them, so
    # the two cannot key one folder two ways. A stray prescan an earlier walk
    # left in the folder is not in them.
    for number, path, record in walked_prescans(folder, manifest):
        image = tiff.read(str(path))
        # Each file as it was written. A walk that added to another may have
        # been made after "rotate all", so its frames need not share the
        # manifest's pair; a record from before that was recorded has only it.
        own = record.get("prescan_rotation")
        own = turn if own is None else int(own)
        own_flip = record.get("prescan_flipped")
        own_flip = mirrored if own_flip is None else bool(own_flip)
        result = Result(
            seq=-number,                     # negative: never a live pass's seq
            kind="prescan",
            label=f"frame {number} (reopened)",
            image=preview.unorient(image, own, own_flip),
            meta={"resolution_dpi": settings.get("prescan_resolution")},
            entry=Path(entries[number]) if number in entries else None,
            registration=record.get("registration") or {},
            position=record.get("transport_position"),
            number=number,
        )
        result.hidden = False
        result.supersedes = None
        result.rotation = own
        result.flipped = own_flip
        result.levels = (preview.levels(result.image)
                         if result.image is not None else None)
        results.append(result)

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
        # one flow rather than two.
        "scanned": scanned_frames(progress),
        "settings": progress.get("settings") or manifest.get("settings") or {},
        "wanted": wanted_frames(manifest, progress),
    }


#: How a roll manifest's `settings` block maps onto the window's controls.
#: Only the ones a resume should put back: `mono` is missing deliberately,
#: because `_sync_film` derives it from the film type and restoring it would be
#: overwritten a moment later by something that looks like it disagreed.
RESTORABLE = {
    "resolution": ("dpi", str),
    "prescan_resolution": ("predpi", str),
    "infrared": ("ir", bool),
    "fast_infrared": ("fast_ir", bool),
    "film": ("film", str),
    "meter": ("meter", str),
    "mono_channel": ("mono_channel", str),
    "correct": ("correct", bool),
    "reverse_hold": ("reverse", bool),
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
    scanned, and the JPEG's own note says what arrived.
    """
    meta = result.meta or {}
    dpi = meta.get("resolution_dpi") or 0
    channels = meta.get("channels") or len(meta.get("channel_order") or "")
    ir = "_ir" if channels and int(channels) >= 4 else ""
    end = export.suffix_for(fmt)
    kind = _safe(result.kind or "scan")
    if result.number:
        return f"{kind}{int(result.number):02d}_{dpi}dpi{ir}{end}"
    return f"{kind}_{abs(int(result.seq)):03d}_{dpi}dpi{ir}{end}"


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
    entries, so a frame without one cannot be exported at all, and a caller that
    wants to say so compares this against `summary["done"]`.
    """
    from types import SimpleNamespace

    folder = summary["folder"]
    _, rotations, flips, _, _ = read_approved(folder)
    settings = summary.get("settings") or {}
    turn = int(settings.get("rotation") or 0)
    mirrored = bool(settings.get("flipped"))
    channels = 4 if settings.get("infrared") else 3
    out = []
    for number in sorted(summary.get("entries") or {}):
        out.append(SimpleNamespace(
            entry=Path(summary["entries"][number]),
            # The frame's own decision where it made one, the roll's otherwise --
            # the same precedence `_orientation_for` uses on the way out.
            rotation=rotations.get(number, turn),
            flipped=flips.get(number, mirrored),
            image=None,
            kind="frame",
            number=number,
            seq=-number,
            meta={"resolution_dpi": settings.get("resolution") or 0,
                  "channels": channels},
        ))
    return out


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


def read_approved(folder, legacy: int = 0):
    """A roll's stored decisions: `(offsets, rotations, flips, entries, sources)`.

    `approved.json` is the one thing in a roll folder that is **not** derivable
    from the library -- the frames and the prescans can be rebuilt, these
    positions and turns cannot -- which is why Delete names it and why this is
    read on its own rather than only as part of loading a whole survey.

    ``legacy`` is how far the walk this file was written against numbered its
    frames from the strip's own, for a file from before frame numbers were
    places on the strip; see `session.legacy_shift`. It keeps no positions of
    its own, so its numbers can only move with the walk's. A file that says it
    numbers by the strip is read as it stands, and so is every file when the
    caller leaves ``legacy`` at 0 -- the export path, whose library entries
    carry the same numbers the file does.
    """
    folder = Path(folder)
    offsets: dict[int, float] = {}
    rotations: dict[int, int] = {}
    flips: dict[int, bool] = {}
    entries: dict[int, str] = {}
    # Who decided each position. Written since the sheet began proposing them,
    # and read back so a reopened roll does not relabel the ensemble's numbers
    # as his -- the same reason the sheet's own state carries them.
    sources: dict[int, str] = {}
    approved_path = folder / "approved.json"
    if not approved_path.exists():
        return offsets, rotations, flips, entries, sources
    try:
        stored = json.loads(approved_path.read_text(encoding="utf-8"))
        records = stored.get("frames", [])
    except (OSError, ValueError, AttributeError):
        return offsets, rotations, flips, entries, sources
    shift = 0 if stored.get("numbering") == NUMBERING else legacy
    for record in records:
        try:
            number = int(record["number"]) + shift
        except (KeyError, TypeError, ValueError):
            continue
        if record.get("offset_mm"):
            offsets[number] = float(record["offset_mm"])
        # `is not None` rather than truthiness: an explicit zero is a decision
        # here, and a file written before this existed has no key at all rather
        # than a zero.
        if record.get("rotation") is not None:
            rotations[number] = int(record["rotation"]) % 360
        if record.get("flipped") is not None:
            flips[number] = bool(record["flipped"])
        if record.get("reference_entry"):
            entries[number] = record["reference_entry"]
        if record.get("source"):
            sources[number] = str(record["source"])
    return offsets, rotations, flips, entries, sources


def roll_entry_index(library_root) -> dict[str, dict[int, Path]]:
    """Every library entry that belongs to a roll, by roll name and frame.

    One glob for the whole library rather than one per roll: there are two
    hundred entries and a dozen rolls, and asking the question per roll turns a
    listing into a quadratic one.

    The join is on `film.frame`, which `ScanSession._file` sets to
    ``"{roll}-{NN}"`` for every roll frame. Nothing in a roll manifest records
    its entries -- the entry is created on the writer thread *after* the frame's
    record is written, and writing that file from both threads is a hazard worth
    not introducing for a convenience.

    `library.entries()` cannot be used here: it returns the records and throws
    away the folder each came from, which is the only part this needs.
    """
    out: dict[str, dict[int, Path]] = {}
    root = Path(library_root)
    if not root.is_dir():
        return out
    for record_path in sorted(root.glob("*/scan.json")):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        member = (record.get("extra") or {}).get("roll_membership")
        if isinstance(member, dict):
            # Said by the entry itself. Only a scanned frame is the frame:
            # a walk's prescan shares its roll and number, and Export used to
            # deliver the 300 dpi prescan as the frame at full resolution.
            if member.get("kind") != "frame":
                continue
            roll, number = str(member.get("roll") or ""), member.get("number")
            if not roll or not isinstance(number, int):
                continue
            out.setdefault(roll, {})[number] = record_path.parent
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
        out.setdefault(roll, {})[int(number)] = record_path.parent
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


def roll_summary(folder, entries: dict | None = None) -> dict | None:
    """What a roll folder holds, without reading a single pixel.

    This is what the browser lists from, so it has to be cheap: a roll
    directory can hold 38 frames at 142 MB each, and opening them to find out
    whether the roll is finished would make the list unusable. Everything here
    comes from the two small JSON files.

    ``None`` for a directory that is not a roll at all, so the caller can scan
    a folder of them without filtering first.
    """
    folder = Path(folder)
    survey_path, roll_path = folder / "survey.json", folder / "roll.json"
    if not survey_path.exists() and not roll_path.exists():
        return None
    try:
        manifest = json.loads(
            (survey_path if survey_path.exists()
             else roll_path).read_text(encoding="utf-8"))
        progress = (json.loads(roll_path.read_text(encoding="utf-8"))
                    if roll_path.exists() else {})
    except (OSError, ValueError):
        return None
    # The same numbering `read_survey` puts on them, so the list and the sheet
    # it opens cannot disagree about which frames are left.
    progress = renumbered(progress, fallback=legacy_shift(manifest) or 0)
    manifest = renumbered(manifest)

    settings = progress.get("settings") or manifest.get("settings") or {}
    wanted = wanted_frames(manifest, progress)
    done = scanned_frames(progress)
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
        "remaining": [n for n in wanted if n not in done],
    }


def rolls_on_disk(root, library_root=None) -> list[dict]:
    """Every roll under `root`, newest first, as `roll_summary` describes them.

    Sorted by the directory name because that is the date the session wrote --
    `rolls/2026-09-14` -- which orders correctly as text and does not move when
    a file inside is touched, as an mtime would. The table re-sorts it anyway;
    this is the order it opens on.

    `library_root` is where the entries are looked for, once for the whole
    listing. Without it the rolls still list, and none of them knows whether it
    can be exported.
    """
    root = Path(root)
    if not root.is_dir():
        return []
    index = roll_entry_index(library_root) if library_root else {}
    out = []
    for folder in sorted(root.iterdir(), key=lambda p: p.name, reverse=True):
        if not folder.is_dir():
            continue
        summary = roll_summary(folder)
        if summary is None:
            continue
        summary["entries"] = dict(index.get(summary["roll"], {}))
        out.append(summary)
    return out


#: What the table can be ordered by, and how. Each is a key function over a
#: summary; the browser sorts with these rather than on the rendered text, so
#: "2 of 10" does not sort before "2 of 4" and 900 MB does not sort before 1 GB.
SORT_KEYS = {
    "roll": lambda r: str(r["roll"]).lower(),
    "frames": lambda r: (len(r["remaining"]), -len(r["wanted"])),
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
    """
    wanted, done = len(summary["wanted"]), len(summary["done"])
    if not summary["scanned"]:
        frames = f"walked, {wanted}"
    elif wanted and done >= wanted:
        frames = f"finished, {done}"
    elif wanted:
        frames = f"{done} of {wanted}"
    else:
        frames = f"{done} scanned"
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
        if unfinished_only and not roll["remaining"]:
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


def roll_line(summary: dict) -> str:
    """One row of the browser: what this roll is and how far it got."""
    wanted, done = len(summary["wanted"]), len(summary["done"])
    if not summary["scanned"]:
        state = f"walked, {wanted} frames, none scanned"
    elif wanted and done >= wanted:
        state = f"finished, {done} frames"
    elif wanted:
        state = f"{done} of {wanted} scanned -- {wanted - done} left"
    else:
        state = f"{done} scanned"
    parts = [summary["roll"], state]
    if summary["resolution"]:
        parts.append(f"{summary['resolution']} dpi"
                     + (" RGBI" if summary["infrared"] else " RGB"))
    if summary["film"]:
        parts.append(str(summary["film"]))
    return "  ·  ".join(parts)


def scanned_frames(manifest: dict) -> set[int]:
    """Which frames of a roll are finished, from its manifest.

    A frame that errored is **not** finished. That is the case a resume exists
    for, so it comes back offered again rather than counted as done.

    Manifests written before `done` existed have no key at all; there an image
    filed without an error is the best evidence available, which is the same
    test `done` records.
    """
    out: set[int] = set()
    for record in manifest.get("frames") or ():
        try:
            number = int(record["number"])
        except (KeyError, TypeError, ValueError):
            continue
        finished = record.get("done")
        if finished is None:
            finished = not record.get("error") and "prescan" not in record
        if finished:
            out.add(number)
    return out


def wanted_frames(manifest: dict, progress: dict) -> list[int]:
    """Every frame this roll is meant to end up with, in order.

    The operator's own choice where he made one -- `only` is what a contact
    sheet's ticks become -- and otherwise every frame the walk found. Without
    this a resume cannot say "11 of 24": it would know what was done and not
    what was asked for.
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
    numbers = []
    for record in manifest.get("frames") or ():
        try:
            numbers.append(int(record["number"]))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(set(numbers))


def snap_offset(millimetres: float) -> float:
    """The nearest position the transport can actually reach.

    A number finer than the hardware is a lie. The reachable set starts at one
    SLIDE command and steps by param, so there is nothing at all between zero
    and `FINE_STEP_MM` -- showing an operator "+1.3 units" invites him to aim at
    a place that does not exist. Clamped to what eight commands can chain,
    which is `MAX_TRAVEL_MM`, so the planner is never asked for a distance it
    would refuse.
    """
    want = max(-MAX_TRAVEL_MM, min(MAX_TRAVEL_MM, float(millimetres)))
    sign = -1.0 if want < 0 else 1.0
    try:
        plan = plan_nudges(want)
    except ValueError:
        plan = []
    # The result has to be re-plannable, or the adjuster stores a number the
    # mover would later refuse. Eight commands of the largest step sum to
    # slightly more than eight times the nominal maximum, so the top of the
    # range can snap to a value just past what the planner accepts back. Drop
    # a step until it survives the round trip.
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
#: transport can reach, which is not a constant -- the lattice is 2.57 units
#: off zero and 1.0 everywhere above it.
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


def _propose_positions(results, kept: dict, remembered=None, *, film=None,
                       progress=None) -> tuple[dict, dict]:
    """Where the walked strip says each frame should go, his numbers winning.

    The positions centre each frame between its two edges, as the frame-edge
    detector reads them (`tools/frame_edges`, a copy of the study's
    `ensemble_v2`): every frame against the other frames of its walk, and the
    frame taken to be `framing.FRAME_WIDTH_UNITS` wide -- measured, and wider
    than the aperture, so a centred frame shows no base at either edge.
    ``film`` is the film the walk was on; the detector reads negatives only.

    The whole survey in one call. The window no longer calls this: it reads a
    walk in the background as the prescans arrive (`frame_edges.EdgeWatch`)
    and puts the answer through `_snap_proposals` and `_merge_kept`, which is
    this function after its first step -- so both reach the same positions.
    Every frame is read against the whole walk, not only the frames behind it:
    a finished walk can speak for a frame from both sides.

    A frame the operator has already positioned is left exactly as he left it
    and is not re-proposed. His number is the authority here and stays it --
    the sheet is where he corrects this, so overwriting what he typed would
    undo the correction it exists to collect. A position remembered from the
    *machine* is different: it is read again, so every number on the sheet
    that is not his is today's detector's. Every frame carries the detector's
    reading of its edges either way.

    `remembered` says who decided each kept position, from the sheet's own
    stored state. Without it every kept offset was stamped `operator`, which
    was true when the only way to have one was to type it and false from the
    moment the sheet began proposing them: reopening a sheet relabelled the
    whole strip as his.
    """
    frames = [(int(getattr(r, "number", 0)), r.image)
              for r in results
              if getattr(r, "image", None) is not None
              and getattr(r, "number", None)]
    if not frames:
        return dict(kept), {}
    try:
        offsets, notes = frame_edges.propose_centred(
            frames, film=film or FILM_NEGATIVE, progress=progress)
    except Exception as exc:                                  # noqa: BLE001
        # A sheet that will not open is worse than one with no proposals: the
        # walk has already been paid for and the frames are still choosable.
        return dict(kept), {0: {"source": "none", "reason": str(exc)}}
    return _merge_kept(*_snap_proposals(offsets, notes), kept, remembered)


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
    than kept, so today's reading replaces it; see `_propose_positions`.
    """
    known = remembered or {}
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

    ``(left, right)`` in units. Positive is picture beyond the aperture, which
    the scan will not see; negative is unexposed base showing inside it. The
    frame is 350.6 units and the aperture 344.5, so a centred frame reads
    about +3 on both sides -- the red line sits just outside the orange one on
    purpose. The shift is the big view's own: ``offset_mm`` over the aperture,
    in the prescan's columns, as `measure_shift_mm` checks it.
    """
    ends = frame_ends(read)
    if ends is None:
        return None
    left, right, _ = ends
    width = int(read["width"])
    shift = offset_mm / APERTURE_MM * width
    per = units_per_column(width)
    return (-(left + shift) * per, (right + shift - width) * per)


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
    if outcome in ("not_converged", "abandoned", "budget", "stopped"):
        return f"   ·   not aimed ({outcome.replace('_', ' ')})"
    reason = (fix.get("reason") or "").split(";")[0].split(" -- ")[0]
    return f"   ·   not aimed{f': {reason}' if reason else ''}"


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
    it, and absent means `operator`: that is what an approval used to mean
    before the sheet pre-filled a position for every frame it could read. It is
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
            source=(labels.get(number) or {}).get("source") or "operator",
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


def stop_label(job: str) -> str:
    """What the stop button should say while `job` is running.

    "Stop" cannot mean "now" on this device: a pass in flight always finishes,
    because an abandoned read is what costs a power cycle. So the button has to
    say which of the two things it will do, and be right about it for the whole
    run -- it once read the progress label, which the line counter overwrites a
    second in, and so relabelled itself mid-roll.
    """
    return "Stop after this frame" if "roll" in job else "Stop (finishes this pass)"


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
                  "and step the film in the position window.\n\n"
                  "No shortcut starts a scan, calibrates, or moves film. Those "
                  "cost minutes of the scanner or move your negative, and a "
                  "slip on the keyboard is not a decision to do either.")
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
            held = shortcuts.in_scope(self.keys, scope).get(sequence)
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

    WIDTH, HEIGHT = 900, 640
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

        self.top = tk.Toplevel(gui.root)
        self.top.title("Frame position")
        self.top.transient(gui.root)
        self.top.geometry(f"{self.WIDTH}x{self.HEIGHT}")

        outer = ttk.Frame(self.top, padding=10)
        outer.pack(fill="both", expand=True)

        self.v_title = tk.StringVar()
        ttk.Label(outer, textvariable=self.v_title,
                  font=_font(12, bold=True)).pack(anchor="w")
        ttk.Label(
            outer, foreground="#777",
            text=("Drag the picture, or use the arrow keys, to say where the "
                  "film should sit. \u201cfinest\u201d moves to the next "
                  "position the transport can reach; the others move by that "
                  "much and land on the nearest one. The dashed lines are the "
                  "aperture -- exactly what the scan takes, and anything past "
                  "them will not be scanned. The red dotted line is where the "
                  "edge detector reads the picture ending and unexposed film "
                  "beginning; the lighter one is where the frame's other end "
                  "must be, a frame's measured width away. Both move with the "
                  "picture. A frame is a little wider than the aperture, so "
                  "centred it overhangs both guides by about 3 units -- the "
                  "red lines sit just outside the orange ones on purpose, and "
                  "the line under the picture says how much is past each. "
                  "Centre puts the frame back as it was walked; Reset "
                  "puts it back where the detector puts it. Return "
                  "keeps this frame and moves to the next. Shown as the film "
                  "sits, not arranged.")
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
        for sequence, action_id in shortcuts.in_scope(
                self.gui.keys, "adjuster").items():
            run = actions.get(action_id)
            if run is not None:
                self.top.bind(sequence, self.gui._runner(run, sequence))
                self._bound.append(sequence)

    def _accept(self) -> None:
        """Keep this frame and move on to the next one.

        The offset is already recorded -- `_set` writes it into the sheet as
        the picture is dragged -- so there is nothing here to save. What this
        does is say yes: tick it for scanning, and show the next frame. That
        is the whole of the job this window exists for, done one key at a time
        rather than one mouse round trip at a time.

        The last frame closes the window, because there is nowhere further to
        go and leaving it open invites a press that does nothing.
        """
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
        value = snap_offset(millimetres)
        if value:
            self.sheet.offsets[self.number] = value
        else:
            self.sheet.offsets.pop(self.number, None)
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
        self.gui._say(f"frame {self.number}: position reset to the detector's")

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
        self.v_title.set(
            f"Frame {self.number} of {len(self.sheet.frames)}")
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
        # The aperture is where the picture sits when nothing is asked for, so
        # the guides stay put and the picture moves against them -- which is
        # what the film will actually do.
        shift = int(round(self.offset / APERTURE_MM * width))

        self.canvas.delete("all")
        self.canvas.create_image(left + shift, top, image=self._photo,
                                 anchor="nw")
        for x in (left, left + width):
            self.canvas.create_line(x, top, x, top + height,
                                    fill=self.GUIDE, dash=(4, 3), width=2)
        if shift:
            self.canvas.create_rectangle(
                left, top, left + width, top + height,
                outline="#6a6a6a", dash=(2, 4))
        self._draw_edges(left + shift, top, width, height)

    def _draw_edges(self, x0: int, top: int, width: int, height: int) -> None:
        """The detector's edges, on the picture as it is drawn at ``x0``.

        On the film, not on the aperture: the line moves with the picture, so
        with the proposed move applied it shows where the edge lands against
        the guides -- just outside them, for a frame wider than the aperture.
        Tilted when the reading was, from its top row to its bottom row.
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
        self.table = ttk.Treeview(
            body, columns=[key for key, *_ in self.COLUMNS],
            show="headings", selectmode="extended")
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
        self.v_note = tk.StringVar(
            value="Unfinished rolls are marked. Opening one offers to finish it.")
        ttk.Label(buttons, textvariable=self.v_note, foreground="#777",
                  wraplength=420, justify="left").pack(side="left")

        self._fill()

    # -- what is showing ---------------------------------------------------

    def _fill(self) -> None:
        """Apply the filter and the sort, and redraw the rows."""
        rows = matching(self.rolls, self.v_find.get(),
                        self.v_unfinished.get())
        key, descending = self._sort
        rows.sort(key=SORT_KEYS.get(key, SORT_KEYS["created"]),
                  reverse=descending)
        self.shown = rows
        self.table.delete(*self.table.get_children())
        for index, summary in enumerate(rows):
            tags = []
            if summary["remaining"]:
                tags.append("unfinished")
            # Scanned, but nothing left in the library to re-correct from, so
            # Export cannot work on it. Said in the list rather than at the end
            # of a failed export.
            if summary["done"] and not summary["entries"]:
                tags.append("orphaned")
            self.table.insert("", "end", iid=str(index),
                              values=roll_cells(summary), tags=tuple(tags))
        left = sum(1 for r in rows if r["remaining"])
        self.v_count.set(f"{len(rows)} of {len(self.rolls)} shown"
                         + (f", {left} unfinished" if left else ""))

    def _sort_by(self, key: str) -> None:
        current, descending = self._sort
        self._sort = (key, not descending if key == current else key != "roll")
        self._fill()

    def _reload(self) -> None:
        """Re-read the shelf. After anything that changed it on disk."""
        self.rolls = rolls_on_disk(self.gui.session.rolls,
                                   self.gui.session.root)
        for summary in self.rolls:
            stored = (self.gui.remembered.get("rolls") or {}).get(
                Path(summary["folder"]).name) or {}
            summary["opened"] = stored.get("opened")
        self._fill()

    def _selected(self) -> list[dict]:
        return [self.shown[int(iid)] for iid in self.table.selection()
                if iid.isdigit() and int(iid) < len(self.shown)]

    def _one(self, what: str):
        picked = self._selected()
        if len(picked) != 1:
            messagebox.showinfo(what, f"Pick one roll to {what.lower()}.")
            return None
        return picked[0]

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
        self.top.destroy()
        self.gui.open_roll(summary["folder"])

    def _export(self) -> None:
        picked = self._selected()
        if not picked:
            messagebox.showinfo("Export", "Pick at least one roll to export.")
            return
        self.gui.on_export_rolls(picked)

    def _duplicate(self) -> None:
        summary = self._one("Duplicate")
        if summary is not None:
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
        picked = self._selected()
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
    OPTIONS = ("dpi", "predpi", "film", "meter", "ir", "fast_ir", "correct")
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
                 readings=None, generation=None):
        self.gui = gui
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
        #: Where the operator says each frame should sit, in mm, relative to
        #: where it was surveyed. Absent means "as surveyed" -- an explicit
        #: zero never lands here, because snap_offset returns it as absent.
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
        self.top.title("Contact sheet")
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
                  text=f"{len(self.frames)} frames walked").pack(anchor="w")
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
            said = ("Tick what is worth scanning. " + said + " A frame is "
                    "scanned the way you leave it here. Positions you set are "
                    "used as given -- nothing moves until you commission the "
                    "scan, and the automatic nudge does not apply to frames "
                    "you adjust. The film is rewound to the start of the strip "
                    "first, and every frame nobody ticked costs its advance "
                    "only.")
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
                          ("fast_ir", "infrared at scan resolution"),
                          ("correct", "nudge registration between frames")):
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
        for sequence, action_id in shortcuts.in_scope(
                self.gui.keys, "sheet").items():
            run = actions.get(action_id)
            if run is not None:
                self.top.bind(sequence, self.gui._runner(run, sequence))
                self._bound.append(sequence)
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
        picture.bind("<Double-Button-1>", lambda _e, i=index: self.adjust(i))
        for seq in MENU_EVENTS:
            picture.bind(
                seq, lambda e, i=index: self.on_cell_menu(e, i))

        ttk.Checkbutton(cell, text=f"Frame {number}", variable=var,
                        command=self._changed).pack(anchor="w", pady=(4, 0))
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
        self.gui.remember_arrangement(result)
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
        self.gui._say(f"frame {number}: {said} -- scanned this way; "
                      "the other frames are unchanged")

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
            self.gui.session.rotation = last.rotation
            self.gui.session.flip = last.flipped
        self.gui._reshow(*self.frames)
        self.gui._say(f"every frame: {said} -- and new scans follow this "
                      "until something says otherwise")

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
        """The cell's line under the picture.

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

        The three per-frame maps keep their own conventions rather than being
        flattened together. `offsets` treats an absent entry and an explicit
        zero as the same thing; `rotations` and `flips` must not, because
        "rotate all" moves the session default and a frame straightened by
        hand would fall back to it and be scanned sideways. That happened on
        the first strip this was driven on.
        """
        return {
            "ticks": {int(n): bool(v.get()) for n, v in self.ticks.items()},
            "offsets": {int(n): float(v) for n, v in self.offsets.items()},
            "rotations": {int(n): int(t) for n, t in self.rotations.items()},
            "flips": {int(n): bool(f) for n, f in self.flips.items()},
            # Who decided each position. Without it a reopened sheet handed
            # every offset back as `kept`, and `_propose_positions` stamps
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
        if self._adjuster is not None and self._adjuster.alive():
            self._adjuster.top.destroy()
        self._dismiss()
        self.gui.on_scan_chosen(picked, approved, options)

    def alive(self) -> bool:
        try:
            return bool(self.top.winfo_exists())
        except tk.TclError:
            return False


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
                         "demo/library with --demo)")
    ap.add_argument("--demo-source", default="library",
                    help="which library --demo shows pictures from; a roll "
                         "after the first also draws on every library beside "
                         "it, such as 'library 2'")
    ap.add_argument("--demo-entry", default=None,
                    help="a specific library entry for --demo to show; by "
                         "default the highest-resolution one that has both a "
                         "prescan and a scan of the same picture")
    ap.add_argument("--reference", default=None)
    ap.add_argument("--rolls", default=None)
    ap.add_argument("--out", default=None,
                    help="also write a TIFF of every scan here")
    ap.add_argument("--settings", default=None,
                    help=f"where the window remembers its setup "
                         f"(default: {settings.DEFAULT_PATH}, or "
                         f"${settings.PATH_ENV}, or demo/gui-settings.json "
                         f"with --demo)")
    ap.add_argument("--open-roll", default=None, metavar="ROLL",
                    help="open this roll folder as soon as the window is up, "
                         "as a path or as a bare name under the rolls "
                         "directory. Its positions are proposed afresh from "
                         "the prescans, every launch.")
    ap.add_argument("--look-only", action="store_true",
                    help="there is no film in the transport. Every control "
                         "still works; anything that reaches for film says so")
    args = ap.parse_args()

    home = DEMO_ROOT if args.demo else Path(".")

    # A demo keeps its own remembered geometry and sheet state. It used to
    # write them into the real file, which is the one thing DEMO_ROOT exists
    # to prevent everywhere else.
    settings_path = args.settings
    if settings_path is None and args.demo:
        settings_path = str(DEMO_ROOT / "gui-settings.json")

    # Resolved before a window exists, so a typo is a line of text rather than
    # a dialog behind a half-built window.
    #
    # A path is taken as given, which under --demo means a real walk in
    # `rolls/` and not `demo/rolls`. That is deliberate: the point of the demo
    # sheet is a strip that was actually walked. It is safe because opening a
    # roll only reads it -- `_write_approved` derives its folder from
    # `session.rolls`, which --demo pins under `demo/`, so nothing the window
    # does afterwards can write back into the walk it is showing.
    open_roll = None
    if args.open_roll:
        rolls_dir = Path(args.rolls) if args.rolls else home / "rolls"
        for candidate in (Path(args.open_roll), rolls_dir / args.open_roll):
            if candidate.is_dir():
                open_roll = candidate
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
    ScannerGui(root, session, demo=args.demo, settings_path=settings_path,
               look_only=args.look_only, open_roll=open_roll)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
