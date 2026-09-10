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

Opening the window claims the device and asks it who it is, and does nothing
else. Nothing moves the mechanism until a button is pressed.
"""
from __future__ import annotations

import argparse
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

from rps7200 import library, preview, settings, tiff      # noqa: E402
from rps7200.direct import (                              # noqa: E402
    FILM_BW,
    FILM_TYPES,
    INFRARED_IS_BLIND_TO,
    METER_MODES,
)
from rps7200.framing import FULL_FRAME                    # noqa: E402
from rps7200.library import FilmNotes                     # noqa: E402
from rps7200.mono import MONO_CHANNEL, to_monochrome      # noqa: E402
from rps7200.protocol import COORD_PER_INCH, MM_PER_INCH  # noqa: E402
from rps7200.session import (                             # noqa: E402
    Calibrate,
    Move,
    Prescan,
    Roll,
    Scan,
    ScanSession,
    estimate_seconds,
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
DPI_LADDER = (300, 600, 900, 1800, 3600, 7200)

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
              "frames", "startat")

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
        self._shown = None                   # what was last drawn, for clicks
        self._scrollers: list = []           # (widget, handler) for the wheel
        self._zoom_travel = 0                # trackpad pixels not yet spent
        self.rotation = 0                    # applied to new passes and files
        self.survey: list = []               # the prescans a dry run walked
        self._surveying = False              # a dry run is running right now
        self._survey_start = 1               # the `start at` it was walked with
        self._transport = None               # last frame position the device gave
        self.sheet = None                    # the contact sheet, while it is open

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
        self._later(POLL_MS, self._pump)

    # -- layout ------------------------------------------------------------

    def _build(self) -> None:
        head = ttk.Frame(self.root, padding=(10, 6))
        head.pack(fill="x")
        ttk.Button(head, text="About the scanner",
                   command=self.on_about).pack(side="left")
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
        for key, value in self.remembered["film"].items():
            if key in REMEMBERED_FILM and key in self.fields:
                self.fields[key].set(str(value))
        # An explicit --out wins: it was typed for this run.
        if self.remembered["output"] and self.session.out_dir is None:
            self._set_outdir(str(self.remembered["output"]))
        elif self.session.out_dir is not None:
            self.v_outdir.set(str(self.session.out_dir))
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
            }, self._settings_path)
        except Exception as exc:                         # noqa: BLE001
            self._say(f"could not save the settings: {exc}")

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
            values=["R", "G", "B"])
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
        ttk.Label(box, foreground="#777", wraplength=210, justify="left",
                  text=("A TIFF of every scan is written here as it lands, "
                        "on top of the library entry. Leave empty for the "
                        "library only.")).pack(anchor="w", pady=(4, 0))

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
        for seq in ("<Button-3>", "<Button-2>", "<Control-Button-1>"):
            self.strip.bind(seq, self.on_strip_menu)
        self.menu = tk.Menu(self.root, tearoff=0)

        bottom = ttk.Frame(parent)
        parent.add(bottom, weight=2)
        prog = ttk.Frame(bottom, padding=(6, 4))
        prog.pack(fill="x")
        self.v_progress = tk.StringVar()
        ttk.Label(prog, textvariable=self.v_progress).pack(anchor="w")
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
            self.v_channel.set(self.v_mono_channel.get())
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
        self.session.submit(Prescan(resolution=dpi, notes=self._notes(),
                                    tags=self._tags()))

    def on_scan(self) -> None:
        dpi, exposure = self._dpi(), self._exposure()
        if dpi is None or exposure is None:
            return
        self.session.submit(Scan(
            resolution=dpi, infrared=self.v_ir.get(), film=self.v_film.get(),
            auto_exposure=self.v_expmode.get() == "auto",
            exposure_scale=exposure, mono=self.v_mono.get(),
            mono_channel=self.v_mono_channel.get(),
            notes=self._notes(), tags=self._tags(),
        ))

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
            self._surveying = True
            self._survey_start = start_at
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
        self.sheet = _ContactSheet(self, self.survey)

    def _per_frame_seconds(self) -> float:
        """Roughly what one frame of the roll will cost, metering included."""
        try:
            dpi = int(self.v_dpi.get().strip())
        except ValueError:
            dpi = 1800
        return estimate_seconds(dpi, self.v_ir.get()) + 70

    def on_scan_chosen(self, numbers: tuple[int, ...]) -> None:
        """Rewind to where the survey began, then scan only what was ticked."""
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
            + moved + "\n\nStart?",
        ):
            return
        if back:
            self.session.submit(Move(frames=-back))
        self.session.submit(Roll(
            frames=walked, start_at=self._survey_start, resolution=dpi,
            prescan_resolution=predpi, infrared=self.v_ir.get(),
            film=self.v_film.get(), meter=self.v_meter.get(), dry_run=False,
            correct=self.v_correct.get(), only=tuple(numbers),
            mono=self.v_mono.get(),
            mono_channel=self.v_mono_channel.get(),
            name=self.fields["roll"].get().strip(),
            notes=self._notes(), tags=self._tags(),
        ))

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
                window, counts, clipped, problem = self._measured.get_nowait()
            except queue.Empty:
                break
            if problem:
                self._say(f"could not measure: {problem}")
            else:
                window.show(counts, clipped)
        for event in self.session.poll():
            self._handle(event)
            if not self._alive:
                return
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
                self.progress.configure(value=0)
            self._set_busy(event.busy)
        elif event.kind == "progress":
            self._progress(event.done, event.total)
        elif event.kind == "result":
            self._add_result(event.result)
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
            self.progress.configure(value=1000)
            if self._surveying:
                self._surveying = False
                self.b_sheet.configure(
                    state="normal" if self.survey else "disabled")
                if self.survey:
                    self.on_contact_sheet()
        elif event.kind == "failed":
            # Reported in place, not in a modal: a modal sits inside this pump
            # and stops it, so one failed frame would freeze the window and the
            # rest of a roll would arrive unseen.
            self._say(event.text)
            self.v_progress.set(event.text)
            self.v_caption.set(event.text)
            self.progress.configure(value=0)
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
        if total > 0:
            self.progress.configure(value=int(1000 * done / total))
            self.v_progress.set(f"{done}/{total} lines")

    def _say(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # -- results and the filmstrip ----------------------------------------

    def _add_result(self, result) -> None:
        result.hidden = False
        result.supersedes = None
        # New passes arrive already turned the way the last one was, which is
        # what makes rotating a prescan carry over to the scan of it -- even a
        # scan taken minutes later.
        result.rotation = self.rotation
        # Measured once, from the whole picture. Recomputing per redraw was
        # most of what made zooming feel dead, and it also meant the brightness
        # changed as you panned -- the same negative looking different
        # depending on where you were looking.
        result.levels = preview.levels(result.image) if result.image is not None else None
        # A real scan stands in for the prescan of the same picture, but only
        # when the film has not moved since -- a prescan of a different frame is
        # a different photograph, and hiding it would lose it.
        if result.kind in ("scan", "frame") and result.position is not None:
            for earlier in reversed(self.results):
                if earlier.kind != "prescan":
                    break
                if earlier.position == result.position and not earlier.hidden:
                    earlier.hidden = True
                    result.supersedes = earlier.seq
                    break
        self.results.append(result)
        if self._surveying and result.kind == "prescan" and result.number:
            self.survey.append(result)
        if result.position is not None:
            self._transport = result.position
        for old in self.results[:-WORKING_COPIES]:
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
        extra = f"   \u00b7   {result.rotation}\u00b0" if result.rotation else ""
        if marks.get("offset_mm") is not None:
            extra = (f"   ·   offset {marks['offset_mm']:+.2f} mm, "
                     f"short by {marks.get('shortfall_mm', 0):.2f} mm")
        shading = (result.meta or {}).get("shading")
        if shading and shading.get("clipped"):
            extra += f"   ·   {shading['clipped']} clipped -- lower the exposure"
        if result.supersedes:
            extra += "   ·   replaced its prescan"
        self.v_caption.set(result.label + extra)
        self._schedule_redraw()

    def _redraw_strip(self) -> None:
        self.strip.delete("all")
        self._thumbs = []
        x = 6
        for r in self._visible():
            if r.image is None:
                continue
            arr = preview.render(
                preview.fit(preview.rotate(r.image, r.rotation),
                            THUMB_H * 2, THUMB_H),
                "RGB", self.v_invert.get(),
                cuts=(preview.channel_levels(r.levels, "RGB")
                      if getattr(r, "levels", None) is not None else None))
            photo = tk.PhotoImage(data=preview.to_ppm(arr))
            self._thumbs.append(photo)
            tag = f"r{r.seq}"
            self.strip.create_image(x, 6, image=photo, anchor="nw", tags=tag)
            if r is self.current:
                self.strip.create_rectangle(
                    x - 2, 4, x + photo.width() + 1, 8 + photo.height(),
                    outline="#e8b64c", width=2)
            self.strip.tag_bind(tag, "<Button-1>",
                                lambda _e, s=r.seq: self._show_seq(s))
            x += photo.width() + 8
        self.strip.configure(scrollregion=(0, 0, x, THUMB_H + 12))
        self.strip.xview_moveto(1.0)

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
        self.menu.delete(0, "end")
        self.menu.add_command(label="Save as ...",
                              command=lambda r=target: self.on_save_as(r))
        self.menu.add_command(label="Histogram",
                              command=lambda r=target: self.on_histogram(r))
        self.menu.add_separator()
        for label, degrees in (("Rotate right 90\u00b0", 90),
                               ("Rotate left 90\u00b0", 270),
                               ("Rotate 180\u00b0", 180)):
            self.menu.add_command(
                label=label, command=lambda r=target, d=degrees: self.on_rotate(r, d))
        if target.rotation:
            self.menu.add_command(
                label=f"Straighten (now {target.rotation}\u00b0)",
                command=lambda r=target: self.on_rotate(r, -r.rotation))
        if target.supersedes:
            self.menu.add_separator()
            self.menu.add_command(label="Show prescan",
                                  command=lambda r=target: self.on_show_prescan(r))
        self.menu.add_separator()
        self.menu.add_command(label="Delete",
                              command=lambda r=target: self.on_delete(r))
        self.menu.tk_popup(event.x_root, event.y_root)

    def on_save_as(self, result) -> None:
        name = f"{result.label.replace(' ', '_').replace('·', '')}.tif"
        path = filedialog.asksaveasfilename(
            parent=self.root, defaultextension=".tif", initialfile=name,
            filetypes=[("TIFF", "*.tif")])
        if not path:
            return
        mono = self.v_mono.get()
        if result.entry and (result.entry / "scan.tif").exists():
            if result.rotation or mono:
                # Turned and/or reduced on the way out, so the exported file
                # matches what is on screen and says which film it is by its
                # shape. The entry itself is left alone.
                full = preview.rotate(
                    tiff.read(str(result.entry / "scan.tif")), result.rotation)
                tiff.write(path, to_monochrome(full, self.v_mono_channel.get())
                           if mono else full)
                self._say(f"saved {Path(path).name} at full resolution"
                          + (f", turned {result.rotation}\u00b0"
                             if result.rotation else "")
                          + (", one channel" if mono else ""))
            else:
                # Copied rather than re-written: the entry holds the full
                # resolution, and the working copy in memory is decimated.
                shutil.copy2(result.entry / "scan.tif", path)
                self._say(f"saved {Path(path).name} at full resolution")
        elif result.image is not None:
            turned = preview.rotate(result.image, result.rotation)
            tiff.write(path, to_monochrome(turned, self.v_mono_channel.get())
                             if mono else turned)
            self._say(f"saved {Path(path).name} -- reduced preview, the "
                      "full-resolution file is not filed yet")

    def on_rotate(self, result, degrees: int) -> None:
        """Turn this pass, and everything scanned after it.

        The carry-over is the point: rotating a prescan is how you say which
        way up the film is, and the scan that follows should not need telling
        again. It reaches the files written from here on -- the output folder's
        copy and a roll's own TIFF -- but never the library entry, whose pixels
        have to keep matching the raw bytes filed beside them.
        """
        result.rotation = (result.rotation + degrees) % 360
        self.rotation = result.rotation
        self.session.rotation = result.rotation
        # Anything it stands in for turns with it, so showing the prescan again
        # does not undo what was just decided.
        if result.supersedes:
            for r in self.results:
                if r.seq == result.supersedes:
                    r.rotation = result.rotation
        self._say(f"{result.label}: {result.rotation}\u00b0 -- new scans and "
                  "the files written for them follow this; the library entry "
                  "keeps the scanner's own orientation")
        self._view = [0.0, 0.0]
        self._show(result)
        self._redraw_strip()

    def on_histogram(self, result) -> None:
        """Where the values actually sit, which the preview cannot show.

        The picture on the canvas is stretched so a negative can be judged by
        eye, and a stretch puts the brightest pixel at white whether it was
        against the ceiling or merely near it. This is the unstretched answer.

        Measured on a thread: counting a full 3600 dpi frame exactly is about a
        third of a second, and a window that locks up for that long while you
        wait to be told about clipping is its own kind of unhelpful.
        """
        if result.image is None:
            return
        pixels, source = self._finest_pixels(result)
        window = _Histogram(self.root, result.label, source)

        def work():
            try:
                counts = preview.histogram(pixels)
                clipped = preview.clipping(pixels)
            except Exception as exc:                     # noqa: BLE001
                self._measured.put((window, None, None, str(exc)))
                return
            self._measured.put((window, counts, clipped, None))

        threading.Thread(target=work, daemon=True).start()

    def _finest_pixels(self, result):
        """The best pixels available for `result`, and what to call them."""
        if (self._levels_seq == result.seq and self._levels
                and result is self.current):
            factor, array = self._levels[-1]
            return array, f"the scan's own {array.shape[1]}x{array.shape[0]} pixels"
        return (result.image,
                f"a {result.image.shape[1]}x{result.image.shape[0]} copy "
                "-- the scan itself is not in hand")

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
        return preview.rotate(r.image, r.rotation)

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
                    return preview.rotate(array, r.rotation), factor
            finest, array = self._levels[-1]
            return preview.rotate(array, r.rotation), finest
        return preview.rotate(r.image, r.rotation), 1.0

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
                image, _ = library.load(entry)
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
        # Deliberately not re-measured: the levels stay the working copy's, so
        # a 1:1 look is the same picture as the fit it came from.
        self._redraw()

    def _set_zoom(self, zoom: float) -> None:
        """Fit, or a scale about the middle of what is on screen."""
        src = self._source()
        if zoom > 0 and src is not None:
            w = max(1, self.canvas.winfo_width())
            h = max(1, self.canvas.winfo_height())
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
        w = max(1, self.canvas.winfo_width())
        h = max(1, self.canvas.winfo_height())
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
        w = max(1, self.canvas.winfo_width())
        h = max(1, self.canvas.winfo_height())
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
        w = max(1, self.canvas.winfo_width())
        h = max(1, self.canvas.winfo_height())
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
                w = max(1, self.canvas.winfo_width())
                h = max(1, self.canvas.winfo_height())
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
        flat_x, _flat_y = preview.unrotate_point(
            where[0], where[1], src.shape, self.current.rotation)
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
_CHANNEL_INK = ("#e0605a", "#5ab86a", "#5a8fe0", "#a8a8a8")


class _Histogram:
    """Where a pass's values sit, unstretched, and how much is against the ends.

    Its own window rather than a panel: it answers a question that is asked
    occasionally and about one pass, and the room it needs is room the picture
    would rather have.
    """

    WIDTH, HEIGHT = 540, 260

    def __init__(self, parent: tk.Misc, label: str, source: str):
        self.top = tk.Toplevel(parent)
        self.top.title(f"Histogram -- {label}")
        self.top.transient(parent)
        frame = ttk.Frame(self.top, padding=10)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=label, font=("TkDefaultFont", 12, "bold")).pack(
            anchor="w")
        ttk.Label(frame, text=f"measured on {source}", foreground="#777").pack(
            anchor="w", pady=(0, 6))
        self.canvas = tk.Canvas(frame, width=self.WIDTH, height=self.HEIGHT,
                                background="#141414", highlightthickness=0)
        self.canvas.pack()
        self.canvas.create_text(self.WIDTH // 2, self.HEIGHT // 2,
                                fill="#888", text="measuring ...", tags="wait")
        ttk.Label(frame, foreground="#777", justify="left", wraplength=self.WIDTH,
                  text="Counts on a square-root scale, so a small population "
                       "against an end is visible beside a large one in the "
                       "middle. The horizontal axis is the full range of the "
                       "file, not of this picture.").pack(anchor="w", pady=(6, 8))
        self.table = ttk.Frame(frame)
        self.table.pack(fill="x")
        ttk.Button(frame, text="Close", command=self.top.destroy).pack(
            anchor="e", pady=(10, 0))

    def alive(self) -> bool:
        try:
            return bool(self.top.winfo_exists())
        except tk.TclError:
            return False

    def show(self, counts, clipped) -> None:
        if not self.alive():
            return                                       # closed while measuring
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
        for channel in range(counts.shape[0]):
            ink = _CHANNEL_INK[channel] if channel < len(_CHANNEL_INK) else "#ccc"
            points = []
            for b in range(bins):
                x = 1 + b * (self.WIDTH - 2) / max(1, bins - 1)
                y = self.HEIGHT - 1 - shown[channel][b] / tallest * (self.HEIGHT - 6)
                points.extend((x, y))
            self.canvas.create_line(*points, fill=ink, width=1)
        self.canvas.create_text(4, self.HEIGHT - 8, anchor="w", fill="#666",
                                text="nothing")
        self.canvas.create_text(self.WIDTH - 4, self.HEIGHT - 8, anchor="e",
                                fill="#666", text="full scale")

        for child in self.table.winfo_children():
            child.destroy()
        headings = ("", "at nothing", "at full scale", "near full")
        for column, text in enumerate(headings):
            ttk.Label(self.table, text=text, foreground="#777").grid(
                row=0, column=column, sticky="e", padx=6)
        for channel in range(clipped.shape[0]):
            name = "RGBI"[channel] if channel < 4 else str(channel)
            ttk.Label(self.table, text=name).grid(row=channel + 1, column=0,
                                                  sticky="w", padx=6)
            for column, fraction in enumerate(clipped[channel], start=1):
                # A tenth of a percent of a frame is thousands of pixels, so
                # anything that rounds to zero is said to be zero rather than
                # shown as a very small number that invites squinting.
                text = "--" if fraction == 0 else f"{fraction * 100:.3f}%"
                ttk.Label(self.table, text=text,
                          foreground="#e0605a" if fraction > 0.001 else None).grid(
                    row=channel + 1, column=column, sticky="e", padx=6)


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

    def __init__(self, gui, frames):
        self.gui = gui
        self.frames = [r for r in frames if r.image is not None]
        self.ticks: dict[int, tk.BooleanVar] = {}
        self._photos: list[tk.PhotoImage] = []
        self._rings: dict[int, tk.Frame] = {}

        self.top = tk.Toplevel(gui.root)
        self.top.title("Contact sheet")
        self.top.transient(gui.root)
        self.top.geometry("980x720")

        outer = ttk.Frame(self.top, padding=(10, 8))
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, font=("TkDefaultFont", 12, "bold"),
                  text=f"{len(self.frames)} frames walked").pack(anchor="w")
        ttk.Label(outer, foreground="#777", justify="left", wraplength=940,
                  text=("Tick what is worth scanning. Click a picture to tick "
                        "it, double-click to open it in the preview. The film "
                        "is rewound to the start of the strip first, and every "
                        "frame nobody ticked costs its advance only.")).pack(
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
            self._cell(grid, result, cell // self.COLUMNS, cell % self.COLUMNS)

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
        self._changed()

    # -- one picture -------------------------------------------------------

    def _cell(self, grid, result, row: int, column: int) -> None:
        number = result.number
        var = tk.BooleanVar(value=True)      # everything ticked; untick the duds
        self.ticks[number] = var

        cell = ttk.Frame(grid, padding=6)
        cell.grid(row=row, column=column, sticky="n")
        ring = tk.Frame(cell, background=self.CHOSEN, padx=3, pady=3)
        ring.pack()
        self._rings[number] = ring

        arr = preview.render(
            preview.fit(preview.rotate(result.image, result.rotation),
                        self.CELL, self.CELL),
            "RGB", self.gui.v_invert.get(),
            cuts=(preview.channel_levels(result.levels, "RGB")
                  if getattr(result, "levels", None) is not None else None))
        photo = tk.PhotoImage(data=preview.to_ppm(arr))
        self._photos.append(photo)
        picture = tk.Label(ring, image=photo, borderwidth=0)
        picture.pack()
        picture.bind("<Button-1>", lambda _e, n=number: self._toggle(n))
        picture.bind("<Double-Button-1>",
                     lambda _e, r=result: self.gui._show_seq(r.seq))

        ttk.Checkbutton(cell, text=f"Frame {number}", variable=var,
                        command=self._changed).pack(anchor="w", pady=(4, 0))
        marks = result.registration or {}
        detail = f"contrast {marks.get('contrast', 0):.2f}"
        if marks.get("offset_mm") is not None:
            detail += f"   ·   {marks['offset_mm']:+.2f} mm"
        ttk.Label(cell, text=detail, foreground="#777").pack(anchor="w")
        short = marks.get("shortfall_mm") or 0.0
        if short > 0.85:
            # The same 0.85 mm the driver calls drift. Worth saying here: a
            # frame this far out has picture outside the aperture, and no
            # amount of scanning it brings that back.
            ttk.Label(cell, foreground="#e0605a",
                      text=f"drifted -- {short:.2f} mm outside").pack(anchor="w")

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

    def _changed(self) -> None:
        picked = self.chosen()
        for number, ring in self._rings.items():
            ring.configure(background=self.CHOSEN if self.ticks[number].get()
                           else "#3a3a3a")
        per = self.gui._per_frame_seconds()
        self.v_count.set(
            f"{len(picked)} of {len(self.frames)} chosen"
            + (f"   ·   about {_duration(per * len(picked))}" if picked else ""))
        self.b_scan.configure(state="normal" if picked else "disabled")

    def _scan(self) -> None:
        picked = self.chosen()
        self.top.destroy()
        self.gui.on_scan_chosen(picked)

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
