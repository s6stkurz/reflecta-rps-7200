#!/usr/bin/env python3
"""Does compressing in the background disturb a scan in progress?

    RPS7200_DEBUG=1 uv run python tools/filing_load_test.py          # macOS, Linux
    $env:RPS7200_DEBUG=1; uv run python tools/filing_load_test.py    # PowerShell

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
effect. Debug filing on, as for every scan here: each pass spools the same way
in both arms, so it cannot tilt the comparison.

The verdict is one of three, against a stated line -- see :func:`verdict`.
It used to be two, and one of them could not happen: it called any difference
smaller than twice the spread of *all* passes "safe", and that spread contains
the difference itself, so a consistent 27% slowdown with a second of jitter
read "no measurable effect".

Sends only ordinary scans. Nothing here touches the transport.
"""
from __future__ import annotations

import argparse
import gzip
import math
import statistics
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from rps7200.console import use_utf8_stdout
from rps7200.direct import DEPTH_8, FULL_FRAME, DirectScanner


#: How much slower a pass may run under load and still count as undisturbed, as
#: a fraction of a quiet pass. A line chosen, not measured: the hazard is the
#: read loop starved into a timeout, and a slowdown is the symptom that shows
#: before one. 5% of a 22 s pass is about a second; a line much finer than the
#: jitter between identical passes could only ever return "inconclusive".
#: `--limit` moves it; say so wherever the result is quoted.
SLOWDOWN_LIMIT = 0.05

#: Standard errors of the paired mean that must clear the line on one side or
#: the other before the verdict takes a side.
MARGIN_SE = 2.0

#: Below this many rounds the spread of the differences is itself a guess.
MIN_ROUNDS = 3


def verdict(quiet: list[float], loaded: list[float],
            limit: float = SLOWDOWN_LIMIT) -> tuple[str, str]:
    """``("safe" | "unsafe" | "inconclusive", why)`` for paired pass timings.

    Paired, because the rounds alternate: round *i*'s loaded pass minus its
    quiet one takes out whatever drifted between rounds, and what is left is
    the effect plus noise. The mean of those differences is judged against
    ``limit`` times the quiet mean, with `MARGIN_SE` standard errors either
    side: clear below the line is safe, clear above it is unsafe, and a range
    that straddles it says to run more rounds. Only "safe" says to overlap.
    """
    n = min(len(quiet), len(loaded))
    if n < MIN_ROUNDS:
        return "inconclusive", (f"{n} round(s); at least {MIN_ROUNDS} are needed "
                                f"before the spread of the differences means "
                                f"anything")
    diffs = [b - a for a, b in zip(quiet[:n], loaded[:n])]
    base = statistics.mean(quiet[:n])
    mean = statistics.mean(diffs)
    se = statistics.stdev(diffs) / math.sqrt(n)
    line = limit * base
    lo, hi = mean - MARGIN_SE * se, mean + MARGIN_SE * se
    said = (f"loaded passes {mean:+.2f}s ({mean / base:+.1%}), "
            f"{lo:+.2f}s to {hi:+.2f}s at {MARGIN_SE:g} standard errors, "
            f"against a line of {line:.2f}s ({limit:.0%} of a quiet pass)")
    if lo > line:
        return "unsafe", f"{said} -- slower than the line: do NOT overlap filing"
    if hi < line:
        return "safe", f"{said} -- under the line: overlapping filing looks safe"
    return "inconclusive", (f"{said} -- the range straddles the line: run more "
                            f"rounds before trusting either answer")


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
    use_utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=4,
                    help="quiet/loaded pairs to time")
    ap.add_argument("--mb", type=int, default=32,
                    help="size of the block the background thread compresses")
    ap.add_argument("--limit", type=float, default=100 * SLOWDOWN_LIMIT,
                    metavar="PCT",
                    help="the slowdown, in percent of a quiet pass, past which "
                         "loaded passes count as disturbed (default %(default)g)")
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
        outcome, why = verdict(quiet, loaded, args.limit / 100.0)
        print(f"  -> {outcome}: {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
