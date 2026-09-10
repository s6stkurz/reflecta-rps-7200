#!/usr/bin/env python3
"""What exposure target this scanner can actually carry, measured offline.

    python3 tools/exposure_headroom.py
    python3 tools/exposure_headroom.py --entries 6 --json headroom.json

`auto_exposure` aims the 99.5th percentile of each channel at 0.70 of full
scale. That is the most conservative figure of any driver read for comparison
-- pieusb uses 0.85, nkscan 0.97 -- and a third of a stop of range is not
nothing on a dense negative, where the shadows are what run out of bits.

Copying pieusb's 0.85 would be a guess, though, and it would be *their* guess.
Their comment says why they chose it (scanner.py:70-72):

    Below 1.0 to leave room for specular highlights and for the shading
    correction's per-column gain, which clips edge columns first.

We run the same kind of correction, so the same effect applies here -- but with
our lamp, our falloff and our reference. So measure it rather than inherit it.

**No scanner needed.** Every library entry keeps the scanner's own bytes plus
the shading reference and CCD mask that were live when it was taken, which is
exactly what makes this re-runnable: a higher exposure is simulated on the raw
pixels, the *real* correction is applied, and the clipping is read off the
report the correction already produces.

The simulation is the physical one, not a multiply:

    raw' = (raw - dark) * k + dark

The dark offset does not scale with exposure -- it is the sensor's floor, and
the entry's own two-point reference measured it per column. Scaling it along
with the signal would overstate clipping at every k. The signal above it is
linear in exposure: measured r^2 = 0.99995-0.99998 over a 4x range on the
3600 dpi ladder in the library, with per-channel intercepts of 1006, 580 and
191 DN.

What it cannot tell you: whether a higher target looks better. It reports where
the correction starts throwing samples away, which is the constraint, not the
verdict. Stefan judges the pictures.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from collections.abc import Sequence
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rps7200 import library                                          # noqa: E402
from rps7200.direct import (                                        # noqa: E402
    BLUE_RGBI_HEADROOM,
    CHANNEL_ORDER,
    EXPOSURE_TARGET,
    DirectScanner,
    locks_white_balance,
)
from rps7200.protocol import ScanParameters                         # noqa: E402
from rps7200.shading import ShadingReference, apply_shading         # noqa: E402

#: Targets to try, as a fraction of full scale. 0.70 is what ships, 0.85 is
#: pieusb's, 0.97 is nkscan's; the rest fill in between so the knee is visible
#: rather than inferred from three points.
TARGETS = (0.70, 0.75, 0.80, 0.85, 0.90, 0.95)

#: Where the metering percentile is read. The same one auto_exposure uses, so
#: the number here means the same thing as the number there.
PERCENTILE = 99.5

FULL_SCALE = 65535.0

#: Blue's measured RGBI/RGB ratio, needed here to model where blue lands rather
#: than only where it is aimed. See BLUE_RGBI_HEADROOM for the measurement.
MEASURED_BLUE_RATIO = 4.98


def decode(path: Path) -> tuple[np.ndarray, dict[str, Any]] | None:
    """An entry's raw pixels and its record, or None if it has no bytes."""
    record = json.loads((path / "scan.json").read_text())
    raw = library.read_raw(path)
    if raw is None:
        return None
    layout = (record.get("raw") or {}).get("layout") or {}
    try:
        params = ScanParameters(
            width=int(layout["width"]),
            lines=int(layout["lines"]),
            bytes_per_line=int(layout["bytes_per_line"]),
            filter_offset1=0,
            filter_offset2=0,
            available_lines=0,
        )
        image = DirectScanner._deinterleave(raw, params, int(layout["channels"]))
    except (KeyError, ValueError, TypeError):
        return None
    return image, record


def calibration(path: Path, record: dict[str, Any]):
    """The entry's own reference and mask -- the ones live when it was taken."""
    cal = record.get("calibration") or {}
    ref_file, mask_file = cal.get("shading"), cal.get("ccd_mask")
    if not ref_file or not (path / ref_file).exists():
        return None, None
    reference = ShadingReference.load(path / ref_file)
    mask = ((path / mask_file).read_bytes()
            if mask_file and (path / mask_file).exists() else None)
    return reference, mask


def dark_floor(reference: ShadingReference, channel: int, width: int) -> np.ndarray:
    """Per-column dark offset, or zero where the reference has only one point."""
    if channel in reference.dark:
        dark = reference.dark[channel]
        return dark[:width] if dark.size >= width else np.zeros(width)
    return np.zeros(width)


def scale_exposure(
    image: np.ndarray, reference: ShadingReference, k: Sequence[float]
) -> np.ndarray:
    """Simulate the same frame taken at ``k[c]`` times the exposure."""
    out = np.empty_like(image)
    for c in range(image.shape[2]):
        dark = dark_floor(reference, c, image.shape[1])[None, :]
        vals = (image[..., c].astype(np.float64) - dark) * k[c] + dark
        np.clip(vals, 0, FULL_SCALE, out=vals)
        out[..., c] = vals.astype(image.dtype)
    return out


