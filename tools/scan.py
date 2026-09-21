#!/usr/bin/env python3
"""Scan with the scanner's own shading correction applied.

    uv run python tools/scan.py --dpi 1800 --out scans/negatives/shaded_1800dpi.tif

The scanner returns raw pixels: it measures its per-column response during a
calibration pass and hands that back, but never applies it. So a session runs
the calibration once -- as the vendor software does at power-on -- and every
scan after it is corrected from that reference. Skipping this is what leaves the
vertical stripes in.

The reference is cached to disk so it can be inspected or reused, but prefer a
fresh one: it describes the sensor at the exposure and gain of the pass that
measured it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


import numpy as np

from rps7200 import export, library
from rps7200.console import use_utf8_stdout
from rps7200.direct import DirectScanner, supports_infrared
from rps7200.mono import MONO_CHANNEL, MONO_CHOICES, to_monochrome
from rps7200.library import FilmNotes


def main() -> int:
    use_utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dpi", type=int, default=1800)
    ap.add_argument("--out", default="scan.tif",
                    help="the delivered file. Its extension picks the format: "
                         ".tif or .jpg. A JPEG is the same picture at 8 bits, "
                         "still a negative, and cannot carry infrared")
    ap.add_argument("--quality", type=int, default=export.DEFAULT_QUALITY,
                    metavar="N", help="JPEG quality 60-100 (default "
                                      f"{export.DEFAULT_QUALITY}); ignored for TIFF")
    ap.add_argument("--ir", action="store_true", help="capture the infrared plane too")
    ap.add_argument("--fast-ir", dest="fast_ir", action="store_true",
                    default=True,
                    help="tie the infrared plane's cost to --dpi (default). An "
                         "untied pass costs ~220 s at every resolution; a tied "
                         "one costs what its lines cost -- 110 s at 1800 dpi, "
                         "25 s at 300. See docs/fast-infrared-plan.md.")
    ap.add_argument("--no-fast-ir", dest="fast_ir", action="store_false",
                    help="untie it: the fixed-cost infrared pass. Nothing "
                         "measured says it is better, and above ~3700 dpi the "
                         "two cost the same anyway.")
    ap.add_argument("--reference", default="calibration/shading.npz",
                    help="where to cache the shading reference")
    ap.add_argument("--reuse", action="store_true",
                    help="load the cached reference instead of calibrating")
    ap.add_argument("--no-shading", action="store_true",
                    help="return raw pixels, for comparison")
    ap.add_argument("--auto-exposure", action="store_true")
    ap.add_argument("--bracket", type=int, default=0, metavar="N",
                    help="scan N exposures of this frame (2-9) and merge them by "
                         "inverse-variance weighting, for lower shadow noise. "
                         "Infrared is not bracketed: one pass carries it. "
                         "0, the default, takes a single pass")
    ap.add_argument("--stops", type=float, default=2.0,
                    help="how far the bracket spans, in stops (default 2). The "
                         "top is pinned to the exposure timer's ceiling and the "
                         "rest step down from it")
    ap.add_argument("--exposure-scale", default=None, metavar="X|R,G,B[,I]",
                    help="hold exposure at this multiple of the scanner's own "
                         "settings instead of metering. One value, or one per "
                         "channel. Needed whenever several passes have to be "
                         "comparable: SET GAIN OFFSET does not persist across a "
                         "scan sequence, so every pass sets it afresh, and two "
                         "passes metered independently are not the same "
                         "measurement")
    ap.add_argument("--film", default="negative",
                    choices=["negative", "positive", "kodachrome", "bw"],
                    help="metering only: a negative is metered per channel to "
                         "take the orange mask off before the ADC; everything "
                         "else keeps its cast")
    ap.add_argument("--mono", dest="mono", action="store_true", default=None,
                    help="deliver one channel instead of three. On by default "
                         "for --film bw: a black and white scan is an RGB scan "
                         "on this hardware, and a consumer that has to guess "
                         "from the pixels gets it wrong -- channel correlation "
                         "does not separate B&W from colour negative here. The "
                         "library keeps all three regardless")
    ap.add_argument("--no-mono", dest="mono", action="store_false",
                    help="deliver all three channels even for --film bw")
    ap.add_argument("--mono-channel", default=MONO_CHANNEL,
                    choices=list(MONO_CHOICES),
                    help="what a monochrome file carries: the average of "
                         "the visible channels, or one of them alone "
                         "(default: %(default)s)")
    ap.add_argument("--library", nargs="?", const="library", default="library",
                    metavar="DIR",
                    help="file this scan in the reusable library, with its raw "
                         "bytes and calibration, so it can be re-decoded and "
                         "re-corrected later without rescanning (default: "
                         "library/)")
    ap.add_argument("--no-library", action="store_true",
                    help="do not file this scan. The raw bytes and the "
                         "session's calibration are then gone for good: neither "
                         "can be recovered from the TIFF, so a later change to "
                         "the decode or the correction cannot be applied to it")
    ap.add_argument("--stock", default="", help="film stock, e.g. 'Kodak Gold 200'")
    ap.add_argument("--frame", default="", help="frame position on the roll")
    ap.add_argument("--subject", default="")
    ap.add_argument("--notes", default="", help="anything about this frame worth "
                                                "knowing later, e.g. 'dust top left'")
    ap.add_argument("--tag", action="append", default=[], dest="tags")
    ap.add_argument("-v", "--verbose", action="store_true", default=True)
    args = ap.parse_args()
    if args.ir and not supports_infrared(args.film):
        ap.error(
            f"--ir with --film {args.film}: infrared is blind to it -- its "
            + ("grain" if args.film == "bw" else "cyan layer")
            + " absorbs infrared, so the pass would spend its ~212 s floor a "
            "frame and hand back the picture rather than the dust. Drop --ir. "
            "(Chromogenic C-41 black and white does clean properly: scan that "
            "as --film negative.)"
        )
    if args.no_library:
        args.library = None
    if args.bracket and not (
        DirectScanner.MIN_BRACKET_PASSES
        <= args.bracket
        <= DirectScanner.MAX_BRACKET_PASSES
    ):
        # Before the device is opened, not after: a bracket refused three
        # minutes into a calibration has already cost the calibration.
        ap.error(
            f"--bracket takes {DirectScanner.MIN_BRACKET_PASSES} to "
            f"{DirectScanner.MAX_BRACKET_PASSES} passes, got {args.bracket}"
        )

    exposure_scale: float | list[float] = 1.0
    if args.exposure_scale:
        parts = [float(v) for v in args.exposure_scale.replace(",", " ").split()]
        exposure_scale = parts[0] if len(parts) == 1 else parts
    if args.auto_exposure and args.exposure_scale:
        print("--exposure-scale overrides --auto-exposure", file=sys.stderr)
    if not args.ir:
        # No plane to acquire, so the bit governs nothing. Silently cleared now
        # that it is the default -- warning on every RGB scan about a flag
        # nobody asked for would be noise, where warning about one typed
        # explicitly was not.
        args.fast_ir = False

    ref_path = Path(args.reference)
    # debug=False deliberately: this tool files its own library entries,
    # and letting the driver file as well writes every frame twice --
    # 43 GB of duplicate on a 38-frame roll at 7200 dpi.
    with DirectScanner(verbose=args.verbose, debug=False) as s:
        info = s.inquiry()
        print(f"{info.vendor} {info.model}, firmware {info.firmware}")

        if not args.no_shading and not (args.reuse and ref_path.exists()):
            print("calibrating (about 3-4 minutes; the vendor does this once "
                  "per power-on) ...", flush=True)
        print(s.ensure_shading(ref_path, reuse=args.reuse,
                               skip=args.no_shading)["summary"])

        # Everything the library needs is gathered while the session is open and
        # written after it closes: filing an entry gzips well over a hundred
        # megabytes, and holding the device open and idle through that has
        # preceded it going unresponsive.
        pending: list[dict] = []

        def hold(image, meta, capture) -> None:
            if args.library is None:
                return
            # The RAW pixels, not the ones the scan returned. `scan()` hands
            # back the *corrected* image -- that is what a caller wants to
            # look at -- and keeps the uncorrected one in `last_pixels_raw`.
            # Filing the returned array put shading into `scan.tif` while
            # `corrections_applied` still said nothing was baked in, so
            # `library.corrected()` shaded it a second time and `reconstruct`
            # called every such entry a changed decode.
            #
            # Read here rather than after the loop because the attribute
            # describes the pass that *just* ran: `on_pass` is called as each
            # pass lands, and the pass after it overwrites this.
            raw = getattr(s, "last_pixels_raw", None)
            pending.append(
                dict(capture, inquiry=info, meta=meta,
                     image=image if raw is None else raw)
            )

        bracket = None
        if args.bracket:
            print(f"scanning {args.bracket} exposures over {args.stops:g} stops "
                  f"at {args.dpi} dpi{' (one with IR)' if args.ir else ''} ...",
                  flush=True)
            # Each pass is filed as it lands. Only one pass's raw bytes survive
            # on the scanner -- last_raw is overwritten by the pass after it --
            # so waiting for the return value would file the last and lose the
            # rest, which is the whole point of taking a bracket.
            bracket = s.scan_bracket(
                passes=args.bracket,
                stops=args.stops,
                resolution=args.dpi,
                infrared=args.ir,
                film=args.film,
                auto_exposure=args.auto_exposure and not args.exposure_scale,
                exposure_scale=(
                    list(exposure_scale)
                    if isinstance(exposure_scale, list) else None
                ),
                keep_raw=args.library is not None,
                shading=not args.no_shading,
                fast_infrared=args.fast_ir,
                on_pass=lambda i, image, meta, capture: hold(image, meta, capture),
            )
            image, meta = bracket[0][-1], bracket[2][-1]
        else:
            print(f"scanning at {args.dpi} dpi{' with IR' if args.ir else ''} ...",
                  flush=True)
            image, meta = s.scan(
                resolution=args.dpi,
                infrared=args.ir,
                exposure_scale=exposure_scale,
                auto_exposure=args.auto_exposure and not args.exposure_scale,
                film=args.film,
                shading=not args.no_shading,
                keep_raw=args.library is not None,
                fast_infrared=args.fast_ir,
            )
            hold(image, meta, s.capture_record())

    entries = []
    for held in pending:
        entries.append(library.save(
            held.pop("image"), held.pop("meta"),
            root=args.library,
            film=FilmNotes(stock=args.stock, frame=args.frame,
                           subject=args.subject, notes=args.notes),
            tags=args.tags,
            **held,
        ))

    if bracket is not None:
        from rps7200.bracket import merge_bracket

        frames, ratios, metas = bracket
        # Merge the visible channels only: with --ir the brightest pass is RGBI
        # and the rest RGB, so the frames do not share a channel count.
        merged, stats = merge_bracket([f[..., :3] for f in frames], ratios)
        print(f"bracket: {stats.describe()}")
        if args.ir and frames[-1].shape[2] == 4:
            # The infrared pass is the brightest; carry its plane through.
            image = np.dstack([merged, frames[-1][..., 3]])
        else:
            image = merged
        meta = dict(metas[-1])
        meta["bracket"] = {
            "passes": len(frames), "ratios": ratios,
            "stops": args.stops, "stats": stats.describe(),
        }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    delivered = image
    if args.mono is None:
        args.mono = args.film == "bw"
    if args.mono:
        # One channel, so a consumer cannot mistake this for a colour scan.
        # The library keeps all three either way -- see rps7200/mono.py.
        delivered = to_monochrome(image, args.mono_channel)
        meta = dict(meta, mono_channel=args.mono_channel,
                    channel_order=[args.mono_channel], channels=1)
    note = export.write(out, delivered, resolution=args.dpi,
                        quality=args.quality)
    out.with_suffix(".json").write_text(
        json.dumps(meta, indent=2, default=str), encoding="utf-8")
    print(f"wrote {out}  {delivered.shape}  {delivered.dtype}"
          + (f"  (monochrome, {args.mono_channel})" if args.mono else ""))
    if note:
        print(note)
    if meta.get("shading"):
        r = meta["shading"]
        print(f"shading: {r['columns']}/{r['width']} columns corrected, "
              f"{r['clipped']} samples clipped")
    if entries:
        raw_mb = sum(
            (e / "raw.bin.gz").stat().st_size for e in entries
            if (e / "raw.bin.gz").exists()
        ) / 1e6
        noun = "pass" if len(entries) == 1 else "passes"
        print(f"filed {len(entries)} {noun} in the library:")
        for e in entries:
            print(f"  {e}")
        print(f"  raw bytes kept ({raw_mb:.1f} MB compressed) -- these can be "
              f"re-decoded and re-corrected without the scanner")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
