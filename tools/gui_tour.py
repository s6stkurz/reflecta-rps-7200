#!/usr/bin/env python3
"""A scripted tour of the demo window: every control pressed, every frame judged.

    uv run python tools/gui_tour.py                   # the tour, then report.md
    uv run python tools/gui_tour.py --serve           # ... the window stays up
    uv run python tools/gui_tour.py --serve --no-tour # just the window, served
    uv run python tools/gui_tour.py --demo-source ../reflecta-rps-7200/library
                                                      # pictures from elsewhere
    uv run python tools/gui_tour.py send demo/tour/<stamp> <<'EOF'
    print(app.v_caption.get())
    EOF

For checking a change end to end with no scanner on the bus. The window is
`gui.main()` run with `--demo`, exactly as `make run-demo` runs it -- the same
session, the same stand-in, the same controls -- and nothing here builds a
second copy of that setup to drift from it. The tour then presses what an
operator presses, in order: calibration, prescan, scan, the views and the zoom,
turn and mirror, Save As and Save all, a JPEG delivery, delete, the transport,
presets and settings, a walk that aims each frame, the contact sheet and its
frame-position window, a commission from the sheet, a roll stopped part way,
the rolls browser, and a force abort. A second window then opens the walk it
made in `--look-only`, where the stand-in has no film to give.

Every frame the window shows is judged, not only kept:

- **bit-true**: a demo pass's filed raw pixels are its stored source's raw
  pixels, sampled at the rows and columns the fit names, and nothing else.
  Nothing invented, nothing moved.
- **bands**: no run of identical columns and no flat bar at an edge -- what an
  invented fill looks like when it slips in. A flat bar the stored source has
  at the same columns is the source's, and is a warning, as clipping is.
- **fit against full resolution**: what the window shows at fit and what it
  loads when zoomed in are the same pass.
- **the rail**: how much sits at 0 or full scale, beside how much sat there in
  the stored source, so a clipped source is told from a clipping change.

It writes only under ``demo/tour/<UTC time>/``, which is ignored: its library,
rolls, calibration, settings and deliveries, the pictures it captured from the
window (``shots/``, and ``contact-*.png`` sheets of them) and ``report.md``.
It checks afterwards that the library the pictures came from -- `library/`
unless ``--demo-source`` names another, and any `library 2/` beside it -- and
`demo/rolls/` were not touched. A worktree has no `library/` of its own, so
it borrows the main checkout's that way, and reads it only. Every dialog is
answered by script and recorded with its words.

It never drives a scanner. There is no path here without `--demo`.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

#: A run of identical neighbouring columns this long is a band, not a picture.
#: Measured on stored prescans: film grain leaves no two neighbours equal, and
#: the edge-repeat fill the demo once drew left sixteen in a row.
BAND_COLUMNS = 3
#: The same on screen, where a picture drawn larger than its pixels repeats a
#: column now and then by nearest-neighbour; the band it is looking for was
#: twenty columns wide there.
SHOT_BAND_COLUMNS = 6
#: Of full scale, below which a picture is taken as blank.
BLANK_SPREAD = 0.01
#: A share of pixels at the rail worth saying, per channel.
RAIL_SHARE = 0.005
#: How long a window the tour opened may take to draw its buttons. Tk makes
#: a window's widgets at once and draws them only once the event loop has
#: run, so a press straight after the window appears finds nothing mapped:
#: unattended, the tour stopped at its first prompt that way.
DRAWN_S = 10.0
#: Where the stored pictures the demo shows come from: the window's own
#: ``--demo-source``, passed through, so that the tour judges each pass
#: against the library the window drew it from. `library` beside the
#: checkout unless told otherwise.
SOURCE = Path("library")


# --- what a frame is judged by ---------------------------------------------


def _full_scale(a: np.ndarray) -> float:
    return float(np.iinfo(a.dtype).max) if a.dtype.kind in "ui" else 1.0


def _runs(flags: np.ndarray) -> tuple[int, int, int]:
    """Longest run of True anywhere, and the runs touching each end."""
    longest = run = 0
    for f in flags:
        run = run + 1 if f else 0
        longest = max(longest, run)
    left = int(np.argmin(flags)) if not flags.all() else len(flags)
    right = int(np.argmin(flags[::-1])) if not flags.all() else len(flags)
    return longest, left, right


def frame_problems(image: Any, rail: bool = True, band: int = BAND_COLUMNS,
                   inherited: np.ndarray | None = None) -> tuple[list[str], dict]:
    """What is wrong with a picture as a picture, and the numbers behind it.

    ``inherited``, one flag per column, marks the columns where the stored
    source is itself flat (`source_flat`). A flat bar at an edge made only
    of those is the source's -- a scan clipped to the rail in every row
    stays flat through any per-column correction -- and is no problem here;
    ``facts["flat_from_source"]`` says it was there. One column past them
    and the bar is the window's again.
    """
    a = np.asarray(image)
    if a.ndim == 2:
        a = a[..., None]
    top = _full_scale(a)
    rgb = a[..., : min(3, a.shape[2])].astype(np.float64)
    problems: list[str] = []
    facts: dict[str, Any] = {"shape": list(a.shape), "dtype": str(a.dtype)}
    if not np.isfinite(rgb).all():
        problems.append("non-finite pixels")
        return problems, facts
    spread = float(rgb.std() / top)
    facts["spread"] = round(spread, 4)
    if spread < BLANK_SPREAD:
        problems.append(f"blank: spread {spread:.4f} of full scale")
    if rgb.shape[1] > 1:
        step = np.abs(np.diff(rgb, axis=1)).mean(axis=(0, 2)) / top
        longest, left, right = _runs(step < 1e-6)
        facts["repeated_columns"] = {"longest": longest, "left": left,
                                     "right": right}
        if longest + 1 >= band:
            problems.append(f"band of {longest + 1} identical columns "
                            f"(at the left edge {left}, the right {right})")
        flat = rgb.std(axis=0).mean(axis=1) / top < 1e-6
        _, left, right = _runs(flat)
        facts["flat_edges"] = {"left": left, "right": right}
        if max(left, right) >= band:
            edges = np.zeros(len(flat), bool)
            edges[:left] = True
            edges[len(flat) - right:] = True
            if (inherited is not None and len(inherited) == len(flat)
                    and bool(np.asarray(inherited, bool)[edges].all())):
                facts["flat_from_source"] = {"left": left, "right": right}
            else:
                problems.append(f"flat bar at the edge: {left} columns left, "
                                f"{right} right")
    if rail and a.dtype.kind in "ui":
        at_top = (rgb >= top).mean(axis=(0, 1))
        at_zero = (rgb <= 0).mean(axis=(0, 1))
        facts["rail_top"] = [round(float(v), 4) for v in at_top]
        facts["rail_zero"] = [round(float(v), 4) for v in at_zero]
    return problems, facts


def _chroma(a: np.ndarray) -> float:
    a = np.asarray(a, np.float64)[..., :3]
    return float(np.abs(a - a.mean(axis=2, keepdims=True)).mean())


def _column_shift(a: np.ndarray, b: np.ndarray, reach: int = 60) -> tuple[int, float]:
    """How far `b`'s picture sits right of `a`'s, in columns, by their
    column profiles -- and how well the two profiles match there."""
    def profile(x):
        x = np.asarray(x, np.float64)
        x = x[..., :3].mean(axis=2) if x.ndim == 3 else x
        p = x.mean(axis=0)
        return (p - p.mean()) / (p.std() + 1e-12)
    pa, pb = profile(a), profile(b)
    n = min(len(pa), len(pb))
    pa, pb = pa[:n], pb[:n]
    best = (-2.0, 0)
    for s in range(-min(reach, n - 1), min(reach, n - 1) + 1):
        x = pa[max(0, s):n + min(0, s)]
        y = pb[max(0, -s):n - max(0, s)]
        best = max(best, (float(np.mean(x * y)), s))
    return -best[1], round(best[0], 4)


def _find_key(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = _find_key(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_key(v, key)
            if found is not None:
                return found
    return None


_SOURCES: dict[str, np.ndarray] = {}
_CORRECTED: dict[str, np.ndarray] = {}


def _source_entry(name: str) -> Path | None:
    """The stored entry a demo pass names, in the libraries the demo read."""
    from rps7200.demo import libraries_beside

    if Path(name).is_dir():
        return Path(name)
    for lib in libraries_beside(SOURCE):
        if (Path(lib) / Path(name).name).is_dir():
            return Path(lib) / Path(name).name
    return None


def _drawn_at(h: int, w: int, shown: tuple[int, ...]) -> int | None:
    """The stride that takes an `h` by `w` pass down to `shown`.

    The window's working copy is its pass decimated by one stride on both
    axes (`preview.downscale`), and zoomed in it holds the pass itself:
    stride 1.
    """
    for step in range(1, max(1, h, w) + 1):
        if (-(-h // step), -(-w // step)) == tuple(shown[:2]):
            return step
    return None


def _source_of(entry: Path) -> tuple[np.ndarray, tuple[int, int],
                                     tuple[int, int]] | None:
    """A demo pass's stored source as the tour's other checks read it --
    `library.corrected` of the stored entry -- with the source's shape and
    the pass's, as its fit names them.

    None for a pass drawn from no stored entry, a source not beside
    `SOURCE`, or a fit that does not describe the source found.
    """
    from rps7200 import library

    record = json.loads((entry / "scan.json").read_text(encoding="utf-8"))
    source = _find_key(record, "demo_source") or {}
    fit = _find_key(record, "demo_fit") or {}
    if not source.get("entry"):
        return None
    where = _source_entry(str(source["entry"]))
    if where is None:
        return None
    if where.name not in _CORRECTED:
        _CORRECTED[where.name] = np.asarray(library.corrected(where)[0])
    stored = _CORRECTED[where.name]
    if stored.ndim == 2:
        stored = stored[..., None]
    height, width = fit.get("source_shape") or stored.shape[:2]
    if [height, width] != list(stored.shape[:2]):
        return None
    h, w = fit.get("shape") or (height, width)
    return stored, (int(height), int(width)), (int(h), int(w))


def _flat(a: np.ndarray, top: float) -> np.ndarray:
    """Per column: the same value all the way down, as `frame_problems` has it."""
    rgb = a[..., : min(3, a.shape[2])].astype(np.float64)
    return rgb.std(axis=0).mean(axis=1) / top < 1e-6


def source_flat(entry: Path, shown: tuple[int, ...]) -> np.ndarray | None:
    """Per column of a picture of `shown` rows and columns drawn from this
    demo entry's pass: whether its stored source is flat there.

    The source (`_source_of`) at the rows and columns the fit names, at the
    stride the window shows the pass at. None where the pass names no stored
    entry or the picture cannot be traced to its columns -- turned, say --
    and then nothing is excused.
    """
    found = _source_of(entry)
    if found is None:
        return None
    stored, (height, width), (h, w) = found
    step = _drawn_at(h, w, shown)
    if step is None:
        return None
    rows = (np.arange(0, h, step) * height) // h
    columns = (np.arange(0, w, step) * width) // w
    return _flat(stored[rows[:, None], columns[None, :]], _full_scale(stored))


def screen_flat(entry: Path, working: tuple[int, ...], view: tuple,
                channel: str, invert: bool, cuts: Any,
                depth: int) -> np.ndarray | None:
    """Per column of the main view on screen: whether the stored source,
    drawn there as the window draws it, is flat too.

    `view` is the window's own record of what it drew (`_shown`: width,
    height, scale and origin, in the working copy's coordinates, which is
    what every position in the view is measured against), `working` the
    working copy's shape, and the channel, inversion and levels those of
    the view. Rendered with them, so a column on the rail of the levels
    counts as flat as it does on screen -- and only the channel shown is
    asked: the source's green and blue are clipped across far more of its
    edge than all three together. One screen column either side of each
    flat run is allowed for, since the window may draw from a finer level
    of the scan than the working copy, which can put the column at a flat
    run's boundary one source column over. None where the picture cannot
    be traced to its source.
    """
    from rps7200 import preview
    from rps7200.demo import _at_depth

    found = _source_of(entry)
    if found is None:
        return None
    stored, (height, width), (h, w) = found
    step = _drawn_at(h, w, working)
    if step is None:
        return None
    drawn_w, drawn_h, scale, x0, y0 = (float(v) for v in view[:5])
    drawn_w, drawn_h = int(drawn_w), int(drawn_h)

    def to_source(at: np.ndarray, size: int, passed: int) -> np.ndarray:
        """Working-copy coordinates to the stored indices the pass drew."""
        index = np.clip((at * step).astype(np.intp), 0, passed - 1)
        return (index * size) // passed
    columns = to_source(x0 + np.arange(drawn_w) / scale, width, w)
    rows = to_source(y0 + np.array([0, drawn_h - 1]) / scale, height, h)
    block = stored[int(rows.min()): int(rows.max()) + 1][:, columns]
    if depth == 8:
        block = _at_depth(block, 8)
    flat = _flat(preview.render(block, channel, invert, cuts=cuts), 255.0)
    near = flat.copy()
    near[1:] |= flat[:-1]
    near[:-1] |= flat[1:]
    return near


def _verdict(problems: list[str], facts: dict) -> tuple[str, str]:
    """A check's status and words from `frame_problems`: a flat bar the
    stored source has too is a warning, as clipping the source has is."""
    if problems:
        return "fail", "; ".join(problems)
    bar = facts.get("flat_from_source")
    if bar:
        return "warn", (f"flat bar at the edge: {bar['left']} columns left, "
                        f"{bar['right']} right -- the stored source is flat "
                        "at those columns too")
    return "pass", f"spread {facts.get('spread')}"


def bit_true(entry: Path) -> tuple[str, str, dict]:
    """Whether a demo entry's raw pixels are its stored source's, and nothing else.

    ``(status, detail, facts)``; status is pass, fail, warn or skip.
    """
    from rps7200 import library
    from rps7200.demo import CORRECTED_WHEN_STORED, _at_depth

    record = json.loads((entry / "scan.json").read_text(encoding="utf-8"))
    source = _find_key(record, "demo_source") or {}
    fit = _find_key(record, "demo_fit") or {}
    if not source.get("entry"):
        return "skip", "drawn from no stored entry (a test card)", {}
    if CORRECTED_WHEN_STORED in json.dumps(record):
        return "skip", "its source was stored corrected: no raw pixels", {}
    name = str(source["entry"])
    where = _source_entry(name)
    if where is None:
        return "warn", f"source {name} not found beside {SOURCE}", {}
    if name not in _SOURCES:
        _SOURCES[name] = np.asarray(library.load(where)[0])
    stored = _SOURCES[name]
    if stored.ndim == 2:
        stored = stored[..., None]
    passed = np.asarray(library.load(entry)[0])
    if passed.ndim == 2:
        passed = passed[..., None]
    height, width = fit.get("source_shape") or stored.shape[:2]
    h, w = fit.get("shape") or passed.shape[:2]
    if [height, width] != list(stored.shape[:2]):
        return "fail", (f"the fit names a source of {height}x{width}, the "
                        f"source is {stored.shape[0]}x{stored.shape[1]}"), {}
    rows = (np.arange(h) * height) // h
    columns = (np.arange(w) * width) // w
    expected = stored[rows[:, None], columns[None, :], :]
    depth = 8 if passed.dtype == np.uint8 else 16
    expected = _at_depth(expected, depth)
    planes = min(3, expected.shape[2], passed.shape[2])
    if expected.shape[:2] != passed.shape[:2]:
        return "fail", (f"shape {passed.shape[:2]} against the fitted "
                        f"source's {expected.shape[:2]}"), {}
    got, want = passed[..., :planes], expected[..., :planes]
    top = _full_scale(passed)
    facts = {
        "source": where.name,
        "rail_top_pass": [round(float(v), 4) for v in
                          (got >= top).mean(axis=(0, 1))],
        "rail_top_source": [round(float(v), 4) for v in
                            (want >= top).mean(axis=(0, 1))],
    }
    if np.array_equal(got, want):
        return "pass", f"identical to {where.name}, sampled as fitted", facts
    differ = np.argwhere(np.any(got != want, axis=2))
    columns_off = sorted(set(int(c) for c in differ[:, 1]))
    return "fail", (f"{len(differ)} pixels differ from {where.name} in "
                    f"{len(columns_off)} columns (first {columns_off[:6]}, "
                    f"last {columns_off[-3:]})"), facts


def _has_raw(entry: Path) -> bool:
    return any(p.name.startswith("raw.bin") for p in entry.iterdir())


# --- the window, driven ----------------------------------------------------


DIALOGS: list[dict] = []
ERRORS: list[dict] = []
ANSWERS: dict[str, Any] = {}
T0 = time.monotonic()


def _answer_dialogs(tour: Path) -> None:
    """Every message box and file dialog, answered and recorded."""
    from tkinter import filedialog, messagebox, simpledialog

    ANSWERS.update({
        "askokcancel": True, "askyesno": True, "askyesnocancel": True,
        "askstring": "tour", "askdirectory": str(tour / "delivered"),
        "asksaveasfilename": str(tour / "delivered" / "saved-as.tif"),
    })

    def recorder(kind):
        def answer(*a, **k):
            title = a[0] if a else k.get("title", "")
            words = a[1] if len(a) > 1 else k.get("message", k.get("prompt", ""))
            value = ANSWERS.get(kind)
            DIALOGS.append({"t": round(time.monotonic() - T0, 2), "kind": kind,
                            "title": str(title), "message": str(words),
                            "answer": value})
            return value
        return answer

    for name in ("showinfo", "showerror", "showwarning", "askokcancel",
                 "askyesno", "askyesnocancel"):
        setattr(messagebox, name, recorder(name))
    filedialog.askdirectory = recorder("askdirectory")
    filedialog.asksaveasfilename = recorder("asksaveasfilename")
    simpledialog.askstring = recorder("askstring")


class Tour:
    """Presses what an operator presses, and writes down what happened."""

    def __init__(self, app, root, tour: Path, phase: str):
        self.app, self.root, self.tour, self.phase = app, root, tour, phase
        self.checks: list[dict] = []
        self.shots = tour / "shots"
        self.shots.mkdir(parents=True, exist_ok=True)
        self.facts: dict[str, Any] = {}
        #: Per shot of the main view, what it was drawing (`_note_view`).
        self._view_drawn: dict[str, dict] = {}
        #: The step running, for a check a helper makes on its behalf.
        self.current = "between steps"

    # -- recording ----------------------------------------------------------

    def check(self, step: str, what: str, ok: bool | str, detail: str = "",
              shots: list[str] | None = None) -> bool:
        status = ok if isinstance(ok, str) else ("pass" if ok else "fail")
        self.checks.append({"phase": self.phase, "step": step, "what": what,
                            "status": status, "detail": detail,
                            "shots": shots or []})
        return status != "fail"

    def dialogs_since(self, n: int) -> list[dict]:
        return DIALOGS[n:]

    # -- the event loop -----------------------------------------------------

    def settle(self, rounds: int = 10) -> None:
        for _ in range(rounds):
            self.root.update()
            time.sleep(0.03)

    def still_going(self) -> list[str]:
        """What the window is still doing that a check would read the result of.

        A job ends -- `busy` drops -- before its passes are on disk: the
        session's writer files them on its own thread (`FrameWriter`), and
        only the "filed" event it sends after each tells the window, on its
        next poll, which entry a pass went to. Save all and Export write on
        threads of the window's own (`_writing`). A check that looked when the
        job ended found no entry, no frame to export, no file.
        """
        app = self.app
        going = []
        if app.busy:
            going.append("the job running")
        # The session's own count, from the hand-over to the job's end: the
        # window kept a copy of it once, a tick behind.
        outstanding = getattr(app.session, "outstanding", None)
        held = len(outstanding()) if callable(outstanding) else 0
        if held:
            going.append(f"{held} job(s) queued or running")
        writer = getattr(app.session, "_writer", None)
        owed = writer.queue.unfinished_tasks if writer is not None else 0
        if owed:
            going.append(f"{owed} pass(es) the session's writer has not filed")
        events = getattr(app.session, "_events", None)
        if events is not None and not events.empty():
            going.append("events the window has not read")
        writing = [t.name for t in getattr(app, "_writing", ()) if t.is_alive()]
        if writing:
            going.append("the window writing (" + ", ".join(writing) + ")")
        return going

    def idle(self, timeout: float = 180.0) -> bool:
        """Until the scanner has nothing running or queued, and everything
        it and the window took on is written and known (`still_going`).

        A wait that runs out says what it was still waiting for, as a failed
        check of the step it was in, and returns False.
        """
        self.settle(5)
        end = time.monotonic() + timeout
        while self.still_going() and time.monotonic() < end:
            self.root.update()
            time.sleep(0.03)
        self.settle(5)
        going = self.still_going()
        if going:
            self.check(self.current, f"the window is idle within {timeout:.0f} s",
                       False, "still: " + "; ".join(going))
        return not going

    def until(self, predicate, timeout: float = 30.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return True
            self.root.update()
            time.sleep(0.03)
        return bool(predicate())

    # -- widgets ------------------------------------------------------------

    def walk(self, w=None, into_windows: bool = True):
        """Every widget under `w`. The main window's own, without the windows
        it has opened, when `into_windows` is off: the sheet has its own
        "infrared (RGBI)" box, and pressing the wrong one ticks the wrong
        thing."""
        import tkinter as tk

        w = w or self.root
        yield w
        for c in w.winfo_children():
            if into_windows or not isinstance(c, tk.Toplevel):
                yield from self.walk(c, into_windows)

    @staticmethod
    def text(w) -> str:
        try:
            return str(w.cget("text"))
        except Exception:                                # noqa: BLE001
            return ""

    def buttons(self, top=None) -> list:
        """The buttons drawn under `top`, or in the main window.

        Nothing from a window that has gone: its widgets cannot be asked.
        """
        import tkinter as tk

        out = []
        try:
            for w in self.walk(top, into_windows=top is not None):
                if w.winfo_class() in ("TButton", "Button", "TCheckbutton",
                                       "Checkbutton", "TRadiobutton",
                                       "Radiobutton") and w.winfo_ismapped():
                    out.append(w)
        except tk.TclError:
            return []
        return out

    def press(self, label: str, top=None, timeout: float = DRAWN_S):
        """Press the button called `label`, once it is drawn.

        At once when it already is. Otherwise the event loop is run until it
        is, for up to `timeout` -- a window just opened, or one rebuilding
        its controls -- and a button that never comes is named, with the ones
        that did.
        """
        def find():
            return next((w for w in self.buttons(top)
                         if self.text(w) == label), None)
        button = find()
        if button is None:
            self.until(lambda: find() is not None, timeout)
            button = find()
        if button is None:
            where = "the main window"
            with contextlib.suppress(Exception):
                where = repr(top.title()) if top is not None else where
            raise LookupError(
                f"no button {label!r} in {where} after waiting {timeout:.0f} s "
                f"for it to be drawn; there are "
                f"{sorted({self.text(b) for b in self.buttons(top)})}")
        button.invoke()
        return button

    def state_of(self, label: str, top=None) -> str | None:
        for w in self.buttons(top):
            if self.text(w) == label:
                return str(w.cget("state"))
        return None

    def select_roll(self, browser_window, folder: str,
                    strip: int | None = None) -> None:
        """Select a roll's row in the Rolls window, by its folder's name --
        a roll of strips reads ``tour-strips · 2 strips`` -- or, given
        ``strip``, that strip's row under it."""
        item = self.roll_row(browser_window, folder)
        if strip is not None:
            tree = self.tree(browser_window)
            item = next((child for child in tree.get_children(item)
                         if str(tree.item(child)["values"][0]).startswith(
                             f"strip {strip} ")), None)
            if item is None:
                raise LookupError(f"no row for strip {strip} of {folder}")
        self.tree(browser_window).selection_set(item)
        self.settle(2)

    def tree(self, browser_window):
        """The Rolls window's tree."""
        return next(w for w in self.walk(browser_window)
                    if w.winfo_class() == "Treeview")

    def roll_row(self, browser_window, folder: str) -> str:
        """The item of a roll's row in the Rolls window, by its folder."""
        tree = self.tree(browser_window)
        for item in tree.get_children():
            shown = str(tree.item(item)["values"][0])
            if (shown == folder or shown.startswith(folder + "  (")
                    or shown.startswith(folder + " · ")):
                return item
        raise LookupError(f"no row for {folder}")

    def toplevels(self) -> list:
        import tkinter as tk
        return [w for w in self.walk() if isinstance(w, tk.Toplevel)
                and w.winfo_exists()]

    def top(self, title: str, timeout: float = DRAWN_S):
        """The window titled like `title`, once it is drawn with its buttons.

        A window exists as soon as it is made, and is drawn only once the
        event loop has run: handed back at once, a press into it found no
        button at all ("there are []"), which is how the tour stopped at the
        calibration prompt run unattended. So it waits for the window to be
        on screen with at least one button drawn, for up to `timeout`, and a
        window that never gets there is named with what was missing.
        """
        import tkinter as tk

        def found():
            return [t for t in self.toplevels() if title in t.title()]
        if not self.until(lambda: bool(found()), timeout):
            raise LookupError(f"no window titled like {title!r} within "
                              f"{timeout:.0f} s; open: "
                              f"{[t.title() for t in self.toplevels()]}")
        window = found()[0]
        name = window.title()

        def drawn() -> bool:
            try:
                return bool(window.winfo_ismapped() and self.buttons(window))
            except tk.TclError:
                return False
        if not self.until(drawn, timeout):
            try:
                state = ("on screen" if window.winfo_ismapped()
                         else "never on screen")
                state += f", {len(self.buttons(window))} buttons drawn in it"
            except tk.TclError:
                state = "closed again"
            raise LookupError(f"the window {name!r} did not show its buttons "
                              f"within {timeout:.0f} s ({state})")
        return window

    def labels(self, top=None) -> list[str]:
        return [self.text(w) for w in self.walk(top, into_windows=top is not None)
                if w.winfo_class() in ("TLabel", "Label") and self.text(w)
                and w.winfo_ismapped()]

    def log_lines(self) -> list[str]:
        return self.app.log.get("1.0", "end").rstrip().splitlines()

    def mark(self) -> int:
        return len(self.log_lines())

    def since(self, mark: int) -> list[str]:
        return self.log_lines()[mark:]

    # -- pictures -----------------------------------------------------------

    def shoot(self, name: str, host=None) -> list[str]:
        """Every image `host` shows, as Tk holds it -- what is on screen."""
        host = host or self.app.canvas
        names, seen = [], set()
        widgets = list(self.walk(host))
        for w in widgets:
            images = []
            if w.winfo_class() == "Canvas":
                images += [w.itemcget(i, "image") for i in w.find_all()
                           if w.type(i) == "image"]
            else:
                try:
                    images.append(str(w.cget("image")))
                except Exception:                        # noqa: BLE001
                    pass
            for img in images:
                if not img or img in seen:
                    continue
                seen.add(img)
                path = self.shots / f"{self.phase}-{name}-{len(names):02d}.png"
                self.root.call(img, "write", str(path), "-format", "png")
                names.append(path.name)
                self._note_view(img, path.name)
        return names

    def _note_view(self, img: str, name: str) -> None:
        """What the main view was drawing when `name` was shot of it, so the
        shot can be judged against its stored source (`screen_flat`)."""
        app = self.app
        r = getattr(app, "current", None)
        if (img != str(getattr(app, "_photo", "")) or r is None
                or getattr(r, "entry", None) is None or r.image is None
                or getattr(app, "_shown", None) is None):
            return
        with contextlib.suppress(Exception):
            self._view_drawn[name] = {
                "entry": r.entry, "working": tuple(app._source().shape),
                "view": tuple(app._shown), "channel": app.v_channel.get(),
                "invert": bool(app.v_invert.get()), "cuts": app._cuts(),
                "depth": 8 if r.image.dtype == np.uint8 else 16}

    def shot_array(self, name: str) -> np.ndarray:
        from PIL import Image
        return np.asarray(Image.open(self.shots / name).convert("RGB"))

    def judge_shots(self, step: str, names: list[str]) -> None:
        for name in names:
            shot = self.shot_array(name)
            problems, facts = frame_problems(shot, rail=False,
                                             band=SHOT_BAND_COLUMNS)
            view = self._view_drawn.get(name)
            if view is not None and any(p.startswith("flat bar")
                                        for p in problems):
                # The main view, judged again against the stored source as
                # the window drew it: a channel the source has clipped in
                # every row of its edge is flat there on screen too.
                try:
                    inherited = screen_flat(**view)
                except Exception as exc:                 # noqa: BLE001
                    inherited = None
                    problems.append(f"the stored source could not be read "
                                    f"to compare ({type(exc).__name__}: {exc})")
                if inherited is not None:
                    problems, facts = frame_problems(
                        shot, rail=False, band=SHOT_BAND_COLUMNS,
                        inherited=inherited)
            status, detail = _verdict(problems, facts)
            self.check(step, f"{name} on screen has no band and is not blank",
                       status, detail, [name])

    def judge_result(self, step: str, r, full: bool = True) -> None:
        """One pass the window holds: the picture, its entry, its bytes."""
        label = f"{r.label} (#{r.seq})"
        entry = getattr(r, "entry", None)
        problems, facts = frame_problems(r.image)
        if entry is not None and any(p.startswith("flat bar") for p in problems):
            # Judged again against the source, as the rail is below: an
            # entry clipped in every row of its last columns is flat there
            # through any correction, and its pass is the stored picture.
            try:
                inherited = source_flat(entry, np.asarray(r.image).shape)
            except Exception as exc:                     # noqa: BLE001
                inherited = None
                problems.append(f"the stored source could not be read to "
                                f"compare ({type(exc).__name__}: {exc})")
            if inherited is not None:
                problems, facts = frame_problems(r.image, inherited=inherited)
        status, detail = _verdict(problems, facts)
        self.check(step, f"{label} is a picture", status, detail)
        if entry is None:
            self.check(step, f"{label} was filed", False, "no library entry")
            return
        record = json.loads((entry / "scan.json").read_text(encoding="utf-8"))
        self.check(step, f"{label} names its own entry, not a replaced one",
                   "before" not in (record.get("tags") or []), entry.name)
        self.check(step, f"{label} was filed with its raw bytes",
                   _has_raw(entry), entry.name)
        status, detail, facts = bit_true(entry)
        self.check(step, f"{label} is bit-true to its stored source", status,
                   detail)
        top_pass, top_src = facts.get("rail_top_pass"), facts.get(
            "rail_top_source")
        if top_pass is not None:
            worse = any(p > s + RAIL_SHARE for p, s in zip(top_pass, top_src))
            clipped = any(p > RAIL_SHARE for p in top_pass)
            self.check(step, f"{label} clips no more than its source",
                       "fail" if worse else ("warn" if clipped else "pass"),
                       f"at full scale per channel: pass {top_pass}, source "
                       f"{top_src}" + (" -- the stored source is clipped too"
                                       if clipped and not worse else ""))
        if full:
            self.judge_full(step, r)

    def judge_full(self, step: str, r) -> None:
        """What fit shows and what zooming in loads must be one pass."""
        from rps7200 import preview

        app = self.app
        app._show(r)
        loaded = self.until(lambda: app._full_seq == r.seq, 60)
        if not loaded or app._full is None:
            self.check(step, f"{r.label}: full resolution loads", False,
                       "it never arrived")
            return
        full = np.asarray(app._full)
        work = np.asarray(r.image)
        if full.shape[:2] != work.shape[:2]:
            full = preview.downscale(full, preview.PREVIEW_MAX_SIDE)
        if full.shape[:2] != work.shape[:2]:
            self.check(step, f"{r.label}: fit and zoom are one pass", False,
                       f"shapes {work.shape} and {full.shape}")
            return
        shift, corr = _column_shift(work, full)
        diff = np.abs(work[..., :3].astype(np.float64)
                      - full[..., :3].astype(np.float64)).max()
        diff /= _full_scale(work)
        self.check(step, f"{r.label}: fit and zoom are one pass",
                   shift == 0 and diff <= 0.005,
                   f"columns apart {shift} (profile match {corr}), largest "
                   f"difference {diff:.4f} of full scale")

    # -- the tour -----------------------------------------------------------

    def step(self, name: str, body) -> None:
        errors = len(ERRORS)
        self.current = name
        try:
            body()
        except Exception:                                # noqa: BLE001
            self.check(name, "the step ran to its end", False,
                       traceback.format_exc(limit=6))
        finally:
            self.current = "between steps"
        for err in ERRORS[errors:]:
            self.check(name, "no exception inside the window", False,
                       err["tb"][-1500:])
        # A strip's sheet is titled by its roll and numbers: "Contact sheet
        # -- tour-strips, strip 2 (frames 7-12)".
        stray = [t.title() for t in self.toplevels()
                 if not t.title().startswith("Contact sheet")]
        if stray:
            self.check(name, "no window left open behind it", "warn",
                       ", ".join(stray))
            for t in self.toplevels():
                if t.title() in stray:
                    t.destroy()
            self.settle(3)

    def edges_read(self, timeout: float = 120.0) -> bool:
        """Until the frame-edge reader has placed every frame of the walk.

        A sheet shot before then has no edge marks on the frames still being
        read, which is the reader's timing and not the sheet's; so is a
        position caption that has not arrived yet.
        """
        from tools import frame_edges

        def done():
            progress = self.app.edge_watch.progress()
            return progress.state != frame_edges.READING
        finished = self.until(done, timeout)
        self.settle(20)
        return finished

    def prescans(self):
        return [r for r in self.app.results if r.kind == "prescan"]

    def run_main(self) -> None:
        app = self.app
        s = self.step
        s("the window opens", self._opens)
        s("calibration asks about the film", self._calibrate)
        s("prescan and scan", self._prescan_and_scan)
        s("views and zoom", self._views)
        s("turn, mirror and save", self._turn_and_save)
        s("a JPEG delivery, and delete", self._jpeg_and_delete)
        s("delete while its entry is still being written",
          self._delete_while_filing)
        s("the transport", self._transport)
        s("presets and settings", self._settings)
        s("a walk that aims each frame", self._walk)
        s("the contact sheet", self._sheet)
        s("scanning from the sheet", self._scan_chosen)
        s("a roll stopped part way", self._stopped_roll)
        s("the rolls browser", self._browser)
        s("a roll of two strips", self._two_strips)
        s("force abort", self._abort)
        self.facts["results"] = len(app.results)

    def run_look(self) -> None:
        self.step("look-only: the stored walk", self._look)

    # -- main-phase steps ---------------------------------------------------

    def _opens(self) -> None:
        name = "the window opens"
        self.settle(20)
        # The stand-in opens on the session's thread, and says what it is
        # showing once it has looked through the stored library -- seconds,
        # on one of two thousand entries. Read at once, the log was empty.
        self.until(lambda: any("demo mode" in line
                               for line in self.log_lines()), 60)
        have = {self.text(b) for b in self.buttons()}
        wanted = {"Calibrate", "Prescan", "Scan", "Scan roll", "Rolls ...",
                  "◀ prev slide", "next slide ▶", "◀ back", "forward ▶",
                  "+", "−", "1:1", "Fit", "invert", "RGB", "TIFF", "JPEG"}
        self.check(name, "every main control is there", wanted <= have,
                   f"missing {sorted(wanted - have)}" if wanted - have
                   else f"{len(have)} controls")
        self.check(name, "the log says it is the demo",
                   any("demo mode" in line for line in self.log_lines()),
                   (self.log_lines() or [""])[0])

    def _calibrate(self) -> None:
        name = "calibration asks about the film"
        self.press("Prescan")
        prompt = self.top("Calibrate")
        self.check(name, "a pass with no calibration asks for one", True,
                   prompt.title())
        self.press("Calibrate now", prompt)
        self.settle(3)
        self.check(name, "Calibrate now without the tick is refused",
                   prompt.winfo_exists() and any(
                       "Nothing started" in t for t in self.labels(prompt)))
        self.press("The film is in the transport", prompt)
        self.press("Calibrate now", prompt)
        self.check(name, "the calibration finishes", self.idle(120))
        self.check(name, "the window calls itself calibrated",
                   bool(self.app.calibrated), self.log_lines()[-1])

    def _prescan_and_scan(self) -> None:
        name = "prescan and scan"
        before = len(self.app.results)
        self.press("Prescan")
        self.check(name, "the prescan finishes", self.idle())
        shots = self.shoot("prescan")
        self.judge_shots(name, shots)
        self.press("Scan")
        self.check(name, "the scan finishes", self.idle(300))
        shots = self.shoot("scan")
        self.judge_shots(name, shots)
        new = self.app.results[before:]
        self.check(name, "a prescan and a scan arrived",
                   [r.kind for r in new] == ["prescan", "scan"],
                   str([r.kind for r in new]))
        for r in new:
            self.judge_result(name, r)
        self.check(name, "the caption says what is shown", "warn" if "clipped"
                   in self.app.v_caption.get() else "pass",
                   self.app.v_caption.get())

    def _views(self) -> None:
        name = "views and zoom"
        app = self.app
        scan = [r for r in app.results if r.kind == "scan"][-1]
        app._show(scan)
        self.settle()
        taken = {}
        for n, label in enumerate(("RGB", "invert", "IR", "R", "G", "B",
                                   "MONO", "RGB", "invert")):
            if self.state_of(label) == "disabled":
                continue
            self.press(label)
            self.settle()
            inverted = "inverted" if app.v_invert.get() else "negative"
            shots = self.shoot(f"view-{n}-{label}-{inverted}")
            if shots:
                taken[label] = self.shot_array(shots[0])
                self.judge_shots(name, shots)
        for label in ("IR", "R", "G", "B", "MONO"):
            if label in taken:
                self.check(name, f"{label} is shown as one grey channel",
                           _chroma(taken[label]) < 0.5,
                           f"chroma {_chroma(taken[label]):.2f}")
        digests = {k: hashlib.md5(v.tobytes()).hexdigest()
                   for k, v in taken.items()}
        for label in ("R", "G", "B"):
            if label in digests:
                self.check(name, f"{label} differs from RGB",
                           digests[label] != digests.get("RGB"))
        for press, expect in (("+", lambda z: z != "fit"),
                              ("+", lambda z: z != "fit"),
                              ("1:1", lambda z: z == "100%"),
                              ("−", lambda z: z != "100%"),
                              ("Fit", lambda z: z == "fit")):
            self.press(press)
            self.settle()
            zoom = app.v_zoomtext.get()
            shots = self.shoot(f"zoom-{zoom.replace('%', 'pc')}")
            self.judge_shots(name, shots)
            self.check(name, f"zoom {press} reads {zoom}", expect(zoom), zoom)

    def _turn_and_save(self) -> None:
        from rps7200 import library, tiff

        name = "turn, mirror and save"
        app = self.app
        r = [r for r in app.results if r.kind == "scan"][-1]
        app._show(r)
        self.settle()
        app.on_rotate(r, 90)
        self.settle()
        shots = self.shoot("turned")
        turned = self.shot_array(shots[0]) if shots else None
        self.check(name, "a quarter turn stands the picture up",
                   r.rotation == 90 and turned is not None
                   and turned.shape[0] > turned.shape[1],
                   f"rotation {r.rotation}, on screen "
                   f"{None if turned is None else turned.shape[:2]}")
        app.on_flip(r)
        self.settle()
        self.check(name, "mirror is recorded", bool(r.flipped))
        app.on_rotate(r, -90)
        app.on_flip(r)
        self.settle()
        self.check(name, "and both undo", r.rotation == 0 and not r.flipped)

        target = Path(ANSWERS["asksaveasfilename"])
        target.parent.mkdir(parents=True, exist_ok=True)
        app.on_save_as(r)
        self.until(lambda: target.exists() and not app._saving, 60)
        self.settle(10)
        if self.check(name, "Save As writes the file", target.exists(),
                      str(target)):
            written = np.asarray(tiff.read(target))
            want = np.asarray(library.corrected(r.entry)[0])
            planes = min(written.shape[-1], want.shape[-1])
            same = (written.shape[:2] == want.shape[:2] and np.array_equal(
                written[..., :planes], want[..., :planes]))
            self.check(name, "the file Save As wrote is the corrected entry",
                       same, f"written {written.shape} {written.dtype}, "
                       f"corrected {want.shape} {want.dtype}")
        count = len(list(target.parent.iterdir()))
        app.on_save_all()
        self.until(lambda: not app._saving, 120)
        self.settle(10)
        self.check(name, "Save all writes into the folder",
                   len(list(target.parent.iterdir())) > count)
        scan_caption = app.v_caption.get()
        app.on_show_prescan(r)
        self.settle()
        self.check(name, "show prescan shows the prescan",
                   app.v_caption.get().startswith("prescan"),
                   f"{scan_caption!r} -> {app.v_caption.get()!r}")

    def _jpeg_and_delete(self) -> None:
        name = "a JPEG delivery, and delete"
        app = self.app
        out = Path(app.session.out_dir)
        before = set(out.glob("*")) if out.exists() else set()
        self.press("JPEG")
        app.v_dpi.set("600")
        app._show_estimate()
        if not app.v_ir.get():
            self.press("infrared (RGBI)")
        self.press("Scan")
        self.check(name, "the scan finishes", self.idle(300))
        new = {p.suffix for p in set(out.glob("*")) - before}
        self.check(name, "a JPEG and, for infrared, a DNG beside it",
                   {".jpg", ".dng"} <= new, str(sorted(new)))
        self.press("TIFF")
        app.v_dpi.set("1800")
        app._show_estimate()
        r = app.results[-1]
        # The scan is over when the scanner is idle, not when its entry is
        # filed: the writer files it after, and the window hears of it a
        # tick later. Deleted before then, `r.entry` was None.
        self.check(name, "its entry is filed", self.until(
            lambda: r.entry is not None, 120), r.label)
        self.judge_result(name, r, full=False)
        entry, count = r.entry, len(app.results)
        n = len(DIALOGS)
        app.on_delete(r)
        self.settle()
        asked = [d["title"] for d in self.dialogs_since(n)]
        self.check(name, "delete asks twice before an entry goes",
                   len(asked) == 2, str(asked))
        self.check_trashed(name, entry, Path(app.session.root))
        self.check(name, "the pass leaves the window",
                   len(app.results) == count - 1)

    def check_trashed(self, name: str, gone: Path | None, root: Path,
                      record: str = "scan.json") -> None:
        """That ``gone`` went into ``root``'s trash, whole -- its ``record``
        with it -- and is no longer where it was; with a slide's infrared
        pass beside it."""
        from rps7200 import library

        if gone is None:
            self.check(name, "there was something to move", False, "nothing")
            return
        batch = library.in_trash(root, gone.name)
        self.check(name, f"{gone.name} is in {root / library.TRASH}, whole",
                   not gone.exists() and batch is not None
                   and (batch.path / gone.name / record).exists(),
                   str(batch.path) if batch is not None else "not there")
        if batch is None:
            return
        pairs = [i for i in batch.record.get("items") or ()
                 if (i.get("infrared_pair") or {}).get("role") == "colour"]
        for item in pairs:
            partner = [i["name"] for i in batch.record.get("items") or ()
                       if (i.get("infrared_pair") or {}).get("id")
                       == item["infrared_pair"]["id"]
                       and i is not item]
            self.check(name, "a slide's infrared pass went with it",
                       bool(partner), ", ".join(partner) or "not in it")

    def _delete_while_filing(self) -> None:
        """Delete pressed on a pass whose entry the writer is still filing:
        it said only "remove from this session?", and the entry was filed
        afterwards with nothing said (B1-T2). It says so now, and keeps it."""
        from rps7200 import library

        name = "delete while its entry is still being written"
        app = self.app
        before = len(app.results)
        self.press("Scan")
        if not self.check(name, "a pass arrives", self.until(
                lambda: len(app.results) > before, 300)):
            self.idle(300)
            return
        r = app.results[-1]
        state, _entry = app.session.filing(r.seq)
        if state != "owed":
            self.check(name, "the pass is caught before it is filed", "warn",
                       f"the session already says {state}; nothing to test")
            self.idle(300)
            return
        n = len(DIALOGS)
        app.on_delete(r)
        self.settle()
        said = self.dialogs_since(n)
        self.check(name, "the question says the entry is still being written",
                   len(said) == 1 and "still being written" in said[0]["message"]
                   and "press Delete again" in said[0]["message"],
                   " / ".join(d["message"][:160] for d in said))
        self.check(name, "the pass leaves the window", r not in app.results)
        self.idle(300)
        self.until(lambda: app.session.filing(r.seq)[0] != "owed", 120)
        state, entry = app.session.filing(r.seq)
        root = Path(app.session.root)
        self.check(name, "its entry is filed, and kept in the library",
                   state == "filed" and entry is not None and entry.exists()
                   and library.in_trash(root, entry.name) is None,
                   f"{state}: {entry}")

    def _transport(self) -> None:
        name = "the transport"
        for label, says in (("next slide ▶", "advanced to position"),
                            ("next slide ▶", "advanced to position"),
                            ("◀ prev slide", "went back to position"),
                            ("forward ▶", "nudge +"),
                            ("◀ back", "nudge -")):
            mark = self.mark()
            self.press(label)
            done = self.idle(60)
            said = self.since(mark)
            self.check(name, f"{label} moves the film", done and any(
                says in line for line in said), " | ".join(said[-2:]))

    def _settings(self) -> None:
        name = "presets and settings"
        app = self.app
        for label, title in (("About the scanner", "About the scanner"),
                             ("ⓘ", "Moving the film")):
            n = len(DIALOGS)
            self.press(label)
            self.settle()
            self.check(name, f"{label} says something",
                       [d["title"] for d in self.dialogs_since(n)] == [title])
        ANSWERS["askstring"] = "tour preset"
        self.press("Save")
        self.settle()
        values = list(app.preset_box["values"])
        self.check(name, "a preset is saved", "tour preset" in values,
                   str(values))
        self.press("Forget")
        self.settle()
        values = list(app.preset_box["values"])
        self.check(name, "and forgotten", "tour preset" not in values,
                   str(values))
        self.press("Shortcuts ...")
        shortcuts = self.top("Shortcuts")
        self.settle()
        rows = len(self.buttons(shortcuts))
        self.check(name, "the shortcut editor opens", rows > 10,
                   f"{rows} buttons in it")
        shortcuts.destroy()
        self.settle()
        app.v_dpi.set("900")
        app._show_estimate()
        n = len(DIALOGS)
        self.press("Restore settings ...")
        self.settle()
        self.check(name, "Restore settings puts the dpi back",
                   app.v_dpi.get() == "1800", " / ".join(
                       d["message"][:80] for d in self.dialogs_since(n)))

    def _walk(self) -> None:
        name = "a walk that aims each frame"
        app = self.app
        app.fields["roll"].set("")
        if not app.v_dryrun.get():
            self.press("dry run -- prescan and advance only")
        if not app.v_correct.get():
            self.press("aim each frame while prescanning")
        app.v_startat.set("1")
        app.v_last.set("6")
        before = len(app.results)
        mark = self.mark()
        self.press("Scan roll")
        self.check(name, "the walk finishes", self.idle(300))
        said = self.since(mark)
        self.check(name, "no pass was filed without its own bytes",
                   not any("later pass" in line for line in said),
                   " | ".join(line for line in said if "later pass" in line))
        walked = [r for r in app.results[before:] if r.kind == "prescan"]
        self.check(name, "six prescans arrived", len(walked) == 6,
                   str(len(walked)))
        folder = app._sheet_roll
        self.facts["walk"] = str(folder) if folder else None
        # A walk from an empty roll box is strip 1 of a new roll, by a dated
        # name: rolls/<date>/strip-01.
        self.check(name, "the walk lands in strip-01 of a new roll",
                   folder is not None and Path(folder).name == "strip-01"
                   and Path(folder).parent.parent == Path(app.session.rolls)
                   and (Path(folder).parent / "strips.json").exists(),
                   str(folder))
        if folder:
            survey = json.loads((Path(folder) / "survey.json").read_text(
                encoding="utf-8"))
            self.check(name, "survey.json holds the six",
                       len(survey.get("frames") or []) == 6)
        for r in walked:
            self.judge_result(name, r)
            self.judge_shots(name, self.shoot(f"walk-{r.number}"))
        aimed = [line for line in said if "->" in line and "units" in line]
        self.check(name, "the aim reported each frame", "pass" if aimed
                   else "warn", " | ".join(aimed[:6]))

    def _sheet(self) -> None:
        name = "the contact sheet"
        app = self.app
        sheet = app.sheet
        self.check(name, "the sheet opened after the walk",
                   sheet is not None and sheet.alive())
        top = sheet.top
        self.check(name, "the edge reader finishes", self.edges_read())
        shots = self.shoot("sheet", top)
        self.check(name, "a thumbnail per frame", len(shots) >= 6,
                   str(len(shots)))
        self.judge_shots(name, shots)
        self.press("None", top)
        self.settle()
        self.check(name, "None unticks every frame", sheet.chosen() == ())
        self.press("All", top)
        self.settle()
        self.check(name, "All ticks every frame", len(sheet.chosen()) == 6)
        sheet.adjust(2)
        self.settle()
        adjuster = sheet._adjuster
        frame = adjuster.top
        before = adjuster.offset
        self.press("▶", frame)
        self.settle()
        self.check(name, "▶ moves the frame", adjuster.offset != before,
                   f"{before} -> {adjuster.offset}")
        self.judge_shots(name, self.shoot("adjuster", frame))
        self.press("Centre", frame)
        self.settle()
        self.check(name, "Centre sets no offset", adjuster.offset == 0.0,
                   str(adjuster.offset))
        self.press("Next frame ▶", frame)
        self.settle()
        self.check(name, "Next frame goes to frame 4",
                   any("Frame 4 of 6" in t for t in self.labels(frame)))
        self.press("Show in preview", frame)
        self.settle()
        self.check(name, "Show in preview shows it in the window",
                   app.v_caption.get().startswith("frame 4"),
                   app.v_caption.get())
        self.press("Done", frame)
        self.settle()
        self.check(name, "Done closes it", not frame.winfo_exists())
        n = len(DIALOGS)
        self.press("Reset positions", top)
        self.settle()
        self.check(name, "Reset positions asks first",
                   [d["title"] for d in self.dialogs_since(n)]
                   == ["Reset positions"])

    def _scan_chosen(self) -> None:
        name = "scanning from the sheet"
        app = self.app
        sheet = app.sheet
        top = sheet.top
        self.press("None", top)
        self.press("Frame 2", top)
        self.press("Frame 4", top)
        sheet.v_options["dpi"].set("300")
        sheet.v_options["ir"].set(False)
        # Every frame ticked is held to its position, and a held frame is
        # never aimed: the sheet offers no nudge (GUI2-10).
        self.check(name, "the sheet offers no nudge tick",
                   "correct" not in sheet.v_options
                   and not any("nudge" in self.text(w)
                               for w in self.walk(top)),
                   str(sorted(sheet.v_options)))
        self.settle()
        before = len(app.results)
        folder = Path(app._sheet_roll)
        mark = self.mark()
        self.press("Scan chosen frames", top)
        # The sheet reopened while its roll runs, and frame 4 turned there:
        # for the next commission. The roll writes and shows frame 4 as it
        # was commissioned (GUI2-27). Only where frame 4 has not come in by
        # the time it is turned -- the demo's timing is its own.
        turned_early = False
        if self.until(lambda: type(app._running_job).__name__ == "Roll", 60):
            app.on_contact_sheet()
            self.settle()
            early = not any(r.kind == "frame" and r.number == 4
                            for r in app.results[before:])
            if app.sheet is not None and app.sheet.alive():
                app.sheet._one(4, 90)
                turned_early = early
                self.check(name, "a turn while the roll runs says it waits "
                           "for the next commission",
                           any("kept for the next commission" in line
                               for line in self.since(mark)),
                           " | ".join(self.since(mark)[-2:]))
        self.check(name, "the two frames are scanned", self.idle(300))
        said = self.since(mark)
        self.check(name, "no pass was filed without its own bytes",
                   not any("later pass" in line for line in said))
        written = sorted(p.name for p in folder.glob("frame*.tif"))
        self.check(name, "frame02 and frame04 are written",
                   written == ["frame02.tif", "frame04.tif"], str(written))
        self.check(name, "approved.json records the commission",
                   (folder / "approved.json").exists())
        try:
            recorded = json.loads((folder / "roll.json").read_text(
                encoding="utf-8"))["settings"].get("correct", "absent")
        except (OSError, ValueError, KeyError) as exc:
            recorded = f"unreadable: {exc}"
        self.check(name, "roll.json records 'aim each frame' as not "
                   "applicable", recorded is None, repr(recorded))
        frame4 = [r for r in app.results[before:]
                  if r.kind == "frame" and r.number == 4]
        if turned_early:
            try:
                asked = next(
                    (int(f["rotation"]) % 360, bool(f["flipped"]))
                    for f in json.loads((folder / "approved.json").read_text(
                        encoding="utf-8"))["frames"] if f["number"] == 4)
            except (OSError, ValueError, KeyError, StopIteration):
                asked = None
            self.check(name, "frame 4 arrives as it was commissioned, not as "
                       "turned since", bool(frame4) and all(
                           (r.rotation, r.flipped) == asked for r in frame4),
                       f"commissioned {asked}, arrived "
                       f"{[(r.rotation, r.flipped) for r in frame4]}")
        sheet = app.sheet
        if sheet is not None and sheet.alive():
            # Marked done as each was filed, on the sheet left open.
            self.check(name, "the sheet marks frames 2 and 4 done, unticked",
                       {2, 4} <= sheet.done
                       and not {2, 4} & set(sheet.chosen()),
                       f"done {sorted(sheet.done)}, ticked {sheet.chosen()}")
            if turned_early:
                self.check(name, "and keeps the turn for the next commission",
                           sheet.rotations.get(4) == 90,
                           str(sheet.rotations.get(4)))
                sheet._one(4, 270)
            sheet._dismiss()
            self.settle()
        for r in app.results[before:]:
            self.judge_result(name, r, full=r.kind == "prescan")

    def _stopped_roll(self) -> None:
        name = "a roll stopped part way"
        app = self.app
        # A name with no "roll" in it: the stop label was once read from the
        # job's words, and a roll of strips said "roll" only when its name
        # did -- the 2026-10-12 tour passed on "tour-roll" alone.
        app.fields["roll"].set("tour-film")
        if app.v_dryrun.get():
            self.press("dry run -- prescan and advance only")
        if app.v_correct.get():
            self.press("aim each frame while prescanning")
        app.v_dpi.set("300")
        app._show_estimate()
        if app.v_ir.get():
            self.press("infrared (RGBI)")
        app.v_startat.set("1")
        app.v_last.set("4")
        mark = self.mark()
        self.press("Scan roll")
        started = self.until(lambda: any(
            line.startswith("frame 1:") for line in self.since(mark)), 120)
        self.check(name, "the roll reaches frame 1", started)
        label = "Stop after this frame"
        self.check(name, "the stop button promises the frame boundary",
                   self.state_of(label) == "normal", str(self.state_of(label)))
        self.press(label)
        self.check(name, "the roll ends", self.idle(300))
        said = self.since(mark)
        self.check(name, "it stopped after frame 1, as asked",
                   any("stopped after frame 1" in line for line in said),
                   " | ".join(said[-3:]))
        # A roll from the Roll button with a new name is strip 1 of a new
        # roll, scanned without walking it first.
        folder = Path(app.session.rolls) / "tour-film" / "strip-01"
        self.facts["stopped_roll"] = str(folder)
        written = sorted(p.name for p in folder.glob("frame*.tif"))
        self.check(name, "only frame 1 is written, in tour-film/strip-01",
                   written == ["frame01.tif"], str(written))

    def _browser(self) -> None:
        name = "the rolls browser"
        app = self.app
        rolls = Path(app.session.rolls)
        self.press("Rolls ...")
        top = self.top("Rolls")
        browser = next(v for v in vars(app).values()
                       if type(v).__name__ == "_RollBrowser")

        def select(folder: str) -> None:
            self.select_roll(top, folder)

        select("tour-film")
        browser._duplicate()
        # It copies on a thread, under a dialog of its own, and says it has
        # ended through the pump.
        self.until(lambda: app._copy_stop is None, 120)
        self.settle(10)
        self.check(name, "Duplicate copies the roll with its strips",
                   (rolls / "tour-film-2" / "strip-01" / "roll.json").is_file()
                   and (rolls / "tour-film-2" / "strips.json").is_file()
                   and not (rolls / "tour-film-2" / "INCOMPLETE").exists(),
                   str(sorted(p.name for p in rolls.iterdir())))
        ANSWERS["askstring"] = "tour-renamed"
        select("tour-film-2")
        browser._rename()
        self.settle(5)
        self.check(name, "Rename renames the folder",
                   (rolls / "tour-renamed").is_dir()
                   and not (rolls / "tour-film-2").exists())
        n = len(DIALOGS)
        select("tour-renamed")
        browser._delete()
        self.settle(5)
        asked = [d["message"] for d in self.dialogs_since(n)
                 if d["title"] == "Delete"]
        self.check(name, "Delete names the folder that goes",
                   bool(asked) and "tour-renamed" in asked[0],
                   asked[0][:120] if asked else "no question")
        self.check(name, "and says it goes into the rolls' trash",
                   bool(asked) and "into the rolls' trash" in asked[0],
                   asked[0][:200] if asked else "no question")
        self.check_trashed(name, rolls / "tour-renamed", rolls,
                           "strip-01/roll.json")
        select("tour-film")
        # Into a folder the dialog names and nothing has made yet, as one
        # typed into the picker: it is made after OK, its last folder only,
        # and said in the question (Stefan's call, 2026-10-12). Every frame
        # failed on its temporary file's name here once.
        was = ANSWERS["askdirectory"]
        # Its parent there, as Save As's step leaves it: a parent missing is
        # refused, and that is not what this step is for.
        Path(was).mkdir(parents=True, exist_ok=True)
        fresh = Path(was) / f"export-{time.strftime('%H%M%S')}"
        ANSWERS["askdirectory"] = str(fresh)
        n = len(DIALOGS)
        try:
            browser._export()
            self.until(lambda: not app._saving, 120)
            self.settle(10)
        finally:
            ANSWERS["askdirectory"] = was
        asked = [d["message"] for d in self.dialogs_since(n)
                 if d["title"] == "Export" and d["kind"] == "askokcancel"]
        self.check(name, "Export says a new folder is made for it",
                   bool(asked) and "is not there yet" in asked[0],
                   asked[0][:200] if asked else "no question")
        written = sorted(p.name for p in fresh.glob("*")) if fresh.is_dir() \
            else []
        self.check(name, "Export makes the new folder and writes the roll's "
                   "frames into it", bool(written), str(written[:4]))
        n = len(DIALOGS)
        select("tour-film")
        browser._open()
        self.settle(20)
        said = [d["message"] for d in self.dialogs_since(n)]
        text = " ".join(said)
        self.check(name, "the stopped roll reopens with frames 2 to 4 left",
                   "3 are left" in text or "3 left (2, 3, 4)" in text,
                   text[:300] or "no dialog")
        self.check(name, "and does not call itself finished",
                   "all of them already scanned" not in text
                   and "nothing left" not in text, text[:160])
        with contextlib.suppress(Exception):
            top.destroy()
        for t in self.toplevels():
            t.destroy()
        self.settle(3)

    def _two_strips(self) -> None:
        """A roll of two strips (Stefan's roll model, 2026-10-10): strip 1
        walked into a new roll, the next walk offered as strip 2 and
        numbered on from strip 1's last; two of strip 2's frames scanned
        under their numbers across the roll; the browser's tree; and strip
        2 deleted on its own and brought back by the command it printed."""
        import shlex

        from rps7200 import library

        name = "a roll of two strips"
        app = self.app
        rolls = Path(app.session.rolls)
        roll = rolls / "tour-strips"
        strip1, strip2 = roll / "strip-01", roll / "strip-02"
        out = Path(app.session.out_dir)
        library_root = Path(app.session.root)
        app.fields["roll"].set("tour-strips")
        if not app.v_dryrun.get():
            self.press("dry run -- prescan and advance only")
        if app.v_correct.get():
            self.press("aim each frame while prescanning")
        app.v_startat.set("1")
        app.v_last.set("6")
        # The sheet of the walk before is still the window's: No to adding
        # to it, so this walk starts the roll the box names.
        ANSWERS["askyesnocancel"] = False
        try:
            self.press("Scan roll")
            self.check(name, "strip 1 is walked", self.idle(300))
        finally:
            ANSWERS["askyesnocancel"] = True
        self.check(name, "the walk lands in tour-strips/strip-01",
                   app._sheet_roll is not None
                   and Path(app._sheet_roll) == strip1
                   and (strip1 / "survey.json").is_file(),
                   str(app._sheet_roll))
        self.settle(15)
        self.check(name, "the Roll panel says strip 2 comes next",
                   "strip 2" in app.v_strip_note.get(),
                   app.v_strip_note.get())
        before = fingerprint(strip1)
        n = len(DIALOGS)
        mark = self.mark()
        # No: walk the next strip, not add to this one.
        ANSWERS["askyesnocancel"] = False
        try:
            self.press("Scan roll")
            self.check(name, "strip 2 is walked", self.idle(300))
        finally:
            ANSWERS["askyesnocancel"] = True
        asked = [d["message"] for d in self.dialogs_since(n)
                 if d["kind"] == "askyesnocancel"]
        self.check(name, "the question offers strip 2, numbered from 7",
                   bool(asked) and "strip 2 of tour-strips" in asked[0]
                   and "numbered from 7" in asked[0],
                   asked[0][:300] if asked else "not asked")
        survey = {}
        if (strip2 / "survey.json").is_file():
            survey = json.loads((strip2 / "survey.json").read_text(
                encoding="utf-8"))
        self.check(name, "strip 2's walk carries its claim, from 7",
                   survey.get("strip") == {"number": 2, "first": 7},
                   str(survey.get("strip")))

        def members(kind: str) -> list[tuple[dict, str]]:
            found = []
            for record in library.entries(library_root):
                member = ((record.get("extra") or {}).get("roll_membership")
                          or {})
                if member.get("roll") == "tour-strips" \
                        and member.get("strip") == 2 \
                        and member.get("kind") == kind:
                    found.append((member, str((record.get("film") or {})
                                              .get("frame") or "")))
            return found

        self.until(lambda: len(members("prescan")) >= 6, 60)
        labels = sorted(frame for _m, frame in members("prescan"))
        self.check(name, "its prescans are labelled tour-strips-07 to -12",
                   labels == [f"tour-strips-{n:02d}" for n in range(7, 13)],
                   str(labels))
        delivered = sorted(p.name for p in (out / "prescans").glob(
            "tour-strips_frame07*"))
        self.check(name, "its first prescan is delivered as frame07",
                   bool(delivered), str(delivered))
        self.check(name, "the walk did not take strip 2 for strip 1",
                   not any("looks like frame 1 of" in line
                           for line in self.since(mark)),
                   " | ".join(line for line in self.since(mark)
                              if "looks like" in line))
        sheet = app.sheet
        if not self.check(name, "strip 2's sheet opened",
                          sheet is not None and sheet.alive()):
            return
        top = sheet.top
        self.check(name, "its title names the strip and its numbers",
                   top.title() == "Contact sheet -- tour-strips, strip 2 "
                   "(frames 7-12)", top.title())
        self.press("None", top)
        self.press("Frame 8", top)
        self.press("Frame 10", top)
        sheet.v_options["dpi"].set("300")
        sheet.v_options["ir"].set(False)
        self.settle()
        self.press("Scan chosen frames", top)
        self.check(name, "frames 8 and 10 are scanned", self.idle(300))
        written = sorted(p.name for p in strip2.glob("frame*.tif"))
        self.check(name, "strip-02 holds them as frame02 and frame04",
                   written == ["frame02.tif", "frame04.tif"], str(written))
        delivered = sorted(p.name.split("_")[1]
                           for p in out.glob("tour-strips_frame*"))
        self.check(name, "they are delivered as frame08 and frame10",
                   delivered == ["frame08", "frame10"], str(delivered))
        scanned = sorted(members("frame"), key=lambda m: m[1])
        self.check(name, "filed as tour-strips-08 and -10, places 2 and 4",
                   [(m.get("number"), m.get("roll_number"), frame)
                    for m, frame in scanned]
                   == [(2, 8, "tour-strips-08"), (4, 10, "tour-strips-10")],
                   str(scanned))
        self.check(name, "strip-01 is left as it was",
                   fingerprint(strip1) == before)

        self.press("Rolls ...")
        browser_top = self.top("Rolls")
        browser = next(v for v in vars(app).values()
                       if type(v).__name__ == "_RollBrowser")
        tree = self.tree(browser_top)
        row = self.roll_row(browser_top, "tour-strips")
        children = [str(tree.item(c)["values"][0])
                    for c in tree.get_children(row)]
        self.check(name, "the browser shows the roll with its two strips",
                   len(children) == 2 and children[0].startswith("strip 1 ·")
                   and children[1].startswith("strip 2 · 7-12"),
                   str(children))
        # Strip 2 is the one open here; strip 1 is opened, so 2 can go.
        self.select_roll(browser_top, "tour-strips", strip=1)
        browser._open()
        self.settle(20)
        self.press("Rolls ...")
        browser_top = self.top("Rolls")
        browser = next(v for v in vars(app).values()
                       if type(v).__name__ == "_RollBrowser")
        self.select_roll(browser_top, "tour-strips", strip=2)
        n = len(DIALOGS)
        mark = self.mark()
        browser._delete()
        self.settle(5)
        asked = [d["message"] for d in self.dialogs_since(n)
                 if d["title"] == "Delete"]
        self.check(name, "Delete of a strip says its numbers stay given",
                   bool(asked) and "stay given" in asked[0],
                   asked[0][:300] if asked else "no question")
        batch = library.in_trash(rolls, "strip-02")
        self.check(name, "strip 2 goes into the rolls' trash, as a strip "
                   "of tour-strips",
                   batch is not None and batch.kind == "strips"
                   and batch.within == "tour-strips" and not strip2.exists(),
                   f"{batch.kind if batch else None}, "
                   f"{batch.within if batch else None}")
        printed = next((line for line in self.since(mark)
                        if "restore" in line and "brings it back" in line),
                       "")
        command = printed.split("`")[1] if printed.count("`") >= 2 else ""
        if self.check(name, "the delete prints its restore command",
                      command.startswith("tools/library.py restore"),
                      printed):
            # This window scanned into the roll and holds it until it closes,
            # which the line says: run now, the restore is refused, naming
            # it, and nothing moves. It is run again once the window has
            # closed (`tour_all`), and brings strip 2 back then.
            self.check(name, "and says it runs once this window has closed",
                       "once this window has closed" in printed, printed)
            parts = [p.strip('"') for p in shlex.split(command, posix=False)]
            done = subprocess.run([sys.executable, *parts], cwd=REPO,
                                  capture_output=True, text=True)
            said = (done.stdout + done.stderr).strip()
            self.check(name, "run now, it is refused, naming this window",
                       "in use by the demo window" in said
                       and not strip2.exists(), said[-300:])
            self.facts["strip_restore"] = {"command": parts,
                                           "strip": str(strip2)}
        with contextlib.suppress(Exception):
            browser_top.destroy()
        self.settle(3)

    def _abort(self) -> None:
        name = "force abort"
        app = self.app
        ANSWERS["askstring"] = "not the word"
        app.v_dpi.set("3600")
        app._show_estimate()
        self.press("Scan")
        self.until(lambda: app.busy, 30)
        self.press("Force abort")
        self.idle(300)
        self.check(name, "a wrong word does not abort",
                   not getattr(app.session, "dead", False))
        # Part way through the read, which is the abort's whole point: it
        # used to be pressed once the log showed the pass's fit, and the
        # demo fits after its read, so every abort landed on a complete pass
        # and filed it whole. Metering off, so the read is the first thing
        # the job does, and pressed once its first lines are in.
        if app.v_expmode.get() != "manual":
            self.press("set by hand")
        library_root = Path(app.session.root)
        entries = {p.name for p in library_root.iterdir() if p.is_dir()}
        results = len(app.results)
        ANSWERS["askstring"] = "ABORT"
        self.press("Scan")
        mid = self.until(lambda: app.busy and 0 < float(
            app.progress.cget("value")) < 1000, 60)
        at = float(app.progress.cget("value")) / 10
        self.press("Force abort")
        self.settle(40)
        self.check(name, "the abort lands part way through the read", mid,
                   f"{at:.0f}% of the pass read")
        new = [library_root / n for n in {p.name for p in library_root.iterdir()
                                          if p.is_dir()} - entries]
        whole = [p.name for p in new if (p / "scan.json").exists()
                 and "failed" not in (json.loads((p / "scan.json").read_text(
                     encoding="utf-8")).get("tags") or [])]
        self.check(name, "a read abandoned part way is filed as no whole pass",
                   not whole, ", ".join(whole))
        self.check(name, "and hands the window no picture",
                   len(app.results) == results,
                   f"{len(app.results) - results} new")
        self.check(name, "ABORT marks the session dead",
                   bool(getattr(app.session, "dead", False)),
                   app.v_caption.get())
        live = [label for label in ("Prescan", "Scan", "Scan roll")
                if self.state_of(label) == "normal"]
        self.check(name, "and the controls that drive the scanner grey out",
                   not live, str(live))
        self.check(name, "the caption says to power-cycle",
                   "power-cycle" in app.v_caption.get(), app.v_caption.get())

    # -- look-only ----------------------------------------------------------

    def _look(self) -> None:
        name = "look-only: the stored walk"
        app = self.app
        opened = self.until(lambda: app.sheet is not None and app.sheet.alive(),
                            60)
        self.check(name, "the sheet opens on the stored walk", opened,
                   " / ".join(d["message"][:100] for d in DIALOGS))
        if not opened:
            return
        self.check(name, "the edge reader finishes", self.edges_read())
        top = app.sheet.top
        shots = self.shoot("sheet", top)
        self.check(name, "a thumbnail per frame", len(shots) >= 6,
                   str(len(shots)))
        self.judge_shots(name, shots)
        self.press("Scan chosen frames", top)
        prompt = self.top("Calibrate")
        self.press("The film is in the transport", prompt)
        self.press("Calibrate now", prompt)
        self.idle(60)
        caption = app.v_caption.get()
        self.check(name, "the stand-in refuses to calibrate with no film",
                   "no film" in caption, caption[:160])
        self.check(name, "and the window is not left calibrated",
                   not app.calibrated)
        for t in self.toplevels():
            if "Calibrate" in t.title():
                t.destroy()
        self.press("Prescan")
        self.settle()
        self.check(name, "a prescan asks for the calibration again",
                   any("Calibrate" in t.title() for t in self.toplevels()))
        for t in self.toplevels():
            if "Calibrate" in t.title():
                t.destroy()


