"""What the window remembers between launches.

Small on purpose. Losing this file costs a few seconds of resetting controls, so
nothing here is allowed to be more important than that: a missing file, a
corrupt one, or a read-only directory all end with the window opening on its
defaults rather than not opening.

    values = settings.load()
    values["controls"]["dpi"] = "3600"
    settings.save(values)

`RPS7200_SETTINGS` moves the file. It is one JSON object, meant to be readable
and editable by hand.
"""
from __future__ import annotations

import json
import time
import os
from pathlib import Path
from typing import Any

#: Where it lives unless told otherwise. Beside the library, like the
#: calibration cache -- per-machine state that belongs to this checkout.
DEFAULT_PATH = Path("gui-settings.json")

#: Environment variable that moves it, so a test never writes the real one.
PATH_ENV = "RPS7200_SETTINGS"

#: The shape, and what an empty file means. Anything not listed is dropped on
#: load: a key that no longer exists in the window should not survive in the
#: file for ever.
#:
#: `rolls` is per-roll state the roll folder itself must not carry -- when each
#: was last opened, keyed by folder name. It lives here because opening a roll
#: to look at it should not modify it, and a roll on a read-only backup should
#: still open.
#:
#: `sheet` is the contact sheet's decisions -- which frames are ticked, where
#: each sits, which way up -- keyed by roll folder name, for the same reason
#: and with the same caveat. It is a *fallback*, not the record: a commissioned
#: roll writes its decisions into its own manifest, and `open_roll` restores
#: from there because the manifest still describes that roll a year later while
#: this file has moved on to other film. What this section covers is the gap
#: before a walk has been commissioned, where the manifest does not exist yet
#: and closing the sheet would otherwise throw the decisions away.
#:
#: `last_roll` is the folder name of the roll of strips walked or opened
#: last, which the Roll panel offers to continue after a restart. The roll
#: box itself is not kept -- a stale name there would file today's strips
#: under yesterday's film -- so this is only ever offered, never used until
#: Continue is pressed (Stefan's call, 2026-10-10).
SECTIONS = ("controls", "film", "output", "window", "presets", "shortcuts",
            "rolls", "sheet", "last_roll")

#: The sections that hold a string rather than a mapping.
TEXT_SECTIONS = ("output", "last_roll")


def path(where: str | Path | None = None) -> Path:
    """The settings file: an explicit path, the environment, or the default."""
    if where is not None:
        return Path(where)
    from_env = os.environ.get(PATH_ENV, "").strip()
    return Path(from_env) if from_env else DEFAULT_PATH


def load(where: str | Path | None = None, say=None,
         unread: list[str] | None = None) -> dict[str, Any]:
    """Everything remembered, as a dict with every section present.

    Never raises. A file that is missing, unreadable, not JSON, or JSON of the
    wrong shape all give the same answer as a fresh install, because the window
    opening matters more than the contents of this file.

    ``say`` hears when a file was there and could not be read: the window
    then opened on defaults, with its remembered settings and any sheet not
    yet commissioned moved aside, and nothing on screen said why.

    A file the disk will not let be read -- held a moment by Windows'
    Defender, its indexer or a backup tool -- is asked again over
    `REPLACE_RETRY_S`, as a save's replace is. If it still cannot be read
    it is **left where it is**, not moved aside, and named in ``unread``:
    it is no worse than it was, and a valid file moved aside for a passing
    hold meant the window's next save wrote its defaults over the place it
    had been -- presets, shortcuts and the sheet decisions of other walks
    with it. The window then writes nothing until it can read the file
    (`ScannerGui._remember`). Only a file that was read and makes no sense
    -- not JSON, not UTF-8, the wrong shape -- is moved aside at once.
    """
    from .session import REPLACE_RETRY_S                    # noqa: PLC0415

    blank: dict[str, Any] = {name: {} for name in SECTIONS}
    for name in TEXT_SECTIONS:
        blank[name] = ""
    target = path(where)
    for wait in (*REPLACE_RETRY_S, None):
        try:
            stored = json.loads(target.read_text(encoding="utf-8"))
            break
        except FileNotFoundError:
            return blank
        except (json.JSONDecodeError, ValueError) as exc:
            # Read, and nonsense: UnicodeDecodeError is a ValueError too.
            _said_aside(target, _keep_aside(target), exc, say)
            return blank
        except OSError as exc:
            if wait is not None:
                time.sleep(wait)
                continue
            why = (f"{target} could not be read ({type(exc).__name__}: "
                   f"{exc}); left as it is, and not written over until it "
                   "can be read")
            if unread is not None:
                unread.append(why)
            if say is not None:
                say(why + " -- the window opened with its defaults")
            return blank
    if not isinstance(stored, dict):
        _said_aside(target, _keep_aside(target), "not a JSON object", say)
        return blank
    for name in SECTIONS:
        value = stored.get(name)
        if name in TEXT_SECTIONS:
            blank[name] = value if isinstance(value, str) else ""
        elif isinstance(value, dict):
            blank[name] = value
    return blank


