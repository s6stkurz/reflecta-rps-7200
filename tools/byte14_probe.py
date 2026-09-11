#!/usr/bin/env python3
"""Drives every value byte 14 of MODE SELECT has ever been seen to carry.

    RPS7200_DEBUG=1 python3 tools/byte14_probe.py

Run once, 2026-09-11: the question it was written to answer (does the upper
nibble explain a 2.8x scan-time spread seen across three vendor captures) came
back no -- but bit 0 turned out to reverse every row of a pass, with nothing in
the response to say so, whenever it immediately follows another bit-0-set pass.
See `docs/byte14-plan.md` for the full result and what it means for real scans;
this docstring is the design as written before that was known.

**Do not run this again without reading that finding first.** Bit 0 set is
this driver's unconditional default for every RGBI scan (`0x21`), and running
the ladder again will reproduce reversed passes exactly as the first run did.

**Ask before running this.** Nothing moves: the transport is untouched and no
scan frame is advanced. `set_mode` already takes a `byte14` override, added for
exactly this and never used in normal operation.

What it does, at one fixed exposure throughout:

  * meters once, at 600 dpi RGB
  * then re-scans the same frame six times over, at byte14
    0x10, 0x20, 0x30, 0x11, 0x21, 0x31 -- every value any capture has ever
    sent, and nothing invented -- two repeats each so `noise_split` can tell
    a genuinely faster value from an exposure control by another name
  * a final 0x10 pass at the end, so drift over the ~5 minute run is visible
    rather than mistaken for an effect

Time alone is not the answer. A value that is faster is presumably faster for
a reason -- fewer samples per line, less settling -- so every value's pair is
also run through `noise_split`, exactly as `docs/analog-gain-plan.md`'s gain
ladder was. That ladder looked like free brightness and turned out to be a
digital multiplier worth 0.5%; the same test is what would catch byte 14 doing
the same thing with time instead of gain.

Nothing above 0x31 is sent, because no capture contains a larger value and
there is no reason to invent one -- `main()` refuses a `--ladder` that does.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / ".claude" / "skills" / "measure-scan-quality" / "scripts"))

from rps7200.direct import (                                        # noqa: E402
    FILM_NEGATIVE,
    FULL_FRAME,
    CheckCondition,
    DirectScanner,
)

from metrics import dark_mask, noise_split                          # noqa: E402

#: Every value the seven captures contain. 0x10 opens and closes the run so
#: drift is visible rather than mistaken for an effect; every other value is
#: repeated so noise_split has a pair.
LADDER = (0x10, 0x10, 0x20, 0x20, 0x30, 0x30,
          0x11, 0x11, 0x21, 0x21, 0x31, 0x31, 0x10)

#: No capture has ever sent a value above this. Refused outright.
MAX_BYTE14 = 0x31

#: Statistics are taken here, not over the whole frame: FULL_FRAME includes the
#: clear transport beside the film, which is not what is being timed or
#: measured for noise.
CROP = 0.25


def centre(image: np.ndarray) -> np.ndarray:
    h, w = image.shape[:2]
    return image[int(h * CROP):int(h * (1 - CROP)),
                 int(w * CROP):int(w * (1 - CROP))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resolution", type=int, default=600)
    ap.add_argument("--ladder", default=None,
                    help="comma-separated byte14 values, hex (0x..) or "
                         "decimal; repeat a value for the pair noise_split "
                         "needs. Default: every value ever captured")
    ap.add_argument("--json", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and exit without opening the device")
    args = ap.parse_args()

    ladder = (tuple(int(v, 0) for v in args.ladder.split(","))
              if args.ladder else LADDER)
    over = [v for v in ladder if v > MAX_BYTE14]
    if over:
        print(f"refusing: {[hex(v) for v in over]} exceed {hex(MAX_BYTE14)}, "
              f"which no capture has ever sent", file=sys.stderr)
        return 2

    if args.dry_run:
        print(f"would take {len(ladder)} passes at {args.resolution} dpi, "
              f"byte14 sequence {[hex(v) for v in ladder]}")
        return 0

    if not os.environ.get("RPS7200_DEBUG"):
        print("refusing to run without RPS7200_DEBUG=1: a probe that files "
              "nothing cannot be re-analysed, and this one is worth keeping",
              file=sys.stderr)
        return 2

    results: list[dict] = []
    images: dict[int, list[np.ndarray]] = {}
    scanner = DirectScanner(verbose=True)
    try:
        scanner.open()
        state = scanner.read_state()
        print(f"state: scanning={state.scanning:#04x} "
              f"media_loaded={state.media_loaded} position={state.position}")
        scanner.session_start()
        scanner.wait_warm()

        reference = scanner.get_gain_offset()
        print(f"device reference: exposure={reference.exposure} "
              f"gain={reference.gain}")

        scales = list(scanner.auto_exposure(
            resolution=args.resolution, film=FILM_NEGATIVE, infrared=False))
        print(f"\nexposure held at {[round(v, 3) for v in scales]} for "
              f"every pass\n")

        baseline_ms = None
        for i, byte14 in enumerate(ladder, 1):
            started = time.monotonic()
            image, meta = scanner.scan(
                resolution=args.resolution,
                infrared=False,
                frame=FULL_FRAME,
                exposure_scale=scales,
                auto_exposure=False,
                shading=False,
                keep_raw=True,
                byte14=byte14,
            )
            wall = time.monotonic() - started
            ms_per_line = 1000 * meta["duration_s"] / meta["height"]
            images.setdefault(byte14, []).append(centre(image))

            if byte14 == LADDER[0] and baseline_ms is None:
                baseline_ms = ms_per_line
            ratio = ms_per_line / baseline_ms if baseline_ms else 1.0

            print(f"  [{i:2}/{len(ladder)}] byte14={byte14:#04x}  "
                  f"height={meta['height']}  duration={meta['duration_s']:.1f}s "
                  f" ms/line={ms_per_line:6.2f}  ratio={ratio:.3f}  "
                  f"(wall {wall:.1f}s)")

            results.append({
                "index": i, "byte14": byte14, "resolution": args.resolution,
                "height": meta["height"], "duration_s": meta["duration_s"],
                "ms_per_line": round(ms_per_line, 4),
                "exposure": list(meta["exposure"]),
            })

    except BaseException as exc:                       # noqa: BLE001
        print(f"\nprobe stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
        if not isinstance(exc, (KeyboardInterrupt, CheckCondition)):
            raise
    finally:
        # Nothing to restore on the device -- byte14 is sent fresh with every
        # MODE SELECT, not a persisted register -- but end on the default
        # value regardless, as a guard against an effect that outlasts the
        # command it was sent in.
        try:
            scanner.scan(resolution=args.resolution, infrared=False,
                         frame=FULL_FRAME, exposure_scale=scales,
                         auto_exposure=False, shading=False, keep_raw=False,
                         byte14=None)
            print("\nfinal pass at the default byte14, for the device's sake")
        except BaseException as exc:                    # noqa: BLE001
            print(f"final default pass failed: {exc}", file=sys.stderr)
        try:
            scanner.close()
        except BaseException:                           # noqa: BLE001
            pass

    if not results:
        return 1

    print("\n" + "=" * 70)
    print("time and noise, per value (paired passes only)\n")
    print(f"{'byte14':>8}{'n':>4}{'mean ms/line':>14}{'vs 0x10':>9}   "
          f"{'random DN':>10}{'total DN':>10}  noise/signal")
    base_group = [r["ms_per_line"] for r in results if r["byte14"] == LADDER[0]]
    base = float(np.mean(base_group)) if base_group else None
    summary = []
    for byte14 in sorted(set(r["byte14"] for r in results)):
        rows = [r for r in results if r["byte14"] == byte14]
        mean_ms = float(np.mean([r["ms_per_line"] for r in rows]))
        ratio = mean_ms / base if base else 1.0

        pair = images.get(byte14, [])
        noise_str = "  (no pair)"
        rnd = total = share = None
        if len(pair) >= 2:
            mask = dark_mask(pair[0], 10.0)
            rnd, total, share = noise_split(pair[0], pair[1], mask, channel=1)
            sig = float(np.percentile(pair[0][..., 1], 99.5))
            per_signal = rnd / max(sig, 1e-9)
            noise_str = (f"{rnd:>10.1f}{total:>10.1f}  {per_signal:.4f} "
                        f"(random share {share:.0%})")

        print(f"{byte14:#08x}{len(rows):>4}{mean_ms:>14.2f}{ratio:>9.3f}   "
              f"{noise_str}")
        summary.append({"byte14": byte14, "n": len(rows), "mean_ms_per_line": mean_ms,
                        "ratio_to_0x10": ratio, "random_dn": rnd,
                        "total_dn": total, "random_share": share})

    print(f"\ndrift check: first 0x10 pass {results[0]['ms_per_line']:.2f} "
          f"ms/line, last {[r for r in results if r['byte14']==LADDER[0]][-1]['ms_per_line']:.2f} "
          f"ms/line")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"passes": results, "summary": summary}, indent=2))
        print(f"\nwritten to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
