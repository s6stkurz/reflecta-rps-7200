#!/usr/bin/env python3
"""What resolution is worth scanning at, measured from a dpi ladder.

    uv run python tools/dpi_analysis.py --out probe/dpi

Part 3 of `docs/dpi-tradeoff-plan.md`. Reads a ladder already in the library and
answers three ways, because no single measurement is conclusive:

  A  where real detail meets the noise floor      (absolute, one pass)
  B  aliasing at each resolution's own Nyquist    (relative, needs no calibration)
  C  what each step actually adds                 (within one pass)

B is the one that decides it. A needs an absolute noise floor and C needs a
faithful downsample; B needs only that every pass shows the same picture, and
compares each resolution against the highest one at the *same physical frequency*.

Spectra rather than pixel differences, because passes sit at different column
offsets -- two 3600 dpi passes once correlated at r=0.936 only after a 16-column
shift, still unexplained. Power spectra are shift invariant. C stays inside one
pass, so registration cannot reach it.

No scanner. Everything comes from stored entries, which is the point of keeping
the raw bytes: when shading changes and the floor drops, re-run this.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                      / ".claude/skills/measure-scan-quality/scripts"))

import numpy as np

from metrics import dark_mask, noise_split
from rps7200 import library
from rps7200.console import use_utf8_stdout

MM_PER_INCH = 25.4
CHANNELS = ("red", "green", "blue")


def ladder(root: Path, after: str, before: str) -> list[dict]:
    """The RGB passes of the series, plus whatever RGBI exists for timing."""
    out = []
    for meta in sorted(root.glob("*/scan.json")):
        d = json.loads(meta.read_text(encoding="utf-8"))
        s = d.get("scan") or {}
        if not (after <= d["created"] <= before):
            continue
        if s.get("resolution_dpi") is None or s.get("channels") is None:
            continue
        out.append({
            "dir": meta.parent,
            "dpi": int(s["resolution_dpi"]),
            "channels": int(s["channels"]),
            "seconds": s.get("duration_s"),
            "created": d["created"],
            "exposure": (d.get("device_settings") or {}).get("exposure"),
            "bytes": (meta.parent / "scan.tif").stat().st_size,
        })
    out.sort(key=lambda e: (e["channels"], e["dpi"], e["created"]))
    return out


def plane(entry: dict, channel: int) -> np.ndarray:
    """One channel, read back from the delivered file."""
    a, _ = library.corrected(entry["dir"])
    return a[:, :, channel].astype(np.float64)


def radial_spectrum(p: np.ndarray, dpi: int) -> tuple[np.ndarray, np.ndarray]:
    """Row-averaged power *density* against cycles/mm.

    Row-averaged rather than 2-D: the sensor is a line, so its behaviour along a
    row is what resolution means here, and averaging rows beats down noise
    without touching the frequency axis.

    **Density, not raw power.** `rfft` does not normalise, so raw power scales
    with the row length squared and the bins are wider at low resolution. Two
    passes of one picture would then differ by the ratio of their widths rather
    than by anything optical -- which is exactly what a first version of this did,
    reporting 1.42 at 3600 dpi where a resolving pass must read ~1.0. Dividing by
    the window energy and the bin width makes the value a density in DN^2 per
    (cycle/mm), which is comparable across resolutions by construction.
    """
    p = p - p.mean(axis=1, keepdims=True)
    n = p.shape[1]
    win = np.hanning(n)
    spec = np.abs(np.fft.rfft(p * win[None, :], axis=1)) ** 2
    power = spec.mean(axis=0)
    dx = MM_PER_INCH / dpi                        # mm per sample
    freq = np.fft.rfftfreq(n, d=dx)               # cycles/mm
    df = freq[1] - freq[0]
    # |X|^2 * dx / (N * mean(win^2)). Leaving out the N leaves the ratio
    # scaling as dpi/7200 -- which is what the sanity band below caught, reading
    # 0.04 at 300 dpi where it must read ~1.
    power = power / (np.sum(win ** 2) * df * n)
    return freq[1:], power[1:]


def band_density(freq: np.ndarray, power: np.ndarray,
                 lo: float, hi: float) -> float:
    """Mean density in a physical band, or nan if the pass does not reach it."""
    sel = (freq > lo) & (freq <= hi)
    return float(power[sel].mean()) if sel.any() else float("nan")


def main() -> int:
    use_utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="library")
    ap.add_argument("--after", default="2026-09-11T10:19")
    ap.add_argument("--before", default="2026-09-11T10:45")
    ap.add_argument("--out", default="probe/dpi")
    ap.add_argument("--crop", type=int, default=1024,
                    help="side of the detailed crop taken from the 7200 dpi pass")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    series = ladder(Path(args.root), args.after, args.before)
    rgb = [e for e in series if e["channels"] == 3]
    if len(rgb) < 3:
        print(f"only {len(rgb)} RGB passes found -- nothing to compare")
        return 1

    top = max(rgb, key=lambda e: e["dpi"])
    repeats = [e for e in rgb if e["dpi"] == top["dpi"]]

    print("=" * 72)
    print("the series")
    print("=" * 72)
    print(f"{'dpi':>6} {'ch':>3} {'pixels':>13} {'seconds':>8} {'MB':>7}  exposure R,G,B")
    for e in series:
        px = (library.corrected(e["dir"])[0].shape if e["dpi"] <= 600 else None)
        shape = f"{px[1]}x{px[0]}" if px else ""
        exp = e["exposure"][:3] if e["exposure"] else None
        print(f"{e['dpi']:6d} {e['channels']:3d} {shape:>13} "
              f"{str(e['seconds']):>8} {e['bytes']/1e6:7.1f}  {exp}")

    # ---- the noise floor, from the repeat pair ---------------------------
    print()
    print("=" * 72)
    print("noise floor, measured from the repeat pair (not guessed from the spectrum)")
    print("=" * 72)
    floors = {}
    if len(repeats) >= 2:
        a, _ = library.corrected(repeats[0]["dir"])
        b, _ = library.corrected(repeats[1]["dir"])
        mask = dark_mask(a)
        for c, name in enumerate(CHANNELS):
            rnd, total, share = noise_split(a, b, mask, channel=c)
            floors[name] = rnd
            print(f"  {name:>5}: random {rnd:8.1f} DN   total {total:8.1f} DN"
                  f"   random share {share:5.1%}")
        del a, b
    else:
        print("  no repeat pair -- A and C fall back to a spectral estimate")

    # ---- B: aliasing at each resolution's own Nyquist --------------------
    print()
    print("=" * 72)
    print("B  aliasing: power near each pass's own Nyquist, against 7200 dpi there")
    print("   ~1.0 = resolving.  >>1 = folding energy back.")
    print("=" * 72)
    results: dict[str, dict[int, float]] = {}
    for c, name in enumerate(CHANNELS):
        ref_f, ref_p = radial_spectrum(plane(top, c), top["dpi"])
        results[name] = {}
        for e in rgb:
            if e["dpi"] == top["dpi"]:
                continue
            f, p = radial_spectrum(plane(e, c), e["dpi"])
            nyq = e["dpi"] / (2 * MM_PER_INCH)            # cycles/mm

            # Sanity: well below Nyquist both passes resolve the same picture, so
            # the densities must already agree. If they do not, the comparison is
            # broken and no reading from it means anything.
            low = band_density(f, p, nyq * 0.05, nyq * 0.15)
            ref_low = band_density(ref_f, ref_p, nyq * 0.05, nyq * 0.15)
            sanity = low / ref_low if ref_low else float("nan")

            near = band_density(f, p, nyq * 0.75, nyq)
            ref_near = band_density(ref_f, ref_p, nyq * 0.75, nyq)
            results[name][e["dpi"]] = {
                "ratio": float(near / ref_near) if ref_near else float("nan"),
                "sanity": float(sanity),
            }
    hdr = sorted({d for v in results.values() for d in v})
    print(f"  {'channel':>7} " + " ".join(f"{d:>8}" for d in hdr))
    for name in CHANNELS:
        row = " ".join(f"{results[name][d]['ratio']:8.2f}" if d in results[name]
                       else f"{'--':>8}" for d in hdr)
        print(f"  {name:>7} {row}")
    print()
    print("  sanity -- density well BELOW Nyquist, which must already be ~1.00:")
    for name in CHANNELS:
        row = " ".join(f"{results[name][d]['sanity']:8.2f}" if d in results[name]
                       else f"{'--':>8}" for d in hdr)
        print(f"  {name:>7} {row}")

    # ---- A: where detail meets the measured noise floor ------------------
    print()
    print("=" * 72)
    print("A  where real detail meets the noise floor (from the 7200 dpi pass)")
    print("=" * 72)
    absolute = {}
    full, _ = library.corrected(top["dir"])
    h, w, _ = full.shape
    half = args.crop // 2
    cy, cx = h // 2, w // 2
    crop = full[cy - half:cy + half, cx - half:cx + half, :].astype(np.float64)
    del full
    print(f"  crop {crop.shape[1]}x{crop.shape[0]} from the centre of the 7200 dpi pass")
    for c, name in enumerate(CHANNELS):
        f, p = radial_spectrum(crop[:, :, c], top["dpi"])
        # The floor is white, so its density is flat: take it from the top decade
        # of the band, where detail has certainly gone.
        floor = float(np.median(p[f > f.max() * 0.8]))
        signal = p - floor
        # The limit is where detail drops to the floor itself.
        above = np.where(signal > floor)[0]
        limit = float(f[above[-1]]) if above.size else float("nan")
        absolute[name] = {"limit_cycles_per_mm": limit,
                          "dpi_to_sample": limit * 2 * MM_PER_INCH,
                          "floor_density": floor}
        print(f"  {name:>5}: detail to {limit:6.1f} c/mm"
              f"  -> needs {limit * 2 * MM_PER_INCH:6.0f} dpi to sample")

    # ---- C: what each step adds, inside one pass -------------------------
    print()
    print("=" * 72)
    print("C  what each step adds: downsample the 7200 crop, restore, compare")
    print("   residual below the noise sigma = that step adds nothing real")
    print("=" * 72)
    steps = {}
    print(f"  {'dpi':>6} {'red':>18} {'green':>18} {'blue':>18}")
    for dpi in [e["dpi"] for e in rgb if e["dpi"] < top["dpi"]]:
        factor = top["dpi"] // dpi
        if factor < 2:
            continue
        row = []
        steps[dpi] = {}
        for c, name in enumerate(CHANNELS):
            g = crop[:, :, c]
            n = (g.shape[0] // factor) * factor
            small = g[:n, :n].reshape(n // factor, factor,
                                      n // factor, factor).mean(axis=(1, 3))
            back = np.repeat(np.repeat(small, factor, axis=0), factor, axis=1)
            resid = float(np.std(g[:n, :n] - back))
            sigma = floors.get(name, float("nan"))
            steps[dpi][name] = {"residual_dn": resid, "noise_sigma": sigma,
                                "ratio": resid / sigma if sigma else float("nan")}
            row.append(f"{resid:8.0f} /{sigma:6.0f} ={resid/sigma:4.1f}"
                       if sigma else f"{resid:8.0f}")
        print(f"  {dpi:6d} " + " ".join(f"{r:>18}" for r in row))
    print()
    print("  ratio > 1 means the detail lost by dropping to that resolution is")
    print("  larger than the noise -- real information. Below 1, it is noise.")

    (out / "results.json").write_text(json.dumps(
        {"aliasing": results, "noise_floor_dn": floors,
         "absolute": absolute, "steps": steps,
         "series": [{k: (str(v) if isinstance(v, Path) else v)
                     for k, v in e.items()} for e in series]}, indent=2),
        encoding="utf-8")
    print(f"\nwritten to {out / 'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
