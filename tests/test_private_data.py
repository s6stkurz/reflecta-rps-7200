"""Private photographs never enter the history.

`library personal/` holds lab scans of Stefan's own rolls. They are the
picture source for research/frame-position-net, and everything computed from
them -- caches, synthetic prescans, sheets, weights -- lives under that
folder's ignored `local/`. `.gitignore` keeps both out; this checks what is
actually tracked, so a forced add or a moved ignore rule is caught as well.

Nothing in this repository has ever needed a tracked JPEG or numpy array:
scans are TIFFs, and every array is rebuilt from stored raw bytes.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ("library personal/", "research/frame-position-net/local/")
NEVER_TRACKED = (".jpg", ".jpeg", ".npz", ".npy")


def _tracked() -> list[str]:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    done = subprocess.run(["git", "ls-files", "-z"], capture_output=True, cwd=ROOT)
    if done.returncode != 0:
        pytest.skip("not a git checkout")
    return [p for p in done.stdout.decode("utf-8", "replace").split("\0") if p]


def test_nothing_private_is_tracked():
    tracked = _tracked()
    assert tracked, "git ls-files returned nothing"
    private = [p for p in tracked if p.startswith(PRIVATE)]
    assert private == []


def test_no_photo_or_array_is_tracked():
    stray = [p for p in _tracked() if p.lower().endswith(NEVER_TRACKED)]
    assert stray == []


def test_the_private_folders_are_ignored():
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    probes = ["library personal/R1-00000-0000.JPG",
              "research/frame-position-net/local/weights.npz"]
    done = subprocess.run(["git", "check-ignore", "--no-index", *probes],
                          capture_output=True, text=True, cwd=ROOT)
    if done.returncode == 128:
        pytest.skip("not a git checkout")
    assert sorted(done.stdout.split("\n")[:-1]) == sorted(probes)
