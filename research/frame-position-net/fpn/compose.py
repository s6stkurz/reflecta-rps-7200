"""One synthetic prescan: a strip of frames under the aperture, and its truth.

The strip, left to right, in prescan columns:

    previous frame | gap | THIS FRAME (width F) | gap | next frame

The aperture shows 428 columns of it. `Recipe.left_edge` is where this frame's
picture starts in aperture coordinates -- the study's convention, column j
covering [j, j + 1) -- so a positive value means that many columns of base or
more show on the left. The whole strip may sit slightly rotated (`tilt`, in
columns from top to bottom), and every frame edge is the gate's soft shadow
(`softness`, the sigma of the exposure fall-off).

Exposure adds: the light of every frame in view, each normalised to its own
highlights and scaled by its own exposure, is summed before the film curve,
so gaps come out as unexposed film by themselves. Then grain, the device
(`fpn.device`), and the truth:

- `target`: per column, the share of the trimmed rows that is this frame's
  picture, base, the neighbour's picture, or no film -- what the loss compares;
- `truth`: an `EdgeResult` in the study's own terms, and the move.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Callable

import numpy as np

from rps7200.framing import units_per_column
from tools.frame_edges.centre import decide, frame_columns
from tools.frame_edges.sides import (
    ALL_BASE,
    EDGE,
    NO_FILM,
    PICTURE_TO_BORDER,
    TRIM_ROWS,
    EdgeResult,
    Side,
)

from fpn import device, film
from fpn.inputs import COLUMNS, ROWS
from fpn.personal import FRAME_COLUMNS_MAX, PICTURE_ROWS
from fpn.sampling import OVERSAMPLE, ROW_APERTURE, box_rows

#: The empty gate's first rows read darker: 124-152, 160-172, 175-180 against
#: ~182 from row 3 on (prescan-geometry survey).
GATE_TOP_ROWS = (0.75, 0.91, 0.98)
CLASS_PICTURE, CLASS_BASE, CLASS_NEIGHBOUR, CLASS_NO_FILM = range(4)


@dataclass(frozen=True)
class Recipe:
    film: str                          # "c41", "bw", "slide"
    pictures: tuple[str, str, str]     # previous, this, next; "" = unexposed
    frame_width: float                 # F, columns
    gaps: tuple[float, float]          # before and after this frame, columns
    left_edge: float                   # this frame's picture start, aperture coordinates
    tilt: float                        # columns, top row to bottom row
    softness: float                    # sigma of the gate shadow, columns
    top: float                         # first picture row (its 50% point)
    stops: tuple[float, float, float]  # each picture's exposure, in stops
    strip_end: str                     # "", "left" or "right": the film ends there
    lead: float                        # base beyond the outer frame before the cut
    rows: int                          # 287, or 286 as some sessions return
    calibration: str                   # the entry whose reference corrects it
    seed: int


# -- drawing a recipe ---------------------------------------------------------

def draw_frame_width(rng: np.random.Generator) -> float:
    """435.55 (FRAME_WIDTH_UNITS) for most, other cameras 431.5-441."""
    u = rng.random()
    if u < 0.60:
        return float(rng.normal(frame_columns(), 0.7))
    if u < 0.85:
        return float(rng.uniform(431.5, 438.0))
    return float(rng.uniform(438.0, FRAME_COLUMNS_MAX))


def draw_gaps(rng: np.random.Generator) -> tuple[float, float]:
    """Measured gaps: median 17.2 (IQR 16.9-20.3), prior 4-34 (gapmodel)."""
    mean = rng.uniform(15.0, 22.0)
    out = []
    for _ in range(2):
        g = rng.uniform(4.0, 34.0) if rng.random() < 0.05 else rng.normal(mean, 1.5)
        out.append(float(np.clip(g, 4.0, 34.0)))
    return out[0], out[1]


def draw_base_width(rng: np.random.Generator) -> float:
    u = rng.random()
    if u < 0.28:
        return float(rng.uniform(0.05, 3.0))
    if u < 0.56:
        return float(rng.uniform(3.0, 20.0))
    if u < 0.78:
        return float(rng.uniform(20.0, 60.0))
    if u < 0.92:
        return float(rng.uniform(60.0, 120.0))
    return float(rng.uniform(120.0, COLUMNS / 2))


def draw_recipe(film_name: str, triple: tuple[str, str, str], calibration: str,
                seed: int) -> Recipe:
    rng = np.random.default_rng(seed)
    f = draw_frame_width(rng)
    gaps = draw_gaps(rng)
    kind = rng.random()
    pictures = triple
    strip_end, lead = "", 0.0
    if kind < 0.38:                                   # picture to both borders
        left = float(rng.uniform(COLUMNS - f, 0.0))
    elif kind < 0.90:                                 # base on one side
        b = draw_base_width(rng)
        left = b if rng.random() < 0.5 else COLUMNS - b - f
    elif kind < 0.95:                                 # an unexposed frame
        left = float(rng.uniform(COLUMNS - f - 30.0, 30.0))
        pictures = (triple[0], "", triple[2])
    else:                                             # the end of the strip
        strip_end = "left" if rng.random() < 0.5 else "right"
        b = float(rng.uniform(0.0, 120.0))
        left = b if strip_end == "left" else COLUMNS - b - f
        lead = float(rng.uniform(2.0, 60.0))
        if strip_end == "left":
            pictures = ("", pictures[1], pictures[2])
        else:
            pictures = (pictures[0], pictures[1], "")
    return Recipe(
        film=film_name, pictures=pictures, frame_width=f, gaps=gaps, left_edge=left,
        tilt=float(np.clip(rng.normal(0.0, 0.8), -2.5, 2.5)),
        softness=float(rng.uniform(0.35, 0.8)),
        top=float((ROWS - PICTURE_ROWS) / 2 + rng.uniform(-1.5, 1.5)),
        stops=(float(rng.normal(0, 0.5)), float(rng.normal(0, 0.5)), float(rng.normal(0, 0.5))),
        strip_end=strip_end, lead=lead,
        rows=ROWS if rng.random() < 0.7 else ROWS - 1,
        calibration=calibration, seed=seed)


# -- rendering ----------------------------------------------------------------

def _phi(x: np.ndarray) -> np.ndarray:
    """The normal CDF: how much of the light passes a soft edge."""
    return 0.5 * (1.0 + _erf(x / math.sqrt(2.0)))


def _erf(x: np.ndarray) -> np.ndarray:
    # Abramowitz-Stegun 7.1.26, |error| < 1.5e-7: numpy has no erf.
    sign = np.sign(x)
    a = np.abs(x)
    t = 1.0 / (1.0 + 0.3275911 * a)
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t
               + 0.254829592) * t * np.exp(-a * a)
    return sign * y


def _frame_offsets(r: Recipe) -> dict[int, float]:
    """Each frame's left edge relative to this frame's, in columns."""
    f = r.frame_width
    return {-1: -r.gaps[0] - f, 0: 0.0, 1: f + r.gaps[1]}


def _shift(r: Recipe) -> np.ndarray:
    """Per output row, how far the strip sits right of its centre row (tilt)."""
    rows = np.arange(ROWS) + 0.5
    return r.tilt * (rows - ROWS / 2) / ROWS


def render(r: Recipe, grid: Callable[[str], np.ndarray], pool: device.ReferencePool,
           params: film.FilmParams, roll: film.Roll) -> tuple[np.ndarray, np.ndarray, EdgeResult]:
    rng = np.random.default_rng([r.seed, 1])
    a = np.arange(COLUMNS) + 0.5                         # column centres, aperture coordinates
    shift = _shift(r)[:, None]                           # (287, 1)
    rows_c = np.arange(ROWS) + 0.5 - r.top               # output row centres in picture rows
    mv = _phi(rows_c / r.softness) * _phi((PICTURE_ROWS - rows_c) / r.softness)  # (287,)
    centres = np.clip(rows_c, ROW_APERTURE / 2, PICTURE_ROWS - ROW_APERTURE / 2)
    exposure = np.zeros((ROWS, COLUMNS, 3))
    for k, offset in _frame_offsets(r).items():
        pid = r.pictures[k + 1]
        if not pid:
            continue
        fk = a[None, :] - (r.left_edge + shift + offset)  # position within frame k
        m = _phi(fk / r.softness) * _phi((r.frame_width - fk) / r.softness) * mv[:, None]
        if m.max() < 1e-6:
            continue
        g = grid(pid)                                    # (1134, 1764, 3), normalised
        rows_g = box_rows(g, centres)                    # (287, 1764, 3)
        q = np.clip(fk * (FRAME_COLUMNS_MAX / r.frame_width), 0.0, FRAME_COLUMNS_MAX)
        u = q * OVERSAMPLE - 0.5
        k0 = np.clip(np.floor(u).astype(np.int64), 0, g.shape[1] - 2)
        frac = np.clip(u - k0, 0.0, 1.0)[..., None]
        ri = np.arange(ROWS)[:, None]
        sampled = rows_g[ri, k0] * (1 - frac) + rows_g[ri, k0 + 1] * frac
        exposure += m[..., None] * sampled * 2.0 ** r.stops[k + 1]
    t = film.film_curve(exposure, roll, params)
    gate = _gate_mask(r, shift)                          # (287, 428) True where no film
    t = film.grain(t, roll, params, rng, ~gate)
    profile = np.ones(ROWS)
    profile[: len(GATE_TOP_ROWS)] = GATE_TOP_ROWS
    t = np.where(gate[..., None], profile[:, None, None], t)
    image = device.deliver(t * film.GATE_COUNTS, pool.by_entry(r.calibration), rng)
    if r.rows == ROWS - 1:
        image = image[:-1]
    target = column_target(r)
    return image, target, truth(r)


def _gate_mask(r: Recipe, shift: np.ndarray) -> np.ndarray:
    a = np.arange(COLUMNS)[None, :] + 0.5
    if r.strip_end == "left":
        cut = r.left_edge - r.lead + shift              # film starts here
        return a < cut
    if r.strip_end == "right":
        cut = r.left_edge + r.frame_width + r.lead + shift
        return a >= cut
    return np.zeros((ROWS, COLUMNS), dtype=bool)


# -- truth ---------------------------------------------------------------------

def _intervals(r: Recipe, shift_row: float) -> list[tuple[float, float, int]]:
    """(start, end, class) on one row, aperture coordinates, overlaid in order."""
    out: list[tuple[float, float, int]] = [(-1e9, 1e9, CLASS_BASE)]
    for k, offset in _frame_offsets(r).items():
        if not r.pictures[k + 1]:
            continue
        lo = r.left_edge + shift_row + offset
        out.append((lo, lo + r.frame_width, CLASS_PICTURE if k == 0 else CLASS_NEIGHBOUR))
    if r.strip_end == "left":
        out.append((-1e9, r.left_edge - r.lead + shift_row, CLASS_NO_FILM))
    elif r.strip_end == "right":
        out.append((r.left_edge + r.frame_width + r.lead + shift_row, 1e9, CLASS_NO_FILM))
    return out


def column_target(r: Recipe) -> np.ndarray:
    """(4, 428): per column, the share of the trimmed rows in each class."""
    j = np.arange(COLUMNS, dtype=np.float64)
    shift = _shift(r)
    rows = range(TRIM_ROWS, ROWS - TRIM_ROWS)
    acc = np.zeros((4, COLUMNS))
    for i in rows:
        share = np.zeros((4, COLUMNS))
        for lo, hi, cls in _intervals(r, float(shift[i])):
            cover = np.clip(np.minimum(hi, j + 1) - np.maximum(lo, j), 0.0, 1.0)
            share *= (1.0 - cover)[None, :]               # later intervals paint over
            share[cls] += cover
        acc += share
    acc /= len(rows)
    return (acc / acc.sum(axis=0, keepdims=True)).astype(np.float32)


def truth(r: Recipe) -> EdgeResult:
    """The frame as the study would label it: x at the median trimmed row."""
    shift = _shift(r)
    top, bottom = float(shift[TRIM_ROWS]), float(shift[ROWS - 1 - TRIM_ROWS])
    mid = float(np.median(shift[TRIM_ROWS: ROWS - TRIM_ROWS]))
    x_left = r.left_edge + mid
    x_right = r.left_edge + r.frame_width + mid
    sides: dict[str, Side] = {}
    for name in ("left", "right"):
        if r.strip_end == name:
            cut = (x_left - r.lead) if name == "left" else (x_right + r.lead)
            if (name == "left" and cut > 0.0) or (name == "right" and cut < COLUMNS):
                sides[name] = Side(NO_FILM, conf=1.0)
                continue
        if not r.pictures[1]:
            sides[name] = Side(ALL_BASE, conf=1.0)
            continue
        if name == "left" and x_left > 0.0:
            outer = x_left - r.gaps[0]
            sides[name] = Side(EDGE, x=x_left, conf=1.0,
                               outer=outer if outer > 0.0 and r.pictures[0] else None,
                               x_top=r.left_edge + top, x_bottom=r.left_edge + bottom)
        elif name == "right" and x_right < COLUMNS:
            outer = x_right + r.gaps[1]
            sides[name] = Side(EDGE, x=x_right, conf=1.0,
                               outer=outer if outer < COLUMNS and r.pictures[2] else None,
                               x_top=r.left_edge + r.frame_width + top,
                               x_bottom=r.left_edge + r.frame_width + bottom)
        else:
            sides[name] = Side(PICTURE_TO_BORDER, conf=1.0)
    return EdgeResult(sides["left"], sides["right"])


def move(r: Recipe, result: EdgeResult) -> dict[str, Any]:
    """The move the truth asks for, at the runtime frame width, and the physical one."""
    d = decide(result, COLUMNS, frame_columns())
    centre = r.left_edge + r.frame_width / 2 - COLUMNS / 2
    return {"action": d.action, "units": d.units, "why": d.why,
            "physical_units": -centre * units_per_column(COLUMNS)}


def recipe_json(r: Recipe) -> dict[str, Any]:
    return asdict(r)
