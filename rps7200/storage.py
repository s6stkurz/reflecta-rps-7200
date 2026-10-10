"""What the driver may make on disk, and whether a job's files will fit.

Below everything that writes -- the library, the driver, the session, the
tools -- and above nothing: no device, no pixels, no records. Each rule here
is asked before a job moves anything, because every failure it prevents used
to show itself only after the pass, as an entry kept in a temporary folder or
a copy that was not written, with the film already moved and the minutes
spent.

**Folders.** Every writer made whatever was missing with
``mkdir(parents=True)``. A drive that is not plugged in failed only after the
pass, and worse, a folder whose parent was still there was made again as a
plain local one: the remembered output folder is iCloud's, and had iCloud
taken it away, the next scan would have recreated it on C: without a word --
as would a folder on a stick whose drive letter now belongs to another, or an
empty mount point. So a configured root -- the library, the rolls, the
reference's folder -- may have its last folder made and never its parents
(`make_root`); a folder below one that exists is made as deep as it needs
(`make_within`); and the output folder is never made by a job at all
(`require_dir`). Stefan's call, 2026-10-09: "last folder only, output never".
There is no flag that makes parents, typed or not: a command line replayed
from the shell's history with a drive letter now held by another stick is the
very case this exists for.

**Room.** `check_space` is the one test of free space, whatever the job:
each disk a job writes to must keep `SPACE_RESERVE` free beyond what the job
can put there, and a disk whose room cannot be read is said, never passed as
having room -- an unplugged drive used to be skipped, and a roll on it passed.

**Path length.** On Windows a full path past 259 characters is refused by
much of what reads these files next -- Explorer, NegPy -- whatever Python
manages with long paths on, and it failed after the film had moved, as a
WinError that named no length. `longest_too_long` says it before.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

#: Kept free on every disk a job writes to, beyond everything the job can put
#: there. Stefan's call, 2026-10-09: refuse below one times what a job
#: writes plus this, warn below twice -- for every job that writes, a
#: compaction among them. The library, the rolls and the output folder all
#: share C: here, and without a margin a roll could run it down to nothing.
#: 2 GB is about what one 7200 dpi compaction needs beside its entry while it
#: runs, 1.85 times a pass's pixels; but a compaction is held to it like any
#: other job and never spends it (`library._room_to_compact`), so an entry
#: filed on a disk this tight stays plain and complete until there is room.
#: A long roll stops about one frame earlier for it. Read at each call, so a
#: test can set it.
SPACE_RESERVE = 2 * 10**9

#: The longest full path Windows takes without long paths: MAX_PATH less the
#: terminating null. Checked whatever the long-path setting says, because
#: the programs that read these files next are not all long-path aware.
WINDOWS_PATH_LIMIT = 259


class DriveMissing(OSError):
    """A folder that is not there and is not this driver's to make: a
    configured root whose parent has gone, or the output folder itself.

    An OSError, so every caller that already copes with a disk refusing a
    file -- the spool's fall back to the system's temporary folder, a copy
    not written, a failed job -- copes with this the same way."""


def _is_dir(path: Path) -> bool:
    """Whether ``path`` is a folder. Raises OSError when it cannot be told:
    on Python 3.12 a folder whose name cannot be looked at -- access denied,
    a network share dropped -- raises rather than says no."""
    return path.is_dir()


def _first_missing(path: Path) -> Path:
    """The topmost folder of ``path`` that is not there: the drive itself
    for a stick that is not plugged in, the folder that went for one that
    was taken away."""
    path = Path(os.path.abspath(path))
    missing = path
    for parent in path.parents:
        try:
            if parent.exists():
                break
        except OSError:
            break
        missing = parent
    return missing


def missing_root(path: str | Path) -> str | None:
    """Why ``path`` cannot be made as a root, or None when it can.

    None when it is a folder already, or when its parent is one, so that
    making it makes its last folder only. Creates nothing: a pre-flight asks
    this of every root a job writes to before the job moves anything.
    """
    path = Path(path)
    try:
        if _is_dir(path):
            return None
        if path.exists():
            return f"{path} is a file, not a folder"
        if _is_dir(path.parent):
            return None
        if path.parent.exists():
            return f"{path}: {path.parent} is a file, not a folder"
    except OSError as exc:
        return f"{path} cannot be looked at ({exc})"
    return (f"{path}: {_first_missing(path.parent)} is not there -- is the "
            "drive mounted? Nothing here makes it, so a folder on a missing "
            "drive is never made on another. Create it first if it is new.")


def make_root(path: str | Path) -> Path:
    """Make a configured root's last folder, and never its parents.

    Raises DriveMissing, making nothing, where its parent is not there
    (`missing_root`). There is deliberately no way to ask for the parents:
    see the module's docstring.
    """
    path = Path(path)
    said = missing_root(path)
    if said is not None:
        raise DriveMissing(said)
    path.mkdir(exist_ok=True)
    return path


def require_dir(path: str | Path,
                remedy: str = "Choose the output folder again, or clear it"
                ) -> Path:
    """``path``, which must be a folder already; nothing makes it.

    The output folder's rule. Its parent is always there -- the folder iCloud
    keeps is in the user's own -- so making only the last folder would still
    have made it again on C: once iCloud took it away. ``remedy`` is what the
    refusal tells the operator to do.
    """
    path = Path(path)
    try:
        if _is_dir(path):
            return path
        if path.exists():
            raise DriveMissing(f"{path} is a file, not a folder. {remedy}")
    except DriveMissing:
        raise
    except OSError as exc:
        raise DriveMissing(f"{path} cannot be looked at ({exc})") from exc
    raise DriveMissing(f"{path} is not there -- is the drive mounted? "
                       f"{remedy}")


def inside(folder: str | Path, root: str | Path) -> bool:
    """Whether ``folder`` is ``root`` or lies below it: as written, or once
    links are followed.

    Both, because one folder has two names wherever a link is on the way --
    a rolls folder that is a link or a junction, macOS's /tmp, which is
    /private/tmp -- and the window finds a sheet's folder by the second
    (`_roll_folder`). Asked by the first alone, a folder the window had
    called inside was refused, and the sheet's approved.json not written.
    """
    folder, root = Path(folder), Path(root)
    try:
        Path(os.path.abspath(folder)).relative_to(os.path.abspath(root))
        return True
    except ValueError:
        pass
    try:
        folder.resolve().relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def make_within(root: str | Path, folder: str | Path) -> Path:
    """Make ``folder``, which lies below ``root`` (`inside`): ``root`` by
    `make_root`, and everything between the two as deep as it needs. Raises
    ValueError for a folder outside ``root``, and DriveMissing where
    ``root``'s parent is not there."""
    root, folder = Path(root), Path(folder)
    if not inside(folder, root):
        raise ValueError(f"{folder} is not inside {root}")
    make_root(root)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def nearest_existing(path: str | Path) -> Path:
    """The nearest folder of ``path`` that is there: a roll's folder is made
    only once the roll starts, and the disk it will be on is its parent's."""
    path = Path(os.path.abspath(path))
    while path.parent != path:
        try:
            if path.exists():
                break
        except OSError:
            pass
        path = path.parent
    return path


