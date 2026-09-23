"""A walk's frame edges, read in the background as its prescans arrive.

The contact sheet used to start the detector when it was opened: a third of a
second a frame, a dozen seconds for a roll, spent after the walk had already
finished and while Stefan waited for the sheet. The prescans exist long before
that -- each lands twenty seconds after the last on a real walk, and a stored
walk is on disk before the window asks for it -- so this reads them then.

One thread, one walk at a time. The answer is `propose_centred`'s exactly: each
frame read against the summaries of every *other* frame of its walk, in the
order the frames arrived, and the refused ones filled from the strip's line.
A walk still coming in cannot give that yet, so each frame is read as it
lands against the frames before it, and once the walk has ended every frame
whose context has grown since is read again. Nothing waits on any of it:
`progress()` says how far it has got, and whoever is showing the answer takes
the newer one when it changes.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .propose import fill_refused, film_type, not_read, read_frame, summary
from .roll import Summary
from .units import FRAME_WIDTH_UNITS

#: What the window's light says. ``idle``: no walk. ``reading``: frames are
#: being read, or read again against the whole walk. ``done``: every frame has
#: its final reading. ``failed``: the detector raised on a frame -- that frame
#: has no position and says why. ``skipped``: the film is one the detector
#: does not read (slides, Kodachrome).
IDLE, READING, DONE, FAILED, SKIPPED = "idle", "reading", "done", "failed", "skipped"


@dataclass(frozen=True)
class Progress:
    """How far the walk has been read, and what it says so far."""

    generation: int
    state: str
    #: frames with a reading, final or not
    done: int
    #: frames expected: announced by the walk, or those that have arrived
    total: int
    #: ``propose_centred``'s ``(offsets_mm, notes)`` over the frames read so far
    offsets: dict[int, float] = field(default_factory=dict)
    notes: dict[int, dict] = field(default_factory=dict)
    errors: tuple[str, ...] = ()
    film: str | None = None


@dataclass
class _Frame:
    image: np.ndarray
    version: int
    summary: Summary | None = None
    summarised: int = -1                     # the version `summary` was made from
    #: the reading, the version it was made from, and the others it was read
    #: against -- ``((number, version), ...)`` in arrival order
    reading: tuple[float | None, dict[str, Any]] | None = None
    read: int = -1
    against: tuple[tuple[int, int], ...] | None = None


class EdgeWatch:
    """Reads one walk's frames on its own thread, as they arrive.

    ``begin`` starts a walk (and forgets the last one), ``add`` hands it a
    prescan, ``finish`` says no more are coming, and ``load`` is all three for
    a walk already on disk. ``progress`` is safe to call from any thread and
    cheap enough to call on every tick of a window's pump; ``version`` changes
    whenever it would say something new.
    """

    def __init__(self, frame_units: float = FRAME_WIDTH_UNITS):
        self.frame_units = frame_units
        self._lock = threading.Condition()
        self._frames: dict[int, _Frame] = {}
        self._film: str | None = None
        self._expected: int | None = None
        self._finished = False
        self._errors: dict[int, str] = {}
        self._generation = 0
        self._version = 0
        self._busy = False
        self._closed = False
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="frame-edges")
        self._thread.start()

    # -- the caller's side -------------------------------------------------

    def begin(self, film: str | None, expected: int | None = None) -> int:
        """A new walk of ``film``, ``expected`` frames long if that is known."""
        with self._lock:
            self._generation += 1
            self._frames = {}
            self._errors = {}
            self._film = film
            self._expected = int(expected) if expected else None
            self._finished = False
            self._changed()
            return self._generation

    def add(self, number: int, image: np.ndarray) -> None:
        """A prescan of frame ``number``. A second one of the same frame replaces it."""
        if image is None:
            return
        with self._lock:
            old = self._frames.get(int(number))
            version = old.version + 1 if old is not None else 0
            if old is not None:
                old.image, old.version = image, version   # keeps its place
            else:
                self._frames[int(number)] = _Frame(image, version)
            self._errors.pop(int(number), None)
            self._changed()

    def finish(self) -> None:
        """The walk has ended: read every frame against all the others."""
        with self._lock:
            self._finished = True
            self._changed()

    def load(self, frames: Sequence[tuple[int, np.ndarray]], film: str | None) -> int:
        """A walk that is already complete, such as one read back from disk."""
        with self._lock:
            generation = self.begin(film, len(frames))
            for number, image in frames:
                self.add(number, image)
            self.finish()
            return generation

    @property
    def version(self) -> int:
        return self._version

    @property
    def generation(self) -> int:
        return self._generation

    def progress(self) -> Progress:
        with self._lock:
            return self._progress()

    def wait(self, timeout: float | None = None) -> bool:
        """Until nothing is left to read. For tests and tools, never a window."""
        with self._lock:
            return self._lock.wait_for(
                lambda: not self._busy and self._next() is None, timeout)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._lock.notify_all()

    # -- the thread --------------------------------------------------------

    def _changed(self) -> None:
        self._version += 1
        self._lock.notify_all()

    def _others(self, number: int) -> list[tuple[int, _Frame]]:
        return [(k, f) for k, f in self._frames.items()
                if k != number and f.summarised == f.version]

    def _next(self):
        """The next piece of work, or None. Called with the lock held.

        Every summary first: a summary is what the other frames are read
        against, so a frame read before its neighbours are summarised would
        only have to be read again. Then every frame with no reading at all,
        against what has arrived -- a sheet already open shows nothing for
        those; and once the walk has ended, every frame whose reading predates
        part of it.
        """
        if self._closed or film_type(self._film) is None:
            return None
        for number, f in self._frames.items():
            if f.summarised != f.version:
                return ("summary", number, f.image, f.version, None)
        for again in (False, True):
            if again and not self._finished:
                break
            for number, f in self._frames.items():
                others = self._others(number)
                against = tuple((k, o.version) for k, o in others)
                stale = f.against != against if again else f.read != f.version
                if stale:
                    roll = [o.summary for _k, o in others if o.summary is not None]
                    return ("read", number, f.image, f.version, (roll, against))
        return None

    def _run(self) -> None:
        while True:
            with self._lock:
                self._lock.wait_for(lambda: self._closed or self._next() is not None)
                if self._closed:
                    return
                task = self._next()
                if task is None:
                    continue
                kind, number, image, version, extra = task
                generation, film = self._generation, self._film
                self._busy = True
            outcome: Any = None
            error = None
            try:
                if kind == "summary":
                    outcome = summary(image)
                else:
                    outcome = read_frame(image, film=film, roll=extra[0],
                                         frame_units=self.frame_units)
            except Exception as exc:                          # noqa: BLE001
                error = f"frame {number}: {type(exc).__name__}: {exc}"
            with self._lock:
                self._busy = False
                f = self._frames.get(number)
                if (generation != self._generation or f is None
                        or f.version != version):
                    self._changed()            # superseded: look again
                    continue
                if error is not None:
                    self._errors[number] = error
                    # A frame the detector cannot read is still a frame: it is
                    # given its answer, which is none, so the rest go on.
                    if kind == "summary":
                        f.summary, f.summarised = None, version
                    else:
                        outcome = (None, {"source": "none",
                                          "reason": f"the detector failed: {error}"})
                if kind == "summary":
                    if error is None:
                        f.summary, f.summarised = outcome, version
                else:
                    f.reading, f.read, f.against = outcome, version, extra[1]
                self._changed()

    def _progress(self) -> Progress:
        numbers = list(self._frames)
        total = max(self._expected or 0, len(numbers))
        if film_type(self._film) is None:
            why = not_read(self._film)
            return Progress(self._generation, SKIPPED if numbers else IDLE, 0, total,
                            {}, {n: {"source": "none", "reason": why} for n in numbers},
                            film=self._film)
        offsets: dict[int, float] = {}
        notes: dict[int, dict] = {}
        for number, f in self._frames.items():
            if f.reading is None or f.read != f.version:
                continue
            mm, note = f.reading
            notes[number] = dict(note)
            if mm is not None:
                offsets[number] = mm
        fill_refused(offsets, notes)
        if not numbers:
            state = IDLE
        elif self._busy or self._next() is not None or not self._finished:
            state = READING
        else:
            state = FAILED if self._errors else DONE
        return Progress(self._generation, state, len(notes), total, offsets, notes,
                        tuple(self._errors.values()), self._film)
