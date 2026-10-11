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
decided on a 428-column prescan, the width the device returns at 300 dpi: a
pass whose width is a whole multiple of it is averaged down first and its
positions scaled back; any other width is refused, because nothing here was
validated on it. That makes 300 dpi the only walk the detector reads. This
said 600 and 900 dpi were averaged down, and the device's own passes there
are 860 (or 862) and 1292 columns -- neither a multiple -- so every frame of
such a walk was refused, and nothing said why; see `unread_at`.

A reading keeps what it needs to be made again offline: each member's own
answer (``members``), which picture it read (``image_sha1``) and which frames
it was read against, with one digest over their pictures (``against``, or
``against_index`` in a walk, and ``against_sha1``). A walk's record keeps each
frame's summary as well (`WalkReader.observe`). All of it is bookkeeping, so
none of it can cost a reading: what fails is said in ``record_error``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Sequence
from typing import Any

import numpy as np

from rps7200.framing import fill_from_neighbours
from rps7200.protocol import FILM_BW, FILM_NEGATIVE

from . import vote
from .centre import columns_to_mm, decide, frame_columns
from .roll import Summary, summarise, summary_record
from .sides import ALL_BASE, EDGE, NO_FILM, REFUSE, EdgeResult, Side
from .units import FRAME_WIDTH_UNITS, PRESCAN_COLUMNS, units_per_column

#: The film types the detector reads, and the name its members know them by.
#: Slides and Kodachrome are left out: there the gap between frames is opaque
#: -- black on the scan, not clear -- and no member was built for that.
FILM_TYPES = {FILM_NEGATIVE: "c41", FILM_BW: "bw"}
SCALE = int(PRESCAN_COLUMNS)

#: The note a one-vote gap-with-neighbour carries (`vote.lone_gap`).
_LONE = "gap with neighbour, one vote"


#: The prescan resolutions a walk's frames are read at: only where the device
#: is known to return a width `_downscaled` takes. 300 dpi is 428 columns; at
#: 600 dpi it returns 860 or 862 and at 900 dpi 1292, neither a multiple of
#: 428. A resolution belongs here once a pass at it has been read, not by
#: arithmetic -- the device rounds its widths its own way (see
#: `DemoScanner._shape_for`), so a width predicted from the dpi is a guess.
READ_AT_DPI = (300,)


def film_type(film: str | None) -> str | None:
    """The members' name for ``film``, or None when edges are not read on it."""
    return FILM_TYPES.get(str(film or FILM_NEGATIVE))


def unread_at(dpi: int | None, film: str | None) -> str | None:
    """Why a walk prescanned at ``dpi`` will have no frame edges read, or None.

    For saying so *before* the walk: at a resolution the detector cannot read
    every frame is refused one at a time, the sheet's light still goes green,
    and "correct" leaves every frame as it came -- a roll that looked centred
    and was not, with the reason only in each frame's caption. None as well
    for a film the detector does not read at any resolution: that is a
    different sentence (`not_read`), and not a matter of the resolution.
    """
    if film_type(film) is None or dpi is None or int(dpi) in READ_AT_DPI:
        return None
    read = " or ".join(f"{d} dpi" for d in READ_AT_DPI)
    return (f"Frame edges are not read at a {int(dpi)} dpi prescan. The "
            f"detector reads {SCALE}-column prescans -- the scanner's width at "
            f"{read} -- and at {int(dpi)} dpi the width is not a whole "
            f"multiple of that (860 at 600 dpi, 1292 at 900), so every frame "
            f"of this walk will be refused: no positions proposed, and "
            f"\"correct\" will move nothing. Prescan at {read} for those.")


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


def _scaled(result: EdgeResult, k: float) -> EdgeResult:
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


# -- what a stored reading keeps, to be made again ----------------------------

