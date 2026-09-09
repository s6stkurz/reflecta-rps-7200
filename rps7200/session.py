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
import queue
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np

from . import library, preview, tiff
from .direct import METER_EACH, DirectScanner
from .library import FilmNotes

#: The infrared floor: a pass with infrared on holds the device this long
#: however few lines were asked for. Measured at 212-227 s across resolutions.
INFRARED_FLOOR_S = 212.0

#: Lines per inch of transport travel, for turning dpi into a line count.
_LINES_PER_DPI = 6888 / 7200

#: How many results keep a full working copy in memory. Beyond this only the
#: filmstrip thumbnail is kept and the preview is re-read from the library
#: entry on demand: at 3600 dpi a working copy is ~8 MB and a roll is 36 of
#: them, which is not a thing to hold for the whole session.
WORKING_COPIES = 12


def estimate_seconds(resolution: int, infrared: bool) -> float:
    """Roughly how long one pass will take, for a progress readout.

    Scan time barely depends on resolution -- the carriage traverse dominates.
    The RGB figure is the measured fit `8 + 0.036 x lines`; the infrared one is
    anchored on 227 s at 900 and 1800 dpi and 334 s at 3600. An estimate, and
    labelled as one wherever it is shown.
    """
    lines = max(1.0, resolution * _LINES_PER_DPI)
    rgb = 8.0 + 0.036 * lines
    if not infrared:
        return rgb
    return max(INFRARED_FLOOR_S, 227.0 + (rgb - 70.0) * 1.7)


# ---------------------------------------------------------------------------
# Jobs -- what the UI can ask for
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Calibrate:
    """Acquire this session's shading reference. Once per power-on."""

    mode: str = "measure"                    # measure | reuse | off
    reference: str = "calibration/shading.npz"


