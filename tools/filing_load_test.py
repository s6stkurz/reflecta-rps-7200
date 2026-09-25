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
effect -- and the order inside a round alternates too, quiet first in one
round and loaded first in the next, because a round that always ran quiet
first gave every loaded pass the second slot and whatever drifts across a
round with it. Debug filing on, as for every scan here: each pass spools the
same way in both arms, so it cannot tilt the comparison.

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
from collections.abc import Callable
from contextlib import AbstractContextManager
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


def loaded_first(round_index: int) -> bool:
    """Whether round `round_index`, counted from 0, times its loaded pass first.

    Every other round. Pairing a round's two passes takes out drift *between*
    rounds; it cannot take out drift *inside* one -- the second pass of a
    round running warmer, or on a warmer cache, than the first -- because that
    lands on whichever arm always goes second. Alternating the order puts it on
    the loaded arm in half the rounds and on the quiet arm in the other half,
    where :func:`verdict` cancels it.
    """
    return round_index % 2 == 1


def run_rounds(one: Callable[[], float], rounds: int,
               load: Callable[[], AbstractContextManager],
               show: Callable[[str], None] = print,
               ) -> tuple[list[float], list[float]]:
    """Time `rounds` quiet/loaded pairs, in the order :func:`loaded_first` sets.

    ``one()`` takes a pass and returns its seconds; ``load()`` is the
    background compression, as a context manager that runs while it is open.
    Returns ``(quiet, loaded)``, one of each per round.
    """
    def under_load() -> tuple[float, object]:
        with load() as g:
            return one(), g

    quiet: list[float] = []
    loaded: list[float] = []
    show(f"{'round':>6} {'first':>7} {'quiet':>9} {'loaded':>9} {'difference':>11}")
    for i in range(rounds):
        if loaded_first(i):
            t_loaded, g = under_load()
            t_quiet = one()
        else:
            t_quiet = one()
            t_loaded, g = under_load()
        quiet.append(t_quiet)
        loaded.append(t_loaded)
        show(f"{i + 1:6d} {'loaded' if loaded_first(i) else 'quiet':>7} "
             f"{t_quiet:8.2f}s {t_loaded:8.2f}s {t_loaded - t_quiet:+10.2f}s"
             f"   ({getattr(g, 'passes', 0)} gzip passes alongside)")
    return quiet, loaded


def verdict(quiet: list[float], loaded: list[float],
            limit: float = SLOWDOWN_LIMIT) -> tuple[str, str]:
    """``("safe" | "unsafe" | "inconclusive", why)`` for paired pass timings.

    Paired: round *i*'s loaded pass minus its quiet one takes out whatever
    drifted between rounds. The rounds are in :func:`run_rounds`'s order, so
    a drift inside a round adds to the differences of the quiet-first rounds
    and subtracts from the loaded-first ones; the effect is the mean of the
    two orders' means, which cancels it whatever the count of each. The drift
    stays in the spread, so a large one widens the range rather than taking a
    side, and the two orders' means are reported so it shows.

    That effect is judged against ``limit`` times the quiet mean, with
    `MARGIN_SE` standard errors either side: clear below the line is safe,
    clear above it is unsafe, and a range that straddles it says to run more
    rounds. Only "safe" says to overlap.
    """
    n = min(len(quiet), len(loaded))
    if n < MIN_ROUNDS:
        return "inconclusive", (f"{n} round(s); at least {MIN_ROUNDS} are needed "
                                f"before the spread of the differences means "
                                f"anything")
    diffs = [b - a for a, b in zip(quiet[:n], loaded[:n])]
    quiet_first = [d for i, d in enumerate(diffs) if not loaded_first(i)]
    load_first = [d for i, d in enumerate(diffs) if loaded_first(i)]
    orders = [d for d in (quiet_first, load_first) if d]
    base = statistics.mean(quiet[:n])
    mean = statistics.mean(statistics.mean(d) for d in orders)
    se = (statistics.stdev(diffs) * math.sqrt(sum(1 / len(d) for d in orders))
          / len(orders))
    line = limit * base
    lo, hi = mean - MARGIN_SE * se, mean + MARGIN_SE * se
    said = (f"loaded passes {mean:+.2f}s ({mean / base:+.1%}), "
            f"{lo:+.2f}s to {hi:+.2f}s at {MARGIN_SE:g} standard errors, "
            f"against a line of {line:.2f}s ({limit:.0%} of a quiet pass)")
    if len(orders) == 2:
        said += (f"; {statistics.mean(quiet_first):+.2f}s in rounds run quiet "
                 f"first, {statistics.mean(load_first):+.2f}s loaded first")
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
                    help="quiet/loaded pairs to time; an even number balances "
                         "the two orders")
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

    with DirectScanner(verbose=False) as s:
        s.wait_ready(timeout=180.0)
        s.wait_warm(timeout=300.0)

        def one() -> float:
            t0 = time.monotonic()
            s.scan(resolution=300, infrared=False, depth=DEPTH_8,
                   frame=FULL_FRAME, shading=False, require_media=False)
            return time.monotonic() - t0

        quiet, loaded = run_rounds(one, args.rounds, lambda: Grinder(payload))

        qm, lm = statistics.mean(quiet), statistics.mean(loaded)
        print(f"\n  quiet  mean {qm:.2f}s   (n={len(quiet)})")
        print(f"  loaded mean {lm:.2f}s   (n={len(loaded)})")
        print(f"  difference  {lm - qm:+.2f}s = {(lm - qm) / qm:+.1%}")
        outcome, why = verdict(quiet, loaded, args.limit / 100.0)
        print(f"  -> {outcome}: {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
