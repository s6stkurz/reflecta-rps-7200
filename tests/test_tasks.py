"""`tasks.py`: how `make run`, `run-demo` and `run-sheet` start the window.

On Windows they start it apart from the console they were typed in
(`tasks.start_apart`), because closing that console ends every process
attached to it -- the window's scanner thread inside its read -- and nothing
the window can do buys more than about 5 s (`ConsoleClose`). Elsewhere the
window stays attached and is waited for, as it always was. These run on
every platform with `subprocess.Popen` stood in for -- where a real window
is started, by one that keeps it in a folder of the test's own and ends it;
the real consoles are closed in `test_console_close.py`, opt-in.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _tasks():
    spec = importlib.util.spec_from_file_location("tasks_under_test",
                                                  ROOT / "tasks.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tasks():
    return _tasks()


class FakeChild:
    """`subprocess.Popen` as far as `start_apart` uses it.

    ``ends`` is the exit code of a window that ends inside the settling
    time, having written ``prints`` to the log it was handed; None is one
    still running when this stops watching.
    """

    started: list[dict] = []
    ends: int | None = None
    prints = b""
    refuse_breakaway = False

    def __init__(self, command, **kw):
        if self.refuse_breakaway and kw["creationflags"] & 0x01000000:
            raise PermissionError(13, "Access is denied")
        kw["stdout"].write(self.prints)
        type(self).started.append({"command": command, **kw})

    def wait(self, timeout=None):
        if self.ends is None:
            raise subprocess.TimeoutExpired("gui.py", timeout)
        return self.ends


@pytest.fixture
def child(monkeypatch, tasks):
    monkeypatch.setattr(FakeChild, "started", [])
    monkeypatch.setattr(tasks.subprocess, "Popen", FakeChild)
    return FakeChild


def test_off_windows_the_window_is_attached_and_waited_for(tasks, monkeypatch):
    """Ctrl-C, SIGTERM and the terminal closing all reach it there as Quit's
    stop after the frame in flight: nothing to work around."""
    ran, apart = [], []
    monkeypatch.setattr(tasks, "APART", False)
    monkeypatch.setattr(tasks, "run", lambda command: ran.append(command) or 0)
    monkeypatch.setattr(tasks, "start_apart",
                        lambda *a, **k: apart.append(a) or 0)
    assert tasks.run_demo() == 0
    assert ran == [[sys.executable, "tools/gui.py", "--demo"]] and apart == []


def test_each_window_target_starts_apart_on_windows(tasks, monkeypatch):
    """`run` logs beside the checkout; the demo targets under `demo/`, where
    trying the window out leaves everything else it writes."""
    apart = []
    monkeypatch.setattr(tasks, "APART", True)
    monkeypatch.setattr(tasks, "run", lambda *a, **k: pytest.fail("attached"))
    monkeypatch.setattr(tasks, "start_apart",
                        lambda command, logs: apart.append((command, logs)) or 0)
    tasks.run_gui()
    tasks.run_demo()
    tasks.run_sheet()
    gui = [sys.executable, "tools/gui.py"]
    assert apart == [
        (gui, tasks.ROOT / "logs"),
        (gui + ["--demo"], tasks.ROOT / "demo" / "logs"),
        (gui + ["--demo", "--look-only", "--open-roll", tasks.DEMO_ROLL],
         tasks.ROOT / "demo" / "logs"),
    ]


def test_started_apart_it_has_a_console_with_no_window(tasks, child, tmp_path,
                                                       capsys):
    """Not DETACHED_PROCESS: the venv's python.exe starts the interpreter as
    a child of its own, and that child was given a new, visible console --
    closable, and the window with it. Measured 2026-10-09."""
    assert tasks.start_apart(["python", "tools/gui.py"], tmp_path / "logs",
                             settle=0.01) == 0
    [started] = child.started
    flags = started["creationflags"]
    assert flags & tasks.CREATE_NO_WINDOW
    assert flags & tasks.CREATE_NEW_PROCESS_GROUP
    assert flags & tasks.CREATE_BREAKAWAY_FROM_JOB
    assert not flags & 0x00000008                           # DETACHED_PROCESS
    assert not flags & 0x00000010                           # CREATE_NEW_CONSOLE
    assert started["stdin"] is subprocess.DEVNULL
    assert started["stderr"] is subprocess.STDOUT
    assert started["cwd"] == tasks.ROOT
    assert started["env"]["PYTHONUNBUFFERED"] == "1"
    [log] = (tmp_path / "logs").glob("window-*.log")
    assert Path(started["stdout"].name) == log
    said = capsys.readouterr().out
    assert str(log) in said and "Quit is in the window" in said
    assert "may still end it" not in said


def test_a_job_that_will_not_let_go_still_starts_the_window(tasks, child,
                                                            tmp_path,
                                                            monkeypatch,
                                                            capsys):
    """Where the job this runs in refuses a breakaway, CreateProcess refuses
    the flag; the window is started without it rather than not at all. And
    then the window is in the job a terminal may kill as it closes -- the
    one case where "closing this console cannot end a scan" may be false --
    so it is not said, and Quit in the window first is."""
    monkeypatch.setattr(FakeChild, "refuse_breakaway", True)
    assert tasks.start_apart(["python", "tools/gui.py"], tmp_path,
                             settle=0.01) == 0
    [started] = child.started
    assert not started["creationflags"] & tasks.CREATE_BREAKAWAY_FROM_JOB
    assert started["creationflags"] & tasks.CREATE_NO_WINDOW
    said = capsys.readouterr().out
    assert "cannot end a scan" not in said
    assert "closing this console may still end it" in said
    assert "Quit in the window before closing this console" in said


def test_a_window_that_ends_at_once_is_shown_where_it_was_asked_for(
        tasks, child, tmp_path, monkeypatch, capsys):
    """A roll folder that is not there ends the window in about 2 s. Said only
    in a log file, it would have looked like a window that never opened."""
    monkeypatch.setattr(FakeChild, "ends", 2)
    monkeypatch.setattr(FakeChild, "prints",
                        "gui.py: error: no roll folder at "
                        "'rolls/aligned-strip' → none\n".encode())
    assert tasks.start_apart(["python", "tools/gui.py"], tmp_path,
                             settle=0.01) == 2
    said = capsys.readouterr().out
    assert "no roll folder at 'rolls/aligned-strip'" in said
    assert "exit code 2" in said


def test_two_windows_started_in_one_second_keep_both_logs(tasks, child,
                                                          tmp_path,
                                                          monkeypatch):
    monkeypatch.setattr(tasks.time, "strftime",
                        lambda *a: "20261009T020000Z")
    tasks.start_apart(["python"], tmp_path, settle=0.01)
    tasks.start_apart(["python"], tmp_path, settle=0.01)
    names = sorted(p.name for p in tmp_path.glob("window-*.log"))
    assert names == ["window-20261009T020000Z-2.log",
                     "window-20261009T020000Z.log"]


# -- one window per library -----------------------------------------------------


def _window_held(path, what="the demo window"):
    from conftest import HeldElsewhere

    from rps7200 import locks

    return HeldElsewhere(locks.WINDOW, path, what=what)


@pytest.mark.parametrize("shared", ["library", "rolls", "settings"])
def test_a_second_window_on_one_of_its_folders_exits_at_once_naming_it(
        tasks, tmp_path, shared):
    """`--library X` alone still shares the rolls and the settings, and two
    windows wrote over each other's settings, sheets and rolls. The second
    prints one line naming the first and exits 1 before any window exists
    -- inside the seconds `make run` watches it for, so it is seen there.

    The second is a demo window, in a folder of the test's own -- its
    defaults, `demo/` under where it runs. Were the refusal ever to go, a
    real one would have gone on to open whatever scanner is on this
    machine's bus, from a test that is not marked `hardware`, and been
    killed with the device open; this one opens the stand-in."""
    import time

    pytest.importorskip("tkinter")
    demo = tmp_path / "demo"
    folders = {"library": demo / "library", "rolls": demo / "rolls",
               "settings": demo / "gui-settings.json"}
    with _window_held(folders[shared], what="the window") as first:
        started = time.monotonic()
        done = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "gui.py"), "--demo"],
            cwd=tmp_path, capture_output=True, text=True, timeout=60)
        took = time.monotonic() - started
    assert done.returncode == 1, done.stderr
    label = {"library": "library", "rolls": "rolls folder",
             "settings": "settings file"}[shared]
    assert done.stderr.startswith(f"{label} {folders[shared]} is already "
                                  "open in the window (")
    assert f"pid {first.pid}" in done.stderr
    assert "One window per library, rolls folder and settings file" \
        in done.stderr
    assert done.stderr.count("\n") == 1, "one line"
    assert took < tasks.SETTLE_S, f"refused only after {took:.1f} s"
    assert not demo.exists(), "made something before it was refused"


def test_run_demo_beside_an_open_demo_window_names_it_and_fails(
        tasks, tmp_path, monkeypatch, capfd):
    """`make run-demo` and `make run-sheet` share the demo's folders, so
    each refuses beside the other, and says so where it was typed.

    The window `run_demo` starts is a real one, kept where the test can end
    it: in a folder of the test's own, left in the test's job, and killed if
    it is still running once `run_demo` has returned -- or after a minute,
    off Windows, where it is waited for. Started as `make run-demo` starts
    it, in the checkout and broken away, a refusal that ever went would
    have left a demo window running on the checkout's `demo/` after pytest
    had ended, its locks in the test's lock folder, unseen by the demo
    window Stefan had open there.
    """
    import threading

    pytest.importorskip("tkinter")
    monkeypatch.setattr(tasks, "DEMO_LOGS", tmp_path / "logs")
    real_popen = subprocess.Popen
    gui = str(ROOT / "tools" / "gui.py")
    started, watchdogs, outlived = [], [], []

    def watchdog(child):
        if child.poll() is None:
            outlived.append(child)
            child.kill()

    def contained(command, *args, **kw):
        if command[1:2] == ["tools/gui.py"]:
            command = [command[0], gui, *command[2:]]
            kw["cwd"] = tmp_path
            if "creationflags" in kw:
                kw["creationflags"] &= ~tasks.CREATE_BREAKAWAY_FROM_JOB
        child = real_popen(command, *args, **kw)
        started.append(child)
        timer = threading.Timer(60, watchdog, (child,))
        timer.daemon = True
        timer.start()
        watchdogs.append(timer)
        return child

    left = []
    try:
        # The holder first: it starts a process of its own.
        with _window_held(tmp_path / "demo" / "library") as first, \
                monkeypatch.context() as patched:
            patched.setattr(tasks.subprocess, "Popen", contained)
            code = tasks.run_demo()
    finally:
        for timer in watchdogs:
            timer.cancel()
        left = [child for child in started if child.poll() is None]
        for child in left:
            # The venv's launcher takes the interpreter it started with it.
            child.kill()
            child.wait(10)
    said = capfd.readouterr()
    assert not left and not outlived, \
        f"the demo window was not refused, and was left running: {said}"
    assert [child.args[1] for child in started] == [gui]
    assert code == 1, said
    words = said.out + said.err
    assert "is already open in the demo window (" in words
    assert f"pid {first.pid}" in words
    assert not (tmp_path / "demo").exists(), "made something before it was refused"


#: A window's holds as `hold_window` takes them, in a process of its own --
#: as the window started first has them -- until the release file appears.
_A_WINDOW = """
import contextlib, importlib.util, os, sys, time
from pathlib import Path
library, rolls, settings_file, release = sys.argv[1:5]
spec = importlib.util.spec_from_file_location(
    "gui", os.path.join("tools", "gui.py"))