# --- one window, in its own process ----------------------------------------


def serve(tour: Path, app, root, t: Tour) -> None:
    """Take commands from ``<tour>/cmd/*.py`` until one closes the window."""
    commands, answers = tour / "cmd", tour / "answers"
    commands.mkdir(exist_ok=True)
    answers.mkdir(exist_ok=True)
    space = {"app": app, "root": root, "t": t, "np": np, "DIALOGS": DIALOGS,
             "ERRORS": ERRORS, "ANSWERS": ANSWERS, "frame_problems":
             frame_problems, "bit_true": bit_true}

    def poll() -> None:
        for f in sorted(commands.glob("*.py")):
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out):
                    exec(compile(f.read_text(encoding="utf-8"), f.name, "exec"),
                         space)
            except Exception:                            # noqa: BLE001
                out.write("\n[command failed]\n" + traceback.format_exc())
            (answers / (f.stem + ".txt")).write_text(out.getvalue(),
                                                     encoding="utf-8")
            f.unlink()
        with contextlib.suppress(Exception):
            root.after(150, poll)

    root.after(150, poll)
    (tour / "serving").write_text(str(os.getpid()), encoding="utf-8")
    print(f"serving: send commands with `tools/gui_tour.py send {tour}`",
          flush=True)


def run_phase(phase: str, tour: Path, open_roll: str | None,
              do_tour: bool, keep: bool) -> int:
    os.chdir(REPO)
    _answer_dialogs(tour)
    import tools.gui as gui

    argv = ["gui.py", "--demo",
            "--demo-source", str(SOURCE),
            "--library", str(tour / "library"),
            "--rolls", str(tour / "rolls"),
            "--reference", str(tour / "calibration" / "shading.npz"),
            "--settings", str(tour / f"gui-settings-{phase}.json"),
            "--out", str(tour / "out")]
    if phase == "look":
        argv += ["--look-only", "--open-roll", str(open_roll)]
    holder: dict[str, Tour] = {}

    def on_error(exc, val, tb):
        ERRORS.append({"t": round(time.monotonic() - T0, 2),
                       "tb": "".join(traceback.format_exception(exc, val, tb))})

    original = gui.ScannerGui.__init__

    def built(self, root, *a, **k):
        original(self, root, *a, **k)
        root.report_callback_exception = on_error
        t = holder["tour"] = Tour(self, root, tour, phase)

        def begin():
            if do_tour and phase == "main":
                t.run_main()
            elif do_tour:
                t.run_look()
            if keep:
                serve(tour, self, root, t)
            else:
                self.on_close()

        root.after(800, begin)

    gui.ScannerGui.__init__ = built
    sys.argv = argv
    try:
        code = gui.main()
    finally:
        with contextlib.suppress(OSError):
            (tour / "serving").unlink()
    t = holder.get("tour")
    result = {"phase": phase, "exit": code, "checks": t.checks if t else [],
              "facts": t.facts if t else {}, "dialogs": DIALOGS,
              "errors": ERRORS}
    (tour / f"phase-{phase}.json").write_text(
        json.dumps(result, indent=1, default=str), encoding="utf-8")
    return 0


