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
from typing import Any


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
    passes, or between frames as `scan_roll(should_stop=...)` does, and stops
    there. A second Ctrl-C is taken at its word and raises, for the operator
    who has decided a power cycle is cheaper than waiting.

    The other ways a process is told to stop are taken the same way:
    `SIGNALS`. ``on_request`` is called once, on the first, for a caller that
    has to act rather than poll -- the window, whose loop is Tk's. A signal
    the process was started ignoring -- ``nohup``'s hangup -- is left ignored.

    ``message`` is what the first one says, and it has to be the promise the
    tool keeps. The default promises the pass in flight; a tool that stops
    later than that says where instead. `tools/scan_roll.py` stops after the
    frame in flight -- its prescan, metering and scan -- and promised the
    pass: an operator told one pass and kept waiting through three is the
    one who presses again, and the second press is the abandoned read.

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
    #: Those a platform lacks are left out. A Windows console closing reaches
    #: SIGBREAK's handler too, through the C runtime, but too late to matter:
    #: the C runtime returns at once and Windows ends the process before
    #: Python runs the handler. That is `ConsoleClose`'s.
    SIGNALS = ("SIGINT", "SIGTERM", "SIGHUP", "SIGBREAK")

    #: What the first request says, unless the tool gives ``message``.
    MESSAGE = ("\nstopping after the pass in flight -- interrupting a read "
               "wedges the scanner. Press Ctrl-C again to abort anyway.")

    def __init__(self, say=None, on_request=None, message=None):
        self._requested = False
        self._previous: dict = {}
        self._installed = False
        self._terminal: list[str] = []
        self._say = say or (lambda message: print(message, file=sys.stderr,
                                                  flush=True))
        self._on_request = on_request
        self._message = self.MESSAGE if message is None else message

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
            self._say(self._message)
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
        _to_null_device(self._terminal)
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


def _to_null_device(names: list[str]) -> None:
    """Point each of `sys`'s streams named here at the null device."""
    import os
    for name in names:
        try:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
        except OSError:
            pass


def logged_to(path, say=None):
    """A ``say`` that also appends each line to the file at ``path``.

    For what is said as the process is about to be ended: `ConsoleClose`'s
    line went to stderr, which was by then the null device wherever stderr
    had been the console closing, and so to nobody. Here each line is
    appended with the UTC time and the process before it, its own folder
    made if need be -- never one above it, so a `logs/` whose drive has
    gone is a line not kept rather than folders made on another
    (`storage.make_root`) -- and the file closed again, so a process ended
    a moment later loses nothing: an INCOMPLETE entry, or a scanner that
    wants a power cycle, is explained there. ``say`` is called first, and
    stderr is the default, looked up at the time, which is what keeps a
    stream sent to a file in on it. Neither can raise.
    """
    import os
    import time
    from pathlib import Path

    from .storage import make_root

    def said(message: str) -> None:
        try:
            if say is None:
                print(message, file=sys.stderr, flush=True)
            else:
                say(message)
        except (OSError, ValueError):
            pass
        try:
            where = Path(path)
            make_root(where.parent)
            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            with open(where, "a", encoding="utf-8") as out:
                out.write(f"{stamp} pid {os.getpid()}: "
                          f"{' '.join(message.split())}\n")
        except (OSError, ValueError):
            pass

    return said


