#!/usr/bin/env python3
"""Write the three files used to check corrections by eye, from a library entry.

    1_nothing_done.tif        the entry's decode, as the scanner sent it
    2_corrected.tif           what an operator gets from it: library.corrected
    3_corrected_inverted.tif  the corrected one inverted, for viewing

Run after any change to the scan or correction path:

    uv run python tools/make_comparison.py <entry id or path>
    uv run python tools/make_comparison.py 20260911T094114Z_unknown-film_600dpi

**The files are the delivered path, not a model of it.** `1_` is
`library.load`, the raw decode every entry keeps. `2_` is
`library.corrected(entry)` -- today's correction code on the entry's own
reference and CCD mask, the same call the window's Save As makes -- written
through `export.write`, which is what writes every delivered file. So a change
to the decode, to `apply_shading` or to the write path shows up here, and
nothing that the software does not ship can.

It did not use to. This read any TIFF -- by default a `scans/` file that was
already shading-corrected when it was delivered -- and made `2_corrected.tif`
with `destripe`, a flat-file column interpolation nothing delivered has ever
run. After a change to `apply_shading` the three files came out unchanged, and
the one check Stefan judges by eye could not see the code it was run to check.

The numbers printed are measured from the files as written, read back from
disk: a recomputed array once looked clean while the shipped file carried a
40-column colour ramp from the write path.

There is no vignette correction here and none should be added. The ~39% falloff
across the frame is real but lives entirely in x, which shading already takes to
1.4%; along y it is 1.1% before any correction. See docs/vignette-plan.md.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from rps7200 import export, library, preview, tiff
from rps7200.console import use_utf8_stdout

NAMES = ("1_nothing_done.tif", "2_corrected.tif", "3_corrected_inverted.tif")


def worst_colour(image: np.ndarray, window: int = 25) -> tuple[float, int]:
    """Largest *coloured* column deviation, and where it is.

    Signed and channel-relative, because that is the only form that can express
    "this channel departs from the others". np.abs hides a violet/green pair --
    they cancel -- and a per-channel maximum cannot say a column is tinted, only
    that it is bright.

    Over R, G and B only. An RGBI entry's fourth plane is not a colour, and
    letting it into the mean across channels tinted every column it differed
    in.
    """
    x = image[..., :3].astype(np.float64)
    prof = np.median(x, axis=0)
    k, pad = window | 1, window // 2
    smooth = np.stack([
        np.convolve(np.pad(prof[:, c], pad, mode="reflect"), np.ones(k) / k, "valid")
        for c in range(prof.shape[1])
    ], axis=-1)
    level = float(np.median(prof, axis=0).mean())
    dev = (prof - smooth) / max(level, 1e-9)
    colour = dev - dev.mean(axis=1, keepdims=True)
    strength = np.abs(colour).max(axis=1)
    j = int(np.argmax(strength))
    return 100 * float(strength[j]), j


def invert(image: np.ndarray) -> np.ndarray:
    """Plain per-channel inversion, enough to judge by eye. Use NegPy for real work.

    The stretch itself lives in `rps7200.preview` so that the files written here
    and the GUI's on-screen preview are the same transform -- two copies of "how
    a negative is made judgeable" would drift, and Stefan judges by eye.
    """
    x = preview.normalise(image[..., :3])
    return np.clip((1.0 - x) * 65535, 0, 65535).astype(np.uint16)


def find_entry(name: str, root: Path) -> Path | None:
    """An entry by its path, or by its id under `root`."""
    for candidate in (Path(name), root / name):
        if (candidate / "scan.json").is_file():
            return candidate
    return None


def write_previews(before: np.ndarray, after: np.ndarray, folder: Path) -> list[Path]:
    """Half-size inverted PNGs of both, for a quick look. Not for judging lines:
    a downscaled preview cannot show a one-pixel one, the TIFFs can."""
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    # `previews/` is gitignored, so a fresh checkout has none -- and this used
    # to raise here, after the three TIFFs were already written.
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for name, img in (("cmp_before", before), ("cmp_after", after)):
        v = (invert(img) / 256).astype(np.uint8)
        path = folder / f"{name}.png"
        Image.fromarray(v).resize((max(1, v.shape[1] // 2),
                                   max(1, v.shape[0] // 2))).save(path)
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("entry", help="a library entry: its id, or the path to it")
    ap.add_argument("--library", default=str(library.DEFAULT_ROOT),
                    help="where to look up an entry id (default: %(default)s)")
    ap.add_argument("--out", default=".",
                    help="where the three files go (default: the current "
                         "directory -- run from the repo root, as CLAUDE.md asks)")
    ap.add_argument("--previews", default="previews",
                    help="where the two half-size PNGs go (default: %(default)s)")
    args = ap.parse_args(argv)

    entry = find_entry(args.entry, Path(args.library))
    if entry is None:
        ap.error(f"no library entry {args.entry!r}, as a path or under "
                 f"{args.library}/")

    raw, record = library.load(entry)
    corrected, info = library.corrected(entry)
    state = info.get("corrected")
    if state != "applied":
        # Refused, not written. With nothing applied the pair would show no
        # difference and read as "the correction does nothing"; with pixels
        # corrected before filing, `1_nothing_done` would not be raw at all.
        # Either way the pair would not show what this code does.
        print(f"{entry.name}: correction state is {state!r}, not 'applied' -- "
              f"the pair would not show what today's correction does to this "
              f"entry. Pick an entry filed with its shading reference.",
              file=sys.stderr)
        return 1

    dpi = (record.get("scan") or {}).get("resolution_dpi")
    resolution = int(dpi) if dpi else None
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"entry {entry.name}  {raw.shape}  {resolution} dpi")
    report = info.get("shading_report") or {}
    if report:
        print(f"shading: {report.get('columns')}/{report.get('width')} columns "
              f"corrected, {report.get('clipped')} samples clipped")

    for name, image in zip(NAMES, (raw, corrected, invert(corrected))):
        note = export.write(out / name, image, resolution=resolution)
        if note:
            print(note)

    # What was written, not what was computed.
    before = tiff.read(str(out / NAMES[0]))
    after = tiff.read(str(out / NAMES[1]))
    raw_c, raw_j = worst_colour(before)
    fix_c, fix_j = worst_colour(after)
    print(f"worst coloured column: {raw_c:.2f}% at {raw_j} -> {fix_c:.2f}% at {fix_j}")
    if fix_c > raw_c * 1.2:
        print("  WARNING: the correction made the colour fringing worse",
              file=sys.stderr)
    previews = write_previews(before, after, Path(args.previews))
    print(f"wrote {', '.join(str(out / n) for n in NAMES)} and "
          f"{', '.join(str(p) for p in previews)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
