#!/usr/bin/env python3
"""Drive each command and record what it actually does.

    uv run python tools/verify_protocol.py 2          # sense, 0xE7, state -- no film
    uv run python tools/verify_protocol.py 1 3 4 5    # the rest -- needs film loaded

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
from rps7200.console import use_utf8_stdout
from rps7200.direct import (
    DEPTH_8,
    FULL_FRAME,
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
        x = x - x.mean()
        y = y - y.mean()
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


def _shift_near(a: np.ndarray, b: np.ndarray, expect: float,
                window: int = 30) -> tuple[float, float]:
    """Shift, searched around a *predicted* value rather than around zero.

    Bounding at +/-60 px of zero works while movements are small, but the law is
    now calibrated, so a large move can be predicted and the search centred on
    it. That keeps the window narrow at any distance, which is what stops the
    peak wrapping -- the failure that produced +359.78 px in stage 6.
    """
    def one(pa, pb, centre):
        pa = (pa - pa.mean()) / (pa.std() or 1.0)
        pb = (pb - pb.mean()) / (pb.std() or 1.0)
        c = np.correlate(pb, pa, mode="full")
        zero = len(pa) - 1
        lo = max(0, zero + int(centre) - window)
        hi = min(len(c), zero + int(centre) + window + 1)
        if hi <= lo:
            return float("nan")
        k = lo + int(np.argmax(c[lo:hi]))
        if 0 < k < len(c) - 1:
            y0, y1, y2 = c[k-1], c[k], c[k+1]
            d = y0 - 2*y1 + y2
            k = k + (0.5*(y0-y2)/d if d else 0.0)
        return k - zero
    ga, gb = grey(a), grey(b)
    return (one(ga.mean(axis=0), gb.mean(axis=0), expect),
            one(ga.mean(axis=1), gb.mean(axis=1), 0))


def _shift(a: np.ndarray, b: np.ndarray, limit: int = 60) -> tuple[float, float]:
    """Sub-pixel shift of b relative to a, per axis, by cross-correlation.

    ``limit`` bounds the search. Unbounded, the peak can wrap: in stage 6 a true
    -88 px came back as +359.78 px, which is a shift larger than most of the
    428-column window and cannot be real. A movement predicted well inside the
    limit that reports outside it is an artefact by construction, so the bound
    is not a fudge -- it is the prior that the film did not teleport.
    """
    def one(pa, pb):
        pa = (pa - pa.mean()) / (pa.std() or 1.0)
        pb = (pb - pb.mean()) / (pb.std() or 1.0)
        c = np.correlate(pb, pa, mode="full")
        zero = len(pa) - 1                       # index meaning "no shift"
        lo, hi = max(0, zero - limit), min(len(c), zero + limit + 1)
        k = lo + int(np.argmax(c[lo:hi]))
        if 0 < k < len(c) - 1:
            y0, y1, y2 = c[k-1], c[k], c[k+1]
            d = y0 - 2*y1 + y2
            k = k + (0.5*(y0-y2)/d if d else 0.0)
        return k - zero
    ga, gb = grey(a), grey(b)
    return one(ga.mean(axis=0), gb.mean(axis=0)), one(ga.mean(axis=1), gb.mean(axis=1))


#: mm per prescan column, at 300 dpi across the full 10344-unit window
MM_PER_PX = 36.49 / 428

#: The ladder. Only `param` varies; action and value stay at the combination the
#: vendor pairs with param 1. Every value is far inside the observed 1..87.
LADDER = [1, 2, 3, 4, 6, 8, 12]
REPEATS = 3


def stage7(s: DirectScanner) -> dict:
    """Is `param` a step count, and what is one step worth?

    Three coarse payloads agree on ~0.104 mm per unit; `00 01 00 04` measured
    0.32 mm at param 1, three times off that line. Either the `value` byte
    contributes or a 4-pixel measurement was poor. Which is true decides whether
    a distance can be asked for -- 0.40 mm is param 4 under one model and
    nowhere under the other.

    Only `param` varies, never above the vendor's largest. Direction is never
    reversed inside the ladder, because the first steps after a reversal fall
    short while backlash takes up.
    """
    print("\n=== stage 7: is param a step count?")
    print(f"  action 0x00, value 0x04, param {LADDER}, x{REPEATS} each")
    print("  0.104 mm/unit predicts param 4 = 0.42 mm; 0.32 mm/unit predicts 1.28\n")

    print("  taking up backlash with three throwaway forward steps ...")
    for _ in range(3):
        s.slide(0x00, param=0x01, value=0x04)
        time.sleep(1.4)

    prev, _ = shot(s, "stage7_start")
    start_pos = s.read_state().position
    res, travel = {}, 0.0
    print(f"\n  {'param':>6} {'n':>2} {'dx px':>8} {'mm':>8} {'mm/unit':>9} {'dy px':>7}")
    for param in LADDER:
        got = []
        for n in range(1, REPEATS + 1):
            s.slide(0x00, param=param, value=0x04)
            t0 = time.monotonic()
            while time.monotonic() - t0 < 12 and not s.test_unit_ready():
                time.sleep(0.25)
            img, _ = shot(s, f"stage7_p{param:02d}_{n}")
            dx, dy = _shift(prev, img)
            got.append(dx)
            travel += dx * MM_PER_PX
            print(f"  {param:6d} {n:2d} {dx:+8.2f} {dx*MM_PER_PX:+8.3f} "
                  f"{abs(dx*MM_PER_PX)/param:9.4f} {dy:+7.2f}")
            prev = img
        mean = float(np.mean(got))
        res[param] = {"px": [round(v, 2) for v in got],
                      "mean_px": round(mean, 2),
                      "mean_mm": round(mean * MM_PER_PX, 4),
                      "mm_per_unit": round(abs(mean * MM_PER_PX) / param, 4)}
        if s.read_state().position != start_pos:
            print("    frame counter moved -- stopping")
            break

    print(f"\n  {'param':>6} {'mean mm':>9} {'mm/unit':>9}")
    for p, r in res.items():
        print(f"  {p:6d} {r['mean_mm']:+9.3f} {r['mm_per_unit']:9.4f}")

    # A step count means distance is linear in param and passes through zero.
    ps = np.array(list(res), dtype=float)
    ms = np.array([res[p]["mean_mm"] for p in res], dtype=float)
    if len(ps) >= 3:
        slope, intercept = np.polyfit(ps, ms, 1)
        pred = slope * ps + intercept
        resid = float(np.max(np.abs(ms - pred)))
        print(f"\n  fit: {slope:+.4f} mm per unit, intercept {intercept:+.4f} mm")
        print(f"  worst residual {resid:.4f} mm")
        linear = resid < 0.05 and abs(intercept) < 0.15
        print(f"  -> {'param IS a step count' if linear else 'NOT a clean step count'}")
        res["fit"] = {"mm_per_unit": round(float(slope), 4),
                      "intercept_mm": round(float(intercept), 4),
                      "worst_residual_mm": round(resid, 4),
                      "linear": bool(linear)}
        if linear:
            for want in (0.30, 0.40, 0.50):
                print(f"     {want:.2f} mm -> param {round((want-intercept)/slope):d}")

    print(f"\n  film moved {travel:+.2f} mm; returning with 01 47 00 03")
    for _ in range(max(0, round(travel / 7.47))):
        s.slide(0x01, param=0x47, value=0x03)
        time.sleep(1.4)
    return res


def _ladder(s: DirectScanner, action: int, value: int, params, repeats,
            tag: str, warmup_param: int = 4, warmup: int = 5) -> dict:
    """Measure a run of moves that never reverses direction.

    `warmup` throwaway steps first: after a direction change the transport takes
    up slack for two or three steps before it follows, which in stage 7 wrecked
    the two smallest params. Five is deliberately more than the three that
    proved insufficient.
    """
    print(f"  taking up backlash: {warmup} x param {warmup_param} ...")
    for _ in range(warmup):
        s.slide(action, param=warmup_param, value=value)
        time.sleep(1.4)

    prev, _ = shot(s, f"{tag}_start")
    res, travel = {}, 0.0
    print(f"  {'param':>6} {'n':>2} {'dx px':>8} {'mm':>8} {'dy px':>7}")
    for param in params:
        got = []
        for n in range(1, repeats + 1):
            s.slide(action, param=param, value=value)
            t0 = time.monotonic()
            while time.monotonic() - t0 < 12 and not s.test_unit_ready():
                time.sleep(0.25)
            img, _ = shot(s, f"{tag}_p{param:02d}_{n}")
            dx, dy = _shift(prev, img)
            got.append(dx)
            travel += dx * MM_PER_PX
            print(f"  {param:6d} {n:2d} {dx:+8.2f} {dx*MM_PER_PX:+8.3f} {dy:+7.2f}")
            prev = img
        res[param] = {"px": [round(v, 2) for v in got],
                      "mean_mm": round(float(np.mean(got)) * MM_PER_PX, 4)}
    return {"rows": res, "travel_mm": round(travel, 2)}


def stage8(s: DirectScanner) -> dict:
    """Two gaps: is there a fine step backward, and what does `value` do?

    Everything calibrated so far used action 0x00 and value 0x04, so the model
    `0.105 x param + 0.171 mm` is fitted on one slice of a two-parameter space.
    The 0.171 mm offset may simply be the `value` term.
    """
    out = {}

    print("\n=== stage 8a: is action 0x01 a fine step backward?")
    print("  same ladder, action 0x01, value 0x04\n")
    back = _ladder(s, 0x01, 0x04, [3, 4, 6, 8, 12], 3, "stage8a")
    out["reverse"] = back
    ps = np.array(list(back["rows"]), dtype=float)
    ms = np.array([back["rows"][int(p)]["mean_mm"] for p in ps])
    if len(ps) >= 3:
        slope, inter = np.polyfit(ps, ms, 1)
        resid = float(np.max(np.abs(ms - (slope*ps + inter))))
        print(f"\n  reverse fit: {slope:+.4f} mm/unit, intercept {inter:+.4f} mm,"
              f" worst residual {resid:.4f}")
        print("  forward was: +0.1049 mm/unit, intercept +0.1710 mm")
        out["reverse_fit"] = {"mm_per_unit": round(float(slope), 4),
                              "intercept_mm": round(float(inter), 4),
                              "worst_residual_mm": round(resid, 4)}

    print(f"\n  returning {back['travel_mm']:+.2f} mm before the next test")
    for _ in range(max(0, round(abs(back["travel_mm"]) / 0.591))):
        s.slide(0x00, param=0x04, value=0x04)
        time.sleep(1.4)

    print("\n=== stage 8b: what does `value` do?")
    print("  param fixed at 8, action 0x00, value over the four the vendor sends\n")
    vals = {}
    for value in (0x00, 0x01, 0x03, 0x04):
        r = _ladder(s, 0x00, value, [8], 3, f"stage8b_v{value:02x}",
                    warmup_param=8, warmup=3)
        mm = r["rows"][8]["mean_mm"]
        vals[f"{value:#04x}"] = mm
        print(f"    value {value:#04x}: param 8 -> {mm:+.3f} mm")
    out["value_sweep"] = vals
    spread = max(vals.values()) - min(vals.values())
    print(f"\n  spread across value: {spread:.3f} mm")
    print(f"  -> {'value CHANGES the distance' if spread > 0.08 else 'value does not measurably change the distance'}")
    return out


#: The law, fitted over both directions in stage 8.
MM_PER_UNIT, OVERHEAD_MM = 0.1057, 0.1662


def predict_mm(param: int) -> float:
    return MM_PER_UNIT * param + OVERHEAD_MM


def stage9(s: DirectScanner) -> dict:
    """Does the law hold at large param, and does error accumulate?

    Both were left open after stage 8. The extrapolation to param 70-76 landed
    within 0.15-0.27 mm but all three errors shared a sign, which hints the
    relationship bends slightly at the top; and nothing has tested whether a long
    run of steps drifts away from prediction.
    """
    out = {}

    print("\n=== stage 9a: does the law hold at large param?")
    print("  param up to the vendor's largest (87); search centred on prediction\n")
    print("  warming up backlash ...")
    for _ in range(5):
        s.slide(0x00, param=0x08, value=0x04)
        time.sleep(1.4)

    prev, _ = shot(s, "stage9a_start")
    rows, travel = {}, 0.0
    print(f"  {'param':>6} {'predicted':>10} {'measured':>9} {'error':>8} {'dy':>6}")
    for param in (20, 30, 50, 70, 87):
        pred_mm = predict_mm(param)
        got = []
        for _ in range(2):
            s.slide(0x00, param=param, value=0x04)
            t0 = time.monotonic()
            while time.monotonic() - t0 < 15 and not s.test_unit_ready():
                time.sleep(0.25)
            img, _ = shot(s, f"stage9a_p{param:02d}_{len(got)}")
            dx, dy = _shift_near(prev, img, pred_mm / MM_PER_PX)
            got.append(dx * MM_PER_PX)
            travel += dx * MM_PER_PX
            prev = img
            print(f"  {param:6d} {pred_mm:10.3f} {got[-1]:9.3f} "
                  f"{got[-1]-pred_mm:+8.3f} {dy:+6.2f}")
        rows[param] = {"predicted_mm": round(pred_mm, 3),
                       "measured_mm": round(float(np.mean(got)), 3),
                       "error_mm": round(float(np.mean(got)) - pred_mm, 3)}
    out["large_param"] = rows
    errs = [r["error_mm"] for r in rows.values()]
    print(f"\n  errors: {errs}")
    print(f"  {'all one sign -- the law bends' if all(e>0 for e in errs) or all(e<0 for e in errs) else 'errors change sign -- the law holds'}")

    print(f"\n  returning {travel:+.1f} mm")
    back = max(0, round(travel / predict_mm(87)))
    for _ in range(back):
        s.slide(0x01, param=87, value=0x04)
        time.sleep(1.6)

    print("\n=== stage 9b: does error accumulate over a long run?")
    print("  twenty consecutive param 4 steps, one direction\n")
    for _ in range(5):
        s.slide(0x00, param=0x04, value=0x04)
        time.sleep(1.4)
    first, _ = shot(s, "stage9b_start")
    prev, cum = first, 0.0
    step = predict_mm(4)
    for i in range(1, 21):
        s.slide(0x00, param=0x04, value=0x04)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 12 and not s.test_unit_ready():
            time.sleep(0.25)
        img, _ = shot(s, f"stage9b_{i:02d}")
        dx, _ = _shift_near(prev, img, step / MM_PER_PX)
        cum += dx * MM_PER_PX
        prev = img
        if i % 5 == 0:
            print(f"    after {i:2d} steps: {cum:+7.3f} mm, "
                  f"expected {i*step:+7.3f}, error {cum-i*step:+6.3f}")
    total_err = cum - 20 * step
    out["accumulation"] = {"steps": 20, "measured_mm": round(cum, 3),
                           "expected_mm": round(20 * step, 3),
                           "error_mm": round(total_err, 3),
                           "per_step_mm": round(total_err / 20, 4)}
    print(f"\n  20 steps: {cum:+.3f} mm measured, {20*step:+.3f} expected")
    print(f"  error {total_err:+.3f} mm total = {total_err/20:+.4f} mm per step")
    return out


def _settle(s: DirectScanner, seconds: float = 1.4) -> None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        if s.test_unit_ready():
            return
        time.sleep(0.2)


def stage10(s: DirectScanner) -> dict:
    """What is one unit of `param` worth, and what does a *command* cost?

    The law in section 11 is ``param + c`` units travelled per command, where
    one unit is what a single increment of `param` adds and ``c`` is a fixed
    cost per command rather than per unit. Every measurement behind it used a
    single command, so nothing has ever checked whether commands compose the
    way it says -- and if ``c`` is real then ten commands of `param 1` travel
    more than twice as far as one command of `param 10`, for the same total.

    Three legs of equal param total over different command counts settle it
    with nothing assumed:

        A  10 x param 1      10 units of param, 10 commands
        B   5 x param 2      10 units of param,  5 commands
        C   1 x param 10     10 units of param,  1 command

        cost per command = (A - C) / 9        one unit = (C - cost) / 10

    and B checks both. If the three land together, the cost is zero and param
    is a clean linear scale.

    Everything is reported in prescan pixels, which is what is actually
    observed; the unit is then derived from the measurement rather than assumed
    into it. Everything runs forward, so backlash is spent once at the start.
    """
    print("\n=== stage 10: does a command cost something, over and above param?")
    print("  three legs, each totalling param 10, over 10, 5 and 1 commands\n")

    print("  spending backlash -- five forward commands, not measured")
    for _ in range(5):
        s.slide(0x00, param=0x08, value=0x04)
        _settle(s)

    legs = (("A", 1, 10), ("B", 2, 5), ("C", 10, 1))
    out: dict = {"legs": {}, "pixels_per_column": 1.0}
    prev, _ = shot(s, "stage10_start")

    for name, param, count in legs:
        total = 0.0
        steps = []
        for i in range(count):
            s.slide(0x00, param=param, value=0x04)
            _settle(s)
            img, _ = shot(s, f"stage10_{name}_{i:02d}")
            dx, dy = _shift(prev, img)
            steps.append(round(dx, 3))
            total += dx
            prev = img
        per = total / count
        print(f"  leg {name}: {count:2d} x param {param:2d} -> {total:8.2f} px "
              f"total, {per:6.2f} px per command")
        if count > 1:
            print(f"           each: {steps}")
        out["legs"][name] = {"param": param, "commands": count,
                             "total_px": round(total, 3),
                             "per_command_px": round(per, 3),
                             "steps_px": steps}

    a = out["legs"]["A"]["total_px"]
    b = out["legs"]["B"]["total_px"]
    c = out["legs"]["C"]["total_px"]
    cost = (a - c) / 9.0
    unit = (c - cost) / 10.0
    predicted_b = 10.0 * unit + 5.0 * cost

    print(f"\n  cost of issuing a command : {cost:7.3f} px")
    print(f"  one unit of param         : {unit:7.3f} px")
    print(f"  leg B predicted           : {predicted_b:7.2f} px "
          f"against {b:7.2f} measured, off by {b - predicted_b:+.2f}")
    if abs(unit) > 1e-6:
        print(f"  so a command costs {cost / unit:.2f} units before it moves at all")
    print("  a command travels  param + "
          f"{(cost / unit) if abs(unit) > 1e-6 else float('nan'):.2f}  units")

    if abs(cost) < 0.5:
        print("\n  the cost is under half a pixel: param is a linear scale and")
        print("  many small commands are interchangeable with one large one")
    else:
        print(f"\n  the cost is real: {count_note(a, c)}")

    out["cost_px"] = round(cost, 4)
    out["unit_px"] = round(unit, 4)
    out["leg_b_predicted_px"] = round(predicted_b, 3)
    out["leg_b_error_px"] = round(b - predicted_b, 3)
    return out


def count_note(a: float, c: float) -> str:
    ratio = (a / c) if abs(c) > 1e-9 else float("nan")
    return (f"ten small commands travelled {ratio:.2f}x as far as one large "
            "one for the same param total")


def stage11(s: DirectScanner) -> dict:
    """Carry a frame past the aperture and measure both, in units of param.

    Nothing is extrapolated. The film is driven forward one command at a time
    with a prescan after each, far enough that a whole frame pitch passes the
    window, and every band of unexposed base that enters or leaves is recorded
    where it was seen. The distance between one gap arriving and the next is
    the **pitch**; the width of a gap is the **inter-frame space**; the frame
    the camera exposed is the difference.

    `param 12` because section 11 calls the law good to there and bending above
    about twenty. Twenty-eight commands covers a pitch with room to spare.
    """
    print("\n=== stage 11: a whole frame past the window")
    print("  forward at param 12, one prescan per command\n")

    print("  spending backlash -- five forward commands, not measured")
    for _ in range(5):
        s.slide(0x00, param=0x0C, value=0x04)
        _settle(s)

    prev, _ = shot(s, "stage11_00")
    rows = [{"command": 0, "travel_px": 0.0, "bands": _bands_of(prev)}]
    travel = 0.0
    print(f"  {'cmd':>4} {'step':>7} {'travel':>9}  bands of base (start,width) px")
    print(f"  {0:>4} {'-':>7} {0.0:9.2f}  {rows[0]['bands']}")

    for i in range(1, 29):
        s.slide(0x00, param=0x0C, value=0x04)
        _settle(s)
        img, _ = shot(s, f"stage11_{i:02d}")
        dx, _dy = _shift(prev, img)
        travel += dx
        bands = _bands_of(img)
        rows.append({"command": i, "step_px": round(dx, 3),
                     "travel_px": round(travel, 3), "bands": bands})
        print(f"  {i:>4} {dx:7.2f} {travel:9.2f}  {bands}")
        prev = img

    print(f"\n  {len(rows) - 1} commands, {travel:.2f} px of travel")
    print("  the pitch and the frame come out of the band positions above;")
    print("  they are recorded rather than reduced here, so the reduction can")
    print("  be re-run against the stored passes without the scanner.")
    return {"rows": rows, "total_px": round(travel, 3), "param": 12}


def _bands_of(image: np.ndarray) -> list:
    """Runs of film base in one pass, as (start, width) in columns.

    Deliberately not `framing.base_runs`: that wants a strip-calibrated level,
    and the point here is to measure the film rather than to agree with the
    detector under test. A column is base when it is flat down its length and
    brighter than the frame's own median -- the two properties base has that
    nothing else in the window does.
    """
    grey = image.astype(np.float64).mean(axis=2)
    level, spread = grey.mean(axis=0), grey.std(axis=0)
    flat = spread < max(2.0, float(np.median(spread)) * 0.35)
    bright = level > float(np.median(level))
    is_base = flat & bright
    runs, start = [], None
    for i, on in enumerate(is_base):
        if on and start is None:
            start = i
        elif not on and start is not None:
            if i - start >= 3:
                runs.append((start, i - start))
            start = None
    if start is not None and len(is_base) - start >= 3:
        runs.append((start, len(is_base) - start))
    return runs

def stage12(s: DirectScanner) -> dict:
    """How large can `param` go, and does it stay repeatable up there?

    `param` is one byte, so 255 is the ceiling the protocol allows. The vendor
    never sends above 87 and §11 says in as many words that **nothing above
    that has been tried**. §11 also says the law is sub-linear -- the effective
    step decays from about 0.108 at `param` 3-12 to 0.097 at 87 -- and that the
    87 point is the least trustworthy in the set, two readings 0.84 apart.

    So the question is not only how far a big command goes. It is whether it
    goes the same distance twice. Every correction wants the fewest commands,
    because each one costs 1.84 units before it moves at all and scatters on
    top; but that is only worth having if the big command is repeatable. There
    should be an optimum, and it has never been looked for.

    **Escalating here is not the `SET_SCAN_HEAD` hazard.** That command is
    dangerous because it reports nothing, so a step count cannot be calibrated
    by escalating it. This one is measured by a prescan after every send, and
    even `param 255` moves less than one frame pitch, so it cannot run away.

    Each value goes twice, so the spread is measured rather than assumed.
    """
    print("\n=== stage 12: the top of the parameter range")
    print("  nothing above param 87 has ever been sent; each value goes twice\n")

    print("  spending backlash -- five forward commands, not measured")
    for _ in range(5):
        s.slide(0x00, param=0x0C, value=0x04)
        _settle(s)

    prev, _ = shot(s, "stage12_start")
    out: dict = {"params": {}}
    print(f"  {'param':>6} {'pass':>5} {'moved px':>9} {'per param':>10} "
          f"{'bands':>26}")

    for param in (87, 120, 160, 200, 255):
        got, note = [], "accepted"
        for attempt in range(2):
            try:
                s.slide(0x00, param=param, value=0x04)
            except CheckCondition:
                note = f"REFUSED {Sense.parse(s.sense())}"
                print(f"  {param:6d}  {note}")
                break
            except UsbError as e:
                note = f"{type(e).__name__}"
                print(f"  {param:6d}  {note}: {e}")
                break
            _settle(s, 2.5)
            img, _ = shot(s, f"stage12_p{param:03d}_{attempt}")
            # The window is generous because a big move leaves little overlap;
            # the band positions below are the cross-check that does not care.
            expect = param * 1.1
            dx, dy = _shift_near(prev, img, expect, window=120)
            got.append(dx)
            bands = _bands_of(img)
            print(f"  {param:6d} {attempt:5d} {dx:9.2f} {dx/param:10.3f} "
                  f"{str(bands)[:26]:>26}")
            prev = img
        if not got:
            out["params"][param] = {"note": note}
            print("  stopping: the device refused it")
            break
        spread = (max(got) - min(got)) if len(got) > 1 else 0.0
        out["params"][param] = {
            "note": note, "moved_px": [round(g, 3) for g in got],
            "mean_px": round(float(np.mean(got)), 3),
            "per_param_px": round(float(np.mean(got)) / param, 4),
            "spread_px": round(spread, 3),
        }
        if len(got) > 1:
            print(f"  {param:6d}  mean {np.mean(got):8.2f} px, "
                  f"the two differ by {spread:.2f} px "
                  f"({spread/max(abs(np.mean(got)), 1e-9)*100:.1f}%)")
        if got and abs(np.mean(got)) < 2.0:
            print("  stopping: it barely moved")
            break

    rows = [(p, v) for p, v in out["params"].items() if "per_param_px" in v]
    if len(rows) > 1:
        print("\n  effective travel per unit of param, as param grows:")
        for p, v in rows:
            print(f"    param {p:>3}: {v['per_param_px']:.3f} px/param, "
                  f"the two passes {v['spread_px']:.2f} px apart")
        best = min(rows, key=lambda r: r[1]["spread_px"] / max(r[1]["mean_px"], 1e-9))
        print(f"\n  most repeatable of these, relative to what it moves: "
              f"param {best[0]}")
    return out

def _travel(prev: np.ndarray, cur: np.ndarray) -> tuple[float, float]:
    """How far the film moved between two passes, and how much to believe it.

    Searched at several reaches and the most confident answer kept. A single
    reach is what went wrong twice: `measure_shift_mm` looks only +-105 px and
    refused every large move in stage 12, while `_shift_near` looks in a narrow
    window around a *predicted* value and returns the tallest bump in it
    whatever the confidence -- which is how section 11 came to call `param 87`
    the least repeatable point in its set when it is one of the best.
    """
    from rps7200.uniformity import luminance, register

    a, b = luminance(prev), luminance(cur)
    best = (0.0, -1.0)
    for reach in (60, 120, 180, 240):
        _dy, dx, c = register(a, b, max_shift=reach)
        if c > best[1]:
            best = (float(-dx), float(c))
    return best


def _gaps(image: np.ndarray) -> list:
    """Runs of unexposed base, by the two properties base has and nothing else.

    Stricter than stage 11's version, which took any flat run brighter than the
    frame's median and so fired on sky. Base is both **flat down the column**
    and near the brightest thing in the window, and a gap is a solid run of it.
    """
    grey = image.astype(np.float64).mean(axis=2)
    level, spread = grey.mean(axis=0), grey.std(axis=0)
    top = float(np.percentile(level, 92))
    is_base = (spread < 2.5) & (level > top - 4.0)
    runs, start = [], None
    for i, on in enumerate(is_base):
        if on and start is None:
            start = i
        elif not on and start is not None:
            if i - start >= 4:
                runs.append((start, i - start))
            start = None
    if start is not None and len(is_base) - start >= 4:
        runs.append((start, len(is_base) - start))
    return runs


def stage13(s: DirectScanner) -> dict:
    """The film's own geometry, measured in the fewest commands that can be read.

    Stage 11 did this at `param 12` and needed 26 commands to cross a pitch,
    accumulating about six pixels of scatter. `param 87` crosses it in four for
    about two, because the per-command scatter is roughly one pixel whatever
    the command size -- so fewer, larger commands is strictly better, which is
    the opposite of what one might expect and is measured rather than argued.

    `param 87` and not higher because above about 160 the two passes no longer
    share enough film for the correlation to lock, and an unmeasurable move is
    useless however far it goes. At 87 two identical sends landed one pixel
    apart with confidences of 127 and 113.

    Twelve commands carries about three pitches past the window, so several
    gaps enter and leave and each one gives its own reading of the spacing.
    """
    print("\n=== stage 13: the film's geometry, in the fewest readable commands")
    print("  param 87, twelve commands, about three frame pitches\n")

    print("  spending backlash -- five forward commands, not measured")
    for _ in range(5):
        s.slide(0x00, param=0x57, value=0x04)
        _settle(s, 2.5)

    prev, _ = shot(s, "stage13_00")
    travel = 0.0
    rows = [{"command": 0, "travel_px": 0.0, "confidence": None,
             "gaps": _gaps(prev)}]
    print(f"  {'cmd':>3} {'step':>7} {'conf':>7} {'travel':>8}  gaps (start,width)")
    print(f"  {0:>3} {'-':>7} {'-':>7} {0.0:8.1f}  {rows[0]['gaps']}")

    for i in range(1, 13):
        s.slide(0x00, param=0x57, value=0x04)
        _settle(s, 2.5)
        img, _ = shot(s, f"stage13_{i:02d}")
        dx, conf = _travel(prev, img)
        travel += dx
        gaps = _gaps(img)
        rows.append({"command": i, "step_px": round(dx, 2),
                     "confidence": round(conf, 1),
                     "travel_px": round(travel, 2), "gaps": gaps})
        flag = "" if conf >= 55 else "   <- weak, do not trust this step"
        print(f"  {i:>3} {dx:7.2f} {conf:7.1f} {travel:8.1f}  {gaps}{flag}")
        prev = img

    steps = [r["step_px"] for r in rows[1:] if (r["confidence"] or 0) >= 55]
    print(f"\n  {len(steps)} of 12 steps measured confidently")
    if steps:
        arr = np.array(steps)
        print(f"  step at param 87: mean {arr.mean():.2f} px, "
              f"spread {arr.min():.2f}-{arr.max():.2f}, "
              f"sd {arr.std(ddof=1) if len(arr) > 1 else 0:.2f}")
        print(f"  per unit of param: {arr.mean()/87:.4f} px")
    print("\n  the pitch and the frame are reduced from the gap positions above,")
    print("  offline, against the stored passes.")
    return {"rows": rows, "param": 87,
            "steps_px": [round(x, 2) for x in steps]}

#: A step this large is not a sub-frame move. `param 0` cannot legitimately
#: travel further than `param 12` does, so anything past it means the byte was
#: read as something other than a step count -- and the run stops before a
#: second one compounds it.
RUNAWAY_PX = 37.0


def stage14(s: DirectScanner) -> dict:
    """Does `param 0` move the film, and by how much?

    The question is Stefan's and it is the sharpest one available about the
    transport, because the two surviving explanations for the per-command cost
    predict *opposite* answers here. If firmware executes ``param + K`` steps,
    `param 0` travels the cost alone, about two units. If instead the cost is
    an accelerate-cruise-decelerate profile, no profile runs for zero steps and
    `param 0` travels nothing. Nothing in the stored data separates them: both
    fit every ladder ever taken, because no ladder has ever included zero.

    **`param 0` has never been sent to this device by anyone.** Across all six
    vendor captures there are 37 SLIDE commands in twelve distinct payloads and
    the param byte is never 0; the smallest CyberView sends is 1. This driver
    floors it at 1 in three separate places, none of which says why. So this is
    an invented payload under the rule in docs/protocol.md -- allowed here
    because Stefan asked for it by name, not because it is routine.

    It is **not** the `SET_SCAN_HEAD` hazard, and the difference is the whole
    reason this is safe to ask. 0xD2 reports nothing, so a step count cannot be
    calibrated by escalating it. Here a prescan witnesses every single command,
    the frame counter is checked after each one, and a move that went somewhere
    unexpected is undone by one command in the other direction.

    The shape, and why it is not simply "prescan, send, prescan":

    * **Warm-up first, forward.** The roll left the transport loaded in whatever
      direction it last moved. A command that reverses direction loses two to
      three commands to backlash, so an un-warmed `param 0` could read zero for
      a reason that has nothing to do with `param 0`. Three `param 12` commands
      spend the slack, and they are measured rather than thrown away because
      they re-anchor the cost against section 11's ladder using *this* session's
      estimator -- which is the disagreement (1.57 against 1.86 against 2.09)
      that no stored data can settle.
    * **Five sends, not one.** One reading of zero cannot tell a genuine no-op
      from a command that silently did nothing, and the stored data holds
      exactly one such event in twenty-eight sends (stage 11, row 15).
    * **A `param 2` control at the end.** The best-behaved command in the whole
      set, sd 0.035 units. If it reads clean immediately after the zeros, then
      the measurement could see a small move at that moment and a zero really
      was a zero.

    Everything runs forward, so no reading in it is contaminated by a reversal.
    """
    print("\n=== stage 14: does param 0 move the film?")
    print("  an invented payload -- 00 00 00 04 -- never sent by anyone.")
    print("  warm-up forward, then five sends, then a known-good control.\n")

    start = s.read_state()
    pos0 = getattr(start, "position", None)
    print(f"  transport position at the start: {pos0}")

    rows: list[dict] = []
    prev, _ = shot(s, "stage14_00")
    print(f"  {'step':>16} {'param':>6} {'moved px':>9} {'conf':>7} "
          f"{'pos':>4}  note")
    print(f"  {'baseline':>16} {'-':>6} {'-':>9} {'-':>7} {str(pos0):>4}")

    def send(param: int, label: str, tag: str) -> bool:
        """One command, then look. False means stop the stage."""
        nonlocal prev
        try:
            s.slide(0x00, param=param, value=0x04)
        except CheckCondition:
            # Sense is one-shot: read it now or lose why it refused.
            note = f"REFUSED {Sense.parse(s.sense())}"
            rows.append({"step": label, "param": param, "note": note})
            print(f"  {label:>16} {param:>6} {note}")
            return False
        except UsbError as e:
            rows.append({"step": label, "param": param,
                         "note": f"{type(e).__name__}: {e}"})
            print(f"  {label:>16} {param:>6} {type(e).__name__}: {e}")
            return False

        _settle(s, 2.5)
        img, _ = shot(s, tag)
        dx, conf = _travel(prev, img)
        state = s.read_state()
        pos = getattr(state, "position", None)

        note = ""
        stop = False
        if abs(dx) > RUNAWAY_PX:
            note, stop = "RUNAWAY -- stopping", True
        elif pos != pos0:
            # A sub-frame move must not touch the frame counter. If it did,
            # this was not a sub-frame move and the premise is void.
            note, stop = f"FRAME COUNTER MOVED {pos0}->{pos} -- stopping", True
        elif conf < 55:
            note = "weak -- do not trust this step"

        rows.append({"step": label, "param": param, "moved_px": round(dx, 3),
                     "confidence": round(conf, 1), "position": pos,
                     "note": note})
        print(f"  {label:>16} {param:>6} {dx:9.2f} {conf:7.1f} {str(pos):>4}"
              f"  {note}")
        prev = img
        return not stop

    for i in range(3):
        if not send(12, f"warm-up {i + 1}/3", f"stage14_warm{i}"):
            return {"rows": rows, "aborted": "warm-up"}

    for i in range(5):
        if not send(0, f"PARAM 0  {i + 1}/5", f"stage14_zero{i}"):
            return {"rows": rows, "aborted": "param 0"}

    send(2, "control", "stage14_control")

    zeros = [r for r in rows if r.get("param") == 0
             and r.get("moved_px") is not None]
    good = [r["moved_px"] for r in zeros if (r.get("confidence") or 0) >= 55]
    warm = [r["moved_px"] for r in rows if r.get("param") == 12
            and (r.get("confidence") or 0) >= 55]

    print()
    if good:
        a = np.array(good)
        # One unit is about 1.24 px at 300 dpi; param 1 travels about 2.6.
        print(f"  param 0 moved {a.mean():.2f} px on average "
              f"({a.mean() / 1.2423:.2f} units), "
              f"spread {a.min():.2f}-{a.max():.2f}, "
              f"sd {a.std(ddof=1) if len(a) > 1 else 0:.2f}")
        if abs(a.mean()) < 0.6:
            print("  -> it does NOT move. The cost is part of the first step, "
                  "and the floor at param 1 was right.")
        else:
            print("  -> it DOES move. The cost is per command, and there is a "
                  "move below the current floor.")
    else:
        print("  no confident reading of param 0 -- inconclusive, do not "
              "change anything on this")
    if warm:
        w = np.array(warm)
        print(f"  param 12 here: {w.mean():.2f} px "
              f"({w.mean() / 1.2423:.2f} units), for the cost re-anchor")
    return {"rows": rows, "param0_px": good, "param12_px": warm}


#: The rungs, and how many times each is sent. Low params repeat because the
#: per-command cost is determined by the *intercept*, and the intercept is
#: pinned by the small end of the ladder; the big ones repeat because whether
#: they are repeatable is the whole question about raising the clamp.
STAGE15_RUNGS = ((12, 3), (2, 3), (8, 3), (20, 2), (87, 1), (160, 2))

#: A step this far past what the law predicts is not the move that was asked
#: for. 30% is wide enough for the 1.57-versus-1.84 disagreement the stage
#: exists to settle, and narrow enough to catch a byte read as something else.
STAGE15_RUNAWAY = 1.30


def stage15(s: DirectScanner) -> dict:
    """How large a single correction can be, and what a command really costs.

    Two questions in one ladder, because they need the same passes.

    **Can the clamp come up?** `MAX_CORRECTION_PARAM = 8` refuses any
    correction past about 9.6 units, so a larger one is chained across three
    commands -- three times the scatter and three times the time. Its stated
    reason is `MAX_REGISTRATION_MM = 0.49`: "a larger reading is the detector
    failing, not the film moving". This branch measured otherwise. The walk of
    2026-09-22 proposed corrections out to 13.78 units and every one of them
    placed its frame, and walk A sat 1.4 to 2.2 mm out. The premise is gone, so
    the number resting on it has to be re-derived rather than inherited.

    **What does a command cost?** `DirectScanner.OVERHEAD_MM` says 1.57 units
    and `framing.COMMAND_COST` says 1.84, a 17% disagreement between two
    constants describing one command. They came from different sessions using
    *different correlation estimators*, which is the most likely explanation
    and is not resolvable from anything already stored. One ladder, one
    estimator, repeats per rung, settles it.

    Everything runs **backward**. The film sits on the last frame of the strip,
    so there is nothing forward of it to correlate against -- and a ladder is
    only as good as the picture it is measured on. Direction costs nothing
    here: forward and reverse intercepts agree to 6% and slopes to 1.5%
    (`docs/protocol.md` section 11).

    The warm-up is backward too, for the same reason it exists in stages 13 and
    14: the first command after a direction change loses two to three commands
    to backlash, and a rung measured through that is measuring the reversal.

    Afterwards it **walks the film back to where it started**. Sub-frame moves
    do not touch the frame counter, so a ladder that simply stopped would leave
    the counter claiming a frame the film is no longer on -- a desync that has
    cost a run here before. The restore is verified against the opening pass,
    and what it could not close is reported rather than assumed.
    """
    print("\n=== stage 15: how large a correction can be, and what one costs")
    print("  backward -- the film is on the last frame, nothing lies forward")
    print(f"  rungs: {', '.join(f'param {p} x{n}' for p, n in STAGE15_RUNGS)}\n")

    start = s.read_state()
    pos0 = getattr(start, "position", None)
    home, _ = shot(s, "stage15_home")
    prev, travel = home, 0.0
    rows: list[dict] = []
    print(f"  {'param':>6} {'#':>3} {'moved px':>9} {'units':>8} {'per param':>10} "
          f"{'conf':>7}  note")

    def one(param: int, tag: str) -> bool:
        """Send it backward, look, and say whether the ladder may continue."""
        nonlocal prev, travel
        try:
            s.slide(0x01, param=param, value=0x04)
        except CheckCondition:
            note = f"REFUSED {Sense.parse(s.sense())}"
            rows.append({"param": param, "note": note})
            print(f"  {param:6d}  {note}")
            return False
        except UsbError as e:
            rows.append({"param": param, "note": f"{type(e).__name__}: {e}"})
            print(f"  {param:6d}  {type(e).__name__}: {e}")
            return False

        _settle(s, 2.5)
        img, _ = shot(s, tag)
        dx, conf = _travel(prev, img)
        moved = abs(dx)
        # what the law in direct.py predicts, in prescan columns
        want = (param + 1.5724) * 1.2423
        pos = getattr(s.read_state(), "position", None)

        note, stop = "", False
        if moved > want * STAGE15_RUNAWAY:
            note, stop = f"RUNAWAY -- {want:.0f} px expected", True
        elif pos != pos0:
            note, stop = f"FRAME COUNTER MOVED {pos0}->{pos}", True
        elif conf < 55:
            note, stop = "cannot be verified -- the ceiling is below here", True

        travel += moved
        rows.append({"param": param, "moved_px": round(moved, 3),
                     "units": round(moved / 1.2423, 3),
                     "confidence": round(conf, 1), "position": pos,
                     "note": note})
        print(f"  {param:6d} {len(rows):3d} {moved:9.2f} {moved/1.2423:8.2f} "
              f"{moved/max(param, 1):10.3f} {conf:7.1f}  {note}")
        prev = img
        return not stop

    stopped = None
    for param, repeats in STAGE15_RUNGS:
        for i in range(repeats):
            if not one(param, f"stage15_p{param:03d}_{i}"):
                stopped = param
                break
        if stopped is not None:
            break

    # -- put the film back ---------------------------------------------------
    print(f"\n  travelled {travel:.1f} px back ({travel/1.2423:.1f} units); "
          "walking it forward again")
    restored = None
    for attempt in range(6):
        left = travel
        if left < 3.0:
            break
        param = min(160, max(1, int(round(left / 1.2423 - 1.5724))))
        try:
            s.slide(0x00, param=param, value=0x04)
        except (CheckCondition, UsbError) as e:
            print(f"  restore stopped: {type(e).__name__}")
            break
        _settle(s, 2.5)
        img, _ = shot(s, f"stage15_back{attempt}")
        dx, conf = _travel(prev, img)
        travel -= abs(dx)
        prev = img
        print(f"    forward param {param:3d} -> {abs(dx):6.2f} px, "
              f"{travel:6.2f} px still to go (conf {conf:.0f})")
        if conf < 55:
            print("    the restore cannot be verified; stopping here")
            break
    else:
        restored = travel
    _dy, dx_home, conf_home = _travel(home, prev) if prev is not home else (0, 0.0, 0.0)
    print(f"\n  against the opening pass: {abs(dx_home):.1f} px "
          f"({abs(dx_home)/1.2423:.1f} units) from home, confidence {conf_home:.0f}")
    if abs(dx_home) > 12:
        print("  ** the film did NOT come back. The frame counter still reads "
              f"{pos0}; re-register before scanning. **")

    # -- what it says --------------------------------------------------------
    good = [r for r in rows if (r.get("confidence") or 0) >= 55
            and r.get("moved_px") is not None]
    print()
    if len(good) >= 3:
        xs = np.array([r["param"] for r in good], float)
        ys = np.array([r["moved_px"] for r in good], float)
        slope, intercept = np.polyfit(xs, ys, 1)
        cost = intercept / slope
        print(f"  fit over {len(good)} confident steps: "
              f"{slope:.4f} px/param + {intercept:.3f} px")
        print(f"  -> one unit is {slope:.4f} px, a command costs {cost:.3f} units")
        print("     direct.py says 1.572, framing.py says 1.84")
        for param, _n in STAGE15_RUNGS:
            at = [r["moved_px"] for r in good if r["param"] == param]
            if len(at) > 1:
                a = np.array(at)
                print(f"     param {param:>3}: {a.mean():7.2f} px, "
                      f"sd {a.std(ddof=1):.2f}, spread {a.max()-a.min():.2f}")
    highest = max((r["param"] for r in good), default=None)
    print(f"\n  highest param that measured confidently: {highest}")
    if stopped is not None:
        print(f"  stopped at param {stopped} -- do not raise the clamp above "
              f"{highest}")
    return {"rows": rows, "travelled_px": round(travel, 2),
            "from_home_px": round(abs(dx_home), 2), "restored": restored,
            "highest_confident_param": highest, "stopped_at": stopped}


STAGES = {1: stage1, 2: stage2, 3: stage3, 4: stage4, 5: stage5, 6: stage6,
          7: stage7, 8: stage8, 9: stage9,
          10: stage10, 11: stage11, 12: stage12, 13: stage13, 14: stage14,
          15: stage15}
NEEDS_FILM = {1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15}


def main() -> int:
    use_utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("stages", nargs="+", type=int, choices=sorted(STAGES))
    ap.add_argument("--out", default="probe")
    args = ap.parse_args()

    global OUT
    OUT = Path(args.out)
    OUT.mkdir(parents=True, exist_ok=True)

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
    prev = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    prev.update(results)
    path.write_text(json.dumps(prev, indent=2), encoding="utf-8")
    print(f"\nwritten to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
