"""Closing the console on Windows: `ConsoleClose`, and the window started apart.

Closing a console window raises CTRL_CLOSE_EVENT in every process attached
to it. The C runtime hands it to the SIGBREAK handler and returns at once,
and that return ends the process before Python has run the handler -- for
the window, its scanner thread inside a bulk read. `DeferredInterrupt` never
gets to see it; `ConsoleClose` takes it as Quit's "stop after the frame in
flight", and `tasks.start_apart` keeps `make run`'s window off the console
altogether.

The unit tests below hand `ConsoleClose` a stand-in for kernel32, so they run
on every platform. The last five close real consoles and are opt-in --
``RPS7200_CONSOLE_CLOSE=1``, Windows only, about a minute -- because they
open hidden console windows and post WM_CLOSE to them, and one opens the
demo window for a few seconds. They are the measurements behind the "about
5 s" and the SIGBREAK in CLAUDE.md, and touch no scanner.
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from rps7200.console import ConsoleClose, logged_to

ROOT = Path(__file__).resolve().parent.parent

CTRL_C, CTRL_BREAK, CTRL_CLOSE = 0, 1, 2


class FakeKernel32:
    """GetConsoleWindow and SetConsoleCtrlHandler, and nothing else."""

    def __init__(self, window=0x1234):
        self.window = window
        self.calls: list[tuple[object, bool]] = []

    def GetConsoleWindow(self):                          # noqa: N802
        return self.window

    def SetConsoleCtrlHandler(self, callback, add):      # noqa: N802
        self.calls.append((callback, bool(add)))
        return 1


def _console(kernel32=None, **kw):
    kw.setdefault("say", lambda message: None)
    kw.setdefault("hold", lambda: None)
    return ConsoleClose(kernel32=kernel32 or FakeKernel32(), **kw)


def test_closing_the_console_asks_once_and_holds_on():
    """Returning from the handler is what ends the process there and then,
    so it asks the window to quit and then holds -- until the window has
    quit, or Windows ends it about 5 s on."""
    kernel32 = FakeKernel32()
    asked, held, said = [], [], []
    closed = ConsoleClose(on_close=lambda: asked.append(1), say=said.append,
                          hold=lambda: held.append(1), kernel32=kernel32)
    with closed:
        assert closed.installed()
        callback = kernel32.calls[0][0]
        assert kernel32.calls == [(callback, True)]
        assert closed._handler(CTRL_CLOSE) is True
        assert asked == [1] and held == [1] and closed.closed()
        assert "5 s" in said[0] and "abandoned" in said[0]
        closed._handler(CTRL_CLOSE)                  # there is only one close
        assert asked == [1] and held == [1, 1]
    # Windows keeps its handlers locked while one runs: taking a held one off
    # waited for it, until the process was ended instead.
    assert kernel32.calls == [(callback, True)]
    assert not closed.installed()


def test_the_window_having_quit_lets_the_held_close_return():
    """Its return is what ends the process, and the window has put away all
    it had: waiting on would only have waited for Windows to end it."""
    import threading

    returned = []
    closed = ConsoleClose(kernel32=FakeKernel32(), say=lambda m: None)
    with closed:
        holding = threading.Thread(
            target=lambda: returned.append(closed._handler(CTRL_CLOSE)))
        holding.start()
        holding.join(0.2)
        assert holding.is_alive() and returned == [], "it did not hold"
    holding.join(5)
    assert returned == [True]


def test_without_a_close_the_handler_is_taken_off():
    kernel32 = FakeKernel32()
    with _console(kernel32):
        callback = kernel32.calls[0][0]
    assert kernel32.calls == [(callback, True), (callback, False)]


def test_a_close_as_the_window_quits_does_not_hold_it_open():
    """A close that lands once the window is quitting returns at once: held,
    it would wait on a list the quitting thread is taking it off."""
    closed = ConsoleClose(kernel32=FakeKernel32(), say=lambda m: None)
    with closed:
        pass
    assert closed._handler(CTRL_CLOSE) is True       # and does not block


@pytest.mark.parametrize("event", [CTRL_C, CTRL_BREAK])
def test_ctrl_c_and_ctrl_break_are_left_to_deferred_interrupt(event):
    """Handed on, they reach the C runtime, which makes them SIGINT and
    SIGBREAK -- `DeferredInterrupt`'s, asking and then insisting, as before."""
    asked, held = [], []
    with _console(on_close=lambda: asked.append(1),
                  hold=lambda: held.append(1)) as closed:
        assert closed._handler(event) is False
    assert asked == [] and held == [] and not closed.closed()