class KeptLog:
    """What the window says, kept in a file of its own for each launch.

    The log pane was the only account of a session: what was filed and
    where, why a roll stopped, a pass not filed, a traceback. It went with
    the window, and a roll that ended badly overnight could be explained
    only by whoever had read the pane before quitting. Stefan's calls,
    2026-10-10: one file per launch, ``<stem>-<UTC start>.log`` in the
    window's ``logs/`` (``demo/logs/`` under ``--demo``), nothing ever
    deleting them; and in it every line the pane shows, each with its UTC
    time, besides what the pane does not show (`ScannerGui._keep`).

    Opened at the first line and held, one write per line, flushed as it is
    written, so the file is whole up to the moment the process ends --
    Windows ends one about 5 s after its console closes. A name another
    launch took in the same second gets ``-2``, ``-3``. Never raises: the
    first write that fails returns its reason, once, for the window to say
    in the pane, and nothing is written after it. Safe from any thread.
    Closed by `close`, or as the object goes, for a window a test never
    quits.
    """

    def __init__(self, folder, stem: str = "pane"):
        import threading
        import time
        import weakref
        from pathlib import Path

        self.folder = Path(folder)
        self.stem = stem
        #: The file, once the first line has opened it.
        self.path: Path | None = None
        self._started = time.gmtime()
        self._lock = threading.Lock()
        #: The open handle, in a box the finalizer can close without holding
        #: on to this object.
        self._box: list = [None]
        self._failed: str | None = None
        self._closed = False
        weakref.finalize(self, _close_kept, self._box)

    def _open(self):
        import time

        from .storage import make_root
        stamp = time.strftime("%Y%m%dT%H%M%SZ", self._started)
        # Its own folder at most, beside the settings it sits with, never
        # the ones above it (`storage`): a logs folder on a drive that has
        # gone is a log not kept, and said, rather than one made elsewhere.
        make_root(self.folder)
        n = 1
        while True:
            where = self.folder / (f"{self.stem}-{stamp}"
                                   + ("" if n == 1 else f"-{n}") + ".log")
            try:
                # "x": made here, never one another launch is writing.
                handle = open(where, "x", encoding="utf-8", errors="replace",
                              newline="\n", buffering=1)
            except FileExistsError:
                n += 1
                continue
            self.path = where
            return handle

    def write(self, text) -> str | None:
        """Append ``text``, each of its lines after the UTC time. Returns
        why it could not be, the first time it cannot; None otherwise."""
        import time
        with self._lock:
            if self._closed or self._failed is not None:
                return None
            try:
                if self._box[0] is None:
                    self._box[0] = self._open()
                now = time.time()
                stamp = (time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now))
                         + f".{int(now % 1 * 1000):03d}Z")
                lines = str(text).splitlines() or [""]
                self._box[0].write("".join(f"{stamp} {line}\n"
                                           for line in lines))
            except Exception as exc:                         # noqa: BLE001
                self._failed = f"{type(exc).__name__}: {exc}"
                _close_kept(self._box)
                return (f"the log could not be kept in {self.folder} "
                        f"({self._failed}); it is shown in the window only")
        return None

    def close(self) -> None:
        """Close the file; anything written after is dropped."""
        with self._lock:
            self._closed = True
            _close_kept(self._box)


def _close_kept(box: list) -> None:
    """Close a `KeptLog`'s handle, if it has one, never raising."""
    handle, box[0] = box[0], None
    if handle is not None:
        try:
            handle.close()
        except (OSError, ValueError):
            pass


