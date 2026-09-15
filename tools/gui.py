#!/usr/bin/env python3
"""A window for scanning negatives on the Reflecta RPS 7200.

    make run                      # the real scanner
    make run-demo                 # stored library entries, nothing on the bus

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
import json
import math
import queue
import shutil
import sys
import time
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rps7200 import export, library, preview, settings, shortcuts, tiff  # noqa: E402
from rps7200.direct import (                              # noqa: E402
    FILM_BW,
    FILM_TYPES,
    INFRARED_IS_BLIND_TO,
    METER_MODES,
)
from rps7200.framing import FULL_FRAME                    # noqa: E402
from rps7200.library import FilmNotes                     # noqa: E402
from rps7200.mono import (                                 # noqa: E402
    MONO_AVERAGE,
    MONO_CHANNEL,
    MONO_CHOICES,
    to_monochrome,
)
from rps7200.protocol import COORD_PER_INCH, MM_PER_INCH  # noqa: E402
from rps7200.session import (                             # noqa: E402
    Approved,
    Result,
    _safe,
    Calibrate,
    Move,
    Prescan,
    Roll,
    Scan,
    ScanSession,
    estimate_seconds,
    plan_nudges,
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
REMEMBERED = ("dpi", "predpi", "ir", "film", "expmode", "exposure", "shading",
              "meter", "dryrun", "correct", "fine", "aim", "reverse",
              "frames", "startat", "outfmt", "jpegq", "adjuststep")

#: What a preset carries: the scan settings, and nothing about the film in the
#: transport or where the files go.
PRESET_KEYS = ("dpi", "predpi", "ir", "film", "expmode", "exposure", "shading",
               "meter")

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

#: The smallest move the transport can make: param 1 of the calibrated
#: sub-frame law. Asking for less does not get you less, it gets you this.
FINE_STEP_MM = 0.27
#: How finely `step_offset` looks for the next reachable position. A quarter
#: of the lattice's own spacing, so it cannot step over one.
FINEST_PROBE_MM = 0.026
#: The largest one SLIDE command delivers, param 8.
MAX_FINE_MM = 1.01
#: How many of those one move may chain, and how far that reaches.
#:
#: Not the twenty the calibration is good for: a fine adjustment that travels
#: half the aperture is misuse of the tool, and letting a click in the middle
#: of the picture ask for 18 mm of nudging would be doing badly and slowly what
#: the slide buttons do properly. Eight covers the worst real mis-framing seen
#: -- CyberView lost 6 mm on one frame of its own strip -- with margin.
MAX_FINE_STEPS = 8
MAX_TRAVEL_MM = MAX_FINE_MM * MAX_FINE_STEPS

THUMB_H = 76
POLL_MS = 120

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


class ScannerGui:
    def __init__(self, root: tk.Tk, session: ScanSession, demo: bool = False,
                 settings_path=None):
        self.root = root
        self.session = session
        self.demo = demo
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
        self.calibrated = False
        self._asked_to_calibrate = False
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
        self._survey_start = 1               # the `start at` it was walked with
        #: The prescan resolution the survey walked at. A commissioned scan is
        #: held to it when approved positions are in play: checking a frame
        #: against a reference from another resolution works geometrically but
        #: costs about half the correlation confidence -- 93.5 against 47.4 on
        #: real passes -- enough to drop a good match below the floor and
        #: report a frame as unverified for no reason.
        self._survey_predpi = None
        self._transport = None               # last frame position the device gave
        self.sheet = None                    # the contact sheet, while it is open

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

        root.title("Reflecta RPS 7200" + ("  --  demo" if demo else ""))
        root.geometry(self.remembered["window"].get("geometry") or "1280x860")
        root.minsize(900, 600)
        self._build()
        self._restore()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        # One binding at the root, dispatched by what the pointer is actually
        # over. Binding per widget did not work: the panel's children sit on
        # top of the canvas, so <Enter> fired for them and never for it.
        for sequence in ("<MouseWheel>", "<Shift-MouseWheel>",
                         "<Button-4>", "<Button-5>"):
            root.bind_all(sequence, self._on_wheel, add="+")
        root.bind_all("<TouchpadScroll>", self._on_touchpad, add="+")
        self.session.start()
        self._bind_shortcuts()
        self._later(POLL_MS, self._pump)

    # -- layout ------------------------------------------------------------

    def _build(self) -> None:
        head = ttk.Frame(self.root, padding=(10, 6))
        head.pack(fill="x")
        ttk.Button(head, text="About the scanner",
                   command=self.on_about).pack(side="left")
        ttk.Button(head, text="Shortcuts ...",
                   command=self.on_shortcuts).pack(side="left", padx=6)
        self.v_state = tk.StringVar(value="opening ...")
        ttk.Label(head, textvariable=self.v_state).pack(side="right")
        self.light = tk.Canvas(head, width=14, height=14, highlightthickness=0)
        self.light.pack(side="right", padx=(0, 8))
        self._bulb = self.light.create_oval(2, 2, 12, 12, fill=LIGHT["idle"],
                                            outline="")
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
        widget.bind("<TouchpadScroll>", touchpad, add="+")
        if deep:
            for child in widget.winfo_children():
                self._bind_scroll(child, wheel, touchpad)

    def _scrollable(self, parent: ttk.PanedWindow) -> ttk.Frame:
        """A left column that scrolls, because it is taller than the window.

        Tk has no scrollable frame, so it is the usual Canvas with a Frame
        inside, kept in step by their <Configure> events.
        """
        host = ttk.Frame(parent)
        parent.add(host, weight=1)
        canvas = tk.Canvas(host, width=262, highlightthickness=0, borderwidth=0)
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
        box = ttk.LabelFrame(parent, text="Scan", padding=8)
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
        self.b_calibrate = ttk.Button(box, text="Calibrate again",
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
        box = ttk.LabelFrame(parent, text="Transport", padding=8)
        box.pack(fill="x", pady=(8, 0))

        row = ttk.Frame(box)
        row.pack(fill="x")
        self.v_position = tk.StringVar(value="frame position: ?")
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
        ttk.Label(row, text="mm", width=4).pack(side="left")
        self.v_fine = tk.StringVar(value=f"{FINE_STEP_MM:.2f}")
        ttk.Entry(row, textvariable=self.v_fine, width=7).pack(side="left")
        ttk.Label(row, text=f"{FINE_STEP_MM:.2f}-{MAX_TRAVEL_MM:.0f}",
                  foreground="#777").pack(side="left", padx=4)

        self.v_aim = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, variable=self.v_aim, command=self._schedule_redraw,
                        text="click the prescan to align an edge").pack(
                            anchor="w", pady=(4, 0))
        self.v_reverse = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, variable=self.v_reverse,
                        text="reverse the direction").pack(anchor="w")

    def _build_roll(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="Roll", padding=8)
        box.pack(fill="x", pady=(8, 0))
        for label, var, default in (("frames", "v_frames", "6"),
                                    ("start at", "v_startat", "1")):
            row = ttk.Frame(box)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=label, width=9).pack(side="left")
            setattr(self, var, tk.StringVar(value=default))
            ttk.Entry(row, textvariable=getattr(self, var), width=6).pack(side="left")

        row = ttk.Frame(box)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="meter", width=9).pack(side="left")
        self.v_meter = tk.StringVar(value="each")
        ttk.Combobox(row, textvariable=self.v_meter, width=8, state="readonly",
                     values=list(METER_MODES)).pack(side="left")

        self.v_dryrun = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, text="dry run -- prescan and advance only",
                        variable=self.v_dryrun).pack(anchor="w", pady=2)
        self.v_correct = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, text="nudge registration between frames",
                        variable=self.v_correct).pack(anchor="w")
        self.b_roll = ttk.Button(box, text="Scan roll", command=self.on_roll)
        self.b_roll.pack(fill="x", pady=(6, 0))
        # Opens by itself when a dry run ends; this is for getting back to it
        # after it has been closed, which is most of the time it is wanted.
        self.b_sheet = ttk.Button(box, text="Contact sheet ...", state="disabled",
                                  command=self.on_contact_sheet)
        self.b_sheet.pack(fill="x", pady=(4, 0))
        ttk.Button(box, text="Reopen a survey ...",
                   command=self.on_reopen_survey).pack(fill="x", pady=(4, 0))
        ttk.Label(box, foreground="#777", wraplength=210, justify="left",
                  text=("A dry run walks the strip in about 20 seconds a frame "
                        "and opens a contact sheet. Tick the frames worth "
                        "having and only those are scanned.")).pack(
            anchor="w", pady=(4, 0))

    def _build_film(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="Film", padding=8)
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
        box = ttk.LabelFrame(parent, text="Save scans to", padding=8)
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
        # Packed and unpacked by `_sync_format`, so the knob is only there when
        # it does something. TIFF has no quality to set.
        self.quality_row = ttk.Frame(box)
        ttk.Label(self.quality_row, text="Quality").pack(side="left")
        self.v_jpegq = tk.StringVar(value=str(export.DEFAULT_QUALITY))
        spin = ttk.Spinbox(self.quality_row, from_=60, to=100, width=5,
                           textvariable=self.v_jpegq,
                           command=self._sync_format)
        spin.pack(side="left", padx=(6, 0))
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
        if jpeg:
            self.quality_row.pack(fill="x", pady=(4, 0))
        else:
            self.quality_row.pack_forget()
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
        self.strip = tk.Canvas(middle, height=THUMB_H + 12, background="#111",
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
        self.v_estimate.set(
            f"about {_duration(estimate_seconds(dpi, self.v_ir.get()))} a pass")

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
            "and READ_STATE confirms the move, so these are the reliable ones.\n\n"
            "Back / forward move a fraction of a frame. The frame counter does "
            "not see these at all, so only a prescan shows whether one landed. "
            f"The smallest step the hardware can make is {FINE_STEP_MM:.2f} mm; "
            f"one command delivers at most {MAX_FINE_MM:.2f} mm, and anything "
            f"further is several of them, up to {MAX_TRAVEL_MM:.0f} mm before "
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

    def ask_to_calibrate(self) -> None:
        """The first thing the window does, before anything else can be run.

        The reference belongs to the power-on that measured it, so a session
        that scans before calibrating is a session whose corrections describe
        some other day's sensor. Not a modal: a modal here sits inside the event
        pump and stops it, and the device is opening behind this window.
        """
        top = tk.Toplevel(self.root)
        top.title("Calibrate")
        top.transient(self.root)
        top.resizable(False, False)
        frame = ttk.Frame(top, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, font=("TkDefaultFont", 13, "bold"),
                  text="Calibrate before scanning").pack(anchor="w")
        ttk.Label(
            frame, wraplength=430, justify="left", padding=(0, 8),
            text=("The scanner measures its own per-column response and hands "
                  "it back; this driver does the dividing. Without it the "
                  "vertical striping is simply left in.\n\n"
                  "The reference belongs to the power-on that measured it, so "
                  "it is done once per session -- and with the film loaded, "
                  "which is what the vendor software does. The calibration "
                  "frame is the lower part of the transport, which the film "
                  "does not cover, so the sensor is measured either way.")
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
            top.destroy()
            if mode:
                self.v_shading.set(mode)
                self.on_calibrate(mode)

        ttk.Button(buttons, text="Not yet",
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
        x = self.root.winfo_rootx() + (self.root.winfo_width() - top.winfo_width()) // 2
        y = self.root.winfo_rooty() + 120
        top.geometry(f"+{max(0, x)}+{max(0, y)}")

    def on_prescan(self) -> None:
        dpi = self._prescan_dpi()
        if dpi is None:
            return
        self.session.submit(Prescan(resolution=dpi, film=self.v_film.get(),
                                    notes=self._notes(),
                                    tags=self._tags()))

    def on_scan(self) -> None:
        dpi, exposure = self._dpi(), self._exposure()
        if dpi is None or exposure is None:
            return
        self._pin_arrangement()
        self.session.submit(Scan(
            resolution=dpi, infrared=self.v_ir.get(), film=self.v_film.get(),
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
        dpi, predpi = self._dpi(), self._prescan_dpi()
        if dpi is None or predpi is None:
            return
        frames = self._int(self.v_frames, "Frames", 0, 100)
        start_at = self._int(self.v_startat, "Start at", 1, 100)
        if frames is None or start_at is None:
            return
        dry = self.v_dryrun.get()
        per = 23.0 if dry else estimate_seconds(dpi, self.v_ir.get()) + 70
        if not messagebox.askokcancel(
            "Scan roll",
            f"{'Walk' if dry else 'Scan'} {frames or 'as many frames as there are'} "
            f"frames at {dpi} dpi"
            f"{' with infrared' if self.v_ir.get() and not dry else ''}.\n\n"
            f"Roughly {_duration(per * (frames or 6))}. The film should already "
            "be at the first picture -- it is scanned before anything moves.\n\n"
            "Start?",
        ):
            return
        if dry:
            # Only cleared here, so a survey outlives the window that showed it
            # and the sheet can be opened again without walking the strip twice.
            self.survey = []
            # A fresh strip has no arrangements yet, and the last one's would
            # be applied to whatever pictures happen to land on the same frame
            # numbers -- a different film, shown and written sideways. The
            # positions go too: the film has moved, so they name nothing now.
            self.orientations = {}
            self._surveying = True
            self._survey_start = start_at
            self._survey_predpi = predpi
        # Starts the whole-roll estimate at the same rough figure the dialog
        # above just showed, so the number on screen does not jump the moment
        # scanning begins. `frames` is already "how many this run will do" --
        # scan_roll takes `start_at` as where to resume, not added on top.
        # The button this handler is behind is disabled while busy, so this
        # cannot race a job that is still running.
        self._roll_wall_start = time.monotonic()
        self._roll_dry = dry
        self._roll_frames_total = frames or None
        self._roll_frames_done = 0
        self._roll_seconds_per_frame = per
        self._update_roll_eta()
        self.session.submit(Roll(
            frames=frames or None, start_at=start_at, resolution=dpi,
            prescan_resolution=predpi, infrared=self.v_ir.get(),
            film=self.v_film.get(), meter=self.v_meter.get(), dry_run=dry,
            correct=self.v_correct.get(), mono=self.v_mono.get(),
            mono_channel=self.v_mono_channel.get(),
            name=self.fields["roll"].get().strip(),
            notes=self._notes(), tags=self._tags(),
        ))

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
        self._say(f"contact sheet: {len(self.survey)} walked "
                  f"{[getattr(r, 'number', '?') for r in self.survey]}")
        self.sheet = _ContactSheet(self, self.survey)

    def _per_frame_seconds(self) -> float:
        """Roughly what one frame of the roll will cost, metering included."""
        try:
            dpi = int(self.v_dpi.get().strip())
        except ValueError:
            dpi = 1800
        return estimate_seconds(dpi, self.v_ir.get()) + 70

    def on_reopen_survey(self) -> None:
        """Open a strip walked earlier instead of walking it again.

        A survey is four minutes of transport and it used to die with the
        window: `survey.json` has been written since rolls existed and was
        never read back. Closing the app between walking a strip and deciding
        on it meant doing the walk twice.
        """
        if self.busy:
            messagebox.showinfo(
                "Reopen a survey",
                "The scanner is working. Wait for it to finish, then try "
                "again -- reopening replaces whatever survey is loaded now.")
            return
        folder = filedialog.askdirectory(
            title="Reopen a survey", initialdir=str(self.session.rolls))
        if not folder:
            return
        try:
            out = read_survey(folder)
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror(
                "Reopen a survey",
                f"{Path(folder).name} does not hold a survey this can read.\n\n"
                f"{exc}\n\nA survey folder has a survey.json and the "
                "prescanNN.tif files beside it.")
            return
        if not out["results"]:
            messagebox.showerror(
                "Reopen a survey",
                f"{Path(folder).name} has a survey.json but none of its "
                "prescan files -- there is nothing to show.")
            return

        self.survey = out["results"]
        self._survey_start = out["start_at"]
        self._survey_predpi = out["prescan_resolution"]
        for result in out["results"]:
            self.results.append(result)
        self.b_sheet.configure(state="normal")
        self._redraw_strip()
        self._say(f"reopened {out['roll']}: {len(out['results'])} frames"
                  + (f", {len(out['offsets'])} with a position already set"
                     if out["offsets"] else "")
                  + (f", {len(out['rotations'])} already turned"
                     if out["rotations"] else "")
                  + (f", {sum(out['flips'].values())} flipped"
                     if any(out["flips"].values()) else ""))
        # The film is almost certainly not where the walk left it, and only
        # Stefan can see that. Said rather than guessed at.
        messagebox.showinfo(
            "Reopen a survey",
            f"{len(out['results'])} frames from {out['roll']}.\n\n"
            "The frame numbers are counted from where that walk started, so "
            "put the film back to the start of the strip before scanning "
            "anything -- nothing here can see where it is now.")
        if self.sheet is not None and self.sheet.alive():
            self.sheet.top.destroy()
        self.sheet = _ContactSheet(self, self.survey, offsets=out["offsets"],
                                   rotations=out["rotations"],
                                   flips=out["flips"])

    def on_scan_chosen(self, numbers: tuple[int, ...], approved=()) -> None:
        """Rewind to where the survey began, then scan only what was ticked.

        `approved` carries the positions set by hand in the sheet. Nothing in
        this increment consumes them -- they are written down and logged so the
        numbers can be read back before any of them is allowed to move film.
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
        dpi, predpi = self._dpi(), self._prescan_dpi()
        if dpi is None or predpi is None:
            return
        if approved and self._survey_predpi and self._survey_predpi != predpi:
            self._say(f"prescan held at {self._survey_predpi} dpi to match the "
                      f"survey your positions were set on (you asked for "
                      f"{predpi})")
            predpi = self._survey_predpi
        back = rewind_frames([r.position for r in self.survey],
                             self._survey_start)
        walked = len(self.survey)
        per = self._per_frame_seconds()
        moved = ""
        expected = max((r.position for r in self.survey
                        if r.position is not None), default=None)
        if (expected is not None and self._transport is not None
                and self._transport != expected):
            # The film has been moved since the walk, so counting frames back
            # from here lands somewhere else. Said rather than corrected: only
            # the operator can see the transport.
            moved = ("\n\nThe film has moved since the strip was walked (it "
                     f"was at {expected}, it is at {self._transport}). Put it "
                     "back, or walk the strip again -- the frame numbers below "
                     "are counted from where the walk started.")
        if not messagebox.askokcancel(
            "Scan chosen frames",
            f"Scan {len(numbers)} of the {walked} frames walked: "
            f"{', '.join(str(n) for n in numbers)}.\n\n"
            f"At {dpi} dpi{' with infrared' if self.v_ir.get() else ''}, "
            f"roughly {_duration(per * len(numbers) + back * 7)} including "
            f"rewinding {back} frame{'s' if back != 1 else ''} to the start of "
            "the strip first. The frames nobody ticked cost their advance only."
            + self._approved_note(approved) + moved + "\n\nStart?",
        ):
            return
        self._write_approved(approved)
        # Kept so the frames that come back are shown the way they were
        # written. Without it a roll returns pictures the filmstrip draws one
        # way up and the file on disk holds another.
        for record in approved:
            self.orientations[("frame", record.number)] = (
                record.rotation, bool(record.flipped))
        if back:
            self.session.submit(Move(frames=-back))
        self.session.submit(Roll(
            frames=walked, start_at=self._survey_start, resolution=dpi,
            prescan_resolution=predpi, infrared=self.v_ir.get(),
            film=self.v_film.get(), meter=self.v_meter.get(), dry_run=False,
            correct=self.v_correct.get(), only=tuple(numbers),
            approved=tuple(approved), reverse_hold=self.v_reverse.get(),
            mono=self.v_mono.get(),
            mono_channel=self.v_mono_channel.get(),
            name=self.fields["roll"].get().strip(),
            notes=self._notes(), tags=self._tags(),
        ))

    def _approved_note(self, approved) -> str:
        """What the sheet's positions will do, said plainly in the dialog.

        A ticked "nudge registration between frames" that silently does not
        apply is worse than one that is not offered.
        """
        moved = [a for a in approved if a.offset_mm]
        if not moved:
            return ""
        note = (f"\n\n{len(moved)} frame{'s' if len(moved) != 1 else ''} "
                "carry a position you set by hand; those are used exactly as "
                "given.")
        if self.v_correct.get():
            note += (" The automatic nudge stays on for the frames you did "
                     "not adjust.")
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
                "frames": [{"number": a.number,
                            "offset_mm": round(a.offset_mm, 4),
                            "rotation": int(a.rotation),
                            "flipped": bool(a.flipped),
                            "reference_entry": str(a.reference_entry or "")}
                           for a in approved],
            }, indent=2, default=str))
        except Exception as exc:                          # noqa: BLE001
            # Broad on purpose. This file is a note about what was asked for;
            # the scan is the work. Losing the note must never cost the roll,
            # and it did once -- a Path where a str was expected raised inside
            # json.dumps, took the Tk callback with it, and the operator saw
            # a button that did nothing at all.
            self._say(f"could not write approved.json ({exc}); scanning anyway")
            return
        told = ", ".join(f"{a.number}:{a.offset_mm:+.2f}mm"
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
            millimetres = abs(values[0])
        if millimetres < FINE_STEP_MM:
            messagebox.showerror(
                "Fine adjustment",
                f"The smallest move the transport can make is "
                f"{FINE_STEP_MM:.2f} mm.\n\n{millimetres:.2f} mm is less than "
                "that, so it would not move the film at all.")
            return
        if millimetres > MAX_TRAVEL_MM:
            messagebox.showerror(
                "Fine adjustment",
                f"{millimetres:.2f} mm would take more than {MAX_FINE_STEPS} "
                "sub-frame moves, and past that the calibration goes sub-linear "
                "-- the film would not travel what was asked for.\n\n"
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
            if self.session.inquiry_text and not self._asked_to_calibrate:
                self._asked_to_calibrate = True
                self._later(50, self.ask_to_calibrate)
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
                self._roll_frames_done = event.result.number
                elapsed = time.monotonic() - self._roll_wall_start
                self._roll_seconds_per_frame = elapsed / self._roll_frames_done
                self._update_roll_eta()
        elif event.kind == "transport":
            self.v_position.set("frame position: ?" if event.done < 0
                                else f"frame position: {event.done}")
            if event.done >= 0:
                self._transport = event.done
        elif event.kind == "filed":
            for r in self.results:
                if r.seq == event.done:
                    r.entry = Path(event.text)
        elif event.kind == "finished":
            self.v_progress.set(f"{event.text} -- done")
            self.v_pass_eta.set("")
            self.progress.configure(value=1000)
            self._roll_wall_start = None
            self.v_roll_eta.set("")
            if self._surveying:
                self._surveying = False
                self.b_sheet.configure(
                    state="normal" if self.survey else "disabled")
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
            self.v_roll_eta.set("")
            self._set_busy(False)
            self._light("broken")
            if self._surveying:
                self._surveying = False
                self.b_sheet.configure(
                    state="normal" if self.survey else "disabled")
            if not self.session.inquiry_text:
                messagebox.showerror("No scanner", event.text)
        elif event.kind == "closed":
            self._session_closed = True
            self._set_busy(False)
            self.v_state.set("scanner closed")
            if self.closing:
                self._quit()

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
            short = f" ({abs(residual):.2f} mm out)" if residual else ""
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
            return
        elapsed = time.monotonic() - self._roll_wall_start
        per = self._roll_seconds_per_frame
        done, total = self._roll_frames_done, self._roll_frames_total
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

    def remember_arrangement(self, result) -> None:
        """Record how this photograph is arranged, however it was decided."""
        key = picture_of(result)
        if key is not None:
            self.orientations[key] = (result.rotation, result.flipped)
        if self._surveying and result.kind == "prescan" and result.number:
            self.survey.append(result)
        if result.position is not None:
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
        if marks.get("offset_mm") is not None:
            extra = (f"   ·   offset {marks['offset_mm']:+.2f} mm, "
                     f"short by {marks.get('shortfall_mm', 0):.2f} mm")
        shading = (result.meta or {}).get("shading")
        if shading and shading.get("clipped"):
            extra += f"   ·   {shading['clipped']} clipped -- lower the exposure"
        if result.supersedes:
            extra += "   ·   replaced its prescan"
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
                            THUMB_H * 2, THUMB_H),
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
        self.strip.configure(scrollregion=(0, 0, x, THUMB_H + 12))
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
        quality = jpeg_quality(self.v_jpegq.get())
        mono = self.v_mono.get()
        if result.entry and (result.entry / "scan.tif").exists():
            # Corrected, always. The entry holds raw pixels and the reference
            # beside them; what leaves here is what the operator saw. This used
            # to copy scan.tif straight through when nothing was turned, which
            # is now an uncorrected file -- so it is re-written every time, and
            # the copy is gone deliberately rather than by oversight.
            full, entry_record = library.corrected(result.entry)
            full = preview.orient(full, result.rotation, result.flipped)
            if mono:
                full = to_monochrome(full, self.v_mono_channel.get())
            note = export.write(path, full, quality=quality)
            how = entry_record.get("corrected")
            self._say(f"saved {Path(path).name} at full resolution"
                      + (f", turned {result.rotation}\u00b0"
                         if result.rotation else "")
                      + (", one channel" if mono else "")
                      + ("" if how == "applied" else f" ({how})")
                      + (f" -- {note}" if note else ""))
        elif result.image is not None:
            turned = preview.orient(result.image, result.rotation,
                                    result.flipped)
            note = export.write(
                path,
                to_monochrome(turned, self.v_mono_channel.get()) if mono else turned,
                quality=quality)
            self._say(f"saved {Path(path).name} -- reduced preview, the "
                      "full-resolution file is not filed yet"
                      + (f" -- {note}" if note else ""))

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
                f"That point is {abs(want):.2f} mm from the {side} edge of the "
                f"aperture, and the smallest move the transport can make is "
                f"{FINE_STEP_MM:.2f} mm.\n\nIt is already as close as the "
                "hardware can put it.", parent=self.root)
            return
        if abs(want) > MAX_TRAVEL_MM:
            messagebox.showinfo(
                "Aim",
                f"That point is {abs(want):.2f} mm from the {side} edge, which "
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
            f"Move the film {abs(want):.2f} mm {way}, so that point sits at the "
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


def read_survey(folder) -> dict:
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
    the sheet lays over the results afterwards.
    """
    folder = Path(folder)
    manifest = json.loads((folder / "survey.json").read_text())
    turn = int(manifest.get("rotation") or 0)
    mirrored = bool(manifest.get("flipped"))

    offsets: dict[int, float] = {}
    rotations: dict[int, int] = {}
    flips: dict[int, bool] = {}
    entries: dict[int, str] = {}
    approved_path = folder / "approved.json"
    if approved_path.exists():
        for record in json.loads(approved_path.read_text()).get("frames", []):
            number = int(record["number"])
            if record.get("offset_mm"):
                offsets[number] = float(record["offset_mm"])
            # `is not None` rather than truthiness: an explicit zero is a
            # decision here, and a file written before this existed has no key
            # at all rather than a zero.
            if record.get("rotation") is not None:
                rotations[number] = int(record["rotation"]) % 360
            if record.get("flipped") is not None:
                flips[number] = bool(record["flipped"])
            if record.get("reference_entry"):
                entries[number] = record["reference_entry"]

    results = []
    for record in manifest.get("frames", []):
        name = record.get("prescan")
        if not name or not (folder / name).exists():
            continue
        number = int(record["number"])
        image = tiff.read(str(folder / name))
        result = Result(
            seq=-number,                     # negative: never a live pass's seq
            kind="prescan",
            label=f"frame {number} (reopened)",
            image=preview.unorient(image, turn, mirrored),
            meta={"resolution_dpi": manifest.get("prescan_resolution")},
            entry=Path(entries[number]) if number in entries else None,
            registration=record.get("registration") or {},
            position=record.get("transport_position"),
            number=number,
        )
        result.hidden = False
        result.supersedes = None
        result.rotation = turn
        result.flipped = mirrored
        result.levels = (preview.levels(result.image)
                         if result.image is not None else None)
        results.append(result)

    return {
        "results": results,
        "start_at": int(manifest.get("start_at") or 1),
        "prescan_resolution": manifest.get("prescan_resolution"),
        "offsets": offsets,
        "rotations": rotations,
        "flips": flips,
        "roll": manifest.get("roll") or folder.name,
        "rotation": turn,
        "flipped": mirrored,
    }


def snap_offset(millimetres: float) -> float:
    """The nearest position the transport can actually reach.

    A number finer than the hardware is a lie. The reachable set starts at one
    SLIDE command and steps by param, so there is nothing at all between zero
    and `FINE_STEP_MM` -- showing an operator "+0.14 mm" invites him to aim at
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


#: What one press of an arrow in the frame position window moves, as the
#: operator may choose. "finest" is not a distance: it walks to the next
#: position the transport can actually reach, which is the smallest move there
#: is and is not a constant -- the gap is 0.27 mm off zero and 0.11 mm
#: everywhere above that.
ADJUST_STEPS = ("finest", "0.27 mm", "0.50 mm", "1.00 mm")


def step_millimetres(choice: str) -> float:
    """The chosen step as a distance, or 0.0 meaning "the next one along"."""
    try:
        return float(str(choice).split()[0])
    except (ValueError, IndexError):
        return 0.0


def step_offset(current: float, direction: int, step_mm: float = 0.0) -> float:
    """Where one press of an arrow should put the frame.

    `step_mm` of zero means the finest move there is: the adjacent position on
    the transport's own lattice. That is not a fixed distance and cannot be
    written as one. Off zero the first reachable place is 0.27 mm away -- one
    SLIDE command, and nothing exists below it -- while above that the
    positions are 0.11 mm apart, because a command's distance grows by
    `STEP_MM` per param. Adding a constant and snapping gets this wrong at
    both ends: 0.27 steps over two thirds of the reachable positions, and
    0.11 rounds to nothing at all and the frame never moves.

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
    # Enough to cross the widest gap in the lattice, which is the 0.27 mm off
    # zero, several times over.
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


def _arrangement(result) -> str:
    """How a pass is arranged, in words, for a caption or a line in the log.

    One phrasing, so the caption over the picture and the log line underneath
    it cannot describe the same frame two different ways.
    """
    parts = [f"{result.rotation}\u00b0"] if result.rotation else []
    if getattr(result, "flipped", False):
        parts.append("flipped")
    return ", ".join(parts) or "as the scanner sent it"


def approved_from_sheet(frames, ticks, offsets) -> tuple:
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
    """
    picked = set(ticks)
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
        ))
    return tuple(out)


