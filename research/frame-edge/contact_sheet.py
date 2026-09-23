"""Contact sheets: every frame, inverted, with the line where a detector puts the edge.

    uv run python research/frame-edge/contact_sheet.py results/<algo>/<iter>/results.json
    uv run python research/frame-edge/contact_sheet.py --truth        # the ground truth itself

What Stefan asked for: the positive (base black) on a **white** sheet, so a
black strip of unexposed base at a frame's edge stands out from the gutter.

Per tile: the frame at 1:1 with a dashed **red** line at the detector's
exposed/unexposed boundary (dashed so the pixels under it stay visible), and
under it the outer 32 columns of each side magnified 6x across, where the line
can be read to a fraction of a column. A **green** line is the ground truth,
when there is one. The caption gives the columns and the move the detector
would ask for: "+8.1 u ->" (picture to the right), "-6.0 u <-", "none" or
"refuse".

A **red box** in the gutter around a tile marks a frame where the detector
does not say what the truth says -- a side in the wrong state, an edge more
than a column off, a refusal, or a move more than one smallest step off --
and the caption says which. Drawn in the gutter, never over the frame's own
border columns.

One sheet per roll, at most 2000 px wide.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common import (  # noqa: E402
    EDGE, GROUND_TRUTH, GROUND_TRUTH_TEST, PICTURE_TO_BORDER, SMALLEST_MOVE, WORK, EdgeResult,
    Side, decide, display_positive, frame_records, load, load_results, side_agrees,
)

PER_ROW = 4
PER_PAGE = 12       # three rows: a sheet a person can read without zooming out
GUTTER = 16
INSET_COLS = 32
INSET_ZOOM = 6
RED = (230, 0, 0)
GREEN = (0, 190, 0)
LINE = 2            # line width in px -- Stefan: "make the red dotted line a bit bigger"
BOX = 5             # the mismatch box, drawn in the gutter
CAPTION_H = 82


def truth_results(split: str | None = None) -> dict[str, EdgeResult]:
    """The ground truth, as EdgeResults, so it can be drawn like any detector.

    The test split's truth is read only when the test split is asked for, or
    everything (``split=None``) -- never for dev.
    """
    gt: dict[str, Any] = {}
    if split != "test" and GROUND_TRUTH.exists():
        gt.update(json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))["frames"])
    if split in ("test", None) and GROUND_TRUTH_TEST.exists():
        gt.update(json.loads(GROUND_TRUTH_TEST.read_text(encoding="utf-8"))["frames"])
    out = {}
    for fid, g in gt.items():
        if split and g.get("split") != split:
            continue
        sides = []
        for name in ("left", "right"):
            s = g.get(name) or {}
            sides.append(Side(state=s.get("state", "unreadable"), x=s.get("x"), lo=s.get("lo"),
                              hi=s.get("hi"), conf=1.0, outer=s.get("outer"),
                              x_top=s.get("x_top"), x_bottom=s.get("x_bottom"),
                              note=s.get("flag", "")))
        out[fid] = EdgeResult(sides[0], sides[1], {"flags": g.get("flags", [])})
    return out


def frame_widths() -> dict[str, float]:
    """Per roll, the frame width in columns used to turn edges into a move."""
    if not GROUND_TRUTH.exists():
        return {}
    return json.loads(GROUND_TRUTH.read_text(encoding="utf-8")).get("frame_width", {})


def _dashed(draw: ImageDraw.ImageDraw, x0: float, x1: float, y0: int, y1: int,
            colour: tuple[int, int, int], on: int = 9, off: int = 5, phase: int = 0) -> None:
    """A dashed line from (x0, y0) to (x1, y1), possibly tilted."""
    length = y1 - y0
    y = y0 + phase
    while y < y1:
        ya, yb = y, min(y + on, y1)
        fa = (ya - y0) / max(length, 1)
        fb = (yb - y0) / max(length, 1)
        draw.line([(x0 + (x1 - x0) * fa, ya), (x0 + (x1 - x0) * fb, yb)], fill=colour,
                  width=LINE)
        y += on + off


def _line_at(side: Side) -> tuple[float, float] | None:
    """(x at top, x at bottom) of an edge, in columns."""
    if side.state != EDGE or side.x is None:
        return None
    top = side.x_top if side.x_top is not None else side.x
    bottom = side.x_bottom if side.x_bottom is not None else side.x
    return float(top), float(bottom)


def _side_text(name: str, s: Side | None, width: int) -> str:
    if s is None:
        return f"{name} -"
    if s.state == EDGE and s.x is not None:
        b = s.x if name == "L" else width - s.x
        return f"{name} {s.x:.1f} ({b:.1f} base)"
    if s.state == PICTURE_TO_BORDER:
        return f"{name} border"
    return f"{name} {s.state}"


def mismatches(result: EdgeResult, truth: EdgeResult, width: int,
               frame_width: float) -> list[str]:
    """Why the detector's answer is not the truth's, one short reason each; [] if it is."""
    why = []
    for name, short in (("left", "L"), ("right", "R")):
        a, t = result.side(name), truth.side(name)
        ok = side_agrees(t, a, name, width)
        if ok is False:
            if a.state == EDGE and t.state == EDGE and a.x is not None and t.x is not None:
                why.append(f"{short} off {a.x - t.x:+.1f} cols")
            else:
                why.append(f"{short} {a.state} vs {t.state}")
    dg, dp = decide(truth, width, frame_width), decide(result, width, frame_width)
    if dg.units is not None:
        if dp.units is None:
            why.append("move refused")
        elif abs(dp.units - dg.units) > SMALLEST_MOVE:
            why.append(f"move off {dp.units - dg.units:+.1f} u")
    return why


def tile(fid: str, result: EdgeResult | None, truth: EdgeResult | None,
         frame_width: float) -> Image.Image:
    fr = load(fid)
    h, w = fr.image.shape[:2]
    pos = display_positive(fr.image)
    inset_w = INSET_COLS * INSET_ZOOM
    height = h + 8 + h + CAPTION_H
    im = Image.new("RGB", (w, height), "white")
    im.paste(Image.fromarray(pos), (0, 0))
    left_part = Image.fromarray(pos[:, :INSET_COLS]).resize((inset_w, h), Image.Resampling.NEAREST)
    right_part = Image.fromarray(pos[:, w - INSET_COLS:]).resize((inset_w, h),
                                                               Image.Resampling.NEAREST)
    iy = h + 8
    im.paste(left_part, (0, iy))
    im.paste(right_part, (w - inset_w, iy))
    d = ImageDraw.Draw(im)
    for res, colour, phase in ((truth, GREEN, 6), (result, RED, 0)):
        if res is None:
            continue
        for name in ("left", "right"):
            ln = _line_at(res.side(name))
            if ln is None:
                continue
            top, bottom = ln
            # on the 1:1 frame: the boundary x sits on the pixel edge x
            _dashed(d, top - 0.5, bottom - 0.5, 0, h, colour, phase=phase)
            # on the inset
            if name == "left" and max(top, bottom) <= INSET_COLS:
                _dashed(d, top * INSET_ZOOM, bottom * INSET_ZOOM, iy, iy + h, colour,
                        on=10, off=4, phase=phase)
            if name == "right" and min(top, bottom) >= w - INSET_COLS:
                x0 = w - inset_w
                off = w - INSET_COLS
                _dashed(d, x0 + (top - off) * INSET_ZOOM, x0 + (bottom - off) * INSET_ZOOM,
                        iy, iy + h, colour, on=10, off=4, phase=phase)
    font = ImageFont.load_default(size=13)
    cy = iy + h + 3
    d.text((0, cy), fid, fill="black", font=font)
    if result is not None:
        dec = decide(result, w, frame_width)
        txt = (f"{_side_text('L', result.left, w)} | {_side_text('R', result.right, w)}"
               f"  => {dec.caption()}")
        d.text((0, cy + 16), txt, fill=RED, font=font)
    if truth is not None:
        dec = decide(truth, w, frame_width)
        txt = (f"truth {_side_text('L', truth.left, w)} | {_side_text('R', truth.right, w)}"
               f"  => {dec.caption()}")
        d.text((0, cy + 32), txt, fill=GREEN, font=font)
    if result is not None and truth is not None:
        why = mismatches(result, truth, w, frame_width)
        if why:
            d.text((0, cy + 48), "NOT THE TRUTH: " + "; ".join(why), fill=RED, font=font)
    return im


def sheets(results: dict[str, EdgeResult] | None, out_dir: Path, title: str,
           split: str | None = "dev", with_truth: bool = True,
           order: list[str] | None = None) -> list[Path]:
    truth = truth_results(split) if with_truth else {}
    widths = frame_widths()
    rows = frame_records(split=split, kind="film")
    if order:
        rank = {fid: i for i, fid in enumerate(order)}
        rows = sorted(rows, key=lambda r: rank.get(r["id"], len(rank)))
    by_roll: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        key = "flagged" if order and r["id"] in order else r["roll"]
        by_roll.setdefault(key, []).append(r)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    head = 40
    pages: list[tuple[str, list[dict[str, Any]]]] = []
    for roll, recs in by_roll.items():
        chunks = [recs[i:i + PER_PAGE] for i in range(0, len(recs), PER_PAGE)]
        for k, chunk in enumerate(chunks, start=1):
            pages.append((roll if len(chunks) == 1 else f"{roll}_p{k}", chunk))
    for roll, recs in pages:
        tiles = []
        boxed = []
        for r in recs:
            res = results.get(r["id"]) if results is not None else None
            tru = truth.get(r["id"])
            fw = float(widths.get(r["roll"], widths.get("_all", 425.2)))
            tiles.append(tile(r["id"], res, tru if results is not None else None, fw)
                         if results is not None else tile(r["id"], tru, None, fw))
            width = int(load(r["id"]).image.shape[1])
            boxed.append(bool(res is not None and tru is not None
                              and mismatches(res, tru, width, fw)))
        tw, th = tiles[0].size
        n_rows = (len(tiles) + PER_ROW - 1) // PER_ROW
        sheet = Image.new("RGB", (PER_ROW * (tw + GUTTER) + GUTTER,
                                  head + n_rows * (th + GUTTER) + GUTTER), "white")
        d = ImageDraw.Draw(sheet)
        wrong = sum(boxed)
        d.text((GUTTER, 10), f"{title}  --  {roll}  ({len(tiles)} frames"
               + (f", {wrong} not the truth: red box" if results is not None and truth else "")
               + ")   red dashes = detector, green dashes = truth", fill="black",
               font=ImageFont.load_default(size=18))
        for i, t in enumerate(tiles):
            x = GUTTER + (i % PER_ROW) * (tw + GUTTER)
            y = head + GUTTER + (i // PER_ROW) * (th + GUTTER)
            sheet.paste(t, (x, y))
            if boxed[i]:
                d.rectangle([x - BOX - 2, y - BOX - 2, x + tw + 1 + BOX, y + th + 1 + BOX],
                            outline=RED, width=BOX)
        path = out_dir / f"sheet_{roll}.png"
        sheet.save(path)
        paths.append(path)
    return paths


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("results", nargs="?", type=Path)
    ap.add_argument("--truth", action="store_true", help="draw the ground truth itself")
    ap.add_argument("--split", default="dev")
    args = ap.parse_args()
    split = None if args.split == "all" else args.split
    if args.truth:
        gt = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
        flagged = [f.split()[0].rstrip(":") for f in gt.get("flags", [])]
        paths = sheets(truth_results(), WORK / "results" / "truth", "ground truth (red)",
                       split=split, with_truth=False, order=flagged)
    else:
        path = args.results if args.results.is_absolute() else HERE / args.results
        results = load_results(path)
        paths = sheets(results, path.parent, f"{path.parent.parent.name} {path.parent.name}",
                       split=split)
    for p in paths:
        print(p)


if __name__ == "__main__":
    main()