def wanted(target: float, achieved: Sequence[float], channels: int,
           locked: bool) -> list[float]:
    """Per-channel k, modelling what metering would actually command.

    One k for the whole frame would be wrong, and wrong in a way that looks
    alarming: a negative is metered **per channel**, and in RGBI blue is aimed
    lower still. Scaling every channel by the factor red and green need drags
    blue up with them, so an entry taken under the old headroom -- where blue
    already sat near 90% -- reads as though raising the target blows blue out.
    It is the old constant showing through, not the target.

    A locked film (slide, Kodachrome, B&W) is the opposite case and is modelled
    as it is metered: one factor for all three, so the cast survives.
    """
    aims = [target] * channels
    if channels == 4:
        # What blue is aimed at, and where it therefore lands: the divisor sits
        # a little above the measured ratio on purpose, so blue comes in just
        # under the others. See BLUE_RGBI_HEADROOM.
        aims[2] = target * MEASURED_BLUE_RATIO / BLUE_RGBI_HEADROOM
        aims[3] = achieved[3]           # infrared is never metered
    if locked:
        one = min(a / m for a, m in zip(aims[:3], achieved[:3]) if m > 0)
        return [one] * 3 + ([1.0] if channels == 4 else [])
    return [a / m if m > 0 else 1.0 for a, m in zip(aims, achieved)]


def study(path: Path, targets=TARGETS) -> dict[str, Any] | None:
    """One entry, at every target. Returns None if it cannot be studied."""
    got = decode(path)
    if got is None:
        return None
    image, record = got
    reference, mask = calibration(path, record)
    if reference is None:
        return None

    channels = image.shape[2]
    names = list(CHANNEL_ORDER[:channels])

    # Where this entry actually landed, per channel, after its own correction.
    # That is the anchor: a target is only meaningful relative to what the
    # metering it was taken under achieved.
    base, _ = apply_shading(image, reference, mask)
    achieved = [
        float(np.percentile(base[..., c], PERCENTILE)) / FULL_SCALE
        for c in range(channels)
    ]
    if max(achieved) <= 0:
        return None
    film = (record.get("scan") or {}).get("film") or "negative"
    try:
        locked = locks_white_balance(film)
    except ValueError:
        locked = False

    rows = []
    for target in targets:
        k = wanted(target, achieved, channels, locked)
        lifted = scale_exposure(image, reference, k)
        corrected, report = apply_shading(lifted, reference, mask)
        per = report.get("clipped_per_channel") or [0] * channels
        samples = corrected[..., 0].size
        rows.append({
            "target": target,
            "k": [round(v, 4) for v in k],
            "clipped_pct": [round(100 * n / samples, 4) for n in per],
            "level": [
                round(float(np.percentile(corrected[..., c], PERCENTILE)) / FULL_SCALE, 4)
                for c in range(channels)
            ],
        })

    return {
        "entry": path.name,
        "resolution_dpi": (record.get("scan") or {}).get("resolution_dpi"),
        "channels": names,
        "achieved": [round(v, 4) for v in achieved],
        "rows": rows,
    }


def report(results: list[dict[str, Any]]) -> None:
    for r in results:
        print(f"\n{r['entry']}  {r['resolution_dpi']} dpi  "
              f"{'/'.join(r['channels'])}")
        print(f"  as taken, {PERCENTILE} percentile landed at "
              + "  ".join(f"{n}={v:.0%}" for n, v in
                          zip(r["channels"], r["achieved"])))
        head = "  target   " + "".join(f"{n + ' clip%':>10}" for n in r["channels"])
        print(head)
        floor = r["rows"][0]["clipped_pct"]
        for row in r["rows"]:
            mark = "  <- ships" if row["target"] == EXPOSURE_TARGET else ""
            print(f"  {row['target']:>5.0%}   "
                  + "".join(f"{p:>10.3f}" for p in row["clipped_pct"]) + mark)
        gained = [b - a for a, b in zip(floor, r["rows"][-1]["clipped_pct"])]
        print(f"  cost of {r['rows'][0]['target']:.0%} -> "
              f"{r['rows'][-1]['target']:.0%}: "
              + "  ".join(f"{n}+{g:.3f}" for n, g in zip(r["channels"], gained))
              + "\n  (absolutes include the transport gate beside the film, "
                "which is clear and saturates whatever the exposure -- the "
                "increase is the part that is about the target)")

    if len(results) < 2:
        print("\nOnly one entry studied. A sensor effect and picture content "
              "cannot be told apart from one frame -- run this over entries "
              "from at least two different frames before trusting it.")
        return

    # The recommendation is the highest target no channel of any entry clips
    # meaningfully at. Stated per channel, because blue is the one that binds.
    print("\nworst case across every entry studied:")
    names = results[0]["channels"]
    print("  target   " + "".join(f"{n + ' clip%':>10}" for n in names))
    for i, target in enumerate(TARGETS):
        worst = [
            max(r["rows"][i]["clipped_pct"][c] for r in results
                if c < len(r["rows"][i]["clipped_pct"]))
            for c in range(len(names))
        ]
        print(f"  {target:>5.0%}   " + "".join(f"{p:>10.3f}" for p in worst))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(library.DEFAULT_ROOT),
                    help="library root (default: %(default)s)")
    ap.add_argument("--entries", type=int, default=4,
                    help="how many entries to study (default: %(default)s)")
    ap.add_argument("--dpi", type=int, default=None,
                    help="only entries at this resolution")
    ap.add_argument("--json", default=None, help="also write the results here")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"no library at {root}", file=sys.stderr)
        return 1

    results = []
    for path in sorted(root.glob("2*/"), reverse=True):
        if len(results) >= args.entries:
            break
        record_path = path / "scan.json"
        if not record_path.exists():
            continue
        if args.dpi is not None:
            record = json.loads(record_path.read_text())
            if (record.get("scan") or {}).get("resolution_dpi") != args.dpi:
                continue
        got = study(path)
        if got is not None:
            results.append(got)

    if not results:
        print("no entry had both raw bytes and a shading reference",
              file=sys.stderr)
        return 1

    report(results)
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2))
        print(f"\nwritten to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
