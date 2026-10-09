#!/usr/bin/env python3
"""What the Makefile targets actually do, as plain Python.

`make` is still the interface -- `make test`, `make run-demo`, the same words
on macOS, Linux and Windows. What moved here is the *body* of each recipe, so
that every one of them is a single command with no shell syntax in it.

That is not tidiness. GNU make on Windows hands a recipe to `cmd.exe` unless a
POSIX `sh` happens to be on PATH, and two recipes were Bourne-only:

    RPS7200_NO_TIFFFILE=1 $(UV) pytest tests/ -q      # inline env-var prefix
    rm -rf .pytest_cache ...                          # and `find ... -exec`

Both were measured failing from PowerShell before this file existed: *"Der
Befehl "FOO" ist entweder falsch geschrieben oder konnte nicht gefunden
werden."* An environment variable and a recursive delete are things Python
does the same way everywhere, so they are done here instead.

The pinned tools are still what execute, which is the point of the `uv run`
convention. Running under `uv run python tasks.py ...` means this interpreter
already lives in the project's environment, so its `Scripts/`/`bin/` directory
is where ruff, ty and pytest come from -- no second resolve, and no chance of
picking up whatever is on PATH. Invoked outside that environment it falls back
to asking `uv`.

Runnable on its own -- `python tasks.py test` -- for a machine that has no
make. That is a fallback, not the documented path.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

#: The package, with **nothing silenced**.
#:
#: This used to carry seven `--ignore` flags, and they were doing less than
#: they looked. Measured across the whole repo with every rule on: five of the
#: seven silenced nothing at all inside `rps7200/`, and one of those --
#: `possibly-missing-attribute` -- fires zero times anywhere in the repo under
#: any scope. What the list actually bought was 41 diagnostics over six lines,
#: and 35 of those were a single `**kwargs` splat into `tifffile.imwrite`.
#:
#: So they are gone and the six sites are fixed instead. A blanket ignore hides
#: the next real diagnostic as willingly as it hides the last false one, and
#: this list read as a retreat from numpy far broader than the ground it was
#: actually holding.
#:
#: `tools/` and `tests/` are still excluded, and that is a real gap rather than
#: a decision: `tools/gui.py` is the largest file here and the one an operator
#: drives. It is sized rather than hand-waved -- 27 diagnostics in `tools/`
#: without gui.py (needing `--extra-search-path tests` and one for the skill
#: scripts, which two tools import through a runtime `sys.path` insertion the
#: checker cannot see), and 28 more in gui.py of which several want design
#: changes, not annotations. See TODO.md.
#:
#: `docs/` is excluded because nothing there is the driver: `docs/multi-exposure/`
#: is an archived study whose modules import each other as plain files.
TY_ARGS = [
    "check",
    "--exclude", "tests/",
    "--exclude", "tools/",
    "--exclude", "docs/",
]

#: Build output and caches. Everything here is regenerated, so `clean` may
#: remove it without asking; the scan directories are deliberately absent.
CACHES = (".pytest_cache", ".ruff_cache", ".coverage", "build", "dist")


def tool(name: str) -> list[str]:
    """The pinned `name`, as a command.

    Straight from this interpreter's own environment when there is one, which
    there is under `uv run`. Otherwise `uv` is asked, which is what happens if
    someone runs this file with a bare python.
    """
    beside = Path(sys.executable).parent / (name + (".exe" if os.name == "nt" else ""))
    if beside.exists():
        return [str(beside)]
    return ["uv", "run", name]


def run(command: list[str], *, env: dict[str, str] | None = None) -> int:
    """Run it in the repo root and hand back its exit code.

    No `shell=True` anywhere: the argument list is the same on every platform,
    which is the whole reason this file exists.
    """
    print("$ " + " ".join(command), flush=True)
    merged = None
    if env:
        merged = {**os.environ, **env}
    return subprocess.run(command, cwd=ROOT, env=merged).returncode


# -- the targets -------------------------------------------------------------


def sync() -> int:
    return run(["uv", "sync", "--all-groups"])


def lint() -> int:
    print("Running lint checks (ruff)...")
    return run(tool("ruff") + ["check", "."])


def fix() -> int:
    """Safe autofixes only -- unused imports, redundant f-strings and the like."""
    print("Applying safe autofixes (ruff check --fix)...")
    return run(tool("ruff") + ["check", "--fix", "."])


def type_check() -> int:
    print("Running type checks (ty)...")
    return run(tool("ty") + TY_ARGS)


def test() -> int:
    print("Running unit tests (pytest)...")
    return run(tool("pytest") + ["tests/", "--cov=rps7200",
                                 "--cov-report=term-missing"])


def test_no_tifffile() -> int:
    """The suite as a bare install sees it.

    The optional tifffile dependency has to be genuinely optional: the built-in
    TIFF path is the one a bare install uses, and the two must not disagree
    about what comes back. `tests/conftest.py` reads this variable and refuses
    the import for the whole run.
    """
    print("Running unit tests with tifffile absent...")
    return run(tool("pytest") + ["tests/", "-q"],
               env={"RPS7200_NO_TIFFFILE": "1"})


def test_all() -> int:
    """Both TIFF paths, which is what CI runs."""
    return test() or test_no_tifffile()


def all_() -> int:
    """What to run before committing."""
    return fix() or lint() or type_check() or test()


#: Whether the window is started apart from the console that asked for it,
#: rather than attached to it and waited for: on Windows, see `start_apart`.
APART = os.name == "nt"

#: Where a window started apart writes what it prints. The demo targets keep
#: theirs under `demo/`, so trying the window out still leaves nothing
#: outside it. Both are gitignored.
LOGS = ROOT / "logs"
DEMO_LOGS = ROOT / "demo" / "logs"

#: How long a window started apart is watched before this returns. One that
#: ends sooner -- a roll folder that is not there, an import that fails --
#: is shown here with its log, rather than only in a file nobody opened. The
#: first of those took 2.2 s from `uv run`, measured.
SETTLE_S = 5.0

#: CreateProcess flags, by value so this file imports on every platform.
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000


def window(arguments: list[str], logs: Path) -> int:
    """Start `tools/gui.py` with these arguments.

    Apart from this console on Windows (`start_apart`). Elsewhere attached to
    the terminal and waited for, as it always was: Ctrl-C, SIGTERM and the
    terminal closing all reach it there as Quit's "stop after the frame in
    flight" (`DeferredInterrupt`), so there is nothing to work around.
    """
    command = [sys.executable, "tools/gui.py", *arguments]
    if not APART:
        return run(command)
    return start_apart(command, logs)


def start_apart(command: list[str], logs: Path, *,
                settle: float = SETTLE_S) -> int:
    """Start it on Windows with a console of its own that has no window, and
    return once it is running.

    Closing a console on Windows raises CTRL_CLOSE_EVENT in every process
    attached to it, and ends each. The C runtime hands the event to the
    SIGBREAK handler and returns at once, which is what ends the process,
    before Python has run that handler. Measured 2026-10-09 by posting
    WM_CLOSE to a console, with a stand-in process started from `uv run` as
    this starts the window: started attached, it ended that same moment --
    for the window, its scanner thread inside whatever read it was in -- and
    a handler that holds the close off is ended about 5 s later anyway
    (`ConsoleClose`), which no pass fits in. Started apart, it was still
    running 12 s later, with that console, uv and the process that started
    it all gone; `tests/test_console_close.py` repeats it with this
    function, opt-in. So closing the console `make run` was typed in cannot
    end a scan; Quit is in the window.

    `CREATE_NO_WINDOW` rather than `DETACHED_PROCESS`, measured the same
    day: the venv's python.exe is a launcher that starts the real interpreter
    as a child of its own, and a console program whose parent has no console
    is given a new one -- a visible console window, closing which ends the
    window as before. A console with no window is passed on to that child,
    and there is nothing to close.

    `CREATE_BREAKAWAY_FROM_JOB` where it is allowed. The venv launcher's job
    lets its children leave silently, but a terminal that holds what it runs
    in a job that kills them all as it closes would take the window along;
    where that job will not let go, it is started without -- and then the
    one assurance this gives is the one that may not hold, so it is not
    given: the operator is told to Quit in the window before closing the
    console.

    What the window prints goes to a log file under `logs`, named here. One
    that ends within `settle` seconds has its log shown and its exit code
    returned, so a refusal at launch is still seen where it was asked for.
    """
    logs.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    log = logs / f"window-{stamp}.log"
    number = 2
    while log.exists():
        log = logs / f"window-{stamp}-{number}.log"
        number += 1
    shown = log.relative_to(ROOT) if log.is_relative_to(ROOT) else log
    print("$ " + " ".join(command), flush=True)
    # Unbuffered, so the log is there to read as it happens -- and nothing
    # waits in a buffer when the window is ended rather than quit.
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    with open(log, "wb") as out:
        def start(flags: int) -> subprocess.Popen:
            return subprocess.Popen(
                command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                stdout=out, stderr=subprocess.STDOUT,
                creationflags=CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
                | flags)
        try:
            child = start(CREATE_BREAKAWAY_FROM_JOB)
            broke_away = True
        except OSError:
            child = start(0)
            broke_away = False
    print(f"The window runs apart from this console; what it prints goes to "
          f"{shown}.", flush=True)
    try:
        code = child.wait(timeout=settle)
    except subprocess.TimeoutExpired:
        if broke_away:
            print("Closing this console cannot end a scan: Quit is in the "
                  "window.", flush=True)
        else:
            print("The window could not leave the job this console runs it "
                  "in, so closing this console may still end it -- and a "
                  "scan with it. Quit in the window before closing this "
                  "console.", flush=True)
        return 0
    said = log.read_text(encoding="utf-8", errors="replace")
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(said.encode(encoding, errors="replace").decode(encoding), end="")
    print(f"The window ended within {settle:g} s, exit code {code}.",
          flush=True)
    return code


def run_gui() -> int:
    """Open the window. Claims the device and asks it who it is, nothing more:
    no calibration, no mechanism, until a button is pressed."""
    print("Starting the RPS 7200 scanner GUI...")
    return window([], LOGS)


def run_demo() -> int:
    """The window with no scanner, driven from stored library entries -- or
    from a generated test card where the library is empty."""
    print("Starting the GUI in demo mode (no scanner)...")
    return window(["--demo"], DEMO_LOGS)


#: The walk `run-sheet` opens. Fifteen prescans of a hand-aligned strip taken
#: 2026-09-22, and the only one measured end to end: its proposals come back
#: 10 measured, 4 unconfirmed, 1 neighbours, which is every word the ensemble
#: has in one sheet. The `registration-*` folders are the alternates.
DEMO_ROLL = "rolls/aligned-strip"


def run_sheet() -> int:
    """The contact sheet on a stored walk, with no scanner and no film.

    The positions are proposed from the prescans on every launch rather than
    read back from anywhere, so the same command twice is the measurement run
    twice -- which is the point of it. Within one run the sheet remembers what
    you set, as it always has.

    For a different strip, name it:

        uv run python tools/gui.py --demo --look-only             --open-roll rolls/registration-D
    """
    print(f"Opening the contact sheet for {DEMO_ROLL} (no scanner, no film)...")
    return window(["--demo", "--look-only", "--open-roll", DEMO_ROLL],
                  DEMO_LOGS)


def format_() -> int:
    """Whole-file reformat. Deliberately NOT part of `all`, and not to be run
    casually: this source is hand-wrapped with aligned comment blocks, and
    reformatting it rewrites thousands of lines, which buries every real change
    in the diff and conflicts with any parallel branch."""
    print("Reformatting every file -- see the note in the Makefile first.")
    return run(tool("ruff") + ["format", "."])


def reconstruct() -> int:
    """Re-decode every stored scan with the current code and report what no
    longer matches -- the change tested against every scan ever taken."""
    return run([sys.executable, "tools/library.py", "reconstruct"])


def verify() -> int:
    return run([sys.executable, "tools/library.py", "verify"])


def clean() -> int:
    """`rm -rf` and `find -exec`, done portably.

    Those two were the Makefile's only remaining POSIX-isms, and on Windows
    `find.exe` is a different program entirely -- it would not have errored
    loudly, it would have done nothing.
    """
    removed = 0
    for name in CACHES:
        target = ROOT / name
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            removed += 1
        elif target.exists():
            target.unlink(missing_ok=True)
            removed += 1
    for cache in ROOT.rglob("__pycache__"):
        if cache.is_dir():
            shutil.rmtree(cache, ignore_errors=True)
            removed += 1
    print(f"removed {removed} cache director{'y' if removed == 1 else 'ies'}")
    return 0


TARGETS = {
    "all": all_,
    "install": sync,
    "sync": sync,
    "lint": lint,
    "fix": fix,
    "type": type_check,
    "test": test,
    "test-no-tifffile": test_no_tifffile,
    "test-all": test_all,
    "run": run_gui,
    "run-demo": run_demo,
    "run-sheet": run_sheet,
    "format": format_,
    "reconstruct": reconstruct,
    "verify": verify,
    "clean": clean,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", choices=sorted(TARGETS),
                        help="which developer command to run")
    args = parser.parse_args(argv)
    return TARGETS[args.target]()


if __name__ == "__main__":
    raise SystemExit(main())
