"""Command line entry point: ``rps7200``."""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

from . import tiff
from .device import (
    DEFAULT_RESOLUTION,
    DeviceNotFound,
    Frame,
    Scanner,
    discover,
    sane_version,
)
from .sane_ffi import SaneError


def _write_frame(frame: Frame, path: str, sidecar: bool = True) -> str:
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    tiff.write(path, frame.data, resolution=frame.resolution)
    if sidecar:
        with open(os.path.splitext(path)[0] + ".json", "w") as fh:
            json.dump(frame.metadata(), fh, indent=2, sort_keys=True)
    return path


def _describe(frame: Frame) -> str:
    h, w = frame.data.shape[:2]
    kind = "preview" if frame.preview else "scan"
    ir = "RGB+IR" if frame.has_ir else f"{frame.channels}ch"
    return (
        f"{kind}: {w}x{h} {ir} {frame.depth}-bit @ {frame.resolution} dpi "
        f"({frame.data.nbytes / 1e6:.1f} MB, {frame.duration_s:.1f}s)"
    )


def cmd_list(args: argparse.Namespace) -> int:
    devices = discover(backend="" if args.all else "pieusb")
    if not devices:
        print("No scanner found.", file=sys.stderr)
        print(
            "\nCheck that it is powered on and connected. Note the device name "
            "changes\nevery time it is opened, so a name from an earlier run is "
            "not reusable.",
            file=sys.stderr,
        )
        return 1
    print(f"SANE {sane_version()}")
    for d in devices:
        print(f"\n  {d['name']}")
        print(f"    vendor : {d['vendor']}")
        print(f"    model  : {d['model']}")
        print(f"    type   : {d['type']}")

    if args.options:
        with Scanner(verbose=args.verbose) as scanner:
            print(f"\nOptions for {scanner.device['name']}:\n")
            for opt in scanner.options():
                flags = []
                if not opt["active"]:
                    flags.append("inactive")
                if not opt["settable"]:
                    flags.append("read-only")
                suffix = f"  [{', '.join(flags)}]" if flags else ""
                value = opt.get("value", "")
                constraint = ""
                if "values" in opt:
                    constraint = " {" + "|".join(str(v) for v in opt["values"]) + "}"
                elif "range" in opt:
                    lo, hi, _ = opt["range"]
                    constraint = f" [{lo}..{hi}]"
                print(f"  {opt['name']:<20} = {value!r}{constraint}{suffix}")
    return 0


def cmd_prescan(args: argparse.Namespace) -> int:
    with Scanner(verbose=args.verbose) as scanner:
        print(f"Prescanning on {scanner.device['name']} ...", flush=True)
        print(
            "  (the backend does the whole capture inside one blocking call; "
            "this will sit silent for a while)",
            flush=True,
        )
        frame = scanner.prescan()
        print(_describe(frame))
        _write_frame(frame, args.output)
        print(f"wrote {args.output}")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    overrides = _parse_overrides(args.set)
    with Scanner(verbose=args.verbose) as scanner:
        print(f"Using {scanner.device['name']}", flush=True)
        print(
            "  (each capture happens inside one blocking call and reports "
            "nothing until it finishes)",
            flush=True,
        )

        if args.prescan:
            print("Prescanning (also calibrates the scan) ...", flush=True)
            preview = scanner.prescan()
            print(f"  {_describe(preview)}")
            if args.save_prescan:
                _write_frame(preview, args.save_prescan)
                print(f"  wrote {args.save_prescan}")

        print(f"Scanning at {args.dpi} dpi ...", flush=True)
        frame = scanner.scan(
            resolution=args.dpi,
            settings=overrides,
            use_prescan=args.prescan,
        )
        print(f"  {_describe(frame)}")

        if not frame.has_ir:
            print(
                f"  warning: only {frame.channels} channel(s) came back; "
                "the infrared plane is missing",
                file=sys.stderr,
            )

        path = args.output
        if os.path.isdir(path) or path.endswith(os.sep):
            path = os.path.join(path, "frame001.tif")
        _write_frame(frame, path)
        print(f"wrote {path}")
        if args.split:
            base = os.path.splitext(path)[0]
            tiff.write(f"{base}_rgb.tif", frame.rgb, resolution=frame.resolution)
            print(f"wrote {base}_rgb.tif")
            if frame.has_ir:
                tiff.write(f"{base}_ir.tif", frame.ir, resolution=frame.resolution)
                print(f"wrote {base}_ir.tif")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    """Report on a written file: shape, depth, and whether the IR plane is real."""
    image = tiff.read(args.path)
    if image.ndim == 2:
        image = image[:, :, None]
    h, w, c = image.shape
    print(f"{args.path}: {w}x{h}, {c} channel(s), {image.dtype}")
    print(f"  value range: {int(image.min())}..{int(image.max())}")
    if image.dtype == np.uint16 and image.max() <= 255:
        print("  warning: 16-bit file but no value exceeds 255")

    if c < 4:
        print("  no infrared channel present")
        return 0

    # A genuine IR plane sees through the dye layers, so it should not track the
    # visible channels. High correlation with red usually means IR crosstalk
    # correction was left on, or the channels are misordered.
    rgb = image[..., :3].astype(np.float64)
    ir = image[..., 3].astype(np.float64)
    print(f"  IR range: {int(ir.min())}..{int(ir.max())}, mean {ir.mean():.0f}")
    for i, name in enumerate("RGB"):
        channel = rgb[..., i]
        if channel.std() < 1e-6 or ir.std() < 1e-6:
            print(f"  corr(IR, {name}) = n/a (flat channel)")
            continue
        corr = float(np.corrcoef(channel.ravel(), ir.ravel())[0, 1])
        verdict = "  <-- suspiciously high" if abs(corr) > 0.9 else ""
        print(f"  corr(IR, {name}) = {corr:+.3f}{verdict}")
    return 0


