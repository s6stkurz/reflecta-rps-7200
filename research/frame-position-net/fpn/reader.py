"""The network as the software's edge reader -- the same interface, not wired in.

`tools/frame_edges` is what the window and `tools/scan_roll.py` hand the driver
(`session.edge_reader = walk_reader`). This offers the same names with the
network reading each prescan instead of the four-member vote:

    walk_reader(film)          -> a reader for one roll, or None
        .observe(n, image)        nothing to remember: one prescan is read alone
        .judge(n, image)          (offset_mm, note): the move the hold loop makes
        .reread(n, image)         the driver's second look after a move
    propose_centred(frames, film=...)   a sheet's positions, refused ones filled
    detect(image, film=...)             the EdgeResult itself

Everything after the edges is `tools/frame_edges`' own: `centring` turns them
into `offset_mm` on the hold loop's scale, its `decide` into units, the driver's
`param_for_mm` into the SLIDE param. Positives are read (the vote reads
negatives only); Kodachrome is not.

The weights are local, never in a checkout: `FPN_WEIGHTS`, else
local/weights/current.npz. Without them `walk_reader` says None, as it does for
a film it does not read, and the driver aims as it did before.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from rps7200.framing import FRAME_WIDTH_UNITS
from tools.frame_edges import propose
from tools.frame_edges.sides import REFUSE, EdgeResult, Side

from fpn import paths
from fpn.inputs import COLUMNS

FILM_TYPES = {"negative": "negative", "bw": "bw", "positive": "positive"}
READ_AT_DPI = propose.READ_AT_DPI

_net: Any = None


def weights_path() -> Path | None:
    path = Path(os.environ.get("FPN_WEIGHTS") or paths.LOCAL / "weights" / "current.npz")
    return path if path.exists() else None


def _the_net() -> Any:
    global _net
    if _net is None:
        from fpn.net import Net        # torch is imported only when a frame is read

        path = weights_path()
        if path is None:
            raise FileNotFoundError("no weights: set FPN_WEIGHTS or train one")
        _net = Net(path)
    return _net


def film_type(film: str | None) -> str | None:
    return FILM_TYPES.get(film or "")


def detect(image: np.ndarray, *, film: str | None, roll: Sequence[Any] = (),
           dtype: str | None = None) -> EdgeResult:
    """Both edges of one 300 dpi prescan. ``roll`` is accepted and not used."""
    kind = film_type(film)
    if kind is None:
        why = f"the network does not read {film}"
        return EdgeResult(Side(REFUSE, note=why), Side(REFUSE, note=why))
    image = np.asarray(image)
    if image.ndim != 3 or image.shape[1] != COLUMNS:
        why = f"a {image.shape[1] if image.ndim > 1 else '?'}-column pass is not a 300 dpi prescan"
        return EdgeResult(Side(REFUSE, note=why), Side(REFUSE, note=why))
    result, _ = _the_net().detect(image, film=kind, dtype=dtype or str(image.dtype))
    return result


def read_frame(image: np.ndarray, *, film: str | None,
               frame_units: float = FRAME_WIDTH_UNITS) -> tuple[float | None, dict[str, Any]]:
    result = detect(image, film=film)
    mm, note = propose.centring(result, int(np.asarray(image).shape[1]), frame_units=frame_units)
    note["reader"] = "frame-position-net"
    return mm, note


def propose_centred(frames: Sequence[tuple[int, np.ndarray]], *, film: str | None,
                    frame_units: float = FRAME_WIDTH_UNITS,
                    progress: Callable[[int, int], None] | None = None,
                    ) -> tuple[dict[int, float], dict[int, dict]]:
    """A walk's centred positions by frame number -- `propose.propose_centred`'s contract."""
    frames = list({int(n): (int(n), im) for n, im in frames}.values())
    if film_type(film) is None:
        return {}, {n: {"source": "none", "reason": f"the network does not read {film}"}
                    for n, _ in frames}
    offsets: dict[int, float] = {}
    notes: dict[int, dict] = {}
    for done, (n, im) in enumerate(frames, 1):
        mm, note = read_frame(im, film=film, frame_units=frame_units)
        notes[n] = note
        if mm is not None:
            offsets[n] = mm
        if progress:
            progress(done, len(frames))
    propose.fill_refused(offsets, notes)
    return offsets, notes


class NetWalkReader(propose.WalkReader):
    """`propose.WalkReader` with the network reading each prescan."""

    def observe(self, number: int, image: np.ndarray) -> dict[str, Any]:
        return {"reader": "frame-position-net"}

    def _read(self, number: int, image: np.ndarray) -> tuple[float | None, dict[str, Any]]:
        return read_frame(image, film=self.film, frame_units=self.frame_units)


def walk_reader(film: str | None,
                frame_units: float = FRAME_WIDTH_UNITS) -> NetWalkReader | None:
    """A reader for one roll of ``film``; None for a film it does not read, or no weights."""
    if film_type(film) is None or weights_path() is None:
        return None
    return NetWalkReader(film, frame_units)