gui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gui)
with contextlib.ExitStack() as held:
    refused = gui.hold_window(held, "the window", library=library,
                              rolls=rolls, settings_file=settings_file)
    if refused is not None:
        raise SystemExit(refused)
    print(f"held {os.getpid()}", flush=True)
    deadline = time.monotonic() + 60
    while not Path(release).exists() and time.monotonic() < deadline:
        time.sleep(0.02)
"""


def test_windows_on_folders_of_their_own_hold_them_side_by_side(tmp_path):
    """A demo window beside the real one, each in a process of its own as
    they run: nothing shared, nothing refused. Both in one process, a
    second take of the same folder is counted rather than refused, so that
    could not have failed; the control -- a window on the first one's
    rolls, refused naming it -- shows the first really holds them."""
    import contextlib

    pytest.importorskip("tkinter")
    from conftest import load_tool

    gui = load_tool("gui")
    real = (tmp_path / "library", tmp_path / "rolls",
            tmp_path / "gui-settings.json")
    demo = tmp_path / "demo"
    release = tmp_path / "release"
    first = subprocess.Popen(
        [sys.executable, "-c", _A_WINDOW, *map(str, real), str(release)],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        said = first.stdout.readline().split()
        assert said[:1] == ["held"], first.communicate(timeout=30)[1]
        pid = int(said[1])
        with contextlib.ExitStack() as held:
            assert gui.hold_window(held, "the demo window",
                                   library=demo / "library",
                                   rolls=demo / "rolls",
                                   settings_file=demo / "gui-settings.json") \
                is None
        with contextlib.ExitStack() as held:
            refused = gui.hold_window(held, "the demo window",
                                      library=demo / "library",
                                      rolls=real[1],
                                      settings_file=demo / "gui-settings.json")
        assert refused is not None
        assert refused.startswith(f"rolls folder {real[1]} is already open "
                                  "in the window (")
        assert f"pid {pid}" in refused
    finally:
        release.touch()
        first.communicate(timeout=30)
    assert first.returncode == 0
