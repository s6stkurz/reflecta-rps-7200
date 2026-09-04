#!/usr/bin/env python3
"""Drive each command and record what it actually does.

    python3 tools/verify_protocol.py 2          # sense, 0xE7, state -- no film
    python3 tools/verify_protocol.py 1 3 4 5    # the rest -- needs film loaded

`docs/protocol.md` mixes three kinds of claim: counted in the captures, checked
against Stefan's notes, and *inferred*. This settles the inferred ones against the
hardware, cheapest first.

Everything here is 300 dpi RGB 8-bit and sends only byte values the vendor sends.
No shading calibration -- these are geometry and protocol questions, not colour.
Nothing in stages 1-5 moves the transport.

Results go to probe/, and every measurement is taken from the file that was
written, never from the array it came from.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from rps7200 import tiff
from rps7200.direct import (
    DEPTH_8,
    FULL_FRAME,
    ONE_PASS_COLOR,
    SCSI_VENDOR_E7,
    DirectScanner,
    _cmd,
    frame_contrast,
)
from rps7200.protocol import Sense
from rps7200.usb_transport import CheckCondition, UsbError

OUT = Path("probe")
DPI = 300


def shot(s: DirectScanner, name: str, **kw) -> tuple[np.ndarray, float]:
    """One 300 dpi RGB pass, written to probe/ and read back to be measured."""
    t0 = time.monotonic()
    img, _ = s.scan(
        resolution=DPI, infrared=False, depth=DEPTH_8, frame=FULL_FRAME,
        shading=False, require_media=False, **kw,
    )
    took = time.monotonic() - t0
    path = OUT / f"{name}.tif"
    tiff.write(str(path), img)
    return tiff.read(str(path)), took        # measure the file, not the array


def grey(a: np.ndarray) -> np.ndarray:
    a = a.astype(np.float64)
    return a.mean(axis=2) if a.ndim == 3 else a


def mirror_score(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """(same-ness, mirrored-ness) as correlation, 1.0 meaning identical."""
    ga, gb = grey(a), grey(b)
    if ga.shape != gb.shape:
        return float("nan"), float("nan")
    def corr(x, y):
        x = x - x.mean(); y = y - y.mean()
        d = np.sqrt((x * x).sum() * (y * y).sum())
        return float((x * y).sum() / d) if d else float("nan")
    return corr(ga, gb), corr(ga, np.flipud(gb))


# --------------------------------------------------------------------------


def stage2(s: DirectScanner) -> dict:
    """REQUEST SENSE, the 0xE7 vendor command, and READ STATE with no film."""
    print("\n=== stage 2: sense, 0xE7, and the status bytes (empty transport)")
    out: dict = {}

    st = s.read_state()
    raw = s.t.command(_cmd(0xDD, 13), read_size=13)
    out["state_empty"] = raw.hex(" ")
    print(f"  READ STATE, no film : {raw.hex(' ')}")
    print(f"    position {st.position}  warming {st.warming_up} "
          f"scanning {st.scanning:#04x}")

    # The driver already provokes this reliably, once per frame.
    try:
        s.get_gain_offset()
        out["sense"] = "no condition raised"
        print("  REQUEST SENSE      : nothing to report this time")
    except CheckCondition:
        d = s.sense()
        out["sense"] = d.hex(" ")
        print(f"  REQUEST SENSE      : {Sense.parse(d)}")
        print(f"    raw {d.hex(' ')}")

    try:
        r = s.t.command(_cmd(SCSI_VENDOR_E7, 4))   # size 4, as the driver sends it
        out["e7"] = (r or b"").hex(" ") or "accepted, no data"
        print(f"  vendor 0xE7        : {out['e7']}")
    except CheckCondition:
        # Sense is one-shot: reading it clears the condition, so parse the bytes
        # already in hand rather than asking again and getting zeros.
        d = s.sense()
        parsed = Sense.parse(d)
        out["e7"] = {"refused": True, "sense": d.hex(" "), "decoded": str(parsed)}
        print(f"  vendor 0xE7        : refused -- {parsed}")
        print(f"    raw {d.hex(' ')}")
    except UsbError as e:
        out["e7"] = f"{type(e).__name__}: {e}"
        print(f"  vendor 0xE7        : {out['e7']}")

    # Byte 8 is the interesting one. Every capture has it at 0, with film always
    # loaded; an empty transport is the condition the captures never contained.
    out["byte8"] = raw[8]
    print(f"\n  byte 8 = {raw[8]}  (all 737 capture responses, film loaded: 0)")
    print("    if this reads 0 once film is in, byte 8 is the media flag and")
    print("    media_loaded -- which uses byte 6 and admits it is unreliable --")
    print("    has been reading the wrong byte.")

    return out


def stage1(s: DirectScanner) -> dict:
    """One ordinary scan: 12 of the 14 opcodes in a single pass."""
    print("\n=== stage 1: baseline scan")
    img, took = shot(s, "stage1_baseline")
    p = s.get_parameters()
    print(f"  image {img.shape} in {took:.1f}s  contrast {frame_contrast(img):.4f}")
    print(f"  PARAM says width={p.width} lines={p.lines} bpl={p.bytes_per_line}")
    ok = img.shape[1] == p.width
    print(f"  {'OK' if ok else 'MISMATCH'}: PARAM width matches the image")
    return {"shape": list(img.shape), "seconds": round(took, 1),
            "param": [p.width, p.lines, p.bytes_per_line], "agrees": bool(ok)}


def stage3(s: DirectScanner) -> dict:
    """Byte 14 bit 0: is it the scan direction?"""
    print("\n=== stage 3: byte 14 bit 0 -- the flip")
    a, ta = shot(s, "stage3_b14_20", byte14=0x20)
    b, tb = shot(s, "stage3_b14_21", byte14=0x21)
    same, flipped = mirror_score(a, b)
    print(f"  0x20: {a.shape} in {ta:.1f}s     0x21: {b.shape} in {tb:.1f}s")
    print(f"  correlation as-is     {same:+.4f}")
    print(f"  correlation mirrored  {flipped:+.4f}")
    verdict = ("bit 0 IS the scan direction" if flipped > same + 0.2 else
               "bit 0 is NOT the direction -- protocol.md must be corrected"
               if same > flipped + 0.2 else "inconclusive -- look at the images")
    print(f"  -> {verdict}")
    return {"same": round(same, 4), "flipped": round(flipped, 4),
            "verdict": verdict}


def stage4(s: DirectScanner) -> dict:
    """What the upper nibble of byte 14 selects."""
    print("\n=== stage 4: byte 14 upper nibble")
    res = {}
    for v in (0x10, 0x11, 0x20, 0x21):
        img, took = shot(s, f"stage4_b14_{v:02x}", byte14=v)
        p = s.get_parameters()
        res[f"{v:#04x}"] = {"shape": list(img.shape), "seconds": round(took, 1),
                            "lines": p.lines, "contrast": round(frame_contrast(img), 4)}
        print(f"  {v:#04x}: {img.shape} lines={p.lines} {took:5.1f}s "
              f"contrast {frame_contrast(img):.4f}")
    base = tiff.read(str(OUT / "stage4_b14_10.tif"))
    for v in (0x11, 0x20, 0x21):
        other = tiff.read(str(OUT / f"stage4_b14_{v:02x}.tif"))
        same, flipped = mirror_score(base, other)
        print(f"  0x10 vs {v:#04x}: as-is {same:+.4f}  mirrored {flipped:+.4f}")
        res[f"{v:#04x}"]["vs_0x10"] = [round(same, 4), round(flipped, 4)]
    return res


def stage5(s: DirectScanner) -> dict:
    """Does SLIDE INIT's param byte select anything?"""
    print("\n=== stage 5: SLIDE INIT param")
    res = {}
    for v in (0x01, 0x13, 0x14, 0x15, 0x16):
        img, took = shot(s, f"stage5_init_{v:02x}", slide_init_param=v)
        res[f"{v:#04x}"] = {"seconds": round(took, 1),
                            "contrast": round(frame_contrast(img), 4)}
        print(f"  param {v:#04x}: {img.shape} {took:5.1f}s "
              f"contrast {frame_contrast(img):.4f}")
    base = tiff.read(str(OUT / "stage5_init_16.tif"))
    for v in (0x01, 0x13, 0x14, 0x15):
        other = tiff.read(str(OUT / f"stage5_init_{v:02x}.tif"))
        same, _ = mirror_score(base, other)
        print(f"  0x16 vs {v:#04x}: correlation {same:+.4f}")
        res[f"{v:#04x}"]["vs_0x16"] = round(same, 4)
    return res


