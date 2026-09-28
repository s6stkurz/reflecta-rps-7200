"""The host kept out of idle sleep while a pass reads (`rps7200/awake.py`).

A laptop that idle-sleeps an hour into an unattended roll suspends the bus in
the middle of a READ: an abandoned read, the wedge. Nothing here spawns a real
inhibitor or touches the machine's power state -- every platform's mechanism
is a fake, and `tests/conftest.py` keeps `KeepAwake` off everywhere else.
"""
import ctypes
import os
import subprocess
import sys

import pytest

from conftest import load_tool
from rps7200 import awake, session
from rps7200.awake import ES_CONTINUOUS, ES_SYSTEM_REQUIRED, KeepAwake


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setattr(KeepAwake, "enabled", True)


class FakeProcess:
    def __init__(self, argv, exits=None):
        self.argv = argv
        self.exits = exits
        self.terminated = False

    def wait(self, timeout=None):
        if self.exits is not None:
            return self.exits
        if self.terminated:
            return 0
        raise subprocess.TimeoutExpired(self.argv, timeout)

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True


def spawner(monkeypatch, exits=None):
    started = []

    def popen(argv, **kw):
        started.append(FakeProcess(argv, exits))
        return started[-1]

    monkeypatch.setattr(awake.subprocess, "Popen", popen)
    return started


def test_linux_holds_an_inhibitor_that_ends_with_this_process(monkeypatch):
    monkeypatch.setattr(awake.shutil, "which", lambda name: "/usr/bin/" + name)
    started = spawner(monkeypatch)
    with KeepAwake(platform="linux") as held:
        assert held.problem is None
        (proc,) = started
        assert proc.argv[0] == "systemd-inhibit"
        assert "--what=sleep:idle" in proc.argv
        # Not `sleep infinity`: killed, this process would leave that behind
        # and the machine unable to sleep at all.
        assert f"--pid={os.getpid()}" in proc.argv
        assert not proc.terminated
    assert proc.terminated, "the inhibitor outlived the pass"


def test_macos_holds_caffeinate_tied_to_this_process(monkeypatch):
    started = spawner(monkeypatch)
    with KeepAwake(platform="darwin"):
        assert started[0].argv == ["caffeinate", "-i", "-w", str(os.getpid())]
    assert started[0].terminated


def test_windows_sets_and_clears_the_execution_state(monkeypatch):
    calls = []

    class Kernel32:
        def SetThreadExecutionState(self, flags):
            calls.append(flags)
            return 1

    monkeypatch.setattr(ctypes, "windll",
                        type("windll", (), {"kernel32": Kernel32()})(),
                        raising=False)
    with KeepAwake(platform="win32"):
        assert calls == [ES_CONTINUOUS | ES_SYSTEM_REQUIRED]
    assert calls[-1] == ES_CONTINUOUS


@pytest.mark.parametrize("which, exits, said", [
    (None, None, "systemd-inhibit is not installed"),
    ("/usr/bin/systemd-inhibit", 1, "exited with status 1"),
])
def test_one_that_cannot_be_taken_is_said_and_the_pass_runs(monkeypatch,
                                                            which, exits,
                                                            said):
    """A missing convenience never refuses a scan; it tells the operator to
    set the power plan by hand."""
    monkeypatch.setattr(awake.shutil, "which", lambda name: which)
    spawner(monkeypatch, exits=exits)
    heard = []
    ran = False
    with KeepAwake(platform="linux", say=heard.append) as held:
        ran = True
    assert ran and held.problem and said in held.problem
    assert len(heard) == 1 and "power plan" in heard[0]


class Recorder:
    """A `keep_awake` factory that writes down when it was held."""

    def __init__(self, log):
        self.log = log

    def __call__(self, say=None, **kw):
        log = self.log

        class Held:
            def __enter__(self):
                log.append("awake")
                return self

            def __exit__(self, *exc):
                log.append("asleep")

        return Held()


def test_the_session_keeps_the_host_awake_through_a_pass_not_a_move(
        tmp_path):
    from test_session import FakeScanner

    log = []

    class Scanner(FakeScanner):
        def scan(self, *a, **kw):
            log.append("scan")
            return super().scan(*a, **kw)

    s = session.ScanSession(root=str(tmp_path), rolls=str(tmp_path / "rolls"),
                            open_scanner=Scanner, verbose=False,
                            keep_awake=Recorder(log))
    s.start()
    s.submit(session.Move(frames=1))
    s.submit(session.Scan(resolution=600))
    s.shutdown()
    s.join(timeout=10.0)
    assert log == ["awake", "scan", "asleep"], log


@pytest.mark.parametrize("name", ["scan_roll"])
def test_the_roll_tool_keeps_the_host_awake_while_the_device_is_open(
        tmp_path, monkeypatch, name):
    from test_scan_roll_tool import FakeRollScanner

    tool = load_tool(name)
    log = []

    class Patched(FakeRollScanner):
        def __init__(self, **kw):
            super().__init__(frames=1)

        def __enter__(self):
            log.append("open")
            return self

    closing = session.close_device

    def close_device(scanner):
        log.append("close")
        closing(scanner)

    # Where the device closes: leaving the tool's `with` (`HeldOpen`).
    monkeypatch.setattr(session, "close_device", close_device)
    monkeypatch.setattr(tool, "DirectScanner", Patched)
    monkeypatch.setattr(tool, "KeepAwake", Recorder(log))
    monkeypatch.setattr(
        sys, "argv",
        ["scan_roll.py", "--out", str(tmp_path / "roll"),
         "--library", str(tmp_path / "lib"), "--no-shading",
         "--roll", "awake", "--frames", "1"])
    assert tool.main() == 0
    assert log[0] == "awake" and log[-1] == "asleep", log
    assert log.index("open") < log.index("close"), log


def test_the_scan_tool_keeps_the_host_awake_while_the_device_is_open(
        tmp_path, monkeypatch):
    import test_scan_tool

    log = []
    closing = session.close_device

    def close_device(scanner):
        log.append("close")
        closing(scanner)

    monkeypatch.setattr(session, "close_device", close_device)
    monkeypatch.setattr(test_scan_tool.scan_tool, "KeepAwake", Recorder(log))
    _scanner, code = test_scan_tool.run(tmp_path, monkeypatch)
    assert code == 0
    assert log == ["awake", "close", "asleep"], log