def _parse_overrides(pairs: list[str] | None) -> dict[str, object]:
    """Turn ``--set name=value`` arguments into option values."""
    out: dict[str, object] = {}
    for item in pairs or []:
        if "=" not in item:
            raise SystemExit(f"--set expects name=value, got {item!r}")
        name, _, raw = item.partition("=")
        name, raw = name.strip(), raw.strip()
        low = raw.lower()
        if low in ("yes", "true", "on"):
            out[name] = True
        elif low in ("no", "false", "off"):
            out[name] = False
        else:
            try:
                out[name] = int(raw)
            except ValueError:
                try:
                    out[name] = float(raw)
                except ValueError:
                    out[name] = raw
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rps7200",
        description="Scan film on a Reflecta RPS 7200, keeping RGB and raw infrared.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="print session diagnostics"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="show detected scanners")
    p_list.add_argument(
        "--all", action="store_true", help="include non-pieusb devices"
    )
    p_list.add_argument(
        "--options", action="store_true", help="also dump every backend option"
    )
    p_list.set_defaults(func=cmd_list)

    p_pre = sub.add_parser(
        "prescan",
        help="run a preview scan (resolution is chosen by the scanner)",
    )
    p_pre.add_argument("-o", "--output", default="preview.tif")
    p_pre.set_defaults(func=cmd_prescan)

    p_scan = sub.add_parser("scan", help="capture one frame as RGB + infrared")
    p_scan.add_argument(
        "-o", "--output", default="scan.tif", help="output .tif path or a directory"
    )
    p_scan.add_argument(
        "--dpi",
        type=int,
        default=DEFAULT_RESOLUTION,
        help=f"optical resolution, 25-7200 (default: {DEFAULT_RESOLUTION})",
    )
    p_scan.add_argument(
        "--no-prescan",
        dest="prescan",
        action="store_false",
        help="skip the calibration prescan",
    )
    p_scan.add_argument(
        "--save-prescan", metavar="PATH", help="also write the prescan image"
    )
    p_scan.add_argument(
        "--split",
        action="store_true",
        help="additionally write separate _rgb and _ir files",
    )
    p_scan.add_argument(
        "--set",
        action="append",
        metavar="NAME=VALUE",
        help="override any backend option (repeatable)",
    )
    p_scan.set_defaults(func=cmd_scan, prescan=True)

    p_inspect = sub.add_parser(
        "inspect", help="check a written file's channels and IR plane"
    )
    p_inspect.add_argument("path")
    p_inspect.set_defaults(func=cmd_inspect)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except DeviceNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(
            "\nThe scanner re-enumerates on every open, so its device name "
            "changes.\nRun 'rps7200 list' to confirm it is visible at all.",
            file=sys.stderr,
        )
        return 1
    except SaneError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