def test_nothing_is_installed_where_there_is_no_console_window():
    """`make run` starts the window with a console that has no window, and a
    harness gives a process none: there is nothing anyone can close."""
    kernel32 = FakeKernel32(window=0)
    with _console(kernel32) as closed:
        assert not closed.installed()
    assert kernel32.calls == []


def test_nothing_is_installed_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with ConsoleClose() as closed:
        assert not closed.installed()


def test_a_request_that_raises_still_holds_the_close_off():
    """An exception out of the handler reaches Windows as "not handled", and
    the next handler in line ends the process at once."""
    held = []

    def on_close():
        raise RuntimeError("main thread is not in main loop")

    with _console(on_close=on_close, hold=lambda: held.append(1)) as closed:
        assert closed._handler(CTRL_CLOSE) is True
    assert held == [1]


class _Console:
    """A stream that was the console on the way in."""

    def isatty(self):
        return True

    def write(self, text):
        raise OSError(232, "The pipe is being closed")

    def flush(self):
        pass


def test_the_console_goes_with_its_close_and_a_file_does_not(monkeypatch):
    """As a hangup takes its terminal: a line of progress in those seconds
    must not raise inside the read. A stream sent to a file keeps the line."""
    logged = io.StringIO()
    monkeypatch.setattr("sys.stdout", _Console())
    monkeypatch.setattr("sys.stderr", logged)
    with ConsoleClose(kernel32=FakeKernel32(), hold=lambda: None) as closed:
        closed._handler(CTRL_CLOSE)
        print("frame 3 filed")                       # does not raise
        assert sys.stderr is logged
    assert "the console was closed" in logged.getvalue()
    sys.stdout.close()


def test_the_close_is_kept_where_it_outlives_the_console(monkeypatch,
                                                          tmp_path):
    """stderr was the console, so by the time the line is said it is the null
    device, and the log pane goes with the process a moment later: an
    INCOMPLETE entry, or a wedged scanner, had nothing anywhere to explain it.
    The file is written after the streams have gone, and closed again at
    once, since the process may be ended the next moment."""
    monkeypatch.setattr("sys.stdout", _Console())
    monkeypatch.setattr("sys.stderr", _Console())
    kept = tmp_path / "logs" / "console-closed.log"          # not there yet
    with ConsoleClose(kernel32=FakeKernel32(), hold=lambda: None,
                      say=logged_to(kept)) as closed:
        closed._handler(CTRL_CLOSE)
        assert not isinstance(sys.stderr, _Console), "said to the console"
    [line] = kept.read_text(encoding="utf-8").splitlines()
    stamp, rest = line.split(" ", 1)
    assert time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
    assert rest.startswith(f"pid {os.getpid()}: the console was closed")
    assert "about 5 s" in rest and "power cycle" in rest
    sys.stdout.close()
    sys.stderr.close()


def test_a_close_kept_in_a_file_is_still_said_and_neither_can_raise(tmp_path):
    """Said first, as before -- a stderr sent to a file keeps the line -- and
    a file that cannot be written, or a ``say`` that fails, is a line lost,
    never an exception inside the handler Windows called."""
    said = []
    kept = tmp_path / "console-closed.log"
    logged_to(kept, say=said.append)("one\n  two")
    assert said == ["one\n  two"]
    assert kept.read_text(encoding="utf-8").endswith(": one two\n")

    def broken(message):
        raise OSError(232, "The pipe is being closed")

    logged_to(kept, say=broken)("three")
    assert kept.read_text(encoding="utf-8").endswith(": three\n")
    blocked = tmp_path / "a-file"
    blocked.write_text("", encoding="utf-8")
    logged_to(blocked / "console-closed.log", say=said.append)("four")
    assert said[-1] == "four"


def test_the_callback_windows_is_handed_answers_as_windows_reads_it():
    """Through ctypes, as Windows calls it: a BOOL back, 1 for the close and
    0 for what is handed on."""
    kernel32 = FakeKernel32()
    with _console(kernel32):
        callback = kernel32.calls[0][0]
        assert callback(CTRL_CLOSE) == 1
        assert callback(CTRL_C) == 0


# -- real consoles, opt-in ---------------------------------------------------

real = pytest.mark.skipif(
    sys.platform != "win32" or not os.environ.get("RPS7200_CONSOLE_CLOSE"),
    reason="closes real console windows: Windows, RPS7200_CONSOLE_CLOSE=1")

