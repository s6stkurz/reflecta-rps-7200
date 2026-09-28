"""What every hardware probe here needs before and around its passes.

Not a probe itself: the three steps each probe in `tools/` carried its own copy
of, and all of them went wrong the same way when the driver moved under them.

**Filing.** Each probe refused to run unless `RPS7200_DEBUG` was set -- to
anything. The driver files only for `1`, `true`, `yes` or `on`, so
`RPS7200_DEBUG=0` passed every gate and a forty-minute run filed nothing, the
loss the gate exists to prevent. :func:`refuse_unfiled` asks the scanner
itself, so the gate and the filing read the variable the same way.

**The reference.** Every probe's first pass is a corrected one -- a prescan,
or metering -- and since the driver stopped calibrating lazily inside a pass,
a session with no reference refuses it. Every probe opened the device, warmed
the lamp, and died on its first pass with `ShadingUnavailable`.
:func:`ensure_reference` gets one first, from the cache or by calibrating, the
way `tools/scan.py` does -- with the film in, as CLAUDE.md requires. That
calibration is three to four minutes of every run that does not reuse the
cache, and :func:`calibration_seconds` is how each probe's estimate counts it.

**Ctrl-C.** None of them deferred it, so one press in a 25-40 minute run
abandoned the read in flight: the device marked suspect, and usually a power
cycle. :class:`Guard` holds Ctrl-C until the pass in flight has
finished and then refuses to start another, raising :class:`Stopped` -- a
KeyboardInterrupt, so every probe's existing handling of one applies -- at the
point where nothing is being read.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from rps7200.console import DeferredInterrupt
from rps7200.protocol import ShadingUnavailable

#: Where the reference is cached, as `tools/scan.py` and `tools/scan_roll.py`
#: keep it, so a probe run beside a scan can reuse the scan's reference.
DEFAULT_REFERENCE = "calibration/shading.npz"

#: What a calibration costs, for a probe's estimate: 3-4 minutes, the figure
#: `tools/scan.py` budgets for the same call.
CALIBRATION_S = 210.0

#: The calls that drive the device, stopped at once the operator has asked.
#: The pass entry points and the transport moves; everything a probe does to
#: the scanner goes through one of these.
GUARDED = ("scan", "nudge", "advance", "retreat")


class Stopped(KeyboardInterrupt):
    """The operator's Ctrl-C, raised between passes rather than inside one."""


def add_arguments(ap: argparse.ArgumentParser) -> None:
    """`--reference` and `--reuse`, as the scan tools spell them."""
    ap.add_argument("--reference", default=DEFAULT_REFERENCE,
                    help="where the shading reference is cached "
                         "(default %(default)s)")
    ap.add_argument("--reuse", action="store_true",
                    help="use the cached reference instead of calibrating "
                         "(3-4 minutes). Only within the power-on that "
                         "measured it")


class _Unopened:
    """A transport that is never used: the gate reads a setting, nothing more."""


def refuse_unfiled(factory: Any) -> bool:
    """True, having said why, when a scanner from `factory` would file nothing.

    Asked of a scanner built on a transport that is never opened, so the
    gate reads the variable exactly as the driver does and nothing -- not
    even the USB library -- is touched before it has said yes.
    """
    if getattr(factory(transport=_Unopened()), "debug", False):
        return False
    print("refusing to run without RPS7200_DEBUG=1 (or true/yes/on): a probe "
          "that files nothing cannot be re-analysed, and none of these is "
          "worth spending scanner time on twice", file=sys.stderr)
    return True


def calibration_seconds(args: argparse.Namespace) -> float:
    """What getting this run's reference will cost, for its estimate.

    Nothing only where `--reuse` will find the cache: given `--reuse` and no
    file, `ensure_shading` calibrates anyway. The estimates are the one guard
    against the harness's ten-minute foreground kill, and a killed read
    wedges the scanner -- yet every probe calibrated by default and all but
    one left it out, so a walk quoted at seven minutes, with no word about
    backgrounding it, would have run for ten and a half.
    """
    return 0.0 if args.reuse and Path(args.reference).exists() else CALIBRATION_S


def ensure_reference(scanner: Any, args: argparse.Namespace) -> None:
    """A shading reference for this session, before its first pass.

    Calibrates unless `--reuse` finds the cache; either way with the film in
    the transport, which every probe here already requires.
    """
    if args.reuse and not Path(args.reference).exists():
        # Said, because the estimate this run printed counted it too: a
        # silent calibration here was the 3-4 minutes nobody expected.
        print(f"\n--reuse given but {args.reference} does not exist: "
              f"calibrating instead, with the film in the transport "
              f"(3-4 minutes)")
    elif not args.reuse:
        print("\ncalibrating with the film in the transport (3-4 minutes)")
    result = scanner.ensure_shading(args.reference, reuse=args.reuse)
    print(str(result.get("summary", "")).strip())
    if result.get("reference") is None:
        raise ShadingUnavailable("no shading reference for this session; "
                                 "every pass here needs one")


class Guard:
    """Ctrl-C finishes the pass in flight, and no further pass starts.

    Installs :class:`rps7200.console.DeferredInterrupt` and puts a check in
    front of every call in :data:`GUARDED` on this one scanner, so a probe
    stops at its next pass or move without each loop having to ask. Metering
    goes through `scan` too, so a stop asked for during metering takes effect
    before its next round. A second Ctrl-C aborts outright, as everywhere.

    In force from construction until :meth:`release`, so it wraps a probe's
    own ``try``/``finally`` -- and a cleanup pass in the ``finally`` is not
    started once the operator has asked to stop either.
    """

    def __init__(self, scanner: Any):
        self._scanner = scanner
        self._stop = DeferredInterrupt()
        self._stop.__enter__()
        own = vars(scanner)
        self._before = {name: own[name] for name in GUARDED if name in own}
        self._set: list[str] = []
        for name in GUARDED:
            real = getattr(scanner, name, None)
            if real is None:
                continue

            def guarded(*args: Any, _real: Any = real, _name: str = name,
                        **kwargs: Any) -> Any:
                if self._stop.requested():
                    raise Stopped(f"stopped at the operator's request, before "
                                  f"the next {_name}")
                return _real(*args, **kwargs)

            setattr(scanner, name, guarded)
            self._set.append(name)

    def requested(self) -> bool:
        return self._stop.requested()

    def release(self) -> None:
        """Put the scanner's calls and Ctrl-C back. Safe to call twice."""
        for name in self._set:
            if name in self._before:
                setattr(self._scanner, name, self._before[name])
            else:
                delattr(self._scanner, name)
        self._set = []
        self._stop.__exit__(None, None, None)

    def __enter__(self) -> "Guard":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release()


def suspect(scanner: Any) -> str | None:
    """Why the device must not be driven any further, or None.

    A pass that stopped part way leaves it busy with a read nobody finished;
    cleanup that drives it -- a last pass, a register restored, a rewind --
    is sent to a scanner that may still be streaming. The driver refuses only
    at the points it checks, and several setup commands go out before those.
    """
    return getattr(scanner, "suspect", None)