def rewind_frames(positions, start_at: int = 1) -> int:
    """How far back the film has to go before the chosen frames are scanned.

    A survey leaves the transport at the last picture it walked, and a roll
    always begins where the film already is -- so it has to be put back at the
    picture the walk started from. `start_at` advances from there, so the film
    goes back that much further again for the advance to land in the same place.

    Counted from the transport's own positions rather than from how many frames
    came back: a frame that failed still moved the film, and counting results
    would leave it short by one for every failure.
    """
    seen = list(positions)
    known = [p for p in seen if p is not None]
    if len(known) >= 2:
        walked = max(known) - min(known)
    else:
        # No positions to count, so fall back to one frame per result. It is
        # what the transport did if nothing failed, which is the usual case.
        walked = max(0, len(seen) - 1)
    return max(0, walked + max(0, start_at - 1))


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


def _wheel_amount(event: tk.Event) -> tuple[int, bool]:
    """How far to scroll, and whether sideways, from any platform's event.

    X11 sends Button-4/5 with no delta at all. Windows sends multiples of 120.
    A macOS trackpad sends small numbers -- single digits, and occasionally a
    zero for a movement too small to matter -- so treating anything under 20 as
    already being a line count is what makes two fingers feel like two fingers
    rather than one notch per gesture.
    """
    number = getattr(event, "num", 0)
    if number in (4, 5):
        return (-1 if number == 4 else 1), False
    delta = int(getattr(event, "delta", 0) or 0)
    if delta == 0:
        return 0, False
    size = abs(delta)
    step = size if size < 20 else max(1, size // 120)
    return (-step if delta > 0 else step), bool(event.state & 0x0001)


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
        self.frame = tk.Frame(parent, background=self.BACK,
                              highlightthickness=1, highlightbackground=self.EDGE)
        self.v_source = tk.StringVar(value="nothing scanned yet")
        # One line, clipped rather than wrapped: this is a caption, and three
        # lines of it made the panel taller than the chart it describes.
        tk.Label(self.frame, textvariable=self.v_source, background=self.BACK,
                 foreground="#777", font=("TkDefaultFont", 9), anchor="w",
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
                     foreground="#666", font=("TkDefaultFont", 9)).grid(
                row=0, column=column, sticky="e", padx=(0, 6))
        for row, name in enumerate(_CHANNEL_NAMES, start=1):
            tk.Label(self.table, text=name, background=self.BACK,
                     foreground=_CHANNEL_INK[row - 1],
                     font=("TkDefaultFont", 9)).grid(row=row, column=0,
                                                     sticky="w", padx=(0, 6))
            for column in range(1, 4):
                cell = tk.Label(self.table, text="--", background=self.BACK,
                                foreground="#999", font=("TkDefaultFont", 9))
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
                                text="0", font=("TkDefaultFont", 9))
        self.canvas.create_text(self.WIDTH - 3, self.HEIGHT - 7, anchor="e",
                                fill="#555", text="full scale",
                                font=("TkDefaultFont", 9))

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
        self.top.geometry("620x760")

        outer = ttk.Frame(self.top, padding=(12, 10))
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, font=("TkDefaultFont", 13, "bold"),
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
                      font=("TkDefaultFont", 11, "bold")).grid(
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

    def __init__(self, sheet, gui, index: int):
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
                  font=("TkDefaultFont", 12, "bold")).pack(anchor="w")
        ttk.Label(
            outer, foreground="#777",
            text=("Drag the picture, or use the arrow keys, to say where the "
                  "film should sit. \u201cfinest\u201d moves to the next "
                  "position the transport can reach; the others move by that "
                  "much and land on the nearest one. The dashed lines are the "
                  "aperture -- anything past them will not be scanned. Return "
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
        ttk.Label(row, text="step").pack(side="left", padx=(12, 4))
        ttk.Combobox(row, textvariable=gui.v_adjuststep, width=8,
                     state="readonly", values=list(ADJUST_STEPS)).pack(side="left")
        self.v_read = tk.StringVar()
        ttk.Label(row, textvariable=self.v_read,
                  font=("TkDefaultFont", 11)).pack(side="left", padx=12)

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
        value = snap_offset(millimetres)
        if value:
            self.sheet.offsets[self.number] = value
        else:
            self.sheet.offsets.pop(self.number, None)
        self._refresh()
        self.sheet._refresh_caption(self.number)

    def _step(self, direction: int) -> None:
        """One step. Drag is coarse; this is how a frame is landed.

        "finest" walks to the next position the transport can reach, which is
        the smallest move there is. What this replaced added a flat 0.27 mm
        and snapped, and 0.27 is not the lattice's spacing -- it is the
        distance of a single command off zero. Above that the positions are
        0.11 mm apart, so the arrows were stepping over two out of every three
        places the film could actually be put.
        """
        self._set(step_offset(self.offset, direction,
                              step_millimetres(self.gui.v_adjuststep.get())))

    def _centre(self) -> None:
        self._set(0.0)

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
            self.v_read.set("as surveyed")
        else:
            self.v_read.set(
                f"{self.offset:+.2f} mm   \u00b7   {moves} "
                f"move{'s' if moves != 1 else ''}   \u00b7   "
                f"about {seconds:.0f} s")
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
    CHOSEN = "#e8b64c"                       # the filmstrip's amber, reused
    SKIPPED = "#7a3b3b"                      # unmistakably not amber
    SELECTED = "#ffffff"                     # the keyboard's place, not a tick

    def __init__(self, gui, frames, offsets=None, rotations=None, flips=None):
        self.gui = gui
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
        #: Where the operator says each frame should sit, in mm, relative to
        #: where it was surveyed. Absent means "as surveyed" -- an explicit
        #: zero never lands here, because snap_offset returns it as absent.
        self.offsets: dict[int, float] = dict(offsets or {})
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

        self.top = tk.Toplevel(gui.root)
        self.top.title("Contact sheet")
        self.top.transient(gui.root)
        self.top.geometry("980x720")
        # Its own menu, parented on this window, so closing the sheet takes it
        # with it rather than leaving one attached to the main window.
        self.menu = tk.Menu(self.top, tearoff=0)

        outer = ttk.Frame(self.top, padding=(10, 8))
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, font=("TkDefaultFont", 12, "bold"),
                  text=f"{len(self.frames)} frames walked").pack(anchor="w")
        ttk.Label(outer, foreground="#777", justify="left", wraplength=940,
                  text=("Tick what is worth scanning. Click a picture to tick "
                        "it, double-click or press Return to set where the film "
                        "should sit, right-click to arrange it. The arrow keys "
                        "move between frames and Space ticks. A frame is "
                        "scanned the way you leave it here. Positions you set "
                        "are used as given -- nothing moves until you "
                        "commission the scan, and the automatic nudge does not "
                        "apply to frames you adjust. The film is rewound to the "
                        "start of the strip first, and every frame nobody "
                        "ticked costs its advance only.")).pack(
            anchor="w", pady=(0, 8))

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

        foot = ttk.Frame(outer)
        foot.pack(fill="x", pady=(8, 0))
        ttk.Button(foot, text="All", command=lambda: self._set_all(True)).pack(
            side="left")
        ttk.Button(foot, text="None", command=lambda: self._set_all(False)).pack(
            side="left", padx=4)
        self.v_count = tk.StringVar()
        ttk.Label(foot, textvariable=self.v_count, foreground="#777").pack(
            side="left", padx=10)
        ttk.Button(foot, text="Close", command=self.top.destroy).pack(side="right")
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
            "sheet_close": self.top.destroy,
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
        var = tk.BooleanVar(value=True)      # everything ticked; untick the duds
        self.ticks[number] = var

        cell = ttk.Frame(grid, padding=6)
        cell.grid(row=row, column=column, sticky="n")
        ring = tk.Frame(cell, background=self.CHOSEN, padx=3, pady=3)
        ring.pack()
        self._rings[number] = ring

        photo = self._render(result)
        picture = tk.Label(ring, image=photo, borderwidth=0)
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
                      text=f"drifted -- {short:.2f} mm outside").pack(anchor="w")

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
        acted on. The measured registration offset it replaces reads +-0.00 mm
        on every real prescan, because film_bounds abstains on all of them.
        """
        caption = self._captions.get(number)
        if caption is None:
            return
        offset = self.offsets.get(number)
        if offset:
            caption.configure(text=f"moved {offset:+.2f} mm",
                              foreground=self.CHOSEN)
            return
        marks = next((r.registration or {} for r in self.frames
                      if r.number == number), {})
        caption.configure(text=f"contrast {marks.get('contrast', 0):.2f}",
                          foreground="#777")

    def adjusted(self) -> dict:
        """The positions set by hand, keyed by frame number."""
        return dict(self.offsets)

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
        per = self.gui._per_frame_seconds()
        self.v_count.set(
            f"{len(picked)} of {len(self.frames)} chosen"
            + (f"   ·   about {_duration(per * len(picked))}" if picked else ""))
        self.b_scan.configure(state="normal" if picked else "disabled")

    def _scan(self) -> None:
        picked = self.chosen()
        approved = approved_from_sheet(self.frames, picked, self.offsets)
        if self._adjuster is not None and self._adjuster.alive():
            self._adjuster.top.destroy()
        self.top.destroy()
        self.gui.on_scan_chosen(picked, approved)

    def alive(self) -> bool:
        try:
            return bool(self.top.winfo_exists())
        except tk.TclError:
            return False


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--demo", action="store_true",
                    help="drive the window from stored library entries, with "
                         "no scanner on the bus; writes under demo/")
    ap.add_argument("--library", default=None,
                    help="where scans are filed (default: library, or "
                         "demo/library with --demo)")
    ap.add_argument("--demo-source", default="library",
                    help="which library --demo shows pictures from")
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
                         f"${settings.PATH_ENV})")
    args = ap.parse_args()

    home = DEMO_ROOT if args.demo else Path(".")
    session = ScanSession(
        root=args.library or str(home / "library"),
        reference=args.reference or str(home / "calibration" / "shading.npz"),
        rolls=args.rolls or str(home / "rolls"),
        out_dir=args.out,
    )
    if args.demo:
        from rps7200.demo import DemoScanner
        source, entry = args.demo_source, args.demo_entry
        session._open_scanner = lambda: DemoScanner(source, entry=entry)

    root = tk.Tk()
    ScannerGui(root, session, demo=args.demo, settings_path=args.settings)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
