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
TY_ARGS = [
    "check",
    "--exclude", "tests/",
    "--exclude", "tools/",
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


def run_gui() -> int:
    """Open the window. Claims the device and asks it who it is, nothing more:
    no calibration, no mechanism, until a button is pressed."""
    print("Starting the RPS 7200 scanner GUI...")
    return run([sys.executable, "tools/gui.py"])


def run_demo() -> int:
    """The window with no scanner, driven from stored library entries -- or
    from a generated test card where the library is empty."""
    print("Starting the GUI in demo mode (no scanner)...")
    return run([sys.executable, "tools/gui.py", "--demo"])


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
