"""`tasks.py`: how `make run`, `run-demo` and `run-sheet` start the window.

On Windows they start it apart from the console they were typed in
(`tasks.start_apart`), because closing that console ends every process
attached to it -- the window's scanner thread inside its read -- and nothing
the window can do buys more than about 5 s (`ConsoleClose`). Elsewhere the
window stays attached and is waited for, as it always was. These run on
every platform with `subprocess.Popen` stood in for; the real consoles are
closed in `test_console_close.py`, opt-in.
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
