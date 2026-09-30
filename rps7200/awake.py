"""Keep the host awake while the scanner is being read.

A roll runs for an hour or two with nobody at the keyboard, and every laptop
power plan sleeps after some minutes without keyboard or mouse input. None of
them counts a process's USB traffic as activity: sleep stops this process and
suspends the bus in the middle of a READ, which is an abandoned read -- the
state CLAUDE.md names as the cause of wedges -- and the frames after it are
never scanned. So a pass takes a power assertion for as long as it runs, and
lets it go when it ends; the scanner held open and idle between jobs does not
keep the machine up.

Each platform's own way, with nothing to install:

- **Windows**: `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`,
  on the thread that reads, and cleared on the same thread.
- **macOS**: `caffeinate -i -w <this process>`, which ends by itself if this
  process does.
- **Linux**: `systemd-inhibit --what=sleep:idle`, holding a `tail --pid` that
  ends with this process, so a kill never leaves the machine unable to sleep.

Where none can be taken it says so, once per job, and the job runs anyway: the
operator can still set the power plan by hand, and refusing to scan because a
convenience is missing would be worse. Nothing here speaks to the scanner.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from collections.abc import Callable

#: `SetThreadExecutionState` flags: keep the state until cleared, and keep the
#: system out of sleep. The display may still turn off; that is harmless.
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

#: How long a Linux inhibitor is given to fail before it is taken as held:
#: `systemd-inhibit` exits at once when logind refuses it.
SETTLE_S = 0.3


class KeepAwake:
    """A context manager that keeps the host out of idle sleep while open.

    ``say`` hears why it could not, when it could not. ``platform`` is
    `sys.platform` unless a test says otherwise.
    """

    #: Off in the test suite (`tests/conftest.py`), so no test spawns a real
    #: inhibitor or changes the machine's power state.
    enabled = True

    def __init__(self, why: str = "reading from the film scanner",
                 say: Callable[[str], None] | None = None,
                 platform: str | None = None):
        self.why = why
        self.say = say
        self.platform = sys.platform if platform is None else platform
        self._proc: subprocess.Popen | None = None
        self._windows = False
        #: Why it could not be taken, or None when it was (or is off).
        self.problem: str | None = None

    def __enter__(self) -> KeepAwake:
        if not self.enabled:
            return self
        try:
            self.problem = self._take()
        except Exception as exc:                         # noqa: BLE001
            self.problem = str(exc)
        if self.problem is not None and self.say is not None:
            self.say(f"could not keep this computer awake ({self.problem}); "
                     "if it sleeps during a pass, the read is abandoned and "
                     "the scanner may need a power cycle -- set its power "
                     "plan not to sleep while this runs")
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()

    def _take(self) -> str | None:
        if self.platform == "win32":
            kernel32 = getattr(ctypes, "windll").kernel32
            if not kernel32.SetThreadExecutionState(
                    ES_CONTINUOUS | ES_SYSTEM_REQUIRED):
                return "SetThreadExecutionState refused"
            self._windows = True
            return None
        if self.platform == "darwin":
            argv = ["caffeinate", "-i", "-w", str(os.getpid())]
        else:
            if shutil.which("systemd-inhibit") is None:
                return "systemd-inhibit is not installed"
            argv = ["systemd-inhibit", "--what=sleep:idle", "--who=rps7200",
                    f"--why={self.why}", "--mode=block",
                    "tail", f"--pid={os.getpid()}", "-f", os.devnull]
        try:
            # In a session of its own, out of the terminal's process group. In
            # it, the first Ctrl-C reached the inhibitor too and ended it at
            # once, while `DeferredInterrupt` kept the tool reading to finish
            # the pass in flight -- minutes at 7200 dpi -- with nothing
            # holding the machine up. Only `release` ends it now.
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL,
                                    start_new_session=True)
        except OSError as exc:
            return f"{argv[0]}: {exc}"
        try:
            code = proc.wait(timeout=SETTLE_S)
        except subprocess.TimeoutExpired:
            self._proc = proc
            return None
        return f"{argv[0]} exited with status {code}"

    def release(self) -> None:
        """Let the host sleep again. Safe to call twice."""
        if self._windows:
            self._windows = False
            try:
                getattr(ctypes, "windll").kernel32.SetThreadExecutionState(
                    ES_CONTINUOUS)
            except Exception:                            # noqa: BLE001
                pass
        proc, self._proc = self._proc, None
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
