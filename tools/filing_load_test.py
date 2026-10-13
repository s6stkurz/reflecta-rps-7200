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

The verdict is one of three, against a stated line -- see :func:`verdict` --
and the exit status says which (`EXIT_CODES`), so a script can gate on it.
It used to be two, and one of them could not happen: it called any difference
smaller than twice the spread of *all* passes "safe", and that spread contains
the difference itself, so a consistent 27% slowdown with a second of jitter
read "no measurable effect".

Sends only ordinary scans. Nothing here touches the transport. What a run
should cost, and whether to background it, is said before the device opens:
the default four rounds come to about six minutes at the slow end, and six
rounds are past the line.

    uv run python tools/filing_load_test.py --library library

opens no device at all: it reads the library's records and says how the
passes that ran beside another entry's filing compare with those that did
not, and -- for passes filed since 2026-10-13 -- what each recorded of the
host work beside it (`extra.host_load`) and of the gaps between its reads
(`extra.commands.image_reads`). Counts and medians, never a verdict: the
passes are not paired, and frame content and resolution confound them.
Stefan's decision of 2026-10-13 ("measure"): this, and the record on every
pass, before any more scanner time; the real-load arm is not built.
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from rps7200 import library, session
from rps7200.console import DeferredInterrupt, use_utf8_stdout
from rps7200.direct import DEPTH_8, FULL_FRAME, DirectScanner

#: Every pass this takes, quiet and loaded alike: what it times and what its
#: estimate counts, from one number.
RESOLUTION = 300


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

#: What `main` returns for each verdict, so a script gating the unattended
#: roll path on this run can tell them apart: it returned 0 for all three, and
#: "unsafe" passed the gate. Inconclusive is 3 rather than 2 because 2 is
#: argparse's own, for arguments it refused -- which is not a run at all.
EXIT_CODES = {"safe": 0, "unsafe": 1, "inconclusive": 3}


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


#: A filing longer than this is not believed: an entry filed plain is
#: compacted after the device closes, which rewrites its `scan.json`, so its
#: modification time is the compaction's and not the filing's end.
MAX_FILING_S = 120.0


def _when(text: Any) -> datetime | None:
    """A record's UTC time, or None where it has none that reads."""
    if not isinstance(text, str) or not text:
        return None
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _kind(record: dict[str, Any]) -> str:
    """What a pass was for: a roll's frame or prescan, a probe, a hold, a
    scan."""
    extra = record.get("extra") or {}
    member = (extra.get("roll_membership") or {}).get("kind")
    if member:
        return str(member)
    role = extra.get("pass_role")
    role = role.get("kind") if isinstance(role, dict) else role
    if role:
        return str(role)
    tags = set(record.get("tags") or ())
    for tag in ("probe", "hold", "before"):
        if tag in tags:
            return tag
    return "scan"


def library_rows(root: Path) -> list[dict[str, Any]]:
    """Each stored pass as the report reads it: when it ran, how long, and
    when the library filed it -- from the timestamps every record carries."""
    rows = []
    for record in library.entries(root):
        extra = record.get("extra") or {}
        scan = record.get("scan") or {}
        started = _when(extra.get("started_utc"))
        duration = scan.get("duration_s")
        if started is None or not isinstance(duration, (int, float)):
            continue
        filed = _when(record.get("created"))
        filed_end = None
        try:
            stamp = (root / record["_dir"] / "scan.json").stat().st_mtime
            filed_end = datetime.fromtimestamp(stamp, timezone.utc)
        except (OSError, KeyError, TypeError, ValueError):
            pass
        if (filed is not None and filed_end is not None
                and not 0 <= (filed_end - filed).total_seconds()
                <= MAX_FILING_S):
            filed_end = None
        reads = (extra.get("commands") or {}).get("image_reads") or {}
        rows.append({
            "id": record.get("id") or record.get("_dir"),
            "kind": _kind(record),
            "dpi": scan.get("resolution_dpi"),
            "depth": scan.get("depth"),
            "start": started,
            "end": started + timedelta(seconds=float(duration)),
            "duration": float(duration),
            "filed": filed,
            "filed_end": filed_end,
            "host_load": extra.get("host_load"),
            "host_gap_max_s": reads.get("host_gap_max_s"),
        })
    return rows


