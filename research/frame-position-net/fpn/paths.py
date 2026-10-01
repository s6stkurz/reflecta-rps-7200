"""Where things are. Everything read here is gitignored data, so a checkout
without it -- a worktree, CI -- points `FPN_DATA_ROOT` at one that has it."""

from __future__ import annotations

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent          # research/frame-position-net
ROOT = HERE.parent.parent                               # the repository
DATA_ROOT = Path(os.environ.get("FPN_DATA_ROOT", str(ROOT)))

LIBRARY = DATA_ROOT / "library"
PERSONAL_SOURCE = DATA_ROOT / "library personal"
STUDY = DATA_ROOT / "research" / "frame-edge"           # the frame-edge study's work folders
STUDY_LIB2 = STUDY / "lib2"

LOCAL = HERE / "local"                                  # gitignored: caches, sets, sheets, weights
PERSONAL_CACHE = LOCAL / "personal"
SPLIT = HERE / "split.json"                             # committed, frozen before training