def image_digest(image: np.ndarray) -> str | None:
    """A sha1 over the pixels' dtype, shape and bytes; None where it cannot be had.

    Which picture a reading was made on, so a prescan found later can be
    checked against it before it is read again. Taken over a contiguous copy
    where the array is not one: a pass read bottom-up is turned upright as a
    view, and sha1 refuses a view whose rows run backwards.
    """
    try:
        pixels = np.asarray(image)
        if pixels.dtype.hasobject:
            return None              # its bytes would be addresses, not pixels
        digest = hashlib.sha1(f"{pixels.dtype.str} {pixels.shape}".encode())
        digest.update(np.ascontiguousarray(pixels))
        return digest.hexdigest()
    except Exception:                                             # noqa: BLE001
        return None


def context_digest(pairs: Iterable[tuple[int, str | None]]) -> str | None:
    """One sha1 over the frames a reading was read against, ``(number, image_sha1)``.

    Sorted, so it is the set that counts: the members read their context as
    one (medians and counts), not in the order it arrived. One digest rather
    than one per frame, which would grow with the square of a walk in every
    note: it says whether the context is the one stored, though not which
    frame of it differs.
    """
    try:
        digest = hashlib.sha1()
        for number, image in sorted((int(n), str(d)) for n, d in pairs):
            digest.update(f"{number}:{image}\n".encode())
        return digest.hexdigest()
    except Exception:                                             # noqa: BLE001
        return None


def _record_error(record: dict[str, Any], what: str, exc: BaseException) -> None:
    """Say in ``record`` what its bookkeeping could not keep, beside anything said."""
    said = f"{what}: {type(exc).__name__}: {exc}"
    earlier = record.get("record_error")
    record["record_error"] = f"{earlier}; {said}" if earlier else said


def mark_context(note: dict[str, Any], pairs: Iterable[tuple[int, str | None]],
                 key: str = "against") -> None:
    """Name in ``note`` the frames it was read against, and digest their pictures.

    ``pairs`` is ``(number, image_sha1)`` for each frame whose summary was in
    the reading's context -- frame numbers on a sheet (``against``), the
    transport's indexes in a walk (``against_index``). Never raises.
    """
    try:
        context = sorted((int(n), d) for n, d in pairs)
        note[key] = [n for n, _ in context]
        note["against_sha1"] = context_digest(context)
    except Exception as exc:                                      # noqa: BLE001
        _record_error(note, key, exc)


