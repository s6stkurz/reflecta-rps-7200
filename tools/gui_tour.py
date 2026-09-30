#!/usr/bin/env python3
"""A scripted tour of the demo window: every control pressed, every frame judged.

    uv run python tools/gui_tour.py                   # the tour, then report.md
    uv run python tools/gui_tour.py --serve           # ... the window stays up
    uv run python tools/gui_tour.py --serve --no-tour # just the window, served
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
  invented fill looks like when it slips in.
- **fit against full resolution**: what the window shows at fit and what it
  loads when zoomed in are the same pass.
- **the rail**: how much sits at 0 or full scale, beside how much sat there in
  the stored source, so a clipped source is told from a clipping change.

It writes only under ``demo/tour/<UTC time>/``, which is ignored: its library,
rolls, calibration, settings and deliveries, the pictures it captured from the
window (``shots/``, and ``contact-*.png`` sheets of them) and ``report.md``.
It checks afterwards that `library/`, `library 2/` and `demo/rolls/` were not
touched. Every dialog is answered by script and recorded with its words.

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


def frame_problems(image: Any, rail: bool = True,
                   band: int = BAND_COLUMNS) -> tuple[list[str], dict]:
    """What is wrong with a picture as a picture, and the numbers behind it."""
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
        if max(left, right) >= band:
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


def bit_true(entry: Path) -> tuple[str, str, dict]:
    """Whether a demo entry's raw pixels are its stored source's, and nothing else.

    ``(status, detail, facts)``; status is pass, fail, warn or skip.
    """
    from rps7200 import library
    from rps7200.demo import CORRECTED_WHEN_STORED, _at_depth, libraries_beside

    record = json.loads((entry / "scan.json").read_text(encoding="utf-8"))
    source = _find_key(record, "demo_source") or {}
    fit = _find_key(record, "demo_fit") or {}
    if not source.get("entry"):
        return "skip", "drawn from no stored entry (a test card)", {}
    if CORRECTED_WHEN_STORED in json.dumps(record):
        return "skip", "its source was stored corrected: no raw pixels", {}
    name = str(source["entry"])
    where = Path(name) if Path(name).is_dir() else None
    for lib in libraries_beside("library"):
        if where is None and (Path(lib) / Path(name).name).is_dir():
            where = Path(lib) / Path(name).name
    if where is None:
        return "warn", f"source {name} not found beside library/", {}
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

    def idle(self, timeout: float = 180.0) -> bool:
        """Until the scanner has nothing running or queued."""
        self.settle(5)
        end = time.monotonic() + timeout
        while (self.app.busy or self.app._queued) and time.monotonic() < end:
            self.root.update()
            time.sleep(0.03)
        self.settle(5)
        return not (self.app.busy or self.app._queued)

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
        out = []
        for w in self.walk(top, into_windows=top is not None):
            if w.winfo_class() in ("TButton", "Button", "TCheckbutton",
                                   "Checkbutton", "TRadiobutton",
                                   "Radiobutton") and w.winfo_ismapped():
                out.append(w)
        return out

    def press(self, label: str, top=None):
        for w in self.buttons(top):
            if self.text(w) == label:
                w.invoke()
                return w
        raise LookupError(f"no button {label!r}; there are "
                          f"{sorted({self.text(b) for b in self.buttons(top)})}")

    def state_of(self, label: str, top=None) -> str | None:
        for w in self.buttons(top):
            if self.text(w) == label:
                return str(w.cget("state"))
        return None

    def select_roll(self, browser_window, folder: str) -> None:
        """Select a roll's row in the Rolls window, by its folder's name."""
        tree = next(w for w in self.walk(browser_window)
                    if w.winfo_class() == "Treeview")
        for item in tree.get_children():
            shown = str(tree.item(item)["values"][0])
            if shown == folder or shown.startswith(folder + "  ("):
                tree.selection_set(item)
                self.settle(2)
                return
        raise LookupError(f"no row for {folder}")

    def toplevels(self) -> list:
        import tkinter as tk
        return [w for w in self.walk() if isinstance(w, tk.Toplevel)
                and w.winfo_exists()]

    def top(self, title: str, timeout: float = 5.0):
        def found():
            return [t for t in self.toplevels() if title in t.title()]
        if not self.until(lambda: bool(found()), timeout):
            raise LookupError(f"no window titled like {title!r}; open: "
                              f"{[t.title() for t in self.toplevels()]}")
        return found()[0]

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
        return names

    def shot_array(self, name: str) -> np.ndarray:
        from PIL import Image
        return np.asarray(Image.open(self.shots / name).convert("RGB"))

    def judge_shots(self, step: str, names: list[str]) -> None:
        for name in names:
            problems, facts = frame_problems(self.shot_array(name), rail=False,
                                             band=SHOT_BAND_COLUMNS)
            self.check(step, f"{name} on screen has no band and is not blank",
                       not problems, "; ".join(problems) or
                       f"spread {facts.get('spread')}", [name])

    def judge_result(self, step: str, r, full: bool = True) -> None:
        """One pass the window holds: the picture, its entry, its bytes."""
        label = f"{r.label} (#{r.seq})"
        problems, facts = frame_problems(r.image)
        self.check(step, f"{label} is a picture", not problems,
                   "; ".join(problems) or f"spread {facts.get('spread')}")
        entry = getattr(r, "entry", None)
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
        try:
            body()
        except Exception:                                # noqa: BLE001
            self.check(name, "the step ran to its end", False,
                       traceback.format_exc(limit=6))
        for err in ERRORS[errors:]:
            self.check(name, "no exception inside the window", False,
                       err["tb"][-1500:])
        stray = [t.title() for t in self.toplevels()
                 if t.title() not in ("Contact sheet",)]
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
        s("the transport", self._transport)
        s("presets and settings", self._settings)
        s("a walk that aims each frame", self._walk)
        s("the contact sheet", self._sheet)
        s("scanning from the sheet", self._scan_chosen)
        s("a roll stopped part way", self._stopped_roll)
        s("the rolls browser", self._browser)
        s("force abort", self._abort)
        self.facts["results"] = len(app.results)

    def run_look(self) -> None:
        self.step("look-only: the stored walk", self._look)

    # -- main-phase steps ---------------------------------------------------

    def _opens(self) -> None:
        name = "the window opens"
        self.settle(20)
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
        self.judge_result(name, r, full=False)
        entry, count = r.entry, len(app.results)
        n = len(DIALOGS)
        app.on_delete(r)
        self.settle()
        asked = [d["title"] for d in self.dialogs_since(n)]
        self.check(name, "delete asks twice before an entry goes",
                   len(asked) == 2, str(asked))
        self.check(name, "the pass and its entry are gone",
                   len(app.results) == count - 1 and not entry.exists())

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
        self.settle()
        before = len(app.results)
        folder = Path(app._sheet_roll)
        mark = self.mark()
        self.press("Scan chosen frames", top)
        self.check(name, "the two frames are scanned", self.idle(300))
        said = self.since(mark)
        self.check(name, "no pass was filed without its own bytes",
                   not any("later pass" in line for line in said))
        written = sorted(p.name for p in folder.glob("frame*.tif"))
        self.check(name, "frame02 and frame04 are written",
                   written == ["frame02.tif", "frame04.tif"], str(written))
        self.check(name, "approved.json records the commission",
                   (folder / "approved.json").exists())
        for r in app.results[before:]:
            self.judge_result(name, r, full=r.kind == "prescan")

    def _stopped_roll(self) -> None:
        name = "a roll stopped part way"
        app = self.app
        app.fields["roll"].set("tour-roll")
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
        folder = Path(app.session.rolls) / "tour-roll"
        self.facts["stopped_roll"] = str(folder)
        written = sorted(p.name for p in folder.glob("frame*.tif"))
        self.check(name, "only frame 1 is written", written == ["frame01.tif"],
                   str(written))

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

        select("tour-roll")
        browser._duplicate()
        self.settle(10)
        self.check(name, "Duplicate copies the folder",
                   (rolls / "tour-roll-2").is_dir(),
                   str(sorted(p.name for p in rolls.iterdir())))
        ANSWERS["askstring"] = "tour-renamed"
        select("tour-roll-2")
        browser._rename()
        self.settle(5)
        self.check(name, "Rename renames the folder",
                   (rolls / "tour-renamed").is_dir()
                   and not (rolls / "tour-roll-2").exists())
        n = len(DIALOGS)
        select("tour-renamed")
        browser._delete()
        self.settle(5)
        asked = [d["message"] for d in self.dialogs_since(n)
                 if d["title"] == "Delete"]
        self.check(name, "Delete names the folder that goes",
                   bool(asked) and "tour-renamed" in asked[0],
                   asked[0][:120] if asked else "no question")
        self.check(name, "and it goes", not (rolls / "tour-renamed").exists())
        select("tour-roll")
        count = len(list(Path(ANSWERS["askdirectory"]).glob("*")))
        browser._export()
        self.until(lambda: not app._saving, 120)
        self.settle(10)
        self.check(name, "Export writes the roll's frames",
                   len(list(Path(ANSWERS["askdirectory"]).glob("*"))) > count)
        n = len(DIALOGS)
        select("tour-roll")
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
        ANSWERS["askstring"] = "ABORT"
        mark = self.mark()
        self.press("Scan")
        self.until(lambda: app.busy and any(
            "3600 dpi" in line for line in self.since(mark)), 60)
        self.press("Force abort")
        self.settle(40)
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
    watched = ["library", "library 2", str(Path(gui.DEMO_ROOT) / "rolls")]
    untouched = {name: fingerprint(REPO / name) for name in watched}
    print(f"tour: {tour}", flush=True)
    me = [sys.executable, str(Path(__file__).resolve())]
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
            print(answer.read_text(encoding="utf-8"), end="")
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
    ap.add_argument("--phase", choices=("main", "look"), help=argparse.SUPPRESS)
    ap.add_argument("--dir", type=Path, help=argparse.SUPPRESS)
    ap.add_argument("--open-roll", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.no_tour:
        args.serve = True
    if args.phase:
        return run_phase(args.phase, args.dir, args.open_roll,
                         do_tour=not args.no_tour, keep=args.serve)
    return tour_all(args)


if __name__ == "__main__":
    raise SystemExit(main())
