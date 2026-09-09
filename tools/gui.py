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
import shutil
import sys
import time
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rps7200 import library, preview, tiff                # noqa: E402
from rps7200.direct import FILM_TYPES, METER_MODES        # noqa: E402
from rps7200.framing import FULL_FRAME                    # noqa: E402
from rps7200.library import FilmNotes                     # noqa: E402
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

#: Integer divisors of the 7200 dpi optical resolution -- the only values any
#: USB capture of the vendor software has ever used. The box is editable, and
#: the device refuses anything it dislikes before a byte is transferred.
DPI_LADDER = (300, 360, 400, 450, 480, 600, 720, 800, 900,
              1200, 1440, 1800, 2400, 3600, 7200)

#: What a prescan is worth spending. 300 dpi is the scanner's own fast-preview
#: resolution and what the vendor uses before every frame, at ~16 s.
PRESCAN_LADDER = (300, 360, 400, 450, 480, 600, 720, 900, 1200)

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

#: While a gesture is running the picture is drawn at half resolution and
#: enlarged by Tk, which costs a quarter of the pixels and about a third of the
#: time. On a 3600 dpi frame that is 32 ms a frame against 14. It is visibly
#: coarser, so it lasts only as long as the hand is moving.
_GESTURE_FACTOR = 2
#: How long after the last gesture event the full-quality frame is drawn.
_SETTLE_MS = 130

LIGHT = {"idle": "#5a5a5a", "busy": "#3fb950", "broken": "#f05050"}