def stage6(s: DirectScanner) -> dict:
    """The five SLIDE payloads nobody has identified.

    Sent exactly as the vendor sends them -- no invented values, no escalation.
    In the captures each occupies the mechanism 1.5-3 s without moving the frame
    counter, which is what a sub-frame movement would look like and is the last
    remaining candidate for the vernier the front-panel keys perform.

    A prescan before and after every one, measuring both axes, so a movement too
    small for the counter still shows.
    """
    print("\n=== stage 6: the unidentified SLIDE actions")
    print("  five payloads CyberView sends; none moves the frame counter there\n")

    prev, _ = shot(s, "stage6_before")
    start_pos = s.read_state().position
    print(f"  starting at position {start_pos}\n")

    res = {}
    for action, param, value in (
        (0x00, 0x46, 0x00), (0x00, 0x4C, 0x01), (0x00, 0x01, 0x04),
        (0x01, 0x46, 0x00), (0x01, 0x47, 0x03),
    ):
        tag = f"{action:02x}{param:02x}00{value:02x}"
        payload = f"{action:02x} {param:02x} 00 {value:02x}"
        t0 = time.monotonic()
        note = "accepted"
        try:
            s.slide(action, param=param, value=value)
        except CheckCondition:
            note = f"REFUSED {Sense.parse(s.sense())}"
        except UsbError as e:
            note = f"{type(e).__name__}: {e}"
        # how long it stays busy, which is the signature in the captures
        busy = 0.0
        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline:
            if s.test_unit_ready():
                busy = time.monotonic() - t0
                break
            time.sleep(0.25)

        img, _ = shot(s, f"stage6_{tag}")
        pos = s.read_state().position
        dx, dy = _shift(prev, img)
        moved = abs(dx) > 0.5 or abs(dy) > 0.5
        print(f"  SLIDE {payload}  {note}")
        print(f"    busy {busy:4.1f}s  position {pos}"
              f"{' (CHANGED)' if pos != start_pos else ''}"
              f"  shift x{dx:+.2f}px y{dy:+.2f}px"
              f"{'  <- MOVED' if moved else ''}")
        res[payload] = {"note": note, "busy_s": round(busy, 1), "position": pos,
                        "dx_px": round(dx, 2), "dy_px": round(dy, 2)}
        prev = img
        if pos != start_pos:
            print("    frame counter moved -- stopping, this is not sub-frame")
            break
    return res


