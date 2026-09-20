# Developer commands. Everything runs through `uv run`, so the pinned dev tools
# in pyproject.toml's [dependency-groups] are what execute -- never whatever
# happens to be on PATH.
#
# Every recipe below is a single command with no shell syntax in it, and that
# is deliberate: GNU make on Windows hands recipes to cmd.exe unless a POSIX
# `sh` is on PATH, where an inline `VAR=1 cmd` prefix and `rm -rf` are both
# syntax errors. The bodies live in tasks.py, which does those two things the
# same way on all three platforms. `make test` is still what to type.
PY = uv run python

.PHONY: all
all:
	@$(PY) tasks.py all

.PHONY: install sync
install sync:
	@$(PY) tasks.py sync

.PHONY: lint
lint:
	@$(PY) tasks.py lint

.PHONY: type
type:
	@$(PY) tasks.py type

.PHONY: test
test:
	@$(PY) tasks.py test

# The optional tifffile dependency has to be genuinely optional: the built-in
# TIFF path is the one a bare install uses, and the two must not disagree about
# what comes back. conftest.py blocks the import for the whole run.
.PHONY: test-no-tifffile
test-no-tifffile:
	@$(PY) tasks.py test-no-tifffile

# Both TIFF paths, which is what CI runs.
.PHONY: test-all
test-all:
	@$(PY) tasks.py test-all

# Safe autofixes only -- unused imports, redundant f-strings and the like.
# This is what `all` runs, and what to run before committing.
.PHONY: fix
fix:
	@$(PY) tasks.py fix

# Run the scanning GUI locally. Opening the window claims the device and asks
# it who it is, and nothing else: no calibration, no mechanism, until a button
# is pressed. `--demo` needs no scanner at all and drives the window from
# stored library entries.
.PHONY: run
run:
	@$(PY) tasks.py run

.PHONY: run-demo
run-demo:
	@$(PY) tasks.py run-demo

# Whole-file reformat. Deliberately NOT part of `all`, and not to be run
# casually: this source is hand-wrapped at ~79 columns with aligned comment
# blocks, and reformatting it rewrites ~1800 lines across 22 files, which buries
# every real change in the diff and conflicts with any parallel branch. Run it
# only on a file you are already rewriting, and only with the user's agreement.
.PHONY: format
format:
	@$(PY) tasks.py format

# Re-decode every stored scan with the current code and report what no longer
# matches. Run after any change to how the scanner's bytes become pixels: it
# tests the change against every scan ever taken, not just the next one.
.PHONY: reconstruct
reconstruct:
	@$(PY) tasks.py reconstruct

.PHONY: verify
verify:
	@$(PY) tasks.py verify

.PHONY: clean
clean:
	@$(PY) tasks.py clean
