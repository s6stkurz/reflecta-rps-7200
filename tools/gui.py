#!/usr/bin/env python3
"""A window for scanning negatives on the Reflecta RPS 7200.

    make run                      # the real scanner
    python3 tools/gui.py --demo   # stored library entries, nothing on the bus

Prescan, scan, walk a roll, and look at what came off -- including the infrared
plane on its own, which is the one channel no ordinary viewer will show you.
Files are written exactly as the command-line tools write them: raw negatives,
filed in the library with their raw bytes, their shading reference and their CCD
mask. Inverting, dust removal and colour are NegPy's job and happen later; the
inversion in this window is for your eyes only and never reaches disk.

Opening the window claims the device and asks it who it is, and does nothing
else. Nothing moves the mechanism until a button is pressed -- calibration, the
lamp, the transport, all of it waits for you.
"""
from __future__ import annotations

import argparse
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rps7200 import library, preview                     # noqa: E402
from rps7200.direct import FILM_TYPES, METER_MODES       # noqa: E402
from rps7200.library import FilmNotes                    # noqa: E402
from rps7200.session import (                            # noqa: E402
    Calibrate,
    Prescan,
    Roll,
    Scan,
    ScanSession,
    estimate_seconds,
)

#: Integer divisors of the 7200 dpi optical resolution -- the only values any
#: USB capture of the vendor software has ever used. The box is editable, and
#: the device refuses anything it dislikes before a single byte is transferred,
#: so a value from outside this list costs a round trip and nothing worse.
DPI_LADDER = (300, 360, 400, 450, 480, 600, 720, 800, 900,
              1200, 1440, 1800, 2400, 3600, 7200)

#: How many results keep a full-size working copy. Older ones are shrunk rather
#: than dropped, so every channel and the invert toggle keep working on them;
#: it just gets soft. A 36-frame roll then costs ~50 MB instead of ~300.
WORKING_COPIES = 12
ARCHIVE_MAX_SIDE = 512