class ConsoleClose:
    """Windows: the console this process was started from being closed.

    Windows raises CTRL_CLOSE_EVENT in every process attached to the
    console. The C runtime hands every event that is not Ctrl-C to the
    SIGBREAK handler and returns at once, and for a close, returning is
    what ends the process: before Python has run its handler for SIGBREAK
    -- `DeferredInterrupt`'s, or none -- the window's scanner thread is
    ended inside its bulk read, the abandoned read, and the writer part way
    through an entry. Measured 2026-10-09 with a C-level SIGBREAK handler
    registered through ucrtbase: it was called with 21 at the moment of the
    close, the process ended 0.02 s later (0xC000013A), and Python's own
    handler never ran.

    So this is registered after the C runtime's handler, which Python
    installs at startup, and Windows calls the newest first: it sees the
    close before the C runtime can return. Registered first, it would never
    be reached.

    Held off, it is ended anyway. Measured 2026-10-09 on Windows 10 by
    posting WM_CLOSE to a console: a handler that does not return kept its
    process at work, and Windows ended it 5.0-5.5 s after the close, part
    way through whatever it was doing -- alone, under the venv's launcher
    and under `uv run` alike. One that returns ends it there and then. So
    this asks once -- ``on_close`` -- and then holds the handler's thread
    until the window has quit, or Windows ends the process. About 5 s:
    enough to put away an idle window's sheet and settings (the demo window
    was gone 0.2 s after the close, its settings written), and never enough
    for a pass. What saves a pass is not being attached to the console at
    all, which is how `make run` starts the window on Windows (`tasks.py`).
    `tests/test_console_close.py` repeats all of this, opt-in.

    And the hold has to be let go of rather than outlived. Windows keeps
    the process's list of handlers locked while one runs, so taking this
    one off then waited on it, and so did the process's own exit, measured:
    a window that had quit inside the 5 s still sat there until Windows
    ended it. So `__exit__`, once a close is held, lets the handler return
    -- which ends the process, with everything put away -- and takes
    nothing off.

    Installed only on Windows, and only where there is a console window for
    someone to close: not under `make run`, whose window has a console of
    its own with none, nor under a harness that gives a process no window.

    The console's streams go with it, as a hangup's terminal does: pointed
    at the null device before anything is said, so a line of progress in
    those seconds cannot raise inside the read. Which leaves what is said
    nowhere a person will read it, when stderr was that console: a ``say``
    from `logged_to` also puts it in a file, which outlives the process.
    """

    #: What Windows passes the handler for the console closing. Ctrl-C (0)
    #: and Ctrl-Break (1) are passed on to the C runtime, and so reach
    #: `DeferredInterrupt` as they did. A logoff or shutdown (5, 6) reaches
    #: only a process that has not loaded user32, and a window has.
    CTRL_CLOSE_EVENT = 2

    SAID = ("\nthe console was closed: stopping after the pass in flight. "
            "Windows ends this process about 5 s after its console closes, "
            "whatever it is doing, so a pass still being read then is "
            "abandoned, and the scanner will want a power cycle.")

    def __init__(self, on_close=None, say=None, hold=None, kernel32=None):
        import threading
        self._on_close = on_close
        self._say = say or (lambda message: print(message, file=sys.stderr,
                                                  flush=True))
        # Set by `__exit__` alone: returning is what ends the process, so
        # the handler waits for the window to have quit.
        self._release = threading.Event()
        self._hold = hold or self._release.wait
        # kernel32, or a stand-in for it: `GetConsoleWindow` and
        # `SetConsoleCtrlHandler` are all this asks of it.
        self._kernel32: Any = kernel32
        self._callback = None
        self._installed = False
        self._closed = False
        self._terminal: list[str] = []

    def closed(self) -> bool:
        return self._closed

    def installed(self) -> bool:
        return self._installed

    def _handler(self, event: int) -> bool:
        """What Windows calls, on a thread of its own making.

        Nothing may escape it: an exception here comes back to Windows as
        "not handled", and the next handler is the one that ends the
        process at once.
        """
        if event != self.CTRL_CLOSE_EVENT:
            return False
        if not self._closed:
            self._closed = True
            _to_null_device(self._terminal)
            self._terminal = []
            try:
                self._say(self.SAID)
            except (OSError, ValueError):
                pass
            if self._on_close is not None:
                try:
                    self._on_close()
                except Exception:                            # noqa: BLE001
                    pass
        self._hold()
        return True

    def __enter__(self) -> "ConsoleClose":
        kernel32 = self._kernel32
        try:
            import ctypes
            if kernel32 is None:
                if sys.platform != "win32":
                    return self
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.GetConsoleWindow.restype = ctypes.c_void_p
            if not kernel32.GetConsoleWindow():
                return self
            # BOOL HandlerRoutine(DWORD); WINFUNCTYPE is Windows' own.
            prototype = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
            callback = prototype(ctypes.c_long, ctypes.c_ulong)(self._handler)
            if kernel32.SetConsoleCtrlHandler(callback, True):
                self._kernel32 = kernel32
                self._callback = callback
                self._installed = True
                self._terminal = [name for name in ("stdout", "stderr")
                                  if _is_terminal(getattr(sys, name, None))]
        except (AttributeError, OSError):
            # No console to speak of, or none that will take a handler:
            # closing it ends the process as it always did.
            pass
        return self

    def __exit__(self, *exc) -> None:
        # First, so a close that lands from here on returns at once rather
        # than holding a list this thread is about to wait on.
        self._release.set()
        if not self._installed:
            return
        self._installed = False
        if self._closed:
            # Held: its return ends the process. Taking it off would wait
            # for it, until Windows ended the process instead.
            return
        try:
            self._kernel32.SetConsoleCtrlHandler(self._callback, False)
        except OSError:
            pass
        # The callback is kept, not dropped: a close that arrived as this
        # ran is still inside it, and freeing it would pull the code out
        # from under that thread.


#: What a tool says before a calibration it has not been told the film is in
#: for. CLAUDE.md's rule, in the operator's terms.
FILM_QUESTION = (
    "A calibration runs first, and it runs with the film in the transport, "
    "as the vendor's does: calibrating an empty one preceded a wedge, and "
    "nothing the scanner reports can say which it is. Is the film in the "
    "transport? [y/N] ")


def film_unconfirmed(asserted: bool, ask=input, interactive=None,
                     instead: str | None = "scan raw with --no-shading",
                     ) -> str | None:
    """Why a calibration may not start, or None once the film is said to be in.

    Only the operator can see the transport: READ STATE's byte 8 was measured
    against it once, with one variable changed, and every capture was taken
    with film in, so nothing corroborates it. The window asks with a box
    that starts unticked. The tools calibrated without a word -- `scan.py`
    straight after INQUIRY, `scan_roll.py` after a seek that, on frame 1,
    moves nothing and so proves nothing.

    ``asserted`` is the tool's ``--film-loaded``. Without it the question is
    asked where someone can answer it, and refused where nobody can -- a run
    in the background -- with the flag named, and ``instead``, the tool's
    other way past the calibration, after it. The probes have no raw pass to
    offer, and named one they do not take.
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
            "the scanner can see which it is. Say so with --film-loaded"
            + (f", or {instead}" if instead else ""))
