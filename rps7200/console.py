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

    The other ways a process is told to stop are taken the same way:
    `SIGNALS`. ``on_request`` is called once, on the first, for a caller that
    has to act rather than poll -- the window, whose loop is Tk's. A signal
    the process was started ignoring -- ``nohup``'s hangup -- is left ignored.

    A hangup differs in two ways, both because no person sent it. It never
    insists: the shell passes one to its jobs as it exits, and the kernel
    sends the foreground its own once the shell has gone, so one closed
    terminal can deliver two. And it takes the terminal with it: every later
    write to stdout or stderr there raises EIO, so the streams that were the
    terminal are pointed at the null device, where a line of progress cannot
    end a read, or the roll after it.

    Only the main thread can install a signal handler; anywhere else, and
    where there is no SIGINT, this does nothing and Ctrl-C behaves as usual.
    """

    #: SIGTERM is `kill`'s default and a service manager's; SIGHUP is the
    #: terminal the tool was started from closing; SIGBREAK is Windows'
    #: Ctrl-Break. Each killed a tool mid-read, so each abandoned the read --
    #: and `writer.finish()` never ran -- where Ctrl-C had stopped doing that.
    #: Those a platform lacks are left out.
    SIGNALS = ("SIGINT", "SIGTERM", "SIGHUP", "SIGBREAK")

    def __init__(self, say=None, on_request=None):
        self._requested = False
        self._previous: dict = {}
        self._installed = False
        self._terminal: list[str] = []
        self._say = say or (lambda message: print(message, file=sys.stderr,
                                                  flush=True))
        self._on_request = on_request

    def requested(self) -> bool:
        return self._requested

    def _handler(self, signum, frame):
        import signal
        hangup = signum == getattr(signal, "SIGHUP", None)
        if hangup:
            self._let_go_of_the_terminal()
        if self._requested:
            if hangup:
                # A hangup never insists: taken as the second press, the
                # other of the pair a closed terminal sends raised inside the
                # read this exists to protect.
                return
            self._restore()
            raise KeyboardInterrupt
        self._requested = True
        try:
            self._say("\nstopping after the pass in flight -- interrupting a "
                      "read wedges the scanner. Press Ctrl-C again to abort "
                      "anyway.")
        except (OSError, ValueError):
            # A terminal that has closed -- SIGHUP -- takes stderr with it,
            # and raising here would land in the middle of the very read
            # this exists to protect.
            pass
        if self._on_request is not None:
            self._on_request()

    def _let_go_of_the_terminal(self) -> None:
        """Point the streams that were the terminal at the null device.

        Decided from what they were on the way in: a hung-up terminal fails
        `isatty()` too, and a stream sent to a file is still worth writing.
        """
        import os
        for name in self._terminal:
            try:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            except OSError:
                pass
        self._terminal = []

    def _restore(self) -> None:
        if self._installed:
            import signal
            for signum, previous in self._previous.items():
                signal.signal(signum, previous)
            self._previous = {}
            self._installed = False

    def __enter__(self) -> "DeferredInterrupt":
        import signal
        import threading
        if (threading.current_thread() is threading.main_thread()
                and hasattr(signal, "SIGINT")):
            try:
                for name in self.SIGNALS:
                    signum = getattr(signal, name, None)
                    if signum is None:
                        continue
                    if signal.getsignal(signum) is signal.SIG_IGN:
                        # Whoever started the process said so: `nohup`, for
                        # the long runs CLAUDE.md says to background, where
                        # taking the hangup ended the roll at the terminal
                        # closing -- the one thing nohup was asked to prevent.
                        continue
                    self._previous[signum] = signal.signal(signum,
                                                           self._handler)
                self._installed = True
                self._terminal = [name for name in ("stdout", "stderr")
                                  if _is_terminal(getattr(sys, name, None))]
            except (ValueError, OSError):
                # All of them or none: put back what was taken before the
                # refusal, and leave Ctrl-C as it was.
                self._installed = True
                self._restore()
        return self

    def __exit__(self, *exc) -> None:
        self._restore()


def _is_terminal(stream) -> bool:
    try:
        return bool(stream is not None and stream.isatty())
    except (AttributeError, OSError, ValueError):
        return False


#: What a tool says before a calibration it has not been told the film is in
#: for. CLAUDE.md's rule, in the operator's terms.
FILM_QUESTION = (
    "A calibration runs first, and it runs with the film in the transport, "
    "as the vendor's does: calibrating an empty one preceded a wedge, and "
    "nothing the scanner reports can say which it is. Is the film in the "
    "transport? [y/N] ")


def film_unconfirmed(asserted: bool, ask=input, interactive=None) -> str | None:
    """Why a calibration may not start, or None once the film is said to be in.

    Only the operator can see the transport: READ STATE's byte 8 was measured
    against it once, with one variable changed, and every capture was taken
    with film in, so nothing corroborates it. The window asks with a box
    that starts unticked. The tools calibrated without a word -- `scan.py`
    straight after INQUIRY, `scan_roll.py` after a seek that, on frame 1,
    moves nothing and so proves nothing.

    ``asserted`` is the tool's ``--film-loaded``. Without it the question is
    asked where someone can answer it, and refused where nobody can -- a run
    in the background -- with the flag named.
    """
    if asserted:
        return None
    if interactive is None:
        interactive = sys.stdin is not None and sys.stdin.isatty()
    if interactive:
        try:
            answer = ask(FILM_QUESTION)
        except EOFError:
            answer = ""
        if answer.strip().lower() in ("y", "yes"):
            return None
        return "not calibrating: the film was not said to be in the transport"
    return ("a calibration runs first, with the film in the transport -- "
            "calibrating an empty one preceded a wedge, and only someone at "
            "the scanner can see which it is. Say so with --film-loaded, or "
            "scan raw with --no-shading")
