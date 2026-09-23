"""Shared ground for the frame-edge study: data, results, units, the correction.

Everything in this folder speaks the same coordinates, and they are these:

* An image is ``(H, W, 3)`` float32 in the **native counts** of the file it
  came from -- a uint8 prescan stays 0..255, a uint16 one 0..65535. Nothing is
  normalised on load, because the scale differs by a factor of hundreds across
  the library and a detector that quietly depends on it is the thing to catch.
* A **position is a boundary between columns**, a float. ``x = 12.0`` is the
  line between column 11 and column 12.
* The **left** answer is where the picture *starts*: base occupies ``[0, x)``,
  so ``b_L = x`` columns of base show. The **right** answer is where the
  picture *ends*: base occupies ``[x, W)``, so ``b_R = W - x``.
* Transport units: one unit is ``COLUMNS_PER_UNIT`` columns of a 428-wide
  prescan. **Positive units move the picture to higher columns** -- forward,
  ``SLIDE 0x00`` -- which is what black at the right asks for.

This module is the orchestrator's; detectors import it and do not edit it.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
#: Where a dataset lives: its triage, frames, labels, truth and results. This
#: folder by default; `FRAME_EDGE_WORK` points the same scripts at a second
#: dataset (another library) in its own folder, so the two never mix.
WORK = Path(os.environ.get("FRAME_EDGE_WORK") or HERE).resolve()
#: The library the dataset is built from: `library/` unless `FRAME_EDGE_LIBRARY`.
LIBRARY = Path(os.environ.get("FRAME_EDGE_LIBRARY") or HERE.parents[1] / "library").resolve()
DATA = WORK / "data"
FRAMES = DATA / "frames"
MANIFEST = DATA / "manifest.json"
GROUND_TRUTH = WORK / "ground_truth.json"
#: The test split's truth, kept out of `ground_truth.json` so that nobody
#: working on a detector sees it by printing the dev file. Read only by the
#: final test scoring.
GROUND_TRUTH_TEST = WORK / "locked" / "ground_truth_test.json"
RESULTS = WORK / "results"

#: From `docs/frame-measurement-plan.md`: one SLIDE unit is 1.2423 columns of a
#: 300 dpi, 428-column prescan.
COLUMNS_PER_UNIT = 1.2423
PRESCAN_COLUMNS = 428
#: A command travels ``param + COMMAND_COST`` units, so the smallest move there
#: is -- `param 1` -- is this. Below it the answer is "none".
COMMAND_COST = 1.84
SMALLEST_MOVE = 1.0 + COMMAND_COST
#: Gap to gap, measured over four gaps in walk K: 366.5 units.
PITCH_UNITS = 366.5
#: Rows at the top and bottom that can be base across the whole width: the
#: prescan is 24.3 mm tall against a 24 mm frame.
TRIM_ROWS = 5

#: What a side can be.
EDGE = "edge"                        # base shows, and x is where it meets picture
PICTURE_TO_BORDER = "picture_to_border"   # no base: the picture runs off the aperture
ALL_BASE = "all_base"                # the whole frame is base (blank frame, strip end)
NO_FILM = "no_film"                  # the empty gate: brighter than base, no film at all
REFUSE = "refuse"                    # the detector will not say
UNREADABLE = "unreadable"            # a labeller could not tell
STATES = (EDGE, PICTURE_TO_BORDER, ALL_BASE, NO_FILM, REFUSE, UNREADABLE)


def units_per_column(width: int = PRESCAN_COLUMNS) -> float:
    """One column of a ``width``-wide prescan, in transport units."""
    return (PRESCAN_COLUMNS / width) / COLUMNS_PER_UNIT


def pitch_columns(width: int = PRESCAN_COLUMNS) -> float:
    return PITCH_UNITS / units_per_column(width)


# --------------------------------------------------------------------------
# data

@dataclass
class Frame:
    """One prescan and what is known about where it came from."""

    id: str
    roll: str
    index: int
    image: np.ndarray
    meta: dict[str, Any]

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def film(self) -> str:
        return str(self.meta.get("film_type", "unknown"))


def manifest() -> dict[str, Any]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def frame_records(split: str | None = None, kind: str = "film") -> list[dict[str, Any]]:
    """Manifest rows, optionally only one split (``dev``/``test``/``ladder``)."""
    rows = manifest()["frames"]
    if kind:
        rows = [r for r in rows if r.get("kind") == kind]
    if split:
        rows = [r for r in rows if r.get("split") == split]
    return rows


def load(record: dict[str, Any] | str) -> Frame:
    if isinstance(record, str):
        found = [r for r in manifest()["frames"] if r["id"] == record]
        if not found:
            raise KeyError(record)
        record = found[0]
    image = np.load(FRAMES / f"{record['id']}.npy").astype(np.float32)
    return Frame(record["id"], record["roll"], int(record["index"]), image, record)


def frames(split: str | None = None, kind: str = "film") -> list[Frame]:
    return [load(r) for r in frame_records(split, kind)]


def trimmed(image: np.ndarray, rows: int = TRIM_ROWS) -> np.ndarray:
    """The rows that can hold picture: ``TRIM_ROWS`` off the top and bottom."""
    return image[rows:image.shape[0] - rows]


# --------------------------------------------------------------------------
# what a detector answers

@dataclass
class Side:
    """One side of one frame.

    ``x`` is set only for ``EDGE``. ``lo``/``hi`` bracket it -- the detector's
    own honest interval, not a decoration. ``conf`` is 0..1 and means whatever
    the detector can defend; the ensemble calibrates it on dev. ``outer`` is the
    far side of the gap, where the *neighbouring* frame's picture begins, if
    that shows.
    """

    state: str = REFUSE
    x: float | None = None
    lo: float | None = None
    hi: float | None = None
    conf: float = 0.0
    outer: float | None = None
    x_top: float | None = None
    x_bottom: float | None = None
    note: str = ""

    def base_width(self, side: str, width: int) -> float | None:
        """Columns of base showing on this side, or None when not an edge."""
        if self.state != EDGE or self.x is None:
            return None
        return self.x if side == "left" else width - self.x


@dataclass
class EdgeResult:
    left: Side = field(default_factory=Side)
    right: Side = field(default_factory=Side)
    debug: dict[str, Any] = field(default_factory=dict)

    def side(self, name: str) -> Side:
        return self.left if name == "left" else self.right

    def to_json(self) -> dict[str, Any]:
        return {"left": asdict(self.left), "right": asdict(self.right),
                "debug": _jsonable(self.debug)}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> EdgeResult:
        return cls(Side(**data["left"]), Side(**data["right"]), data.get("debug") or {})


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value


# --------------------------------------------------------------------------
# from edges to a move

@dataclass
class Decision:
    """What the transport should do: ``action`` is right/left/none/refuse."""

    action: str
    units: float | None
    lo: float | None = None
    hi: float | None = None
    left_units: float | None = None
    right_units: float | None = None
    why: str = ""

    def caption(self) -> str:
        if self.action == "refuse":
            return "refuse"
        if self.action == "none":
            return "none" if self.units is None else f"none ({self.units:+.1f} u)"
        arrow = "->" if self.action == "right" else "<-"
        return f"{self.units:+.1f} u {arrow}"


def target_base(frame_width: float, width: int = PRESCAN_COLUMNS) -> float:
    """Columns of base each side shows when the frame is centred: ``t``.

    Negative when the frame is wider than the aperture, meaning a centred frame
    overhangs both edges by ``-t``.
    """
    return (width - frame_width) / 2.0


def decide(result: EdgeResult, width: int, frame_width: float,
           bias_units: float = 0.0, deadband: float = SMALLEST_MOVE,
           agree_units: float = 2.0) -> Decision:
    """Turn two sides into one move, in units, positive = picture to the right.

    Each side proposes an interval of moves:

    * ``EDGE`` on the left, ``b_L`` columns of base: ``u = -(b_L - t)``.
    * ``EDGE`` on the right, ``b_R`` columns: ``u = +(b_R - t)``.
    * ``PICTURE_TO_BORDER`` on the left says ``b_L <= 0``, so ``u >= +t``; on
      the right, ``u <= -t``. Half-open, not a point.

    Two edges are averaged and must agree within ``agree_units``. A lone
    half-open side cannot place the frame, so it refuses -- unless the other
    side bounds it too, in which case the move is the interval's middle.
    ``bias_units`` is added to every answer: it is the target, kept apart from
    the detector so a roll-wide offset can be tested without touching it.
    """
    upc = units_per_column(width)
    t = target_base(frame_width, width)
    lo, hi = -np.inf, np.inf
    points: list[float] = []
    per: dict[str, float | None] = {"left": None, "right": None}
    for name, sign in (("left", -1.0), ("right", +1.0)):
        s = result.side(name)
        if s.state in (NO_FILM, ALL_BASE):
            return Decision("refuse", None, why=f"{name}: {s.state}")
        if s.state == EDGE and s.x is not None:
            b = s.base_width(name, width)
            assert b is not None
            u = sign * (b - t) * upc
            per[name] = u
            points.append(u)
        elif s.state == PICTURE_TO_BORDER:
            if name == "left":
                lo = max(lo, t * upc)
            else:
                hi = min(hi, -t * upc)
    if points:
        if len(points) == 2 and abs(points[0] - points[1]) > agree_units:
            return Decision("refuse", None, left_units=per["left"],
                            right_units=per["right"],
                            why=f"sides disagree by {abs(points[0] - points[1]):.1f} u")
        u = float(np.mean(points))
        # A border side still constrains a single edge: it cannot contradict it.
        if u < lo - agree_units or u > hi + agree_units:
            return Decision("refuse", None, left_units=per["left"],
                            right_units=per["right"],
                            why="edge contradicts the other side's border")
        ulo, uhi = u, u
    elif np.isfinite(lo) and np.isfinite(hi):
        if lo > hi + agree_units:
            return Decision("refuse", None, why="both borders, frame narrower than aperture")
        u = (lo + hi) / 2.0
        ulo, uhi = min(lo, hi), max(lo, hi)
    else:
        return Decision("refuse", None, why="no side places the frame")
    u += bias_units
    ulo += bias_units
    uhi += bias_units
    # "none" on the best estimate, not the interval: no base on either side
    # leaves the frame anywhere within its overhang, and every one of those
    # positions shows no base -- which is what "sits right" means.
    if abs(u) < deadband:
        return Decision("none", u, ulo, uhi, per["left"], per["right"])
    return Decision("right" if u > 0 else "left", u, ulo, uhi, per["left"], per["right"])


def side_agrees(truth: Side, answer: Side, side: str, width: int,
                tol: float = 1.0, thin: float = 1.0) -> bool | None:
    """Does ``answer`` say what ``truth`` says on one side? None if the truth cannot say.

    The rule `evaluate.py` scores by: the same state, an edge within ``tol``
    columns, and base narrower than ``thin`` columns counted the same as none.
    A refusal never agrees.
    """
    if truth.state == UNREADABLE:
        return None
    if answer.state == REFUSE:
        return False

    def base(s: Side) -> float:
        assert s.x is not None
        return s.x if side == "left" else width - s.x

    if truth.state == EDGE and answer.state == EDGE and truth.x is not None \
            and answer.x is not None:
        return abs(answer.x - truth.x) <= tol
    if truth.state == EDGE and answer.state == PICTURE_TO_BORDER and truth.x is not None:
        return base(truth) < thin
    if truth.state == PICTURE_TO_BORDER and answer.state == EDGE and answer.x is not None:
        return base(answer) < thin
    return truth.state == answer.state


# --------------------------------------------------------------------------
# pictures for a person

def display_positive(image: np.ndarray, low: float = 0.5, high: float = 99.5) -> np.ndarray:
    """(H, W, 3) uint8: inverted, each channel stretched on its own.

    Per channel, 0.5-99.5 percentile -- the same stretch the window uses
    (`rps7200/preview.py`), so the orange mask cancels and base comes out black.
    """
    img = image.astype(np.float32)
    out = np.empty(img.shape[:2] + (3,), dtype=np.float32)
    for c in range(3):
        ch = img[..., c]
        a, b = np.percentile(ch, [low, high])
        span = max(float(b - a), 1e-6)
        out[..., c] = 1.0 - np.clip((ch - a) / span, 0.0, 1.0)
    return (out * 255.0 + 0.5).astype(np.uint8)


def display_negative(image: np.ndarray, low: float = 0.5, high: float = 99.5) -> np.ndarray:
    """(H, W, 3) uint8: not inverted, each channel stretched -- base comes out white."""
    return 255 - display_positive(image, low, high)


def load_results(path: Path) -> dict[str, EdgeResult]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: EdgeResult.from_json(v) for k, v in data["results"].items()}


def save_results(path: Path, results: dict[str, EdgeResult], about: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"about": about, "results": {k: v.to_json() for k, v in results.items()}}
    path.write_text(json.dumps(body, indent=1), encoding="utf-8")