@dataclass(frozen=True)
class Prescan:
    """A 300 dpi RGB framing pass over the whole transport, ~16 s."""

    resolution: int = 300
    notes: FilmNotes = field(default_factory=FilmNotes)
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Scan:
    """One frame at full quality."""

    resolution: int = 1800
    infrared: bool = True
    film: str = "negative"
    auto_exposure: bool = True
    exposure_scale: Any = 1.0
    shading: bool = True
    frame: tuple[int, int, int, int] | None = None
    notes: FilmNotes = field(default_factory=FilmNotes)
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Roll:
    """Walk a strip or roll, one picture at a time."""

    frames: int | None = None
    start_at: int = 1
    resolution: int = 1800
    infrared: bool = True
    film: str = "negative"
    meter: str = METER_EACH
    dry_run: bool = False
    correct: bool = False
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
#: "transport" carries the frame position in `done`, or -1 when it is unknown.
KINDS = ("state", "log", "progress", "result", "filed", "transport",
         "finished", "failed", "closed")


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
            finally:
                self.queue.task_done()

    def _write(self, job: dict) -> None:
        if job.get("path"):
            tiff.write(str(job["path"]), job["image"], resolution=job["dpi"])
        entry = None
        if job["library"]:
            entry = library.save(
                job["image"], job["meta"],
                root=job["library"],
                film=job["film"],
                tags=job["tags"],
                prescan=job["prescan"],
                inquiry=job["inquiry"],
                **job["capture"],
            )
        self.done.append((job["number"], entry))
        if self.on_done is not None:
            self.on_done(job.get("seq", 0), job["number"], entry, None)

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
    ):
        self.root = str(root) if root else None
        self.reference = str(reference)
        self.rolls = Path(rolls)
        self.verbose = verbose
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
            # We file our own entries below. Letting the driver file as well
            # writes every frame twice -- 43 GB of duplicate on a 7200 dpi roll.
            debug=False,
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
            self._scan(job)
        elif isinstance(job, Roll):
            return self._roll(job)
        elif isinstance(job, Move):
            return self._move(job)
        else:
            raise TypeError(f"unknown job {job!r}")
        return None

    # -- jobs --------------------------------------------------------------

    def _calibrate(self, job: Calibrate) -> None:
        summary = self._scanner.ensure_shading(
            Path(job.reference),
            reuse=job.mode == "reuse",
            skip=job.mode == "off",
        )
        self._emit("log", text=summary["summary"])

    def _prescan(self, job: Prescan) -> None:
        image, _ = self._scanner.prescan(resolution=job.resolution, keep_raw=True)
        label = f"prescan {job.resolution} dpi"
        seq = self._deliver(
            "prescan", label, image, {"resolution_dpi": job.resolution}
        )
        # Filed like everything else. A prescan is ~370 KB and it is the
        # evidence about framing that went missing the last time it was not
        # kept; CLAUDE.md's rule is "file every scan", without an exception.
        self._file(
            seq=seq,
            number=0,
            image=image,
            meta={"resolution_dpi": job.resolution, "channel_order": ["R", "G", "B"]},
            notes=job.notes,
            tags=tuple(job.tags) + ("gui", "prescan"),
        )

    def _scan(self, job: Scan) -> None:
        image, meta = self._scanner.scan(
            resolution=job.resolution,
            infrared=job.infrared,
            film=job.film,
            auto_exposure=job.auto_exposure,
            exposure_scale=job.exposure_scale,
            shading=job.shading,
            frame=job.frame,
            keep_raw=True,
        )
        label = f"{job.resolution} dpi {'RGBI' if job.infrared else 'RGB'}"
        seq = self._deliver("scan", label, image, meta)
        self._file(seq, 0, image, meta, job.notes, tuple(job.tags) + ("gui",))

    def _move(self, job: Move) -> str | None:
        """Whole frames, or a sub-frame nudge. Never both in one job."""
        if job.frames:
            step = self._scanner.advance if job.frames > 0 else self._scanner.retreat
            landed = None
            for _ in range(abs(job.frames)):
                landed = step()
                if landed is None:
                    break
                self._emit("transport", done=landed)
            if landed is None:
                return "the film did not move -- it may be at the end of the strip"
            return f"at frame position {landed}"

        if job.millimetres:
            out = self._scanner.nudge(job.millimetres)
            asked = out.get("asked_mm", job.millimetres)
            # The frame counter does not see a sub-frame move, so the position
            # is reported as whatever it still says rather than pretending it
            # changed. Only a prescan can confirm a nudge landed.
            position = self._scanner.position()
            self._emit("transport", done=-1 if position is None else position)
            return (f"nudged {asked:+.2f} mm -- the frame counter does not see "
                    "this; prescan to check it landed")
        return "nothing to move"

    def _roll(self, job: Roll) -> str | None:
        name = job.name or time.strftime("%Y-%m-%d")
        out = Path(job.out) if job.out else self.rolls / name
        out.mkdir(parents=True, exist_ok=True)
        manifest: dict[str, Any] = {
            "roll": name,
            "dpi": job.resolution,
            "infrared": job.infrared,
            "meter": job.meter,
            "film": job.film,
            "dry_run": job.dry_run,
            "start_at": job.start_at,
            "frames": [],
        }

        frames = self._scanner.scan_roll(
            should_stop=self._stop.is_set,
            frames=job.frames,
            resolution=job.resolution,
            infrared=job.infrared,
            film=job.film,
            meter=job.meter,
            skip=max(0, job.start_at - 1),
            max_failures=job.max_failures,
            dry_run=job.dry_run,
            correct=job.correct,
            keep_raw=True,
        )
        stopped = None
        try:
            for rf in frames:
                number = rf.index + 1
                if rf.prescan is not None:
                    seq = self._deliver(
                        "prescan", f"frame {number} prescan", rf.prescan,
                        {"resolution_dpi": 300}, registration=rf.registration,
                    )
                    if job.dry_run:
                        # On a dry run the prescans are the entire product --
                        # there is no frame entry to hang them off, so they are
                        # filed in their own right. On a real roll they ride
                        # along with the frame instead, which is why this is not
                        # unconditional: that would file every one of them twice.
                        self._file(
                            seq, number, rf.prescan,
                            {"resolution_dpi": 300,
                             "channel_order": ["R", "G", "B"]},
                            replace(job.notes,
                                    frame=job.notes.frame or f"{name}-{number:02d}"),
                            tuple(job.tags) + ("gui", "roll", "prescan", name),
                        )
                if rf.error:
                    self._emit("log", text=f"frame {number}: {rf.error}")
                elif rf.image is not None:
                    label = (
                        f"frame {number} · {job.resolution} dpi "
                        f"{'RGBI' if job.infrared else 'RGB'}"
                    )
                    seq = self._deliver(
                        "frame", label, rf.image, rf.meta,
                        registration=rf.registration,
                    )
                    notes = replace(
                        job.notes, frame=job.notes.frame or f"{name}-{number:02d}"
                    )
                    self._file(
                        seq, number, rf.image, rf.meta, notes,
                        tuple(job.tags) + ("gui", "roll", name),
                        prescan=rf.prescan,
                        path=out / f"frame{number:02d}.tif",
                    )

                manifest["frames"].append({
                    "number": number,
                    "index": rf.index,
                    "transport_position": rf.position,
                    "registration": rf.registration,
                    "error": rf.error,
                })
                # Rewritten after every frame. A roll takes hours and a crash
                # should cost the frame it was on, not the roll.
                (out / "roll.json").write_text(json.dumps(manifest, indent=2, default=str))

                if self._stop.is_set():
                    stopped = f"stopped after frame {number}, as asked"
                    self._emit("log", text=stopped)
                    break
        finally:
            # Ends the generator at its yield rather than leaving it suspended
            # with the device half-way through a roll.
            frames.close()
        return stopped

    # -- shared ------------------------------------------------------------

    def _deliver(
        self,
        kind: str,
        label: str,
        image: np.ndarray,
        meta: dict[str, Any],
        registration: dict[str, Any] | None = None,
    ) -> int:
        """Hand the UI a working copy small enough to keep.

        A copy rather than a view, so the full frame can be freed as soon as the
        writer has it -- a strided view would pin all 142 MB of a 3600 dpi pass.
        The copy is a few megabytes and takes milliseconds, which is the only
        reason it is allowed to happen with the device still open.
        """
        working = np.ascontiguousarray(preview.downscale(image, preview.PREVIEW_MAX_SIDE))
        self._seq += 1
        self._emit("result", result=Result(
            seq=self._seq, kind=kind, label=label, image=working, meta=dict(meta),
            registration=dict(registration or {}),
        ))
        return self._seq

    def _file(
        self,
        seq: int,
        number: int,
        image: np.ndarray,
        meta: dict[str, Any],
        notes: FilmNotes,
        tags: tuple[str, ...],
        prescan: np.ndarray | None = None,
        path: Path | None = None,
    ) -> None:
        if self._writer is None:
            return
        capture = self._scanner.capture_record()
        if capture.get("raw") is not None or capture.get("raw_path") is not None:
            shape = image.shape
            layout = capture.get("raw_layout") or {}
            actual = {
                "lines": shape[0],
                "width": shape[1],
                "channels": shape[2] if len(shape) > 2 else 1,
            }
            # Only fields the layout actually declares are judged; an absent one
            # says nothing, and dropping good bytes over it would be its own bug.
            disagree = {
                k: (layout[k], actual[k])
                for k in actual
                if layout.get(k) is not None and layout[k] != actual[k]
            }
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
        self._writer.submit(
            seq=seq,
            number=number,
            path=path,
            image=image,
            meta=meta,
            dpi=meta.get("resolution_dpi"),
            library=self.root,
            film=notes,
            tags=list(tags),
            prescan=prescan,
            inquiry=getattr(self._scanner, "_inquiry", None),
            capture=capture,
        )


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
        n = job.frames if job.frames else "?"
        return f"{what} a roll of {n} frames"
    if isinstance(job, Move):
        if job.frames:
            way = "forward" if job.frames > 0 else "back"
            n = abs(job.frames)
            return f"moving {n} frame{'s' if n != 1 else ''} {way} (~7 s each)"
        return f"nudging the film {job.millimetres:+.2f} mm"
    return str(job)