def _said_aside(target: Path, aside: Path | None, why, say) -> None:
    """Tell ``say``, if there is one, what `load` did with an unreadable file."""
    if say is None:
        return
    say(f"{target} could not be read ({why}), so the window opened with its "
        "defaults; "
        + (f"the file is kept as {aside.name}, where what it held can be "
           "recovered by hand" if aside is not None else
           "it could not be moved aside either, and the next save replaces "
           "it"))


def _keep_aside(target: Path) -> Path | None:
    """Move an unreadable settings file aside before it is written over.

    The window opens with defaults either way. But the next save wrote the
    whole file, and whatever the damaged one still held -- a contact sheet's
    unsaved decisions among it -- was gone with no word said.

    Named by the time in UTC, and never over an earlier one: two in the same
    second took one name, and the replace put the second over the first.
    """
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    aside = target.with_name(f"{target.name}.unreadable-{stamp}")
    n = 2
    while aside.exists():
        aside = target.with_name(f"{target.name}.unreadable-{stamp}-{n}")
        n += 1
    try:
        target.replace(aside)
        return aside
    except OSError:
        return None


def save(values: dict[str, Any], where: str | Path | None = None,
         why: list[str] | None = None) -> Path | None:
    """Write it, and say where. Returns None if it could not be written.

    Written whole and replaced at the end, so an interrupted write leaves the
    previous settings rather than half of the new ones -- and on the disk
    before it is renamed in, its folder synced after, so a power cut leaves
    one or the other too. For a walk not yet commissioned this file is the
    only record of the contact sheet's decisions.

    The replace waits out a brief hold, as `session.write_manifest` does. On
    Windows it fails outright while any handle on the file lacks
    FILE_SHARE_DELETE -- Defender and the indexer take one on every new file
    -- and the window writes this one often: as a contact sheet's decisions
    are made, not only when it closes.

    ``why``, where given, is told why a save failed (``"PermissionError:
    ..."``), so Quit can say what went wrong rather than only that it did.
    """
    # The fsync, the folder's sync and the manifests' waits, each from its
    # one home.
    from .library import _fsync, _sync_dir                  # noqa: PLC0415
    from .session import REPLACE_RETRY_S                    # noqa: PLC0415

    target = path(where)
    temporary = target.with_name(target.name + ".part")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(values, indent=2, sort_keys=True)
        with open(temporary, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            _fsync(fh.fileno())
        for wait in (*REPLACE_RETRY_S, None):
            try:
                temporary.replace(target)
                break
            except PermissionError:
                if wait is None:
                    raise
                time.sleep(wait)
        _sync_dir(target.parent)
        return target
    except (OSError, TypeError, ValueError) as exc:
        if why is not None:
            why.append(f"{type(exc).__name__}: {exc}")
        # Nothing half-written left beside the settings: a full disk or a
        # replace refused for good left the `.part` there for ever.
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return None