def _members_note(result: EdgeResult) -> dict[str, dict[str, Any]]:
    """Each member's own ``[state, x]`` per side, at the detector's 428 columns.

    The note kept only the vote's two sides, and the four answers behind
    them lived in `vote.detect`'s debug dict, which nothing stored: a reading
    could not be told from another that voted the same way on different
    answers, nor judged again once a member had changed. ``x`` is None where
    the member placed no edge -- picture to the border, a refusal -- and
    0.0 stays 0.0.
    """
    def side(answer: Any) -> list[Any] | None:
        if not answer:
            return None
        x = answer[1] if len(answer) > 1 else None
        return [str(answer[0]), None if x is None else round(float(x), 3)]

    out: dict[str, dict[str, Any]] = {}
    for role, answer in ((result.debug or {}).get("members") or {}).items():
        entry: dict[str, Any] = {"left": side(answer.get("left")),
                                 "right": side(answer.get("right"))}
        for flag in ("failed", "not_a_negative"):
            if answer.get(flag):
                entry[flag] = str(answer[flag])
        out[str(role)] = entry
    return out


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
    # Decided on the positions at the detector's own scale. `detect` hands a
    # pass averaged down from a whole multiple of 428 back with its positions
    # scaled up, for drawing; deciding on those as though they were 428-column
    # ones moved a left base about 1.8x too far at 856 columns and refused a
    # right one outright.
    k = width // SCALE if width > SCALE and width % SCALE == 0 else 1
    dec = decide(_scaled(result, 1 / k) if k > 1 else result, SCALE,
                 frame_columns(SCALE, frame_units))
    note: dict[str, Any] = {"edges": _edges_note(result), "width": int(width),
                            "reason": dec.why or dec.caption()}
    # Every member's answer, not only the vote's: what the reading has to be
    # judged on again once a member changes. Kept on every way out below,
    # and never at the cost of the reading itself.
    try:
        note["members"] = _members_note(result)
        note["member_columns"] = SCALE
    except Exception as exc:                                      # noqa: BLE001
        _record_error(note, "members", exc)
    # A member that raised abstains (`vote._member`), and said so only in the
    # debug dict nothing read: a member broken on every frame left a quiet
    # three-member vote, a green light and notes naming only the members that
    # agreed. Said here, in the note the sheet shows and a roll keeps.
    abstained = {role: answer["failed"] for role, answer in
                 ((result.debug or {}).get("members") or {}).items()
                 if answer.get("failed")}
    if abstained:
        note["abstained"] = abstained
        note["reason"] += "; abstained: " + "; ".join(abstained.values())
    # A positive read as a negative -- a slide with the film left at its
    # default. Only `stepline` can tell, and the vote counts its refusal as an
    # abstention: the other three read the black gap as picture to the
    # border, and the frame came out "measured" with a move of none. It is
    # not placed at all, and not filled from its neighbours either.
    #
    # Unless an edge was read anyway. An edge is what the sheet draws as the
    # red line, and the line is the automatic position: on 2026-10-07 a B&W
    # strip came back "not a negative" on six frames whose edges the other
    # members drew -- the sheet showed the line, filed a move of none, and
    # the scans kept the black band the line marked. Where a side is an edge,
    # the frame is placed by it, and the doubt is said in the note.
    positive = [answer["not_a_negative"] for answer in
                ((result.debug or {}).get("members") or {}).values()
                if answer.get("not_a_negative")]
    drawn = EDGE in (result.left.state, result.right.state)
    if positive and drawn:
        note["doubt"] = (f"{positive[0]}; placed by the edges read anyway -- "
                         "is the film set right?")
    if positive and not drawn:
        note.update(source="none", reason=(
            f"{positive[0]}; is the film set right? Edges are read on "
            "negatives only"))
        return None, note
    if dec.action == "refuse" or dec.units is None:
        gate = {result.left.state, result.right.state} & {NO_FILM, ALL_BASE}
        # The vote passes no ALL_BASE through: a frame every member calls
        # blank, or blank beside the empty gate, comes out of it as "no
        # agreement". Read from the members, so such a frame is not placed
        # from its neighbours as though the detector had merely disagreed.
        members = ((result.debug or {}).get("members") or {}).values()
        states = {side[0] for answer in members
                  for side in (answer.get("left"), answer.get("right")) if side}
        blank = bool(states) and states <= {NO_FILM, ALL_BASE}
        note["source"] = "none" if gate or blank else "refused"
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
    """``(offset_mm, note)`` for one frame read against ``roll``, the others' summaries.

    The note names the picture read (``image_sha1``); who called this names
    the frames ``roll`` came from (`mark_context`), since only it knows.
    """
    result = detect(image, film=film, roll=roll)
    mm, note = centring(result, int(np.asarray(image).shape[1]),
                        frame_units=frame_units)
    note["image_sha1"] = image_digest(image)
    return mm, note


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
    # One per number, the last one given, as `EdgeWatch.add` keeps them. A
    # roll folder can list a number twice, and read by list position each
    # copy was the other's roll context: its own gap width and base colour
    # confirmed it, where the window, holding one, read it differently.
    frames = list({int(n): (int(n), im) for n, im in frames}.values())
    if film_type(film) is None:
        why = not_read(film)
        return {}, {n: {"source": "none", "reason": why} for n, _ in frames}
    total = 2 * len(frames)
    done = 0
    summaries: list[Summary | None] = []
    #: Each picture's digest, made once beside its summary: what a reading
    #: names its context by.
    digests: list[str | None] = []
    for _, im in frames:
        summaries.append(summary(im))
        digests.append(image_digest(im))
        done += 1
        if progress:
            progress(done, total)
    offsets: dict[int, float] = {}
    notes: dict[int, dict] = {}
    for i, (n, im) in enumerate(frames):
        others = [s for j, s in enumerate(summaries) if j != i and s is not None]
        mm, note = read_frame(im, film=film, roll=others, frame_units=frame_units)
        mark_context(note, [(frames[j][0], digests[j]) for j, s in enumerate(summaries)
                            if j != i and s is not None])
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
    prescan of the frame being held, for the driver's second look, and
    ``reread_note`` is the same with the note it read. One reader per roll:
    its memory is that roll's.

    What each returns is filed in the frame's record, and is enough to make
    every judgment again offline: ``observe`` keeps the frame's summary, and
    a judgment names the indexes it was read against (``against_index``) and
    digests their pictures (``against_sha1``).
    """

    def __init__(self, film: str | None, frame_units: float = FRAME_WIDTH_UNITS):
        self.film = film
        self.frame_units = frame_units
        self._summaries: dict[int, Summary] = {}
        #: Each observed frame's `image_digest`, which a reading's context is
        #: named by.
        self._digests: dict[int, str | None] = {}

    def observe(self, number: int, image: np.ndarray) -> dict[str, Any]:
        """Remember what this frame's prescan tells the others; what to file of it.

        Once per frame, on its prescan as it arrived, before any hold or aim
        moved it: `DirectScanner.scan_roll` is the only caller. It said "a
        replacement prescan replaces", and none ever came -- a hold's
        verification prescans are read for the second look, not observed.
        """
        s = summary(image)
        if s is not None:
            self._summaries[int(number)] = s
        record: dict[str, Any] = {"reader": "frame_edges",
                                  "summarised": len(self._summaries)}
        # The summary every later frame is read against, exactly, and the
        # picture it came from: a judgment made against this frame can be
        # made again from the walk's record alone.
        try:
            digest = image_digest(image)
            self._digests[int(number)] = digest
            record.update(index=int(number), image_sha1=digest)
            record["summary"] = None if s is None else summary_record(s)
        except Exception as exc:                                  # noqa: BLE001
            _record_error(record, "observe", exc)
        return record

    def _read(self, number: int, image: np.ndarray) -> tuple[float | None, dict[str, Any]]:
        context = [(k, s) for k, s in self._summaries.items() if k != int(number)]
        mm, note = read_frame(image, film=self.film, roll=[s for _k, s in context],
                              frame_units=self.frame_units)
        mark_context(note, [(k, self._digests.get(k)) for k, _s in context],
                     key="against_index")
        return mm, note

    def judge(self, number: int, image: np.ndarray) -> tuple[float | None, dict[str, Any]]:
        # The note keeps every member's answer (`centring`). It was emptied
        # here to the strip detector's list shape, and so a walk kept none.
        mm, note = self._read(number, image)
        if mm is not None and note.get("source") == "unconfirmed":
            # One member's gap-with-a-neighbour, which no other contradicts
            # and none confirms. The sheet shows it, labelled, for a person to
            # accept or not; a walk that aims has nobody to ask, and the
            # driver moved film on it as if two members had agreed -- the one
            # thing the ensemble exists to refuse.
            note["reason"] = (f"only one member read it ({note.get('reason')})"
                              "; a walk does not move film on one vote")
            return None, note
        return mm, note

    def reread_note(self, number: int, image: np.ndarray
                    ) -> tuple[float | None, dict[str, Any]]:
        """`reread` with the note it read, which the driver files as the hold's
        second look (`DirectScanner._rejudge_for`)."""
        return self._read(number, image)

    def reread(self, number: int, image: np.ndarray) -> float | None:
        return self._read(number, image)[0]


def walk_reader(film: str | None,
                frame_units: float = FRAME_WIDTH_UNITS) -> WalkReader | None:
    """A fresh reader for one roll of ``film``, or None when edges are not read on it."""
    if film_type(film) is None:
        return None
    return WalkReader(film, frame_units)
