"""Where each frame should go: centred positions for a sheet, and for a walk.

Two callers, one detector:

* the **contact sheet** proposes every frame of a walk at once
  (`propose_centred`) -- the same ``(offsets_mm, notes)`` contract as
  `framing.propose_offsets`, so the window, its saved state and
  `approved.json` need nothing new;
* the **in-walk correction** asks frame by frame as the walk goes
  (`walk_reader`), handed to `DirectScanner.scan_roll` so that `rps7200`
  never imports this package.

Each frame is read with the other frames of its walk as context (`roll.py`),
and centred with the measured frame width (`centre.py`). Positions are
decided on a 428-column prescan: a pass whose width is a whole multiple of it
(600, 900 dpi) is averaged down first and its positions scaled back; any
other width is refused, because nothing here was validated on it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import numpy as np

from rps7200.framing import fill_from_neighbours
from rps7200.protocol import FILM_BW, FILM_NEGATIVE

from . import vote
from .centre import columns_to_mm, decide, frame_columns
from .roll import Summary, summarise
from .sides import ALL_BASE, EDGE, NO_FILM, REFUSE, EdgeResult, Side
from .units import FRAME_WIDTH_UNITS, PRESCAN_COLUMNS, units_per_column

#: The film types the detector reads, and the name its members know them by.
#: Slides and Kodachrome are left out: there the gap between frames is opaque
#: -- black on the scan, not clear -- and no member was built for that.
FILM_TYPES = {FILM_NEGATIVE: "c41", FILM_BW: "bw"}
SCALE = int(PRESCAN_COLUMNS)

#: The note a one-vote gap-with-neighbour carries (`vote.lone_gap`).
_LONE = "gap with neighbour, one vote"


def film_type(film: str | None) -> str | None:
    """The members' name for ``film``, or None when edges are not read on it."""
    return FILM_TYPES.get(str(film or FILM_NEGATIVE))


