"""Collect every 300 dpi prescan on this machine into one dataset.

    uv run python research/frame-edge/build_dataset.py --triage   # contact sheets of every candidate
    uv run python research/frame-edge/build_dataset.py            # build data/ from triage.json

Two sources, both in `library/`:

* entries filed at 300 dpi -- a prescan's own entry; its pixels are raw and
  `library.corrected` applies that session's shading, which is what the
  software itself looks at;
* `prescan.tif` beside a higher-resolution entry -- the 8-bit prescan as the
  driver delivered it, kept next to the scan it positioned. The higher-
  resolution scan is recorded as that frame's `hires`, for anchoring.

`demo/rolls` is left out on purpose: it is these same pictures cycled into
fake strips. `probe/stage9b_*` is added as a ladder -- twenty prescans of one
strip moved about 7 columns each -- which needs no labels at all.

What is film and what is an empty gate, a target or a slide is decided by eye,
once, in `triage.json`; this script never guesses it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from common import (  # noqa: E402
    DATA, FRAMES, LIBRARY, MANIFEST, WORK, display_negative, display_positive,
)
from rps7200 import library, tiff  # noqa: E402

PROBE = ROOT / "probe"
TRIAGE = WORK / "triage.json"
CANDIDATES = DATA / "candidates.json"


def _record(entry: Path) -> dict[str, Any]:
    return json.loads((entry / "scan.json").read_text(encoding="utf-8"))


def candidates() -> list[dict[str, Any]]:
    """Every 300 dpi prescan in the library, in capture order."""
    out: list[dict[str, Any]] = []
    for entry in sorted(p for p in LIBRARY.iterdir() if (p / "scan.json").exists()):
        rec = _record(entry)
        shape = (rec.get("image") or {}).get("shape") or []
        dpi = (rec.get("scan") or {}).get("resolution_dpi")
        film = rec.get("film") or {}
        base = {
            "entry": entry.name,
            "created": rec.get("created"),
            "tags": rec.get("tags") or [],
            "film_stock": film.get("stock") or "",
            "film_frame": film.get("frame") or "",
            "notes": film.get("notes") or "",
            "rotation": (rec.get("scan") or {}).get("rotation"),
        }
        if dpi == 300 and len(shape) == 3 and shape[1] == 428:
            out.append({**base, "source": "scan", "file": "scan.tif",
                        "dtype": (rec.get("image") or {}).get("dtype")})
        if (entry / "prescan.tif").exists():
            out.append({**base, "source": "prescan", "file": "prescan.tif",
                        "hires": {"entry": entry.name, "dpi": dpi, "shape": shape}})
    for i, c in enumerate(out):
        c["cid"] = f"c{i:03d}"
    return out


def pixels(c: dict[str, Any]) -> tuple[np.ndarray, str, str]:
    """(image, dtype, how it was corrected) for one candidate."""
    entry = LIBRARY / c["entry"]
    if c["source"] == "scan":
        image, rec = library.corrected(entry)
        return image, str(image.dtype), str(rec.get("corrected"))
    image = tiff.read(str(entry / c["file"]))
    return image, str(image.dtype), "as delivered"


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def triage() -> None:
    """Contact sheets of every candidate, numbered, to classify by eye."""
    cands = candidates()
    DATA.mkdir(parents=True, exist_ok=True)
    out_dir = WORK / "crops" / "triage"
    out_dir.mkdir(parents=True, exist_ok=True)
    tile_w, tile_h, cap = 214, 143, 34
    per_row, per_sheet = 8, 48
    font = _font(11)
    stats = []
    tiles = []
    for c in cands:
        img, dtype, how = pixels(c)
        f = img.astype(np.float32)
        med = [float(np.median(f[..., k])) for k in range(3)]
        mx = [float(np.percentile(f[..., k], 99.5)) for k in range(3)]
        c.update({"dtype": dtype, "corrected": how, "shape": list(img.shape),
                  "median_rgb": med, "p995_rgb": mx,
                  "sha": hashlib.sha1(np.ascontiguousarray(img).tobytes()).hexdigest()})
        stats.append(c)
        pos = Image.fromarray(display_positive(f)).resize((tile_w, tile_h))
        neg = Image.fromarray(display_negative(f)).resize((tile_w, tile_h))
        tiles.append((c, pos, neg))
    CANDIDATES.write_text(json.dumps(stats, indent=1), encoding="utf-8")
    for s in range(0, len(tiles), per_sheet):
        chunk = tiles[s:s + per_sheet]
        rows = (len(chunk) + per_row - 1) // per_row
        sheet = Image.new("RGB", (per_row * (tile_w + 6) + 6,
                                  rows * (2 * tile_h + cap + 10) + 6), "white")
        d = ImageDraw.Draw(sheet)
        for i, (c, pos, neg) in enumerate(chunk):
            x = 6 + (i % per_row) * (tile_w + 6)
            y = 6 + (i // per_row) * (2 * tile_h + cap + 10)
            sheet.paste(pos, (x, y))
            sheet.paste(neg, (x, y + tile_h))
            med = c["median_rgb"]
            label = (f"{c['cid']} {c['source'][:2]} {c['dtype']} "
                     f"{c['entry'][:15]}\n{c['film_frame'][:18] or c['film_stock'][:18]}"
                     f" m={med[0]:.0f}/{med[1]:.0f}/{med[2]:.0f}")
            d.text((x, y + 2 * tile_h + 1), label, fill="black", font=font)
        path = out_dir / f"triage_{s // per_sheet:02d}.png"
        sheet.save(path)
        print(path)
    print(f"{len(cands)} candidates -> {CANDIDATES}")


def _probe_ladder() -> list[dict[str, Any]]:
    """The stage9b ladder -- only for the main dataset, never a second library's."""
    if WORK != HERE:
        return []
    rows = []
    for i, path in enumerate(sorted(PROBE.glob("stage9b_*.tif")), start=1):
        rows.append({"path": str(path.relative_to(ROOT)), "index": i})
    return rows


