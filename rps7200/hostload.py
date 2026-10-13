"""What heavy work the host ran while each pass was in flight.

Every pass records it (`extra.host_load`), because the question CLAUDE.md
leaves open -- does heavy local work beside an open scanner do it harm? --
was answerable only from timestamps that some passes have and others do not.
Filing records when it started and the scan when it ran, so a roll frame's
overlap with the prescan after it can be worked out afterwards; a metering
probe or a hold is mostly not filed, and the window's Save all, Export,
Duplicate and full-resolution view leave no timestamps at all. Stefan's
decision, 2026-10-13: record it on every pass rather than run the load test.

So the work stamps a ledger as it runs -- the writer thread around each job,
the window's threads around theirs -- and the driver reads it **once**, after
a pass's last READ has returned, as the seconds of each kind of work that
overlapped the pass. Never from inside the read loop, never from the
progress hook: a read is the one thing on this host that must not wait on
anything, and an abandoned read is the wedge.

Nothing here raises. A stamp that cannot be kept is lost, said nowhere, and
the work it would have described runs as it would have; a reading that
cannot be made says why in place of the seconds. Its own clock, always: the
driver's `time` is replaced in tests, and the two clocks must be one.
"""
from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

#: How long a finished span is kept: past the longest pass anyone takes --
#: an untied infrared pass at 7200 dpi is under eight minutes -- with room.
KEEP_S = 3600.0
#: And how many, whatever their age: a stamp per filing, so a day of rolls
#: is a few thousand.
KEEP_SPANS = 4096


class Ledger:
    """Spans of host work, by kind, on this process's performance counter:
    monotonic, and fine enough on Windows, where `time.monotonic` ticks
    every 15.6 ms and a short overlap read as none."""

    def __init__(self, clock: Any = time.perf_counter):
        self.clock = clock
        self._lock = threading.Lock()
        #: Finished spans: (kind, start, end).
        self._done: list[tuple[str, float, float]] = []
        #: Spans still running, by token: (kind, start).
        self._running: dict[int, tuple[str, float]] = {}
        self._tokens = itertools.count(1)

    def now(self) -> float | None:
        """This ledger's clock, or None where it will not answer."""
        try:
            return float(self.clock())
        except Exception:                                # noqa: BLE001
            return None

    def begin(self, kind: str) -> int | None:
        """A span of ``kind`` starts now; its token, or None if not kept."""
        try:
            start = float(self.clock())
            with self._lock:
                token = next(self._tokens)
                self._running[token] = (str(kind), start)
            return token
        except Exception:                                # noqa: BLE001
            return None

    def end(self, token: int | None) -> None:
        """The span ``token`` began has ended."""
        if token is None:
            return
        try:
            end = float(self.clock())
            with self._lock:
                begun = self._running.pop(token, None)
                if begun is None:
                    return
                self._done.append((begun[0], begun[1], end))
                # Pruned here, by the work's own thread, never by a reading.
                horizon = end - KEEP_S
                if len(self._done) > KEEP_SPANS or (
                        self._done and self._done[0][2] < horizon):
                    self._done = [s for s in self._done[-KEEP_SPANS:]
                                  if s[2] >= horizon]
        except Exception:                                # noqa: BLE001
            pass

    def during(self, since: float | None) -> dict[str, Any]:
        """Seconds of each kind of work that overlapped ``since`` to now.

        A kind that ran beside it is named with its seconds, to the
        millisecond; nothing beside it is ``{}``. A span still running counts
        up to now. Where it cannot be read -- no start, a clock that failed
        -- ``{"unread": why}`` in place of the seconds, so a record is never
        taken for one that saw nothing.
        """
        try:
            if since is None:
                return {"unread": "the pass's start was not read"}
            until = float(self.clock())
            with self._lock:
                spans = list(self._done) + [
                    (kind, start, until)
                    for kind, start in self._running.values()]
            seconds: dict[str, float] = {}
            for kind, start, end in spans:
                overlap = min(end, until) - max(start, since)
                if overlap > 0:
                    seconds[kind] = seconds.get(kind, 0.0) + overlap
            return {kind: round(s, 3) for kind, s in sorted(seconds.items())}
        except Exception as exc:                         # noqa: BLE001
            return {"unread": f"{type(exc).__name__}: {exc}"}

    def running(self) -> list[str]:
        """The kinds of work running now, for a test or a log line."""
        try:
            with self._lock:
                return sorted(kind for kind, _ in self._running.values())
        except Exception:                                # noqa: BLE001
            return []


#: The process's one ledger. A test replaces it with a fresh one.
LEDGER = Ledger()


def now() -> float | None:
    """The ledger's clock, read where a pass begins (`during`)."""
    return LEDGER.now()


def during(since: float | None) -> dict[str, Any]:
    """The work that overlapped a pass begun at ``since``: `Ledger.during`."""
    return LEDGER.during(since)


@contextmanager
def stamp(kind: str) -> Iterator[None]:
    """Stamp the ledger while the work in the ``with`` runs.

    The work runs whatever the ledger does: neither its start nor its end
    can raise into it.
    """
    ledger = LEDGER
    token = ledger.begin(kind)
    try:
        yield
    finally:
        ledger.end(token)