def _downscaled(image: np.ndarray) -> tuple[np.ndarray, int] | None:
    """``(float32 image at 428 columns, factor)``, or None when it is not a multiple."""
    img = np.asarray(image).astype(np.float32)
    if img.ndim != 3 or img.shape[2] < 3:
        return None
    img = img[..., :3]
    w = img.shape[1]
    if w == SCALE:
        return img, 1
    if w % SCALE:
        return None
    k = w // SCALE
    h = (img.shape[0] // k) * k
    small = img[:h].reshape(h // k, k, SCALE, k, 3).mean(axis=(1, 3))
    return small.astype(np.float32), k


def _scaled(result: EdgeResult, k: int) -> EdgeResult:
    if k == 1:
        return result

    def up(s: Side) -> Side:
        return Side(s.state, *(None if v is None else v * k
                               for v in (s.x, s.lo, s.hi)),
                    conf=s.conf, outer=None if s.outer is None else s.outer * k,
                    x_top=None if s.x_top is None else s.x_top * k,
                    x_bottom=None if s.x_bottom is None else s.x_bottom * k, note=s.note)
    return EdgeResult(up(result.left), up(result.right), result.debug)


def _refused(why: str) -> EdgeResult:
    return EdgeResult(Side(REFUSE, note=why), Side(REFUSE, note=why))


def detect(image: np.ndarray, *, film: str | None, roll: Sequence[Summary] = (),
           dtype: str | None = None) -> EdgeResult:
    """Both edges of one prescan, in its own columns.

    ``roll`` holds the summaries of the *other* frames of its walk (`roll.py`);
    empty is allowed, and is what a single prescan gets.
    """
    ft = film_type(film)
    if ft is None:
        return _refused(f"edges are read on negatives only; this film is {film}")
    prepared = _downscaled(image)
    if prepared is None:
        return _refused(f"a {np.asarray(image).shape[1]}-column pass is not a multiple of "
                        f"the {SCALE}-column prescan the detector knows")
    small, k = prepared
    ctx = {"film_type": ft, "dtype": dtype or str(np.asarray(image).dtype),
           "roll": tuple(roll)}
    return _scaled(vote.detect(small, ctx), k)


def summary(image: np.ndarray, dtype: str | None = None) -> Summary | None:
    """What this prescan tells the others of its walk, at the detector's scale."""
    prepared = _downscaled(image)
    if prepared is None:
        return None
    return summarise(prepared[0], dtype or str(np.asarray(image).dtype))


def _edges_note(result: EdgeResult) -> dict[str, Any]:
    return {name: {"state": s.state, "x": s.x, "x_top": s.x_top, "x_bottom": s.x_bottom,
                   "outer": s.outer, "note": s.note}
            for name, s in (("left", result.left), ("right", result.right))}


def centring(result: EdgeResult, width: int, *,
             frame_units: float = FRAME_WIDTH_UNITS) -> tuple[float | None, dict[str, Any]]:
    """``(offset_mm, note)`` that centres a frame read as ``result``, or ``(None, note)``.

    The note carries ``source`` in the window's vocabulary -- ``measured`` when
    the move rests on sides at least two members agree on (or on picture to
    the border both sides, which is a move of none), ``unconfirmed`` when it
    rests on one member's gap-with-neighbour -- plus ``units``, ``columns``,
    ``reason`` and the ``edges`` themselves for drawing.
    """
    scale = width / SCALE
    dec = decide(result, SCALE, frame_columns(SCALE, frame_units))
    note: dict[str, Any] = {"edges": _edges_note(result), "width": int(width),
                            "reason": dec.why or dec.caption()}
    if dec.action == "refuse" or dec.units is None:
        gate = {result.left.state, result.right.state} & {NO_FILM, ALL_BASE}
        note["source"] = "none" if gate else "refused"
        return None, note
    units = 0.0 if dec.action == "none" else float(dec.units)
    columns = units / units_per_column(SCALE) * scale
    used = [s for s in (result.left, result.right) if s.state == EDGE]
    lone = any(s.note.startswith(_LONE) for s in used)
    note.update(source="unconfirmed" if lone else "measured",
                units=round(units, 3), columns=round(columns, 3))
    return columns_to_mm(columns, width), note


def read_frame(image: np.ndarray, *, film: str | None, roll: Sequence[Summary] = (),
               frame_units: float = FRAME_WIDTH_UNITS) -> tuple[float | None, dict[str, Any]]:
    """``(offset_mm, note)`` for one frame read against ``roll``, the others' summaries."""
    result = detect(image, film=film, roll=roll)
    return centring(result, int(np.asarray(image).shape[1]), frame_units=frame_units)


def fill_refused(offsets: dict[int, float], notes: dict[int, dict]) -> None:
    """Give the frames the detector refused the strip's line, in place, or ``none``."""
    unplaced = [n for n, note in notes.items() if note["source"] == "refused"]
    if not unplaced:
        return
    filled = fill_from_neighbours(offsets, unplaced)
    for n in unplaced:
        if n in filled:
            offsets[n] = filled[n]
            notes[n].update(source="neighbours", reason=(
                f"the detector refused ({notes[n]['reason']}); filled from the "
                "line through the frames it placed"))
        else:
            notes[n]["source"] = "none"


def not_read(film: str | None) -> str:
    """Why nothing is proposed on ``film``."""
    return f"edges are read on negatives only; this roll is {film}"


def propose_centred(frames: Sequence[tuple[int, np.ndarray]], *, film: str | None,
                    frame_units: float = FRAME_WIDTH_UNITS,
                    progress: Callable[[int, int], None] | None = None,
                    ) -> tuple[dict[int, float], dict[int, dict]]:
    """Centred positions for a whole walk: ``(offsets_mm, notes)`` by frame number.

    The same contract as `framing.propose_offsets`. ``notes[n]["source"]`` is
    one of ``measured``, ``unconfirmed``, ``neighbours`` (the detector refused
    and the strip's line through the placed frames filled it) and ``none``
    (no film, a blank frame, or a film the detector does not read).
    ``progress(done, total)`` is called as frames are read. `watch.EdgeWatch`
    reaches the same answer a frame at a time, as a walk delivers them.
    """
    frames = [(int(n), im) for n, im in frames]
    if film_type(film) is None:
        why = not_read(film)
        return {}, {n: {"source": "none", "reason": why} for n, _ in frames}
    total = 2 * len(frames)
    done = 0
    summaries: list[Summary | None] = []
    for _, im in frames:
        summaries.append(summary(im))
        done += 1
        if progress:
            progress(done, total)
    offsets: dict[int, float] = {}
    notes: dict[int, dict] = {}
    for i, (n, im) in enumerate(frames):
        others = [s for j, s in enumerate(summaries) if j != i and s is not None]
        mm, note = read_frame(im, film=film, roll=others, frame_units=frame_units)
        notes[n] = note
        if mm is not None:
            offsets[n] = mm
        done += 1
        if progress:
            progress(done, total)
    fill_refused(offsets, notes)
    return offsets, notes


class WalkReader:
    """The detector for one walk, frame by frame, as `StripWalk` asks.

    ``observe`` remembers what each prescan tells the others; ``judge`` reads
    a frame against every *other* frame seen so far; ``reread`` reads a fresh
    prescan of the frame being held, for the driver's second look. One reader
    per roll: its memory is that roll's.
    """

    def __init__(self, film: str | None, frame_units: float = FRAME_WIDTH_UNITS):
        self.film = film
        self.frame_units = frame_units
        self._summaries: dict[int, Summary] = {}

    def observe(self, number: int, image: np.ndarray) -> dict[str, Any]:
        s = summary(image)
        if s is not None:
            self._summaries[int(number)] = s     # a replacement prescan replaces
        return {"reader": "frame_edges", "summarised": len(self._summaries)}

    def _read(self, number: int, image: np.ndarray) -> tuple[float | None, dict[str, Any]]:
        others = [s for k, s in self._summaries.items() if k != int(number)]
        result = detect(image, film=self.film, roll=others)
        return centring(result, int(np.asarray(image).shape[1]),
                        frame_units=self.frame_units)

    def judge(self, number: int, image: np.ndarray) -> tuple[float | None, dict[str, Any]]:
        mm, note = self._read(number, image)
        note["members"] = []           # the shape StripWalk's detail carries
        return mm, note

    def reread(self, number: int, image: np.ndarray) -> float | None:
        return self._read(number, image)[0]


def walk_reader(film: str | None,
                frame_units: float = FRAME_WIDTH_UNITS) -> WalkReader | None:
    """A fresh reader for one roll of ``film``, or None when edges are not read on it."""
    if film_type(film) is None:
        return None
    return WalkReader(film, frame_units)
