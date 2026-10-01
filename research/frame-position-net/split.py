"""Freeze who may be used for what, by picture, before anything is trained.

    FPN_DATA_ROOT=<checkout with the data> uv run python research/frame-position-net/split.py

Writes split.json, which is committed. Run once; it refuses to overwrite.

Roles:
- compose   real dev frames whose pictures the synthetic sets may be built from,
            and whose statistics the C-41 film model is fitted to;
- realval   real dev frames never composed from: choosing the epoch and the
            abstain threshold, and the real-frame gate before any locked score;
- locked_*  the tests, scored once after PREREGISTERED.md is committed:
            main (Gold 200 + hwcheck), test2 (library 2's five pictures),
            bw (bw0910), positive (the x0920 slides, c125, c126);
- unused    the ladder.
The private rolls: A-D train, E synval (synthetic validation).

Pictures cross rolls, so the unit is the picture, never the frame:
r0911, r0914 and x0920 are one strip; library 2's pictures are grouped across
its rolls by registration (lib2/pictures.json). Seeds are fixed and the draw is
made without looking at any result.
"""

from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fpn import paths  # noqa: E402

SEED = 2026
#: One physical strip seen in three walks (experiment/frame-geometry, "Films").
SAME_STRIP = {"r0911": "strip-r0911-r0914-x0920", "r0914": "strip-r0911-r0914-x0920",
              "x0920": "strip-r0911-r0914-x0920"}
LOCKED_MAIN = {"gold200", "hwcheck"}
POSITIVE_EXTRA = ("c125", "c126")       # only in triage.json, excluded there
VAL_MAIN_GROUPS = 2                      # of the small main rolls
VAL_LIB2_PICTURES = 4                    # of library 2's twelve dev pictures
PERSONAL = {"A": "train", "B": "train", "C": "train", "D": "train", "E": "synval"}


def _usable(truth: dict) -> bool:
    if truth.get("flags"):
        return False
    return all((truth.get(s) or {}).get("state") != "unreadable" for s in ("left", "right"))


def build() -> dict:
    rng = random.Random(SEED)
    roles: dict[str, str] = {}
    main = json.loads((paths.STUDY / "data/manifest.json").read_text())["frames"]
    truth = json.loads((paths.STUDY / "ground_truth.json").read_text())["frames"]
    groups: dict[str, list[str]] = defaultdict(list)
    for row in main:
        rid, roll, kind = row["id"], row["roll"], row["kind"]
        if roll in LOCKED_MAIN:
            roles[rid] = "locked_main"
        elif roll == "bw0910":
            roles[rid] = "locked_bw"
        elif kind == "slide":
            roles[rid] = "locked_positive"
        elif kind == "ladder":
            roles[rid] = "unused"
        elif kind == "film":
            groups[SAME_STRIP.get(roll, roll)].append(rid)
    small = sorted(g for g in groups if g not in SAME_STRIP.values())
    val_main = sorted(rng.sample(small, VAL_MAIN_GROUPS))
    for g, ids in groups.items():
        for rid in ids:
            usable = _usable(truth.get(rid, {}))
            roles[rid] = ("realval" if g in val_main else "compose") if usable else "unused"
    for row in main:                     # dups follow the frame they repeat
        if row["kind"] == "dup" and row["id"] not in roles:
            roles[row["id"]] = roles.get(row.get("same_as") or "", "unused")

    lib2 = json.loads((paths.STUDY_LIB2 / "data/manifest.json").read_text())["frames"]
    pictures = json.loads((paths.STUDY_LIB2 / "pictures.json").read_text())
    test2 = json.loads((paths.STUDY_LIB2 / "split.json").read_text())
    lib2_truth = json.loads((paths.STUDY_LIB2 / "ground_truth.json").read_text())["frames"]
    dev_pictures = sorted(p for p in pictures if p not in set(test2["test2_pictures"]))
    val_lib2 = sorted(rng.sample(dev_pictures, VAL_LIB2_PICTURES))
    picture_of = {fid: p for p, fids in pictures.items() for fid in fids}
    for row in lib2:
        rid = row["id"]
        if row["split"] == "test":
            roles[rid] = "locked_test2"
            continue
        p = picture_of.get(rid if row["kind"] != "dup" else (row.get("same_as") or rid))
        if p is None:
            roles[rid] = "unused"
            continue
        usable = row["kind"] == "dup" or _usable(lib2_truth.get(rid, {}))
        roles[rid] = ("realval" if p in val_lib2 else "compose") if usable else "unused"
    for extra in POSITIVE_EXTRA:
        roles[extra] = "locked_positive"

    counts: dict[str, int] = defaultdict(int)
    for role in roles.values():
        counts[role] += 1
    return {
        "about": __doc__.split("\n\n")[0] + f" Seed {SEED}.",
        "seed": SEED,
        "main_groups": {g: sorted(ids) for g, ids in sorted(groups.items())},
        "realval_main_groups": val_main,
        "realval_lib2_pictures": val_lib2,
        "personal_rolls": PERSONAL,
        "counts": dict(sorted(counts.items())),
        "frames": dict(sorted(roles.items())),
    }


def main() -> int:
    if paths.SPLIT.exists() and "--force" not in sys.argv:
        print(f"{paths.SPLIT} exists: the split is frozen", file=sys.stderr)
        return 1
    split = build()
    paths.SPLIT.write_text(json.dumps(split, indent=1) + "\n")
    print(json.dumps({k: split[k] for k in ("realval_main_groups", "realval_lib2_pictures",
                                             "counts")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
