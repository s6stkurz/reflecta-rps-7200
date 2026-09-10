#!/usr/bin/env python3
"""Does compressing in the background disturb a scan in progress?

    RPS7200_DEBUG=0 python3 tools/filing_load_test.py

The question this answers is whether library filing can overlap the *next* frame's
scan, instead of waiting for the session to close. That would remove the spool
entirely and the end-of-roll wait -- 11 minutes on a 38-frame 7200 dpi roll.

The reason not to assume it is CLAUDE.md: gzipping a 140 MB entry with the device
open and idle preceded a wedge. "Preceded" is correlation, and the plausible
mechanism is CPU starving the USB read loop into a timeout, which is a documented
way to wedge this scanner. So measure rather than assume.

Method: time identical 300 dpi passes, alternating between a quiet host and one
gzipping in a background thread, and compare. Alternating rather than
before-and-after so that lamp warm-up or thermal drift cannot masquerade as an
effect.

Sends only ordinary scans. Nothing here touches the transport.
"""
from __future__ import annotations

import argparse
import gzip
import statistics
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from rps7200.direct import DEPTH_8, FULL_FRAME, DirectScanner


class Grinder:
    """Compresses in a background thread, as filing would."""

    def __init__(self, payload: bytes):
        self.payload = payload
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.passes = 0

    def _work(self) -> None:
        while not self._stop.is_set():
            gzip.compress(self.payload, compresslevel=6)
            self.passes += 1

    def __enter__(self) -> "Grinder":
        self._thread = threading.Thread(target=self._work, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=30)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=4,
                    help="quiet/loaded pairs to time")
    ap.add_argument("--mb", type=int, default=32,
                    help="size of the block the background thread compresses")
    args = ap.parse_args()

    # Incompressible, so gzip works as hard as it would on scanner data.
    payload = np.random.default_rng(0).integers(
        0, 255, args.mb << 20, dtype=np.uint8).tobytes()

    quiet: list[float] = []
    loaded: list[float] = []

    with DirectScanner(verbose=False) as s:
        s.wait_ready(timeout=180.0)
        s.wait_warm(timeout=300.0)

        def one() -> float:
            t0 = time.monotonic()
            s.scan(resolution=300, infrared=False, depth=DEPTH_8,
                   frame=FULL_FRAME, shading=False, require_media=False)
            return time.monotonic() - t0

        print(f"{'round':>6} {'quiet':>9} {'loaded':>9} {'difference':>11}")
        for r in range(1, args.rounds + 1):
            q = one()
            with Grinder(payload) as g:
                under_load = one()
            quiet.append(q)
            loaded.append(under_load)
            print(f"{r:6d} {q:8.2f}s {under_load:8.2f}s {under_load - q:+10.2f}s"
                  f"   ({g.passes} gzip passes alongside)")

        qm, lm = statistics.mean(quiet), statistics.mean(loaded)
        print(f"\n  quiet  mean {qm:.2f}s   (n={len(quiet)})")
        print(f"  loaded mean {lm:.2f}s   (n={len(loaded)})")
        print(f"  difference  {lm - qm:+.2f}s = {(lm - qm) / qm:+.1%}")
        if len(quiet) > 2:
            spread = statistics.pstdev(quiet + loaded)
            print(f"  spread across all passes {spread:.2f}s")
            verdict = ("no measurable effect -- overlapping filing looks safe"
                       if abs(lm - qm) < 2 * spread else
                       "reads slow under load -- do NOT overlap filing")
            print(f"  -> {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
