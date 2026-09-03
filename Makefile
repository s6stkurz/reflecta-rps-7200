# Developer commands. Everything runs through `uv run`, so the pinned dev tools
# in pyproject.toml's [dependency-groups] are what execute -- never whatever
# happens to be on PATH.
UV = uv run

.PHONY: all
all: fix lint type test

.PHONY: install sync
install sync:
	@uv sync --all-groups

.PHONY: lint
lint:
	@echo "Running lint checks (ruff)..."
	@$(UV) ruff check .

.PHONY: type
type:
	@echo "Running type checks (ty)..."
	@$(UV) ty check \
		--exclude "tests/" \
		--exclude "tools/" \
		--ignore "unresolved-import" \
		--ignore "unresolved-attribute" \
		--ignore "invalid-argument-type" \
		--ignore "invalid-assignment" \
		--ignore "possibly-missing-attribute" \
		--ignore "unsupported-operator" \
		--ignore "no-matching-overload"

.PHONY: test
test:
	@echo "Running unit tests (pytest)..."
	@$(UV) pytest tests/ --cov=rps7200 --cov-report=term-missing

# The optional tifffile dependency has to be genuinely optional: the built-in
# TIFF path is the one a bare install uses, and the two must not disagree about
# what comes back. conftest.py blocks the import for the whole run.
.PHONY: test-no-tifffile
test-no-tifffile:
	@echo "Running unit tests with tifffile absent..."
	@RPS7200_NO_TIFFFILE=1 $(UV) pytest tests/ -q

# Both TIFF paths, which is what CI should run.
.PHONY: test-all
test-all: test test-no-tifffile

# Safe autofixes only -- unused imports, redundant f-strings and the like.
# This is what `all` runs, and what to run before committing.
.PHONY: fix
fix:
	@echo "Applying safe autofixes (ruff check --fix)..."
	@$(UV) ruff check --fix .

# Whole-file reformat. Deliberately NOT part of `all`, and not to be run
# casually: this source is hand-wrapped at ~79 columns with aligned comment
# blocks, and reformatting it rewrites ~1800 lines across 22 files, which buries
# every real change in the diff and conflicts with any parallel branch. Run it
# only on a file you are already rewriting, and only with the user's agreement.
.PHONY: format
format:
	@echo "Reformatting every file -- see the note in the Makefile before using this."
	@$(UV) ruff format .

# Re-decode every stored scan with the current code and report what no longer
# matches. Run after any change to how the scanner's bytes become pixels: it
# tests the change against every scan ever taken, not just the next one.
.PHONY: reconstruct
reconstruct:
	@$(UV) python tools/library.py reconstruct

.PHONY: verify
verify:
	@$(UV) python tools/library.py verify

.PHONY: clean
clean:
	rm -rf .pytest_cache .ruff_cache .coverage build dist
	find . -type d -name "__pycache__" -exec rm -rf {} +