THUMB_H = 76
POLL_MS = 120


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
        self._centre = None                  # 1:1 focus, in full-image pixels
        self._redraw_job = None
        self._loading = None
        self._alive = True
        self._job = ""                       # what is running, for the stop label

        root.title("Reflecta RPS 7200" + ("  --  demo" if demo else ""))
        root.geometry("1180x820")
        root.minsize(940, 640)
        self._build()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.session.start()
        self.root.after(POLL_MS, self._pump)

    # -- layout ------------------------------------------------------------

    def _build(self) -> None:
        head = ttk.Frame(self.root, padding=(10, 8))
        head.pack(fill="x")
        self.v_device = tk.StringVar(value="opening the scanner ...")
        self.v_state = tk.StringVar(value="")
        ttk.Label(head, textvariable=self.v_device,
                  font=("TkDefaultFont", 12, "bold")).pack(side="left")
        ttk.Label(head, textvariable=self.v_state).pack(side="right")
        ttk.Separator(self.root).pack(fill="x")

        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body, padding=8)
        left.pack(side="left", fill="y")
        right = ttk.Frame(body, padding=(0, 8, 8, 8))
        right.pack(side="left", fill="both", expand=True)

        self._build_scan(left)
        self._build_roll(left)
        self._build_film(left)
        self._build_preview(right)

    def _build_scan(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="Scan", padding=8)
        box.pack(fill="x")

        row = ttk.Frame(box)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="dpi", width=9).pack(side="left")
        self.v_dpi = tk.StringVar(value="1800")
        ttk.Combobox(row, textvariable=self.v_dpi, width=8,
                     values=[str(d) for d in DPI_LADDER]).pack(side="left")

        self.v_ir = tk.BooleanVar(value=True)
        ttk.Checkbutton(box, text="infrared (RGBI)", variable=self.v_ir,
                        command=self._show_estimate).pack(anchor="w", pady=2)

        row = ttk.Frame(box)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="film", width=9).pack(side="left")
        self.v_film = tk.StringVar(value="negative")
        ttk.Combobox(row, textvariable=self.v_film, width=12, state="readonly",
                     values=list(FILM_TYPES)).pack(side="left")

        ttk.Label(box, text="exposure").pack(anchor="w", pady=(6, 0))
        self.v_expmode = tk.StringVar(value="auto")
        ttk.Radiobutton(box, text="meter automatically", value="auto",
                        variable=self.v_expmode,
                        command=self._sync_exposure).pack(anchor="w")
        row = ttk.Frame(box)
        row.pack(fill="x")
        ttk.Radiobutton(row, text="scale", value="manual",
                        variable=self.v_expmode,
                        command=self._sync_exposure).pack(side="left")
        self.v_exposure = tk.StringVar(value="1.0")
        self.e_exposure = ttk.Entry(row, textvariable=self.v_exposure, width=12)
        self.e_exposure.pack(side="left", padx=4)
        ttk.Label(box, text="one value, or R,G,B,I",
                  foreground="#777").pack(anchor="w")

        ttk.Label(box, text="shading").pack(anchor="w", pady=(6, 0))
        self.v_shading = tk.StringVar(value="measure")
        for value, text in (("measure", "measure now (3-4 min)"),
                            ("reuse", "reuse the cached reference"),
                            ("off", "none -- raw, striped pixels")):
            ttk.Radiobutton(box, text=text, value=value,
                            variable=self.v_shading).pack(anchor="w")
        self.b_calibrate = ttk.Button(box, text="Calibrate",
                                      command=self.on_calibrate)
        self.b_calibrate.pack(fill="x", pady=(6, 2))

        self.b_prescan = ttk.Button(box, text="Prescan", command=self.on_prescan)
        self.b_prescan.pack(fill="x", pady=2)
        self.b_scan = ttk.Button(box, text="Scan", command=self.on_scan)
        self.b_scan.pack(fill="x", pady=2)
        self.v_estimate = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.v_estimate,
                  foreground="#777").pack(anchor="w")

    def _build_roll(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="Roll", padding=8)
        box.pack(fill="x", pady=(8, 0))

        row = ttk.Frame(box)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="frames", width=9).pack(side="left")
        self.v_frames = tk.StringVar(value="6")
        ttk.Entry(row, textvariable=self.v_frames, width=6).pack(side="left")

        row = ttk.Frame(box)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="start at", width=9).pack(side="left")
        self.v_startat = tk.StringVar(value="1")
        ttk.Entry(row, textvariable=self.v_startat, width=6).pack(side="left")

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
        for key, label in (("stock", "stock"), ("roll", "roll"),
                           ("frame", "frame"), ("process", "process"),
                           ("subject", "subject"), ("notes", "notes"),
                           ("tags", "tags")):
            row = ttk.Frame(box)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=label, width=9).pack(side="left")
            var = tk.StringVar()
            ttk.Entry(row, textvariable=var, width=16).pack(
                side="left", fill="x", expand=True)
            self.fields[key] = var

    def _build_preview(self, parent: ttk.Frame) -> None:
        self.canvas = tk.Canvas(parent, background="#1b1b1b", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self._schedule_redraw())
        self.canvas.bind("<Button-1>", self.on_canvas_click)

        bar = ttk.Frame(parent, padding=(0, 6))
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
                        command=self._schedule_redraw).pack(side="left", padx=(12, 0))
        self.v_zoom = tk.StringVar(value="fit")
        ttk.Radiobutton(bar, text="fit", value="fit", variable=self.v_zoom,
                        command=self._schedule_redraw).pack(side="right")
        ttk.Radiobutton(bar, text="1:1", value="one", variable=self.v_zoom,
                        command=self._schedule_redraw).pack(side="right")
        ttk.Label(bar, text="zoom").pack(side="right", padx=(0, 6))
        self.v_caption = tk.StringVar(value="nothing scanned yet")
        ttk.Label(parent, textvariable=self.v_caption,
                  foreground="#777").pack(anchor="w")

        self.strip = tk.Canvas(parent, height=THUMB_H + 10, background="#111",
                               highlightthickness=0)
        self.strip.pack(fill="x", pady=(6, 0))

        prog = ttk.Frame(parent, padding=(0, 6))
        prog.pack(fill="x")
        self.v_progress = tk.StringVar(value="")
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

        logbox = ttk.Frame(parent)
        logbox.pack(fill="x")
        self.log = tk.Text(logbox, height=7, wrap="none", background="#111",
                           foreground="#bbb", insertbackground="#bbb",
                           highlightthickness=0, borderwidth=0)
        self.log.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(logbox, command=self.log.yview)
        scroll.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scroll.set, state="disabled")

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

    def _dpi(self) -> int | None:
        try:
            dpi = int(self.v_dpi.get().strip())
        except ValueError:
            messagebox.showerror("Resolution", "dpi has to be a whole number.")
            return None
        if not 25 <= dpi <= 7200:
            messagebox.showerror(
                "Resolution",
                f"{dpi} dpi is outside 25-7200. The scanner's optical "
                "resolution is 7200 dpi.")
            return None
        return dpi

    def _exposure(self):
        if self.v_expmode.get() == "auto":
            return 1.0
        text = self.v_exposure.get().replace(",", " ").split()
        try:
            values = [float(v) for v in text]
        except ValueError:
            messagebox.showerror("Exposure", "Exposure must be numbers.")
            return None
        if not values:
            return 1.0
        return values[0] if len(values) == 1 else values

    def _sync_exposure(self) -> None:
        manual = self.v_expmode.get() == "manual"
        self.e_exposure.configure(state="normal" if manual else "disabled")

    def _show_estimate(self) -> None:
        try:
            dpi = int(self.v_dpi.get().strip())
        except ValueError:
            self.v_estimate.set("")
            return
        seconds = estimate_seconds(dpi, self.v_ir.get())
        self.v_estimate.set(f"about {_duration(seconds)} a pass")

    # -- actions -----------------------------------------------------------

    def on_calibrate(self) -> None:
        mode = self.v_shading.get()
        if mode == "measure" and not messagebox.askokcancel(
            "Calibrate",
            "This runs the scanner's calibration pass, about 3-4 minutes.\n\n"
            "The film should be loaded: the calibration frame is the lower part "
            "of the transport, which the film does not cover, so the sensor is "
            "measured either way -- and an empty transport is a state the "
            "vendor software never creates.\n\nIs the film in?",
        ):
            return
        self.session.submit(Calibrate(mode=mode, reference=self.session.reference))

    def on_prescan(self) -> None:
        self.session.submit(Prescan(notes=self._notes(), tags=self._tags()))

    def on_scan(self) -> None:
        dpi = self._dpi()
        exposure = self._exposure()
        if dpi is None or exposure is None:
            return
        self.session.submit(Scan(
            resolution=dpi,
            infrared=self.v_ir.get(),
            film=self.v_film.get(),
            auto_exposure=self.v_expmode.get() == "auto",
            exposure_scale=exposure,
            shading=self.v_shading.get() != "off",
            notes=self._notes(),
            tags=self._tags(),
        ))

    def on_roll(self) -> None:
        dpi = self._dpi()
        if dpi is None:
            return
        try:
            frames = int(self.v_frames.get().strip() or 0) or None
            start_at = max(1, int(self.v_startat.get().strip() or 1))
        except ValueError:
            messagebox.showerror("Roll", "Frames and start-at must be whole numbers.")
            return
        dry = self.v_dryrun.get()
        per = 23.0 if dry else estimate_seconds(dpi, self.v_ir.get()) + 70
        total = per * (frames or 6)
        if not messagebox.askokcancel(
            "Scan roll",
            f"{'Walk' if dry else 'Scan'} {frames or 'as many frames as there are'} "
            f"frames at {dpi} dpi"
            f"{' with infrared' if self.v_ir.get() and not dry else ''}.\n\n"
            f"Roughly {_duration(total)}. The film should already be at the "
            f"first picture -- it is scanned before anything moves.\n\n"
            "Start?",
        ):
            return
        self.session.submit(Roll(
            frames=frames,
            start_at=start_at,
            resolution=dpi,
            infrared=self.v_ir.get(),
            film=self.v_film.get(),
            meter=self.v_meter.get(),
            dry_run=dry,
            correct=self.v_correct.get(),
            name=self.fields["roll"].get().strip(),
            notes=self._notes(),
            tags=self._tags(),
        ))

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
            "Type ABORT to do it anyway:",
            parent=self.root,
        )
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
            self.v_state.set(event.text)
            if self.session.inquiry_text:
                self.v_device.set(self.session.inquiry_text)
            if event.busy:
                # Kept apart from v_progress, which the line counter overwrites
                # a second into the pass.
                self._job = event.text
                self.v_progress.set(event.text)
                self.progress.configure(value=0)
            self._set_busy(event.busy)
        elif event.kind == "progress":
            self._progress(event.done, event.total)
        elif event.kind == "result":
            self._add_result(event.result)
        elif event.kind == "filed":
            for r in self.results:
                if r.seq == event.done:
                    r.entry = Path(event.text)
        elif event.kind == "finished":
            self.v_progress.set(f"{event.text} -- done")
            self.progress.configure(value=1000)
        elif event.kind == "failed":
            # Reported in place, not in a modal. A modal here sits inside the
            # event pump and stops it: during a roll, one failed frame would
            # freeze the window on that dialog and the thirty frames after it
            # would arrive unseen. A roll is meant to survive a bad frame.
            self._say(event.text)
            self.v_progress.set(event.text)
            self.v_caption.set(event.text)
            self.progress.configure(value=0)
            self._set_busy(False)
            if not self.session.inquiry_text:
                # Except at startup: if the scanner never opened there is
                # nothing else the window can do, and saying so once is right.
                messagebox.showerror("No scanner", event.text)
        elif event.kind == "closed":
            self._set_busy(False)
            self.v_state.set("scanner closed")
            if self.closing:
                self._alive = False
                self.root.destroy()

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        if self.session.dead:
            # Nothing may be started on a device whose read was abandoned; it
            # needs a power cycle at its own switch before it will talk again.
            for b in (self.b_scan, self.b_prescan, self.b_roll, self.b_calibrate,
                      self.b_stop, self.b_abort):
                b.configure(state="disabled")
            self.v_caption.set(
                "aborted -- power-cycle the scanner at its own switch, "
                "then start this window again")
            return
        run = "disabled" if busy else "normal"
        for b in (self.b_scan, self.b_prescan, self.b_roll, self.b_calibrate):
            b.configure(state=run)
        self.b_stop.configure(state="normal" if busy else "disabled")
        self.b_abort.configure(state="normal" if busy else "disabled")
        # Say what stopping will actually do, since it cannot mean "now".
        self.b_stop.configure(text=stop_label(self._job))

    def _progress(self, done: int, total: int) -> None:
        if total <= 0:
            return
        self.progress.configure(value=int(1000 * done / total))
        self.v_progress.set(f"{done}/{total} lines")

    def _say(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # -- results and the filmstrip ----------------------------------------

    def _add_result(self, result) -> None:
        self.results.append(result)
        # Older working copies shrink rather than vanish, so every channel and
        # the invert toggle keep working on them; they just get soft.
        for old in self.results[:-WORKING_COPIES]:
            if old.image is not None and max(old.image.shape[:2]) > ARCHIVE_MAX_SIDE:
                old.image = preview.downscale(old.image, ARCHIVE_MAX_SIDE)
        self._show(result)
        self._redraw_strip()

    def _show(self, result) -> None:
        self.current = result
        self._full = None
        self._full_seq = None
        self._centre = None
        available = (preview.channels_available(result.image)
                     if result.image is not None else ("RGB",))
        for name, button in self.channel_buttons.items():
            button.configure(state="normal" if name in available else "disabled")
        if self.v_channel.get() not in available:
            self.v_channel.set("RGB")
        marks = result.registration
        extra = ""
        if marks.get("offset_mm") is not None:
            extra = (f"   ·   offset {marks['offset_mm']:+.2f} mm, "
                     f"short by {marks.get('shortfall_mm', 0):.2f} mm")
        shading = (result.meta or {}).get("shading")
        if shading and shading.get("clipped"):
            extra += f"   ·   {shading['clipped']} clipped columns -- lower the exposure"
        self.v_caption.set(result.label + extra)
        self._schedule_redraw()

    def _redraw_strip(self) -> None:
        self.strip.delete("all")
        self._thumbs = []
        x = 6
        for i, r in enumerate(self.results):
            if r.image is None:
                continue
            arr = preview.render(
                preview.fit(r.image, THUMB_H * 2, THUMB_H),
                "RGB", self.v_invert.get(),
            )
            photo = tk.PhotoImage(data=preview.to_ppm(arr))
            self._thumbs.append(photo)
            tag = f"r{i}"
            self.strip.create_image(x, 5, image=photo, anchor="nw", tags=tag)
            if r is self.current:
                self.strip.create_rectangle(
                    x - 2, 3, x + photo.width() + 1, 7 + photo.height(),
                    outline="#e8b64c", width=2)
            self.strip.tag_bind(tag, "<Button-1>",
                                lambda _e, k=i: self._show(self.results[k]))
            x += photo.width() + 8
        self.strip.configure(scrollregion=(0, 0, x, THUMB_H + 10))
        self.strip.xview_moveto(1.0)

    # -- drawing -----------------------------------------------------------

    def _schedule_redraw(self) -> None:
        # Coalesced: a window drag fires <Configure> dozens of times, and each
        # redraw is a percentile over the whole working copy.
        if self._redraw_job is not None:
            self.root.after_cancel(self._redraw_job)
        self._redraw_job = self.root.after(60, self._redraw)

    def _redraw(self) -> None:
        self._redraw_job = None
        self.canvas.delete("all")
        r = self.current
        if r is None or r.image is None:
            return
        w = max(1, self.canvas.winfo_width())
        h = max(1, self.canvas.winfo_height())
        if self.v_zoom.get() == "one":
            source = self._one_to_one_source(r)
            if source is None:
                self.canvas.create_text(
                    w // 2, h // 2, fill="#888",
                    text="reading full-resolution pixels ...")
                return
            cx, cy = self._centre or (source.shape[1] / 2, source.shape[0] / 2)
            arr = preview.crop(source, cx, cy, w, h)
        else:
            arr = preview.fit(r.image, w, h)
        try:
            rgb = preview.render(arr, self.v_channel.get(), self.v_invert.get())
        except ValueError as exc:
            self.canvas.create_text(w // 2, h // 2, fill="#888", text=str(exc))
            return
        self._photo = tk.PhotoImage(data=preview.to_ppm(rgb))
        self.canvas.create_image(w // 2, h // 2, image=self._photo)

    def _one_to_one_source(self, r):
        """Full-resolution pixels for the 1:1 view, read from the entry on disk.

        A downscaled preview cannot show grain or shadow noise, so the 1:1 view
        has to come from the file rather than from the working copy. The read is
        on its own thread: at 3600 dpi it is 142 MB, and doing it on the UI
        thread would freeze the window for seconds.
        """
        if self._full_seq == r.seq:
            return self._full
        if r.entry is None:
            return r.image                   # not filed yet; the copy is all there is
        if getattr(self, "_loading", None) == r.seq:
            return None
        self._loading = r.seq

        def work(entry: Path, seq: int) -> None:
            image = None
            try:
                image, _ = library.load(entry)
            except Exception as exc:                     # noqa: BLE001
                # Bound as a default argument: `exc` is gone by the time the
                # main loop runs this, and the name would not resolve.
                self.root.after(
                    0, lambda name=entry.name, why=str(exc):
                    self._say(f"could not read {name}: {why}")
                )
            self.root.after(0, lambda got=image: self._loaded(seq, got))

        threading.Thread(target=work, args=(r.entry, r.seq), daemon=True).start()
        return None

    def _loaded(self, seq: int, image) -> None:
        self._loading = None
        if self.current is None or self.current.seq != seq:
            return
        self._full = image if image is not None else self.current.image
        self._full_seq = seq
        self._redraw()

    def on_canvas_click(self, event: tk.Event) -> None:
        """Click to recentre the 1:1 view, or to enter it from the fit view."""
        r = self.current
        if r is None or r.image is None:
            return
        w = max(1, self.canvas.winfo_width())
        h = max(1, self.canvas.winfo_height())
        if self.v_zoom.get() == "fit":
            shown = preview.fit(r.image, w, h)
            sh, sw = shown.shape[:2]
            fx = (event.x - (w - sw) / 2) / max(1, sw)
            fy = (event.y - (h - sh) / 2) / max(1, sh)
            if not (0 <= fx <= 1 and 0 <= fy <= 1):
                return
            source = self._full if self._full_seq == r.seq else r.image
            self._centre = (fx * source.shape[1], fy * source.shape[0])
            self.v_zoom.set("one")
        else:
            source = self._full if self._full_seq == r.seq else r.image
            cx, cy = self._centre or (source.shape[1] / 2, source.shape[0] / 2)
            self._centre = (cx + event.x - w / 2, cy + event.y - h / 2)
        self._schedule_redraw()


def stop_label(job: str) -> str:
    """What the stop button should say while `job` is running.

    "Stop" cannot mean "now" on this device: a pass in flight always finishes,
    because an abandoned read is what costs a power cycle. So the button has to
    say which of the two things it will actually do, and be right about it for
    the whole run -- it once read the progress label, which the line counter
    overwrites a second in, and so relabelled itself mid-roll.
    """
    return "Stop after this frame" if "roll" in job else "Stop (finishes this pass)"


def _duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 90:
        return f"{seconds} s"
    minutes, rest = divmod(seconds, 60)
    if minutes < 90:
        return f"{minutes}m {rest:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--demo", action="store_true",
                    help="drive the window from stored library entries, with "
                         "no scanner on the bus")
    ap.add_argument("--library", default="library",
                    help="where scans are filed (default: library)")
    ap.add_argument("--reference", default="calibration/shading.npz")
    ap.add_argument("--rolls", default="rolls")
    args = ap.parse_args()

    session = ScanSession(
        root=args.library,
        reference=args.reference,
        rolls=args.rolls,
    )
    if args.demo:
        from rps7200.demo import DemoScanner

        session._open_scanner = lambda: DemoScanner(args.library)
    root = tk.Tk()
    ScannerGui(root, session, demo=args.demo)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