def _shift(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """Sub-pixel shift of b relative to a, per axis, by cross-correlation."""
    def one(pa, pb):
        pa = (pa - pa.mean()) / (pa.std() or 1.0)
        pb = (pb - pb.mean()) / (pb.std() or 1.0)
        c = np.correlate(pb, pa, mode="full")
        k = int(np.argmax(c))
        if 0 < k < len(c) - 1:
            y0, y1, y2 = c[k-1], c[k], c[k+1]
            d = y0 - 2*y1 + y2
            k = k + (0.5*(y0-y2)/d if d else 0.0)
        return k - (len(pa) - 1)
    ga, gb = grey(a), grey(b)
    return one(ga.mean(axis=0), gb.mean(axis=0)), one(ga.mean(axis=1), gb.mean(axis=1))


STAGES = {1: stage1, 2: stage2, 3: stage3, 4: stage4, 5: stage5, 6: stage6}
NEEDS_FILM = {1, 3, 4, 5, 6}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stages", nargs="+", type=int, choices=sorted(STAGES))
    ap.add_argument("--out", default="probe")
    args = ap.parse_args()

    global OUT
    OUT = Path(args.out)
    OUT.mkdir(exist_ok=True)

    wants_film = sorted(set(args.stages) & NEEDS_FILM)
    if wants_film:
        print(f"stages {wants_film} compare images and need film in the transport")

    results = {}
    with DirectScanner(verbose=False) as s:
        s.wait_ready(timeout=180.0)
        s.wait_warm(timeout=300.0)
        for n in sorted(set(args.stages)):
            results[f"stage{n}"] = STAGES[n](s)

    path = OUT / "results.json"
    prev = json.loads(path.read_text()) if path.exists() else {}
    prev.update(results)
    path.write_text(json.dumps(prev, indent=2))
    print(f"\nwritten to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
