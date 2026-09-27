#!/usr/bin/env python3
"""How far this sensor departs from linear, by level, from stored passes.

    uv run python tools/linearity.py --match '20260904T1043*_slide_3600dpi'
    uv run python tools/linearity.py --probe probe/exposure.json     # a whole strip

**No scanner.** It reads an exposure ladder already in the library and measures
the one thing that decided `EXPOSURE_TARGET`: whether a pass at twice the
exposure really is twice as bright, at every level.

This exists because that figure had no derivation. `rps7200/direct.py` says the
sensor "compresses 1.5-1.9%" in the 75-95% band and `TODO.md` repeats it, both as
prose, from a run whose code was never kept -- so the number that chose 0.80 over
0.90 could not be checked. It can now.

### What it measures, and why this way

**Adjacent rungs, and the ratio between them.** A linear sensor gives the same
ratio at every level: if a pixel at 20% of scale goes up by x1.19, so does one at
85%. So the measure is the achieved ratio per level band, relative to the ratio
in the darkest band, and a linear sensor reads 0.00% everywhere.

Adjacent rather than against one reference, because a fit made on dark pixels and
extrapolated to bright ones is asking the data for something it does not contain.
The first version of this did that and reported -16% to -26%, which was the fit
falling apart rather than a sensor bending.

**Saturation is not compression, and telling them apart is most of the job.**
Anything at or near the rail is dropped, because a channel pinned at 65535 reads
as enormous compression and is nothing of the kind. On the slide ladder red pins
its median at 0.91 from the fifth rung on; included, it turns a sub-1% effect into
a -16% one.

**Raw by default, not corrected.** The correction's per-column gain exceeds 1
wherever the lamp falls off, so it pushes near-rail values around and inflates
exactly the band under test. `--corrected` measures `library.corrected`'s
recomputation instead -- today's correction on each entry's own reference,
what the delivered pixels would be, not a delivered file -- which is a
different and also useful question, and the two disagree, which is worth
seeing. An entry that recomputation does not correct ("deliberately raw", "no
reference", "already") is left out and named, rather than measured as though
it were: a series mixing them compares the correction, not the sensor.

**Registered, pair by pair.** A ratio per pixel needs the same film position in
both passes, and consecutive passes of one frame do not give it: the carriage
start has crept 0 to -3 lines across six passes here (`fast_ir_probe`'s
report). Unregistered, edge pixels paired a bright place with a darker one and
landed in the upper bands with inflated ratios -- biasing exactly the 70-95%
departures the target was chosen on. Each adjacent pair is registered and
cropped to its overlap first, and the shift is printed.

### `--probe`: one ladder per frame, and the pictures kept apart

`--match` assumes every entry it finds is the same frame, which was true of a
stored bracket and is false of a strip walk. `tools/exposure_probe.py` files four
ladders and fifteen anchor passes into one glob, and chaining those by exposure
alone would divide one photograph by a different one -- a ratio per pixel needs
the same picture at the same registration, or it measures the subject.

So `--probe` reads the walk's own JSON and groups by frame. It joins back to the
library on `device_settings.exposure`, not on entry names: entries are filed after
`close()` and named from flush time, so their order says nothing about capture
order, while the exposure triple is unique per frame and rung because every frame
metered for itself.

It also drops the `x1.00` anchor from any frame that has a real chain. The anchor
is in the run for three other reasons -- the shipped pass, the repeat pair, the
drift check -- but it sits 4% from `x0.96`, and an adjacent-ratio measure handed a
pair that close divides one noisy number by another and reports the quotient as
compression. That is the same error as a saturated top rung, reached from the
other end.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rps7200 import library                                 # noqa: E402
from rps7200.console import use_utf8_stdout
from rps7200.uniformity import align, register              # noqa: E402

#: Level bands, as fractions of full scale. Finer at the top, which is where the
#: question is: 0.80 was chosen over 0.90 on what happens between them.
BANDS = ((0.10, 0.25), (0.25, 0.40), (0.40, 0.55), (0.55, 0.70),
         (0.70, 0.80), (0.80, 0.90), (0.90, 0.97))

#: Anything at or above this is treated as saturated and dropped. Below the rail
#: on purpose: a pixel one count short of 65535 is already not reporting a level.
SATURATED = 0.985

#: Below this a pixel is too close to the dark floor for a ratio to mean much --
#: the floor does not scale with exposure, so it biases every ratio taken near it.
FLOOR = 1500

#: Pixels a band needs before its median is reported rather than skipped.
MIN_PIXELS = 400

#: The middle of the frame, away from the clear transport beside the film.
CROP = 0.3


def middle(image: np.ndarray) -> np.ndarray:
    """The centre of a frame, away from the clear transport beside the film."""
    height, width = image.shape[:2]
    return image[int(height * CROP):int(height * (1 - CROP)),
                 int(width * CROP):int(width * (1 - CROP))]


class NotCorrected(ValueError):
    """An entry `--corrected` cannot measure: `library.corrected` left it as
    it was, so its pixels are not the corrected ones the series claims."""


def pixels(entry: Path, corrected: bool) -> np.ndarray | None:
    if not corrected:
        return library.decode_raw(entry)
    image, record = library.corrected(entry)
    state = record.get("corrected")
    if state != "applied":
        raise NotCorrected(f"{entry.name}: correction state {state!r}, not "
                           "'applied' -- left out")
    return image


def aligned(dark: np.ndarray, bright: np.ndarray,
            ) -> tuple[np.ndarray, np.ndarray, tuple[int, int]]:
    """One adjacent pair, cropped to where both look at the same film."""
    dy, dx, _confidence = register(dark, bright)
    a, b = align(dark, bright, dy, dx)
    return a, b, (dy, dx)


def ladder(match: str, root="library", corrected: bool = False) -> list[dict]:
    """Every entry matching `match`, in commanded-exposure order.

    Assumes they are all one frame, which is true of a stored bracket and false
    of a strip walk -- see `by_frame`.
    """
    out = []
    for entry in sorted(Path(root).glob(match)):
        try:
            record = json.loads((entry / "scan.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        scale = (record.get("scan") or {}).get("exposure_scale")
        if isinstance(scale, list):
            scale = scale[0] if scale else None
        if scale is None:
            continue
        try:
            image = pixels(entry, corrected)
        except NotCorrected as exc:
            print(f"  {exc}", file=sys.stderr)
            continue
        if image is None:
            continue
        out.append({"name": entry.name, "scale": float(scale),
                    "image": middle(image)})
    out.sort(key=lambda p: p["scale"])
    return out


def entries_by_exposure(root="library") -> dict[tuple[int, ...], list[Path]]:
    """Every entry that records a device exposure, keyed by that triple.

    A list per key, not one entry: a ladder's two `x1.00` anchor passes are the
    same commanded exposure by design, so a key that mapped to one path would
    silently discard half of the repeat pair.
    """
    out: dict[tuple[int, ...], list[Path]] = {}
    directory = Path(root)
    if not directory.is_dir():
        return out
    for entry in sorted(directory.iterdir()):
        try:
            record = json.loads((entry / "scan.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, NotADirectoryError):
            continue
        exposure = (record.get("device_settings") or {}).get("exposure")
        if not exposure:
            continue
        out.setdefault(tuple(int(v) for v in exposure[:3]), []).append(entry)
    return out


def by_frame(probe: str, root="library",
             corrected: bool = False) -> tuple[dict[int, list[dict]], list[str]]:
    """The walk's ladders, one list of passes per frame. Also what went wrong.

    Grouped by frame because a ratio per pixel needs the same picture: chaining
    across frames would divide one photograph by another and measure the subject.
    The `x1.00` anchor is dropped from any frame with a real chain -- it sits 4%
    from `x0.96`, too close for an adjacent ratio to mean anything.
    """
    data = json.loads(Path(probe).read_text(encoding="utf-8"))
    found = entries_by_exposure(root)
    rows_by_frame: dict[int, list[dict]] = {}
    for row in data.get("passes", []):
        rows_by_frame.setdefault(int(row["frame"]), []).append(row)

    out: dict[int, list[dict]] = {}
    notes: list[str] = []
    for frame, rows in sorted(rows_by_frame.items()):
        rungs = [r for r in rows if float(r["rung"]) != 1.0]
        if len(rungs) < 2:
            continue                # an anchor alone cannot give a ratio
        passes = []
        for row in rungs:
            key = tuple(int(v) for v in row["exposure"][:3])
            candidates = found.get(key, [])
            if not candidates:
                notes.append(f"frame {frame} x{row['rung']:.2f}: no entry with "
                             f"exposure {list(key)}")
                continue
            if len(candidates) > 1:
                # Two frames that metered to the same triple. Possible, and the
                # join cannot tell them apart, so say so rather than pick.
                notes.append(f"frame {frame} x{row['rung']:.2f}: "
                             f"{len(candidates)} entries share exposure "
                             f"{list(key)}; used {candidates[0].name}")
            try:
                image = pixels(candidates[0], corrected)
            except NotCorrected as exc:
                notes.append(f"frame {frame} x{row['rung']:.2f}: {exc}")
                continue
            if image is None:
                notes.append(f"frame {frame} x{row['rung']:.2f}: "
                             f"{candidates[0].name} has no readable pixels")
                continue
            passes.append({"name": candidates[0].name,
                           "scale": float(row["rung"]),
                           "image": middle(image)})
        passes.sort(key=lambda p: p["scale"])
        if len(passes) >= 2:
            out[frame] = passes
        else:
            notes.append(f"frame {frame}: only {len(passes)} of "
                         f"{len(rungs)} rungs found")
    return out, notes


def report_walk(frames: dict[int, list[dict]], notes: list[str],
                corrected: bool) -> int:
    """Every frame's ladder, then the bands where they agree.

    One frame's departure is a reading; the same departure on four different
    photographs is the sensor. That is the same argument the library settles
    sensor-versus-picture with everywhere else -- a fixed effect across different
    film positions -- applied to level instead of to column.
    """
    if not frames:
        for note in notes:
            print(f"  {note}", file=sys.stderr)
        print("no frame had two rungs in the library", file=sys.stderr)
        return 1

    per_channel: dict[str, list[tuple[int, float]]] = {}
    for frame, passes in sorted(frames.items()):
        print("=" * 78)
        print(f"frame {frame}")
        report(passes, corrected)
        for channel, name in enumerate("RGB"):
            if passes[0]["image"].ndim < 3 or passes[0]["image"].shape[2] <= channel:
                continue
            worst = 0.0
            for i in range(len(passes) - 1):
                a, b, _shift = aligned(passes[i]["image"],
                                       passes[i + 1]["image"])
                values = departures(a[..., channel], b[..., channel])
                for (low, _), value in zip(BANDS, values):
                    if value is not None and low >= 0.70:
                        worst = min(worst, value)
            per_channel.setdefault(name, []).append((frame, worst))

    print("=" * 78)
    print("largest departure at or above 70% of scale, per frame\n")
    for name, readings in per_channel.items():
        cells = "  ".join(f"f{frame}{value:+6.2f}%" for frame, value in readings)
        values = [v for _, v in readings]
        spread = max(values) - min(values) if values else 0.0
        print(f"  {name}  {cells}   spread {spread:.2f}%")
    print("\nA departure that repeats across frames is the sensor; one that does")
    print("not is the picture. Judge either against the drift the probe printed")
    print("for each frame's own two x1.00 passes -- below that, it is not a")
    print("reading.")
    if notes:
        print(f"\n{len(notes)} note(s):")
        for note in notes:
            print(f"  {note}")
    return 0


def departures(dark: np.ndarray, bright: np.ndarray) -> list[float | None]:
    """Departure from linear per band, as a percentage, for one channel pair.

    `None` where a band has too few pixels to report, which is ordinary: a dark
    rung has nothing in the top bands and a bright one nothing in the bottom.
    """
    x = dark.astype(np.float64)
    y = bright.astype(np.float64)
    usable = (x > FLOOR) & (y > FLOOR) & (y < SATURATED * 65535)
    out: list[float | None] = []
    base = None
    for low, high in BANDS:
        band = usable & (y >= low * 65535) & (y < high * 65535)
        if band.sum() < MIN_PIXELS:
            out.append(None)
            continue
        ratio = float(np.median(y[band] / x[band]))
        if base is None:
            base = ratio
        out.append(100.0 * (ratio / base - 1.0) if base else None)
    return out


def report(passes: list[dict], corrected: bool) -> int:
    if len(passes) < 2:
        print("need at least two passes of one frame at different exposures",
              file=sys.stderr)
        return 1
    print(f"{len(passes)} passes, "
          f"x{passes[0]['scale']:.3f} to x{passes[-1]['scale']:.3f}, "
          f"{'corrected' if corrected else 'raw'} pixels, "
          f"crop {passes[0]['image'].shape}")
    print("\na linear sensor reads 0.00% in every band\n")
    worst: dict[str, float] = {}
    for channel, name in enumerate("RGB"):
        if passes[0]["image"].ndim < 3 or passes[0]["image"].shape[2] <= channel:
            continue
        print(f"{name}")
        print(f"{'rung':>16}{'ratio':>7}  "
              + "".join(f"{f'{lo:.0%}-{hi:.0%}':>10}" for lo, hi in BANDS))
        for i in range(len(passes) - 1):
            dark, bright = passes[i], passes[i + 1]
            a, b, (dy, dx) = aligned(dark["image"], bright["image"])
            values = departures(a[..., channel], b[..., channel])
            cells = "".join(f"{v:9.2f}%" if v is not None else f"{'--':>10}"
                            for v in values)
            # Labelled by the exposures rather than by the entry names: entries
            # taken in one second share a timestamp and differ only by a `-2`
            # suffix, so names made two rows read identically.
            print(f"x{dark['scale']:<6.3f}->x{bright['scale']:<6.3f}"
                  f"{bright['scale'] / dark['scale']:7.3f}  {cells}"
                  f"   shift {dy:+d},{dx:+d}")
            # The top band each pair can still speak for, which is the figure
            # the target was chosen on.
            for (low, _), value in zip(BANDS, values):
                if value is not None and low >= 0.70:
                    worst[name] = min(worst.get(name, 0.0), value)
        print()
    if worst:
        print("largest departure at or above 70% of scale, per channel:")
        for name, value in worst.items():
            print(f"  {name}: {value:+.2f}%")
    return 0


def main() -> int:
    use_utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--match", default="*_slide_3600dpi",
                    help="library entry glob; the whole ladder of one frame. "
                         "Assumes every entry it finds is the same frame, which "
                         "a strip walk is not -- use --probe for those.")
    ap.add_argument("--probe", default=None, metavar="JSON",
                    help="a tools/exposure_probe.py result file. Groups by "
                         "frame and joins to the library on the device exposure, "
                         "because entry names come from flush time and say "
                         "nothing about which frame a pass came from.")
    ap.add_argument("--root", default="library")
    ap.add_argument("--corrected", action="store_true",
                    help="measure today's correction of each entry "
                         "(library.corrected) instead of the raw pixels. A "
                         "different question, and the two disagree: the "
                         "correction's per-column gain exceeds 1 where the lamp "
                         "falls off, which inflates the band under test. "
                         "Entries it does not correct are left out, and named.")
    args = ap.parse_args()
    if args.probe:
        frames, notes = by_frame(args.probe, args.root, args.corrected)
        return report_walk(frames, notes, args.corrected)
    passes = ladder(args.match, args.root, args.corrected)
    if not passes:
        print(f"nothing in {args.root} matching {args.match!r} with raw bytes "
              f"and a commanded exposure", file=sys.stderr)
        return 1
    return report(passes, args.corrected)


if __name__ == "__main__":
    raise SystemExit(main())