def _device(where: Path) -> Any:
    """Which disk ``where`` is on: its volume's number (the volume serial on
    Windows). Raises OSError where it cannot be read."""
    return os.stat(where).st_dev


def same_disk(a: str | Path, b: str | Path) -> bool:
    """Whether ``a`` and ``b`` -- or the nearest folders of each that are
    there (`nearest_existing`) -- are on one disk. False where either
    cannot be told: a place whose disk has gone shares no room with
    anything."""
    try:
        return _device(nearest_existing(a)) == _device(nearest_existing(b))
    except OSError:
        return False


def check_space(needs: Iterable[tuple[Any, int]], factor: float = 1.0,
                reserve: float | None = None) -> tuple[list[str], list[str]]:
    """Each disk too full for ``needs``, and each whose room cannot be read.

    ``needs`` are ``(place, bytes)``. Places on one disk are added together:
    the library and the rolls folder side by side share its space. A disk is
    short when its free space is under ``factor`` times what is put on it
    plus ``reserve`` (`SPACE_RESERVE` unless given). Returns ``(short,
    unknown)``, each said for a person: a disk that cannot be read -- a drive
    not plugged in, a share that has gone -- is never passed as having room.
    """
    reserve = SPACE_RESERVE if reserve is None else reserve
    disks: dict[Any, list] = {}
    unknown: list[str] = []
    for place, need in needs:
        where = nearest_existing(place)
        try:
            device = _device(where)
        except OSError as exc:
            unknown.append(f"cannot tell how much room {place} has ({exc})")
            continue
        disk = disks.setdefault(device, [where, 0, []])
        disk[1] += need
        if str(place) not in disk[2]:
            disk[2].append(str(place))
    short: list[str] = []
    for where, need, places in disks.values():
        try:
            free = shutil.disk_usage(where).free
        except OSError as exc:
            unknown.append(f"cannot tell how much room {' and '.join(places)} "
                           f"has ({exc})")
            continue
        if free < factor * need + reserve:
            kept = (f", and {reserve / 1e9:.0f} GB is kept free" if reserve
                    else "")
            short.append(f"{free / 1e9:.1f} GB free on the disk holding "
                         f"{' and '.join(places)}, where this can need up to "
                         f"{need / 1e9:.1f} GB{kept}")
    return short, unknown


def longest_too_long(paths: Iterable[Any],
                     limit: int = WINDOWS_PATH_LIMIT) -> list[str]:
    """Each of ``paths`` too long for Windows, longest first, said with its
    length and the limit; nothing anywhere else.

    The paths are a job's longest predictions, made from the real name
    functions and their real bounds before anything moves. Refused whatever
    Windows' long-path setting: Python would write them with it on, and
    Explorer or NegPy would then not open them.
    """
    if sys.platform != "win32":
        return []
    lengths = sorted(((len(os.path.abspath(p)), os.path.abspath(p))
                      for p in paths), reverse=True)
    # Said by folder: the file names in a prediction are their longest
    # forms -- a temporary name with a twenty-digit thread number -- and
    # mean nothing to anyone reading the refusal.
    return [f"a file in {Path(path).parent} can reach {length} characters, "
            f"past the {limit} Windows and the programs that read these "
            "files allow -- choose a shorter folder or roll name"
            for length, path in lengths if length > limit]