# --- the whole tour, and what it leaves behind ------------------------------


def fingerprint(folder: Path) -> dict[str, tuple[int, int]]:
    out = {}
    if not folder.exists():
        return out
    for base, _dirs, files in os.walk(folder):
        for f in files:
            p = Path(base) / f
            with contextlib.suppress(OSError):
                st = p.stat()
                out[str(p.relative_to(folder))] = (st.st_size, st.st_mtime_ns)
    return out


def after_checks(tour: Path, untouched: dict[str, dict]) -> list[dict]:
    checks = []

    def check(what, ok, detail=""):
        status = ok if isinstance(ok, str) else ("pass" if ok else "fail")
        checks.append({"phase": "after", "step": "what was left behind",
                       "what": what, "status": status, "detail": detail,
                       "shots": []})

    library_root = tour / "library"
    for verb, words in (("verify", "intact"),
                        ("reconstruct", "decodes to exactly")):
        done = subprocess.run(
            [sys.executable, "tools/library.py", "--root", str(library_root),
             verb], cwd=REPO, capture_output=True, text=True)
        tail = (done.stdout + done.stderr).strip().splitlines()[-3:]
        check(f"library.py {verb} is clean", done.returncode == 0
              and words in (done.stdout + done.stderr), " | ".join(tail))
    from rps7200.demo import CORRECTED_WHEN_STORED
    bare = []
    for record in sorted(library_root.glob("*/scan.json")):
        text = record.read_text(encoding="utf-8")
        if not _has_raw(record.parent) and CORRECTED_WHEN_STORED not in text:
            bare.append(record.parent.name)
    check("every entry was filed with its raw bytes", not bare,
          ", ".join(bare[:8]) + (f" and {len(bare) - 8} more"
                                 if len(bare) > 8 else ""))
    for name, before in untouched.items():
        after = fingerprint(REPO / name)
        changed = sorted(k for k in set(before) | set(after)
                         if before.get(k) != after.get(k))
        check(f"{name}/ was not touched", not changed, ", ".join(changed[:6]))
    return checks