#: Written into each child: a heartbeat file, appended and closed per line so
#: a process ended part way through loses nothing it wrote.
_PRELUDE = """
import ctypes, os, sys, threading, time
sys.path.insert(0, {root!r})
LOG = {log!r}
def note(text):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{{time.time():.3f}} {{os.getpid()}} {{text}}\\n")
kernel32 = ctypes.WinDLL("kernel32")
kernel32.GetConsoleWindow.restype = ctypes.c_void_p
"""


def _close_its_console(tmp_path, body: str, after: float = 12.0,
                       before: float = 1.0):
    """Run ``body`` in a new hidden console, close that console once it has
    said ``window <hwnd>``, and hand back its lines as (seconds since the
    close, pid, text), how many seconds after the close it ended (None for
    not within ``after``), and the wall-clock time of the close."""
    import ctypes
    from ctypes import wintypes

    log = tmp_path / "heartbeat.log"
    log.write_text("", encoding="utf-8")
    script = tmp_path / "child.py"
    script.write_text(_PRELUDE.format(root=str(ROOT), log=str(log)) + body,
                      encoding="utf-8")
    hidden = subprocess.STARTUPINFO()
    hidden.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    hidden.wShowWindow = 0                                  # SW_HIDE
    child = subprocess.Popen([sys.executable, str(script)], cwd=ROOT,
                             creationflags=subprocess.CREATE_NEW_CONSOLE,
                             startupinfo=hidden)
    window = 0
    deadline = time.monotonic() + 30
    while not window and time.monotonic() < deadline:
        time.sleep(0.1)
        for line in log.read_text(encoding="utf-8").splitlines():
            if " window " in line:
                window = int(line.split()[-1])
    assert window, "the child never said which console window it had"
    time.sleep(before)
    user32 = ctypes.WinDLL("user32")
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                    wintypes.WPARAM, wintypes.LPARAM]
    closed_at = time.time()
    assert user32.PostMessageW(window, 0x0010, 0, 0)        # WM_CLOSE
    ended = None
    while time.time() - closed_at < after:
        if ended is None and child.poll() is not None:
            ended = time.time() - closed_at
        time.sleep(0.05)
    if child.poll() is None:
        child.kill()
    lines = [(float(t) - closed_at, pid, text) for t, pid, text in
             (line.split(" ", 2) for line in
              log.read_text(encoding="utf-8").splitlines())]
    return lines, ended, closed_at


@real
def test_the_c_runtime_hands_the_close_to_sigbreak_too_late_for_python(
        tmp_path):
    """Why `DeferredInterrupt`, which takes SIGBREAK, never takes a close.
    Python's SIGBREAK handler is set, and a C-level one put in front of the
    handler Python gave the C runtime, through ucrtbase, passing each call
    on to it. The C runtime calls that with 21 the moment the console
    closes, returns, and the process is ended (0xC000013A) before Python's
    own handler has run. Measured 2026-10-09, 0.02 s three times."""
    lines, ended, _ = _close_its_console(tmp_path, """
import signal
signal.signal(signal.SIGBREAK, lambda signum, frame: note(f"python {signum}"))
ucrt = ctypes.CDLL("ucrtbase")
handler = ctypes.CFUNCTYPE(None, ctypes.c_int)
ucrt.signal.restype = ctypes.c_void_p
ucrt.signal.argtypes = [ctypes.c_int, ctypes.c_void_p]
pythons = None
def seen(signum):
    note(f"c runtime {signum}")
    pythons(signum)
seen_c = handler(seen)
pythons = handler(ucrt.signal(signal.SIGBREAK,
                              ctypes.cast(seen_c, ctypes.c_void_p)))
note(f"window {kernel32.GetConsoleWindow()}")
for _ in range(60):
    time.sleep(0.05)
note("finished")
""", after=3.0)
    texts = [text for _, _, text in lines]
    print(f"the close: {texts[1:]}, ended {ended} s after it")
    assert "c runtime 21" in texts, "the close never reached SIGBREAK"
    assert not any(text.startswith("python") for text in texts)
    assert "finished" not in texts
    assert ended is not None and ended < 1.0, ended