class ScannerGui:
    def __init__(self, root: tk.Tk, session: ScanSession, demo: bool = False):
        self.root = root
        self.session = session
        self.demo = demo
        self.results: list = []
        self.current = None
        self.busy = False
        self.closing = False
        self._photo: tk.PhotoImage | None = None
        self._thumbs: list[tk.PhotoImage] = []
        self._full = None                    # full-resolution pixels, for 1:1
        self._full_seq = None
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
        self._offset = [0.0, 0.0]            # pan, in source pixels
        self._drag = None
        self._pointer = (0.0, 0.0)           # where a zoom should pivot
        self._shown = None                   # what was last drawn, for clicks
        self._scrollers: list = []           # (widget, handler) for the wheel
        self._zoom_travel = 0                # trackpad pixels not yet spent
        self.rotation = 0                    # applied to new passes and files

        root.title("Reflecta RPS 7200" + ("  --  demo" if demo else ""))
        root.geometry("1280x860")
        root.minsize(900, 600)
        self._build()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        # One binding at the root, dispatched by what the pointer is actually
        # over. Binding per widget did not work: the panel's children sit on
        # top of the canvas, so <Enter> fired for them and never for it.
        for sequence in ("<MouseWheel>", "<Shift-MouseWheel>",
                         "<Button-4>", "<Button-5>"):
            root.bind_all(sequence, self._on_wheel, add="+")
        root.bind_all("<TouchpadScroll>", self._on_touchpad, add="+")
        self.session.start()
        self.root.after(POLL_MS, self._pump)

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
        ttk.Checkbutton(box, text="infrared (RGBI)", variable=self.v_ir,
                        command=self._show_estimate).pack(anchor="w", pady=2)

        row = ttk.Frame(box)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="film", width=10).pack(side="left")
        self.v_film = tk.StringVar(value="negative")
        ttk.Combobox(row, textvariable=self.v_film, width=12, state="readonly",
                     values=list(FILM_TYPES)).pack(side="left")

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
                   command=lambda: self._set_zoom(1.0)).pack(side="right")
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
            exposure_scale=exposure,
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
        self.session.submit(Roll(
            frames=frames or None, start_at=start_at, resolution=dpi,
            prescan_resolution=predpi, infrared=self.v_ir.get(),
            film=self.v_film.get(), meter=self.v_meter.get(), dry_run=dry,
            correct=self.v_correct.get(),
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
        self.root.after(150, self._wait_to_quit)

    def _quit(self) -> None:
        self._alive = False
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    # -- the event pump ----------------------------------------------------

    def _pump(self) -> None:
        if not self._alive:
            return
        for event in self.session.poll():
            self._handle(event)
            if not self._alive:
                return
        self.root.after(POLL_MS, self._pump)

    def _handle(self, event) -> None:
        if event.kind == "log":
            self._say(event.text)
        elif event.kind == "state":
            self.v_state.set(event.text.splitlines()[0])
            if self.session.inquiry_text and not self._asked_to_calibrate:
                self._asked_to_calibrate = True
                self.root.after(50, self.ask_to_calibrate)
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
        elif event.kind == "filed":
            for r in self.results:
                if r.seq == event.done:
                    r.entry = Path(event.text)
        elif event.kind == "finished":
            self.v_progress.set(f"{event.text} -- done")
            self.progress.configure(value=1000)
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
        self._offset = [0.0, 0.0]
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
        if result.entry and (result.entry / "scan.tif").exists():
            if result.rotation:
                # Turned on the way out, so the exported file matches what is
                # on screen. The entry itself is left alone.
                tiff.write(path, preview.rotate(
                    tiff.read(str(result.entry / "scan.tif")), result.rotation))
                self._say(f"saved {Path(path).name} at full resolution, "
                          f"turned {result.rotation}\u00b0")
            else:
                # Copied rather than re-written: the entry holds the full
                # resolution, and the working copy in memory is decimated.
                shutil.copy2(result.entry / "scan.tif", path)
                self._say(f"saved {Path(path).name} at full resolution")
        elif result.image is not None:
            tiff.write(path, preview.rotate(result.image, result.rotation))
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
        self._offset = [0.0, 0.0]
        self._show(result)
        self._redraw_strip()

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
        """The pixels to draw: full resolution when close in, else the copy.

        The working copy is used whenever it is enough, even after the
        full-resolution array has been read, because a decimating view over
        142 MB gathers from scattered memory and is a third slower than reading
        a contiguous nine.
        """
        r = self.current
        if r is None:
            return None
        close_in = self._zoom >= 1.0
        if close_in and self._full_seq == r.seq:
            return preview.rotate(self._full, r.rotation)
        if close_in and r.entry is not None:
            self._load_full(r)
        return preview.rotate(r.image, r.rotation)

    def _load_full(self, r) -> None:
        """Read the entry's own pixels, off the UI thread.

        A downscaled preview cannot show grain or shadow noise, so anything at
        1:1 or closer has to come from the file. At 3600 dpi that is 142 MB,
        which would freeze the window for seconds if it were read here.
        """
        if self._loading == r.seq:
            return
        self._loading = r.seq

        def work(entry: Path, seq: int) -> None:
            image = None
            try:
                image, _ = library.load(entry)
            except Exception as exc:                     # noqa: BLE001
                self._later(lambda name=entry.name, why=str(exc):
                            self._say(f"could not read {name}: {why}"))
            self._later(lambda got=image: self._loaded(seq, got))

        threading.Thread(target=work, args=(r.entry, r.seq), daemon=True).start()

    def _later(self, call) -> None:
        """Run `call` on the UI thread, unless the window has gone.

        A full-resolution read takes a moment, and the window can be closed
        inside it. Reaching into Tk from the reading thread afterwards raises
        out of that thread, where nobody catches it and it surfaces as a
        traceback with nothing to do about it.
        """
        if not self._alive:
            return
        try:
            self.root.after(0, call)
        except (tk.TclError, RuntimeError):
            pass

    def _loaded(self, seq: int, image) -> None:
        self._loading = None
        if self.current is None or self.current.seq != seq or image is None:
            return
        # Keep the pan pointing at the same place in the picture.
        if self.current.image is not None:
            grow = image.shape[1] / max(1, self.current.image.shape[1])
            self._offset = [self._offset[0] * grow, self._offset[1] * grow]
        self._full, self._full_seq = image, seq
        # Deliberately not re-measured: the levels stay the working copy's, so
        # a 1:1 look is the same picture as the fit it came from.
        self._redraw()

    def _set_zoom(self, zoom: float) -> None:
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
            self._offset = [src.shape[1] / 2, src.shape[0] / 2]
            self._schedule_redraw(moving=moving)
            return

        target = min(_MAX_ZOOM, target)
        if focus is None:
            if self._zoom <= 0:
                self._offset = [src.shape[1] / 2, src.shape[0] / 2]
        else:
            # Put `focus` back under the anchor at the new scale. The picture
            # is drawn centred, so the offset is measured from that centre.
            out_w = min(w, max(1, int(src.shape[1] * target)))
            out_h = min(h, max(1, int(src.shape[0] * target)))
            left, top = (w - out_w) / 2, (h - out_h) / 2
            self._offset = [
                focus[0] - (anchor[0] - left) / target + out_w / (2 * target),
                focus[1] - (anchor[1] - top) / target + out_h / (2 * target),
            ]
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
            return (scale, 0.0, 0.0,
                    max(1, int(wide * scale)), max(1, int(tall * scale)))
        scale = self._zoom
        out_w = min(w, max(1, int(wide * scale)))
        out_h = min(h, max(1, int(tall * scale)))
        cx, cy = self._offset
        x0 = max(0.0, min(wide - out_w / scale, cx - out_w / (2 * scale)))
        y0 = max(0.0, min(tall - out_h / scale, cy - out_h / (2 * scale)))
        return scale, x0, y0, out_w, out_h

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
        scale, x0, y0, dw, dh = self._geometry(src, w, h)
        ix = min(max(x - (w - dw) / 2, 0.0), float(dw))
        iy = min(max(y - (h - dh) / 2, 0.0), float(dh))
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
            self._offset[0] -= dx / self._zoom
            self._schedule_redraw(moving=True)
            return
        if dy:
            self._zoom_by(math.exp(dy * _ZOOM_PER_PIXEL), moving=True,
                          anchor=self._pointer)

    def _wheel_over_picture(self, amount: int, sideways: bool) -> None:
        if sideways and self._zoom > 0:
            self._offset[0] += amount * 40 / self._zoom
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
            self._settle_job = self.root.after(_SETTLE_MS, self._settle)
        since = (time.monotonic() - self._drawn_at) * 1000
        if since >= _FRAME_MS:
            self._redraw(quick=moving)
        elif self._redraw_job is None:
            self._redraw_job = self.root.after(
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
        self.canvas.delete("all")
        self._shown = None
        src = self._source()
        w = max(1, self.canvas.winfo_width())
        h = max(1, self.canvas.winfo_height())
        if src is None:
            self.canvas.create_text(w // 2, h // 2, fill="#666",
                                    text="nothing scanned yet")
            return

        # Sampled at whatever scale is asked for. Decimating and replicating by
        # whole numbers -- which is what this replaced -- can only show 50%,
        # 100%, 200%, so a smooth zoom reached the screen as jumps between them.
        scale, x0, y0, out_w, out_h = self._geometry(src, w, h)
        # A moving frame is drawn at a fraction of the size and enlarged by
        # Tk, which is far cheaper than sampling and rendering every pixel: both
        # of those costs, and building the image Tk shows, scale with the count.
        coarse = _GESTURE_FACTOR if quick and out_w > 2 * _GESTURE_FACTOR else 1
        arr = preview.sample(src, scale / coarse, x0, y0,
                             max(1, out_w // coarse), max(1, out_h // coarse))
        try:
            rgb = preview.render(arr, self.v_channel.get(), self.v_invert.get(),
                                 cuts=self._cuts())
        except ValueError as exc:
            self.canvas.create_text(w // 2, h // 2, fill="#888", text=str(exc))
            return
        photo = tk.PhotoImage(data=preview.to_ppm(rgb))
        if coarse > 1:
            photo = photo.zoom(coarse, coarse)
        self._photo = photo
        self.canvas.create_image(w // 2, h // 2, image=self._photo)
        self._shown = (photo.width(), photo.height(), scale, x0, y0)
        self.v_zoomtext.set("fit" if self._zoom <= 0 else f"{scale * 100:.0f}%")
        if self._zoom > 0 and (rgb.shape[1] > w or rgb.shape[0] > h):
            self.canvas.configure(cursor="fleur")        # there is room to drag
        else:
            self.canvas.configure(cursor="")
        if self.v_aim.get() and self.current.kind == "prescan":
            dw = self._shown[0]
            for x in ((w - dw) // 2, (w + dw) // 2):
                self.canvas.create_line(x, 0, x, h, fill="#e8b64c", dash=(4, 4))
            self.canvas.create_line(w // 2, 0, w // 2, h, fill="#555", dash=(2, 6))
            self.canvas.create_text(
                w // 2, 12, fill="#e8b64c",
                text="click an edge of the picture -- it moves to the nearer "
                     "side of the aperture")

    def _to_source(self, event: tk.Event):
        """Canvas coordinates to source-image pixels, or None if off-image."""
        if self._shown is None:
            return None
        dw, dh, scale, x0, y0 = self._shown
        w = max(1, self.canvas.winfo_width())
        h = max(1, self.canvas.winfo_height())
        ix = event.x - (w - dw) / 2
        iy = event.y - (h - dh) / 2
        if not (0 <= ix < dw and 0 <= iy < dh):
            return None
        return x0 + ix / scale, y0 + iy / scale

    def on_press(self, event: tk.Event) -> None:
        if self.v_aim.get() and self.current is not None \
                and self.current.kind == "prescan":
            self._aim(event)
            return
        self._drag = (event.x, event.y, list(self._offset))

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
                self._zoom_by(1.0 / fit if fit else 1.0, anchor=self._pointer)
        else:
            self._set_zoom(0.0)
        return "break"

    def on_drag(self, event: tk.Event) -> None:
        if self._drag is None or self._zoom <= 0:
            return
        sx, sy, start = self._drag
        self._offset = [start[0] - (event.x - sx) / self._zoom,
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
#: As close as the picture can be brought. Past eight times, a scanned pixel is
#: a block the size of a fingernail and there is nothing further to see.
_MAX_ZOOM = 8.0


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
    ScannerGui(root, session, demo=args.demo)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