def contact_sheets(tour: Path) -> list[str]:
    """Every picture the tour captured, labelled, in sheets of 24."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return []
    shots = sorted((tour / "shots").glob("*.png"))
    sheets = []
    for n in range(0, len(shots), 24):
        tiles = []
        for path in shots[n:n + 24]:
            im = Image.open(path).convert("RGB")
            im.thumbnail((300, 210))
            tile = Image.new("RGB", (300, 228), (30, 30, 30))
            tile.paste(im, ((300 - im.width) // 2, 0))
            ImageDraw.Draw(tile).text((3, 214), path.stem[-44:],
                                      fill=(255, 220, 0))
            tiles.append(tile)
        cols = 4
        sheet = Image.new("RGB", (cols * 300, -(-len(tiles) // cols) * 228))
        for i, tile in enumerate(tiles):
            sheet.paste(tile, ((i % cols) * 300, (i // cols) * 228))
        out = tour / f"contact-{n // 24 + 1:02d}.png"
        sheet.save(out)
        sheets.append(out.name)
    return sheets


def write_report(tour: Path, checks: list[dict], phases: list[dict],
                 sheets: list[str]) -> Path:
    order = {"fail": 0, "warn": 1, "pass": 2, "skip": 3}
    counts = {k: sum(c["status"] == k for c in checks) for k in order}
    lines = [f"# GUI tour {tour.name}", "",
             f"{counts['fail']} failed, {counts['warn']} warnings, "
             f"{counts['pass']} passed, {counts['skip']} skipped.", ""]
    for phase in phases:
        lines.append(f"- phase {phase['phase']}: exit {phase['exit']}, "
                     f"{len(phase['dialogs'])} dialogs, {len(phase['errors'])} "
                     f"exceptions inside the window")
    lines.append("")
    for status in ("fail", "warn"):
        chosen = [c for c in checks if c["status"] == status]
        if chosen:
            lines += [f"## {status.upper()}", ""]
            for c in chosen:
                lines.append(f"- **{c['step']}** -- {c['what']}: {c['detail']}"
                             + (f" ({', '.join(c['shots'])})" if c["shots"]
                                else ""))
            lines.append("")
    lines += ["## Pictures to look at", ""]
    lines += [f"- {tour / name}" for name in sheets] or ["- none captured"]
    lines += ["", f"Every shot: {tour / 'shots'}", "", "## All checks", ""]
    for c in sorted(checks, key=lambda c: order.get(c["status"], 9)):
        lines.append(f"- [{c['status']}] {c['phase']} / {c['step']} -- "
                     f"{c['what']}" + (f": {c['detail'][:200]}"
                                       if c["detail"] and c["status"] != "pass"
                                       else ""))
    lines += ["", "## Dialogs, in order", ""]
    for phase in phases:
        for d in phase["dialogs"]:
            words = d["message"].replace("\n", " ")[:160]
            lines.append(f"- {phase['phase']} {d['kind']} "
                         f"\"{d['title']}\": {words} -> {d['answer']!r}")
    report = tour / "report.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def tour_all(args) -> int:
    os.chdir(REPO)
    import tools.gui as gui

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    tour = Path(gui.DEMO_ROOT) / "tour" / stamp
    tour.mkdir(parents=True)
    # What the demo read its pictures from, and what it keeps of its own
    # between runs: none of it is the tour's to change.
    beside = sorted(str(p) for p in SOURCE.parent.glob(f"{SOURCE.name} *")
                    if p.is_dir())
    watched = [str(SOURCE), *beside, str(Path(gui.DEMO_ROOT) / "rolls")]
    untouched = {name: fingerprint(REPO / name) for name in watched}
    print(f"tour: {tour}", flush=True)
    from rps7200.demo import libraries_beside
    if not libraries_beside(SOURCE):
        print(f"no stored pictures under {SOURCE}: the demo can only draw "
              "test cards, and no pass can be judged against a source. A "
              "worktree has no library of its own -- name one with "
              "--demo-source.", flush=True)
    me = [sys.executable, str(Path(__file__).resolve()),
          "--demo-source", str(SOURCE)]
    main = me + ["--phase", "main", "--dir", str(tour)]
    if args.serve:
        main.append("--serve")
    if args.no_tour:
        main.append("--no-tour")
    subprocess.run(main, cwd=REPO)
    phases = []
    with contextlib.suppress(OSError, ValueError):
        phases.append(json.loads((tour / "phase-main.json").read_text(
            encoding="utf-8")))
    walk = (phases[0]["facts"].get("walk") if phases else None)
    if not args.no_tour:
        if walk:
            before = fingerprint(Path(walk))
            subprocess.run(me + ["--phase", "look", "--dir", str(tour),
                                 "--open-roll", walk], cwd=REPO)
            with contextlib.suppress(OSError, ValueError):
                phases.append(json.loads((tour / "phase-look.json").read_text(
                    encoding="utf-8")))
            untouched_walk = fingerprint(Path(walk)) == before
        else:
            untouched_walk = None
    checks = [c for p in phases for c in p["checks"]]
    for p in phases:
        for err in p["errors"]:
            checks.append({"phase": p["phase"], "step": "anywhere",
                           "what": "no exception inside the window",
                           "status": "fail", "detail": err["tb"][-1500:],
                           "shots": []})
    if not phases:
        checks.append({"phase": "main", "step": "the window", "what":
                       "the tour ran", "status": "fail", "detail":
                       "no phase-main.json: the window did not come up",
                       "shots": []})
    if not args.no_tour:
        checks += after_checks(tour, untouched)
        # A strip deleted in the window comes back by the command its log
        # line printed, once the window has let go of its roll.
        restore = (phases[0]["facts"].get("strip_restore") if phases
                   else None)
        if restore:
            done = subprocess.run([sys.executable, *restore["command"]],
                                  cwd=REPO, capture_output=True, text=True)
            back = Path(restore["strip"])
            checks.append({
                "phase": "after", "step": "a roll of two strips",
                "what": "the printed restore command brings strip 2 back to "
                        "its place once the window has closed",
                "status": "pass" if done.returncode == 0
                and (back / "roll.json").is_file() else "fail",
                "detail": (done.stdout + done.stderr).strip()[-300:],
                "shots": []})
        if untouched_walk is not None:
            checks.append({"phase": "after", "step": "what was left behind",
                           "what": "look-only wrote nothing into the walk",
                           "status": "pass" if untouched_walk else "fail",
                           "detail": walk, "shots": []})
    sheets = contact_sheets(tour)
    report = write_report(tour, checks, phases, sheets)
    failed = sum(c["status"] == "fail" for c in checks)
    warned = sum(c["status"] == "warn" for c in checks)
    print(f"{failed} failed, {warned} warnings, "
          f"{sum(c['status'] == 'pass' for c in checks)} passed")
    print(f"report: {report}")
    return 1 if failed else 0


def send(folder: Path, timeout: float) -> int:
    """Run stdin as Python inside a served window, and print what it printed."""
    commands, answers = folder / "cmd", folder / "answers"
    if not (folder / "serving").exists():
        print(f"{folder} is not being served: start "
              "`tools/gui_tour.py --serve` first", file=sys.stderr)
        return 2
    name = f"{time.time_ns()}"
    staged = commands / f".{name}.tmp"
    staged.write_text(sys.stdin.read(), encoding="utf-8")
    staged.rename(commands / f"{name}.py")
    answer = answers / f"{name}.txt"
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if answer.exists():
            # Through the console's own encoding, replacing what it cannot
            # show: the window's words carry marks (an "i" in a circle, a
            # middle dot) that a cp1252 console refused, and `send` died
            # printing an answer it had got (2026-10-12).
            text = answer.read_text(encoding="utf-8")
            out = getattr(sys.stdout, "encoding", None) or "utf-8"
            sys.stdout.write(text.encode(out, "replace").decode(out))
            sys.stdout.flush()
            return 0
        time.sleep(0.2)
    print(f"no answer in {timeout:.0f} s -- the window may be busy or closed",
          file=sys.stderr)
    return 1


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "send":
        ap = argparse.ArgumentParser(prog="gui_tour.py send")
        ap.add_argument("send")
        ap.add_argument("dir", type=Path)
        ap.add_argument("--timeout", type=float, default=300.0)
        args = ap.parse_args()
        return send(args.dir, args.timeout)
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serve", action="store_true",
                    help="after the tour, keep the main window up and take "
                         "commands (`send`) until one closes it")
    ap.add_argument("--no-tour", action="store_true",
                    help="press nothing: open the window and serve it")
    ap.add_argument("--demo-source", type=Path, default=None,
                    help="the library the demo shows pictures from, and every "
                         "pass is judged against; read only (default: "
                         "library, beside this checkout). A worktree has "
                         "none: name the main checkout's")
    ap.add_argument("--phase", choices=("main", "look"), help=argparse.SUPPRESS)
    ap.add_argument("--dir", type=Path, help=argparse.SUPPRESS)
    ap.add_argument("--open-roll", help=argparse.SUPPRESS)
    args = ap.parse_args()
    global SOURCE
    if args.demo_source is not None:
        # Resolved here, before either phase moves into the checkout.
        SOURCE = args.demo_source.resolve()
    if args.no_tour:
        args.serve = True
    if args.phase:
        return run_phase(args.phase, args.dir, args.open_roll,
                         do_tour=not args.no_tour, keep=args.serve)
    return tour_all(args)


if __name__ == "__main__":
    raise SystemExit(main())