def _overlapped(row: dict[str, Any], rows: list[dict[str, Any]]) -> bool:
    """Whether another entry's filing ran while this pass did."""
    return any(other["filed"] is not None and other["filed_end"] is not None
               and other["id"] != row["id"]
               and other["filed"] < row["end"]
               and other["filed_end"] > row["start"]
               for other in rows)


def _said(values: list[float]) -> str:
    if not values:
        return "n=0"
    return (f"n={len(values)} median {statistics.median(values):.2f}s "
            f"max {max(values):.1f}s")


def library_report(root: Path, since: datetime | None = None,
                   say: Callable[[str], None] = print) -> dict[str, Any]:
    """Say, per kind of pass, how passes another entry's filing overlapped
    compare with those it did not, and what the passes that record it say
    of the host work beside them. Opens no device; returns what it said, by
    group, for a caller that wants the numbers.

    The filing is read from the record's `created`, when `library.save`
    began, to its `scan.json`'s modification time; the pass from
    `extra.started_utc` and `scan.duration_s`, whole seconds at the start.
    """
    rows = [r for r in library_rows(root)
            if since is None or r["start"] >= since]
    groups: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        key = (row["kind"], row["dpi"], row["depth"])
        group = groups.setdefault(key, {"overlapped": [], "alone": [],
                                        "recorded_load": [],
                                        "recorded_none": [], "gaps_load": [],
                                        "gaps_none": []})
        group["overlapped" if _overlapped(row, rows) else "alone"].append(
            row["duration"])
        load = row["host_load"]
        if isinstance(load, dict) and "unread" not in load:
            busy = bool(load)
            group["recorded_load" if busy else "recorded_none"].append(
                row["duration"])
            gap = row["host_gap_max_s"]
            if isinstance(gap, (int, float)):
                group["gaps_load" if busy else "gaps_none"].append(float(gap))
    unknown = sum(1 for r in rows if r["filed"] is not None
                  and r["filed_end"] is None)
    say(f"{len(rows)} passes in {root}"
        + (f" since {since:%Y-%m-%d}" if since else "")
        + f"; {unknown} whose filing's end cannot be told (compacted later, "
          f"or longer than {MAX_FILING_S:.0f} s) are left out as filings")
    say("No verdict: these passes are not paired, and frame content and "
        "resolution move their times as much as any load.")
    for (kind, dpi, depth), group in sorted(
            groups.items(), key=lambda kv: tuple(str(k) for k in kv[0])):
        say(f"\n{kind}, {dpi} dpi, {depth}-bit")
        say(f"  beside another entry's filing: {_said(group['overlapped'])}")
        say(f"  with no filing beside it:      {_said(group['alone'])}")
        if group["recorded_load"] or group["recorded_none"]:
            say(f"  recorded host work beside it:  "
                f"{_said(group['recorded_load'])}")
            say(f"  recorded none:                 "
                f"{_said(group['recorded_none'])}")
        if group["gaps_load"] or group["gaps_none"]:
            say(f"  longest host gap between reads, with work beside: "
                f"{_said(group['gaps_load'])}; without: "
                f"{_said(group['gaps_none'])}")
    return {"passes": len(rows), "unknown_filing_end": unknown,
            "groups": groups}


