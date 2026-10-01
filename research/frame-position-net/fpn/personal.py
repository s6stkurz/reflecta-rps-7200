"""The private pictures: `library personal/`, lab scans of Stefan's own rolls.

Five lab orders, one folder per roll, 3578x2433 sRGB JPEGs: positives as the lab
delivered them, cropped to the picture, in the film's orientation (landscape).
They are the picture source for slides, and for the colour and B&W negatives
made from them (`fpn.film`).

Nothing here is ever committed or published. Every picture gets an anonymous
id -- roll letter A-E in folder order, then its place in the roll -- and the
mapping from file names to ids, like the cached grids, stays in `local/`.

Each picture is cached in linear light on a grid `OVERSAMPLE` times finer than
a prescan: `FRAME_COLUMNS_MAX` columns by `PICTURE_ROWS` rows of prescan, the
largest frame a synthetic strip draws. `fpn.sampling` then makes the prescan
from it the way the device does. The lab's crop is slightly narrower than a
frame (3578/2433 = 1.471 against 36/24 = 1.5), so mapping its width onto the
frame stretches it a few percent across; for where an edge sits, that is
immaterial.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from rps7200.framing import APERTURE_HEIGHT_MM

from fpn.inputs import ROWS
from fpn.sampling import OVERSAMPLE

JPEG_SIZE = (3578, 2433)
#: The 135 format's picture height. The aperture is 24.30 mm tall
#: (`framing.APERTURE_HEIGHT_MM`) over a 287-row pass.
PICTURE_HEIGHT_MM = 24.0
PICTURE_ROWS = ROWS * PICTURE_HEIGHT_MM / APERTURE_HEIGHT_MM  # 283.5
#: The widest frame a synthetic strip draws, in prescan columns.
FRAME_COLUMNS_MAX = 441
GRID_SHAPE = (round(PICTURE_ROWS * OVERSAMPLE), FRAME_COLUMNS_MAX * OVERSAMPLE)

_VARIANT = re.compile(r"^(?P<stem>.+?)_(?P<n>\d{4})$")


@dataclass(frozen=True)
class Picture:
    id: str          # anonymous: "pA07", "pA07b" for a lab rescan of the same frame
    roll: str        # "A".."E"
    picture: str     # the id shared by a frame and its rescans
    file: str        # relative to the source folder -- local only
    sha256: str


def srgb_to_linear() -> np.ndarray:
    """The exact sRGB transfer function as a 256-entry table."""
    c = np.arange(256, dtype=np.float64) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4).astype(np.float32)


def inventory(source: Path) -> list[Picture]:
    rolls = sorted(p for p in source.iterdir() if p.is_dir())
    if len(rolls) > 26:
        raise ValueError(f"{len(rolls)} rolls: more than letters")
    out: list[Picture] = []
    for letter, roll in zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", rolls):
        files = sorted(f for f in roll.iterdir() if f.suffix.lower() in (".jpg", ".jpeg"))
        stems: dict[str, str] = {}
        for f in files:
            m = _VARIANT.match(f.stem)
            if m is None:
                stems.setdefault(f.stem, f"p{letter}{len(stems):02d}")
        for f in files:
            m = _VARIANT.match(f.stem)
            stem = m["stem"] if m else f.stem
            if stem not in stems:  # a rescan whose original is missing
                stems[stem] = f"p{letter}{len(stems):02d}"
            picture = stems[stem]
            pid = picture if m is None else picture + "bcdefgh"[int(m["n"]) - 1]
            digest = hashlib.sha256(f.read_bytes()).hexdigest()
            out.append(Picture(pid, letter, picture, str(f.relative_to(source)), digest))
    return out


def load_linear(path: Path) -> np.ndarray:
    """The JPEG in linear light, (rows, columns, 3) float32, after the checks."""
    from PIL import Image

    with Image.open(path) as im:
        if im.size != JPEG_SIZE or im.mode != "RGB":
            raise ValueError(f"{path.name}: {im.size} {im.mode}, expected {JPEG_SIZE} RGB")
        orientation = im.getexif().get(0x0112, 1)
        if orientation != 1:
            raise ValueError(f"{path.name}: EXIF orientation {orientation}")
        icc = im.info.get("icc_profile")
        if icc and b"sRGB" not in icc:
            raise ValueError(f"{path.name}: an ICC profile that is not sRGB")
        pixels = np.asarray(im, dtype=np.uint8)
    return srgb_to_linear()[pixels]


def to_grid(linear: np.ndarray) -> np.ndarray:
    """Area-resample onto `GRID_SHAPE` -- a quarter of a prescan column per cell,
    fine enough that the device's point sampling is still what decides."""
    from PIL import Image

    rows, cols = GRID_SHAPE
    channels = [
        np.asarray(Image.fromarray(np.ascontiguousarray(linear[..., c]), mode="F")
                   .resize((cols, rows), Image.Resampling.BOX), dtype=np.float32)
        for c in range(3)
    ]
    return np.stack(channels, axis=-1)


def write_cache(source: Path, out: Path, progress=print) -> list[Picture]:
    grids = out / "grid"
    grids.mkdir(parents=True, exist_ok=True)
    pictures = inventory(source)
    for i, p in enumerate(pictures):
        target = grids / f"{p.id}.npy"
        if not target.exists():
            np.save(target, to_grid(load_linear(source / p.file)).astype(np.float16))
        progress(f"{i + 1}/{len(pictures)} {p.id}")
    (out / "inventory.json").write_text(json.dumps(
        {"source": str(source), "grid_shape": GRID_SHAPE, "oversample": OVERSAMPLE,
         "pictures": [asdict(p) for p in pictures]}, indent=1))
    return pictures


def load_grid(out: Path, pid: str) -> np.ndarray:
    return np.load(out / "grid" / f"{pid}.npy").astype(np.float32)


class GridLoader:
    """Normalised grids by id, a few at a time: each is 24 MB in float32."""

    def __init__(self, out: Path, keep: int = 6):
        self.out = out
        self.keep = keep
        self._cache: dict[str, np.ndarray] = {}

    def __call__(self, pid: str) -> np.ndarray:
        from fpn.film import normalise

        if pid not in self._cache:
            if len(self._cache) >= self.keep:
                self._cache.pop(next(iter(self._cache)))
            self._cache[pid] = normalise(load_grid(self.out, pid))
        return self._cache[pid]


def roll_pictures(out: Path, rolls: set[str]) -> dict[str, list[str]]:
    """The pictures of each roll in film order, rescans left out."""
    pictures = json.loads((out / "inventory.json").read_text())["pictures"]
    by_roll: dict[str, list[str]] = {}
    for p in pictures:
        if p["roll"] in rolls and p["id"] == p["picture"]:
            by_roll.setdefault(p["roll"], []).append(p["id"])
    return {r: sorted(ids) for r, ids in by_roll.items()}


def triples(by_roll: dict[str, list[str]], n: int,
            rng: np.random.Generator) -> list[tuple[str, str, str]]:
    """``n`` (previous, this, next) runs of neighbouring frames."""
    rolls = sorted(by_roll)
    out = []
    for _ in range(n):
        ids = by_roll[rolls[int(rng.integers(len(rolls)))]]
        i = int(rng.integers(1, len(ids) - 1))
        out.append((ids[i - 1], ids[i], ids[i + 1]))
    return out