#: A match this confident is the same picture; below it, `register` has found
#: something in two different frames. Measured on the triage: same-position
#: repeats score 100-240, different frames of one gallery 12-31.
SAME_PICTURE = 50.0


def shifted_pairs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every pair of labelled frames in one roll that show one picture, and the shift.

    ``dx`` is how far the picture moved from ``a`` to ``b``, in columns: a
    feature at column j in ``a`` sits at ``j + dx`` in ``b``. So an edge found
    in both must satisfy ``edge_b - edge_a == dx`` -- no label needed. That is
    also the physical-frame grouping: frames joined by a pair are one frame.
    """
    from rps7200.uniformity import register

    film = [r for r in rows if r["kind"] == "film"]
    norm: dict[str, np.ndarray] = {}
    for r in film:
        img = np.load(FRAMES / f"{r['id']}.npy")
        norm[r["id"]] = img / max(float(np.median(img)), 1e-6)
    parent = {r["id"]: r["id"] for r in film}

    def root(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    out = []
    for i, a in enumerate(film):
        for b in film[i + 1:]:
            if a["roll"] != b["roll"]:
                continue
            dy, dx, conf = register(norm[a["id"]], norm[b["id"]], max_shift=120)
            if conf < SAME_PICTURE:
                continue
            # register: a[i, j] ~ b[i - dy, j - dx], so a feature at j in a is at
            # j - dx in b -- the picture moved by -dx from a to b.
            out.append({"a": a["id"], "b": b["id"], "dx": -dx, "dy": -dy,
                        "conf": round(conf, 1)})
            parent[root(a["id"])] = root(b["id"])
    for r in film:
        r["physical"] = root(r["id"])
    for r in rows:
        if r["kind"] == "dup" and r.get("same_as"):
            r["physical"] = next(f["physical"] for f in film if f["id"] == r["same_as"])
    return out


def build() -> None:
    """data/frames/<id>.npy and data/manifest.json, from triage.json."""
    if not TRIAGE.exists():
        raise SystemExit("triage.json missing -- run --triage and classify first")
    triage_doc = json.loads(TRIAGE.read_text(encoding="utf-8"))
    by_cid = {c["cid"]: c for c in candidates()}
    FRAMES.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    for roll in triage_doc["rolls"]:
        for index, item in enumerate(roll["frames"], start=1):
            c = by_cid[item["cid"]]
            img, dtype, how = pixels(c)
            sha = hashlib.sha1(np.ascontiguousarray(img).tobytes()).hexdigest()
            if sha in seen:
                print(f"skip {c['cid']}: same pixels as {seen[sha]}")
                continue
            fid = f"{roll['name']}_{index:02d}"
            seen[sha] = fid
            np.save(FRAMES / f"{fid}.npy", img.astype(np.float32))
            rows.append({
                "id": fid, "roll": roll["name"], "index": index,
                "kind": item.get("kind", "film"),
                "split": roll.get("split", "dev"),
                "film_type": roll.get("film_type", "unknown"),
                "physical": item.get("physical") or f"{roll['name']}:{c['film_frame'] or index}",
                "cid": c["cid"], "entry": c["entry"], "file": c["file"],
                "source": c["source"], "dtype": dtype, "corrected": how,
                "shape": list(img.shape), "rotation": c.get("rotation"),
                "flipped": False, "created": c.get("created"),
                "film_frame": c["film_frame"], "hires": c.get("hires"),
                "note": item.get("note", ""), "sha": sha,
            })
    ladder = []
    for item in _probe_ladder():
        img = tiff.read(str(ROOT / item["path"]))
        fid = f"ladder9b_{item['index']:02d}"
        np.save(FRAMES / f"{fid}.npy", img.astype(np.float32))
        ladder.append({"id": fid, "roll": "ladder9b", "index": item["index"],
                       "kind": "ladder", "split": "ladder", "film_type": "unknown",
                       "physical": "ladder9b", "path": item["path"],
                       "dtype": str(img.dtype), "shape": list(img.shape)})
    by_cid_fid = {r["cid"]: r["id"] for r in rows}
    for r in rows:
        same = next((f.get("same_as") for roll in triage_doc["rolls"] for f in roll["frames"]
                     if f["cid"] == r["cid"]), None)
        if same:
            r["same_as"] = by_cid_fid[same]
    pairs = shifted_pairs(rows)
    MANIFEST.write_text(json.dumps({"frames": rows + ladder, "pairs": pairs,
                                    "rolls": triage_doc["rolls"]}, indent=1),
                        encoding="utf-8")
    print(f"{len(pairs)} shifted pairs of one picture")
    kinds: dict[str, int] = {}
    for r in rows:
        kinds[f"{r['split']}/{r['kind']}"] = kinds.get(f"{r['split']}/{r['kind']}", 0) + 1
    print(f"{len(rows)} frames + {len(ladder)} ladder -> {MANIFEST}")
    print(kinds)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--triage", action="store_true",
                    help="render every candidate for classification by eye")
    args = ap.parse_args()
    if args.triage:
        triage()
    else:
        build()


if __name__ == "__main__":
    main()
