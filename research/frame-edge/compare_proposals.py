"""Old and new proposals for one walk, side by side, for Stefan's eye.

    uv run --no-sync python research/frame-edge/compare_proposals.py demo/rolls/2026-09-14

Reads the walk exactly as the window does (`tools/gui.read_survey`) and asks
both detectors: today's `framing.propose_offsets` (what the window used before
this branch) and `tools/frame_edges.propose_centred` (what it uses now). One
sheet: the positive on white, the new detector's edge lines in red, and under
each frame both proposals in units. A red box marks a frame where the two
disagree by more than one smallest move.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from rps7200 import framing  # noqa: E402
from rps7200.framing import SMALLEST_MOVE  # noqa: E402
from rps7200.protocol import units  # noqa: E402
from tools import frame_edges  # noqa: E402

OUT = HERE / "contact_sheet_test" / "proposals"
PER_ROW, GUTTER, CAPTION = 4, 16, 50
RED, BLUE, GREY = (230, 0, 0), (40, 90, 200), (90, 90, 90)


def _window():
    """`tools/gui.py` as a module: the window's own reader of a walk folder."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("tools_gui", ROOT / "tools" / "gui.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _say(mm: float | None, source: str | None) -> str:
    if mm is None:
        return f"-- ({source or 'none'})"
    return f"{units(mm):+.1f} u ({source})"


def main() -> None:
    folder = Path(sys.argv[1])
    gui = _window()
    survey = gui.read_survey(folder)
    frames = [(int(r.number), r.image) for r in survey["results"] if r.image is not None]
    film = survey.get("film") or "negative"
    old, old_notes = framing.propose_offsets(frames)
    new, new_notes = frame_edges.propose_centred(frames, film=film)
    font = ImageFont.load_default(size=13)
    tiles = []
    differ = 0
    for n, image in frames:
        pos = frame_edges_display(image)
        h, w = pos.shape[:2]
        tile = Image.new("RGB", (w, h + CAPTION), "white")
        tile.paste(Image.fromarray(pos), (0, 0))
        d = ImageDraw.Draw(tile)
        edges = (new_notes.get(n) or {}).get("edges") or {}
        for side in ("left", "right"):
            e = edges.get(side) or {}
            if e.get("state") == "edge" and e.get("x") is not None:
                x = float(e["x"]) - 0.5
                d.line([(x, 0), (x, h)], fill=RED, width=2)
        a, b = old.get(n), new.get(n)
        gap = abs(units(a or 0.0) - units(b or 0.0))
        d.text((0, h + 3), f"frame {n}", fill="black", font=font)
        d.text((0, h + 18), "old  " + _say(a, (old_notes.get(n) or {}).get("source")),
               fill=GREY, font=font)
        d.text((0, h + 33), "new  " + _say(b, (new_notes.get(n) or {}).get("source")),
               fill=BLUE, font=font)
        tiles.append((tile, gap > SMALLEST_MOVE))
        differ += int(gap > SMALLEST_MOVE)
    tw, th = tiles[0][0].size
    rows = (len(tiles) + PER_ROW - 1) // PER_ROW
    sheet = Image.new("RGB", (PER_ROW * (tw + GUTTER) + GUTTER,
                              44 + rows * (th + GUTTER) + GUTTER), "white")
    d = ImageDraw.Draw(sheet)
    d.text((GUTTER, 12), f"{folder.name}: old framing.propose_offsets (grey) vs new "
           f"frame_edges.propose_centred (blue), red = new edge; {differ} of {len(tiles)} "
           f"differ by more than {SMALLEST_MOVE:.2f} u (red box)",
           fill="black", font=ImageFont.load_default(size=16))
    for i, (tile, boxed) in enumerate(tiles):
        x = GUTTER + (i % PER_ROW) * (tw + GUTTER)
        y = 44 + GUTTER + (i // PER_ROW) * (th + GUTTER)
        sheet.paste(tile, (x, y))
        if boxed:
            d.rectangle([x - 6, y - 6, x + tw + 5, y + th + 5], outline=RED, width=4)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{folder.name}.png"
    sheet.save(path)
    print(path, f"-- {differ} of {len(tiles)} frames differ")


def frame_edges_display(image: np.ndarray) -> np.ndarray:
    """The positive, each channel stretched: base comes out black."""
    img = np.asarray(image, dtype=np.float32)[..., :3]
    out = np.empty(img.shape, dtype=np.float32)
    for c in range(3):
        lo, hi = np.percentile(img[..., c], [0.5, 99.5])
        out[..., c] = 1.0 - np.clip((img[..., c] - lo) / max(float(hi - lo), 1e-6), 0, 1)
    return (out * 255 + 0.5).astype(np.uint8)


if __name__ == "__main__":
    main()