@real
def test_a_held_close_is_ended_about_five_seconds_on(tmp_path):
    """The window still at work -- a pass in flight -- when the console
    closes: the handler holds, and Windows ends the process anyway."""
    lines, ended, _ = _close_its_console(tmp_path, """
asked = []
note(f"window {kernel32.GetConsoleWindow()}")
from rps7200.console import ConsoleClose
with ConsoleClose(on_close=lambda: asked.append(1),
                  say=lambda m: note("said " + m.split(":")[0].strip())):
    for _ in range(120):
        note("alive" + (" asked" if asked else ""))
        time.sleep(0.1)
note("finished")
""")
    assert any(text.startswith("said the console was closed")
               for _, _, text in lines)
    after = [at for at, _, text in lines if text == "alive asked"]
    assert after, "the request never reached the main thread"
    last = max(at for at, _, _ in lines)
    print(f"held: last line {last:.2f} s and ended {ended:.2f} s after the "
          "close")
    assert ended is not None and 3.0 < ended < 8.0, ended
    assert not any(text == "finished" for _, _, text in lines)


@real
def test_a_window_that_quits_inside_the_grace_is_not_kept_waiting(tmp_path):
    """An idle window puts away its sheet and settings and quits in well
    under a second. The handler's hold used to keep it until Windows ended
    it: taking the handler off, and the process's own exit, both waited on
    the lock Windows holds while a handler runs."""
    lines, ended, _ = _close_its_console(tmp_path, """
asked = threading.Event()
note(f"window {kernel32.GetConsoleWindow()}")
from rps7200.console import ConsoleClose
with ConsoleClose(on_close=asked.set) as closed:
    while not asked.wait(0.1):
        pass
    note("quitting")
    time.sleep(0.5)
note("quit")
time.sleep(3)
note("still here")
""", after=8.0)
    # The handler's return ends the process, and may beat the line after the
    # `with`: nothing the window does comes after its mainloop.
    texts = [text for _, _, text in lines]
    assert "quitting" in texts and "still here" not in texts
    print(f"quit inside the grace: ended {ended:.2f} s after the close")
    assert ended is not None and ended < 2.0, ended


@real
def test_a_window_started_apart_outlives_the_console_it_was_started_from(
        tmp_path):
    """`make run`'s path: `tasks.start_apart` from a console that is then
    closed, with the process that started it still waiting in it."""
    beat = tmp_path / "apart.log"
    stand_in = ("import time\n"
                "for _ in range(150):\n"
                f"    open({str(beat)!r}, 'a').write(f'{{time.time()}}\\n')\n"
                "    time.sleep(0.1)\n")
    lines, ended, closed_at = _close_its_console(tmp_path, f"""
note(f"window {{kernel32.GetConsoleWindow()}}")
from pathlib import Path
import tasks
tasks.start_apart([sys.executable, "-c", {stand_in!r}],
                  Path({str(tmp_path / "logs")!r}), settle=0.5)
note("started")
time.sleep(60)
""")
    assert any(text == "started" for _, _, text in lines)
    assert ended is not None, "the console's own process outlived its close"
    beats = [float(t) for t in beat.read_text().split()]
    assert beats, "the stand-in started apart never ran"
    survived = max(beats) - closed_at
    print(f"started apart: still running {survived:.1f} s after the close")
    assert survived > 8.0, survived


@real
def test_the_window_closed_from_its_console_quits_inside_the_grace(tmp_path):
    """The window itself, under --demo: the close is handed to Tk's thread
    from the one Windows makes, Quit's path puts the settings away, and the
    process ends long before Windows would have ended it, the close kept in
    demo/logs/console-closed.log. Opens the demo window for a few seconds;
    no scanner.

    Its settings in a folder of its own under demo/, removed after: the
    demo refuses a settings file outside demo/ (`main`), and this test's
    own folder is one."""
    here = ROOT / "demo" / f"console-close-test-{os.getpid()}"
    here.mkdir(parents=True, exist_ok=True)
    try:
        settings = here / "gui-settings.json"
        kept = ROOT / "demo" / "logs" / "console-closed.log"
        lines, ended, closed_at = _close_its_console(tmp_path, f"""
note(f"window {{kernel32.GetConsoleWindow()}}")
import runpy
sys.argv = ["tools/gui.py", "--demo", "--settings", {str(settings)!r}]
runpy.run_path("tools/gui.py", run_name="__main__")
""", before=8.0, after=8.0)
        print(f"the demo window: ended {ended} s after the close")
        assert ended is not None and ended < 3.0, ended
        assert (settings.is_file()
                and settings.stat().st_mtime >= closed_at - 0.5)
        since = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                              time.gmtime(closed_at - 1))
        assert any(line >= since and "the console was closed" in line
                   for line in kept.read_text(encoding="utf-8").splitlines())
    finally:
        shutil.rmtree(here, ignore_errors=True)