class _Stopped(Exception):
    """Ctrl-C, taken between two passes rather than inside one."""


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
    ap = argparse.ArgumentParser(
        epilog="Exits 0 when the verdict is safe, 1 when it is unsafe and 3 "
               "when it is inconclusive; 2 is refused arguments and 130 a "
               "run stopped at Ctrl-C, neither of which is a verdict.")
    ap.add_argument("--rounds", type=int, default=4,
                    help="quiet/loaded pairs to time; an even number balances "
                         "the two orders")
    ap.add_argument("--mb", type=int, default=32,
                    help="size of the block the background thread compresses")
    ap.add_argument("--limit", type=float, default=100 * SLOWDOWN_LIMIT,
                    metavar="PCT",
                    help="the slowdown, in percent of a quiet pass, past which "
                         "loaded passes count as disturbed (default %(default)g)")
    ap.add_argument("--library", metavar="PATH",
                    help="report from this library's records instead, and "
                         "open no device: passes beside another entry's "
                         "filing against those with none, and what passes "
                         "since 2026-10-13 recorded of the host work beside "
                         "them. Counts and medians, no verdict")
    ap.add_argument("--since", metavar="YYYY-MM-DD",
                    help="with --library: only passes begun on or after this "
                         "UTC day")
    args = ap.parse_args()
    if args.since is not None and args.library is None:
        ap.error("--since reads a library: give --library too")
    if args.library is not None:
        since = None
        if args.since is not None:
            since = _when(args.since + "T00:00:00Z")
            if since is None:
                ap.error(f"--since {args.since}: not a YYYY-MM-DD day")
        root = Path(args.library)
        if not root.is_dir():
            ap.error(f"--library {root}: no such folder")
        library_report(root, since)
        return 0
    # Before the lamp is warmed, not after: --rounds 0 warmed it and then
    # died on the mean of nothing, and fewer than MIN_ROUNDS spend the
    # scanner's time on a run whose only verdict can be "inconclusive".
    if args.rounds < MIN_ROUNDS:
        ap.error(f"--rounds {args.rounds}: a verdict needs at least "
                 f"{MIN_ROUNDS}")
    if args.mb <= 0:
        ap.error(f"--mb must be positive, got {args.mb}")
    if args.limit <= 0:
        ap.error(f"--limit must be positive, got {args.limit:g}")
    # Before the device opens, as every other tool here says it: two passes
    # a round, and --rounds 14 is 28 of them and the lamp, past the
    # ten-minute kill -- which abandons the read in flight -- without a word.
    session.say_estimate(
        2 * args.rounds * session.estimate_seconds(RESOLUTION, False), 0.0,
        say=lambda m: print(m, flush=True),
        warn=lambda m: print(m, file=sys.stderr, flush=True))

    # Incompressible, so gzip works as hard as it would on scanner data.
    payload = np.random.default_rng(0).integers(
        0, 255, args.mb << 20, dtype=np.uint8).tobytes()

    # Ctrl-C finishes the pass in flight and starts no other. With the
    # default handler one press abandoned the read -- in the run CLAUDE.md
    # asks for before trusting the roll path unattended.
    interrupt = DeferredInterrupt()
    try:
        with interrupt, DirectScanner(verbose=False) as s:
            s.wait_ready(timeout=180.0)
            s.wait_warm(timeout=300.0)

            def one() -> float:
                if interrupt.requested():
                    raise _Stopped
                # perf_counter: monotonic() advances in ~15.6 ms steps on
                # Windows, coarse beside the differences this measures.
                t0 = time.perf_counter()
                s.scan(resolution=RESOLUTION, infrared=False, depth=DEPTH_8,
                       frame=FULL_FRAME, shading=False, require_media=False)
                return time.perf_counter() - t0

            quiet, loaded = run_rounds(one, args.rounds,
                                       lambda: Grinder(payload))

            qm, lm = statistics.mean(quiet), statistics.mean(loaded)
            print(f"\n  quiet  mean {qm:.2f}s   (n={len(quiet)})")
            print(f"  loaded mean {lm:.2f}s   (n={len(loaded)})")
            share = f" = {(lm - qm) / qm:+.1%}" if qm > 0 else ""
            print(f"  difference  {lm - qm:+.2f}s{share}")
            outcome, why = verdict(quiet, loaded, args.limit / 100.0)
            print(f"  -> {outcome}: {why}")
    except _Stopped:
        print("stopped at Ctrl-C between passes: part of a run gives no "
              "verdict", file=sys.stderr)
        return 130
    return EXIT_CODES[outcome]


if __name__ == "__main__":
    raise SystemExit(main())
