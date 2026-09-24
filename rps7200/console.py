"""Make the console survive the characters this driver already prints.

Python picks the encoding for stdout from the machine's locale when the
stream is not a terminal. On a German Windows that is **cp1252**, and cp1252
has no arrow, no bullet, no plus-or-minus. Measured here, with nothing else
changed::

    $ python -c "print('\\u2192')" > out.txt
    UnicodeEncodeError: 'charmap' codec can't encode character '\\u2192'

The driver prints all three from its progress and log paths. So redirecting a
scan's output to a file -- or piping it, or running it from anything that is
not a console -- killed the process partway through. That is not cosmetic
here. A scan that dies between the START SCAN and the last READ leaves an
abandoned read, and an abandoned read wedges the scanner until it is
power-cycled at the unit's own switch.

``errors="replace"`` is the half that matters. UTF-8 output is the intent, but
a console that genuinely cannot render a character should print a ``?`` and
carry on, never raise. Nothing this prints is worth a power cycle.

Called at the top of each tool's ``main()`` rather than left to the
environment: ``PYTHONUTF8=1`` would also do it, and it is not something an
operator should have to know, or remember, on the one run that matters.
"""
from __future__ import annotations

import sys


def use_utf8_stdout() -> None:
    """Re-encode stdout and stderr as UTF-8, replacing what will not fit.

    Safe to call more than once, and safe where the streams cannot be
    reconfigured at all -- under a captured or replaced stdout, for instance,
    which is what the test suite and the GUI's log pane provide.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            # A stream that will not take it is one this cannot help; the
            # alternative is refusing to start over the log's encoding.
            pass


class DeferredInterrupt:
    """Ctrl-C asks the pass in flight to finish rather than abandoning it.

    A KeyboardInterrupt raised inside a read unwinds out of the `with` block
    that owns the scanner, which closes the transport under the read: the
    abandoned read that needs a power cycle. So while this is in force the
    first Ctrl-C only sets `requested` and says so; a tool checks it between
    passes -- `scan_roll(should_stop=...)` does -- and stops there. A second
    Ctrl-C is taken at its word and raises, for the operator who has decided a
    power cycle is cheaper than waiting.

    Only the main thread can install a signal handler; anywhere else, and
    where there is no SIGINT, this does nothing and Ctrl-C behaves as usual.
    """

    def __init__(self, say=None):
        self._requested = False
        self._previous = None
        self._installed = False
        self._say = say or (lambda message: print(message, file=sys.stderr,
                                                  flush=True))

    def requested(self) -> bool:
        return self._requested

    def _handler(self, signum, frame):
        if self._requested:
            self._restore()
            raise KeyboardInterrupt
        self._requested = True
        self._say("\nstopping after the pass in flight -- interrupting a read "
                  "wedges the scanner. Press Ctrl-C again to abort anyway.")

    def _restore(self) -> None:
        if self._installed:
            import signal
            signal.signal(signal.SIGINT, self._previous)
            self._installed = False

    def __enter__(self) -> "DeferredInterrupt":
        import signal
        import threading
        if (threading.current_thread() is threading.main_thread()
                and hasattr(signal, "SIGINT")):
            try:
                self._previous = signal.signal(signal.SIGINT, self._handler)
                self._installed = True
            except (ValueError, OSError):
                self._installed = False
        return self

    def __exit__(self, *exc) -> None:
        self._restore()
