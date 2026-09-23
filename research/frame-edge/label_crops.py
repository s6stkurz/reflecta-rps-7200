"""Pictures to label a frame's edges by eye, and the numbers under them.

    uv run python research/frame-edge/label_crops.py            # every film frame
    uv run python research/frame-edge/label_crops.py r0914_03   # just these

For each frame, `crops/label/<id>_A.png` and `<id>_B.png`, and `<id>.txt`:

* **A** is the negative as the detector sees it: not inverted, each channel
  stretched over the whole frame, so unexposed base comes out near white.
* **B** is the positive -- inverted, base black -- as Stefan judges a frame.

Both show the whole frame at 1:1 for context and, under it, the outer
`CROP` columns of each side magnified ``ZOOM_X`` times across and ``ZOOM_Y``
down. The ruler marks every **column boundary**; a number ``n`` sits on the
line between column ``n-1`` and column ``n``, which is exactly the coordinate a
label is given in.

The `.txt` holds, for the same columns, a base-likeness per column in four
horizontal bands (0 = darkest in the frame, 100 = brightest): where it falls
from the base plateau to the picture is the edge, and a band that disagrees
with the others is a tilt or a silhouette.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common import (  # noqa: E402
    TRIM_ROWS, WORK, display_negative, display_positive, frame_records, load,
)

OUT = WORK / "crops" / "label"
CROP = 64
ZOOM_X = 10
ZOOM_Y = 2
BANDS = 4


def base_likeness(image: np.ndarray) -> np.ndarray:
    """(H, W) 0..100: each channel scaled 0.5..99.5 percentile, then averaged.

    On a negative, unexposed base is the top of every channel at once, so it
    sits near 100 and picture falls below it.
    """
    out = np.zeros(image.shape[:2], dtype=np.float32)
    for c in range(3):
        ch = image[..., c].astype(np.float32)
        lo, hi = np.percentile(ch, [0.5, 99.5])
        out += np.clip((ch - lo) / max(float(hi - lo), 1e-6), 0.0, 1.2)
    return out / 3.0 * 100.0


def table(image: np.ndarray) -> str:
    like = base_likeness(image)[TRIM_ROWS:image.shape[0] - TRIM_ROWS]
    h, w = like.shape
    edges = np.linspace(0, h, BANDS + 1).astype(int)
    lines = ["# base-likeness 0..100 per column, rows split in 4 bands (top..bottom), "
             "then all rows; column c spans boundary c .. c+1",
             "col   b1   b2   b3   b4  all"]
    cols = list(range(0, CROP)) + [None] + list(range(w - CROP, w))
    for c in cols:
        if c is None:
            lines.append("...")
            continue
        vals = [float(np.median(like[edges[k]:edges[k + 1], c])) for k in range(BANDS)]
        vals.append(float(np.median(like[:, c])))
        lines.append(f"{c:3d} " + " ".join(f"{v:4.0f}" for v in vals))
    return "\n".join(lines) + "\n"


def _ruler(draw: ImageDraw.ImageDraw, x0: int, y: int, first: int, count: int,
           font: ImageFont.FreeTypeFont | ImageFont.ImageFont, below: bool) -> None:
    for k in range(count + 1):
        col = first + k
        x = x0 + k * ZOOM_X
        major = col % 5 == 0
        length = 12 if major else 5
        if below:
            draw.line([(x, y), (x, y + length)], fill="black")
            if major:
                draw.text((x - 8, y + 13), str(col), fill="black", font=font)
        else:
            draw.line([(x, y - length), (x, y)], fill="black")
            if major:
                draw.text((x - 8, y - 27), str(col), fill="black", font=font)


def render(frame_id: str, view: str) -> Image.Image:
    fr = load(frame_id)
    img = fr.image
    h, w = img.shape[:2]
    shown = display_negative(img) if view == "A" else display_positive(img)
    font = ImageFont.load_default(size=13)
    big = ImageFont.load_default(size=16)
    crop_w = CROP * ZOOM_X
    gap = 60
    width = max(2 * crop_w + gap + 40, 2 * w + 60)
    top = 30
    crop_top = top + h + 70
    height = crop_top + h * ZOOM_Y + 60
    sheet = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(sheet)
    title = (f"{frame_id}  view {view} ({'negative, base white' if view == 'A' else 'positive, base black'})"
             f"  {w}x{h}  film={fr.film}")
    d.text((20, 6), title, fill="black", font=big)
    sheet.paste(Image.fromarray(shown), (20, top))
    other = display_positive(img) if view == "A" else display_negative(img)
    sheet.paste(Image.fromarray(other), (40 + w, top))
    # outline the crops on the context image
    d.rectangle([20, top, 20 + CROP, top + h - 1], outline=(255, 0, 255))
    d.rectangle([20 + w - CROP, top, 20 + w - 1, top + h - 1], outline=(255, 0, 255))
    for side, first in (("left", 0), ("right", w - CROP)):
        x0 = 20 if side == "left" else 20 + crop_w + gap
        part = shown[:, first:first + CROP]
        zoomed = Image.fromarray(part).resize((crop_w, h * ZOOM_Y), Image.Resampling.NEAREST)
        sheet.paste(zoomed, (x0, crop_top))
        # mark the trimmed rows, which are not used
        for yy in (crop_top + TRIM_ROWS * ZOOM_Y, crop_top + (h - TRIM_ROWS) * ZOOM_Y):
            d.line([(x0 - 6, yy), (x0 - 1, yy)], fill=(255, 0, 255))
        _ruler(d, x0, crop_top - 1, first, CROP, font, below=False)
        _ruler(d, x0, crop_top + h * ZOOM_Y, first, CROP, font, below=True)
        d.text((x0, crop_top - 50), f"{side.upper()} edge, columns {first}..{first + CROP}",
               fill="black", font=big)
    return sheet


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    ids = sys.argv[1:] or [r["id"] for r in frame_records(kind="film")]
    for fid in ids:
        for view in ("A", "B"):
            render(fid, view).save(OUT / f"{fid}_{view}.png")
        (OUT / f"{fid}.txt").write_text(table(load(fid).image), encoding="utf-8")
    print(f"{len(ids)} frames -> {OUT}")


if __name__ == "__main__":
    main()
