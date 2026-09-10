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
SECTIONS = ("controls", "film", "output", "window", "presets")


def path(where: str | Path | None = None) -> Path:
    """The settings file: an explicit path, the environment, or the default."""
    if where is not None:
        return Path(where)
    from_env = os.environ.get(PATH_ENV, "").strip()
    return Path(from_env) if from_env else DEFAULT_PATH


def load(where: str | Path | None = None) -> dict[str, Any]:
    """Everything remembered, as a dict with every section present.

    Never raises. A file that is missing, unreadable, not JSON, or JSON of the
    wrong shape all give the same answer as a fresh install, because the window
    opening matters more than the contents of this file.
    """
    blank: dict[str, Any] = {name: {} for name in SECTIONS}
    blank["output"] = ""
    try:
        stored = json.loads(path(where).read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return blank
    if not isinstance(stored, dict):
        return blank
    for name in SECTIONS:
        value = stored.get(name)
        if name == "output":
            blank[name] = value if isinstance(value, str) else ""
        elif isinstance(value, dict):
            blank[name] = value
    return blank


def save(values: dict[str, Any], where: str | Path | None = None) -> Path | None:
    """Write it, and say where. Returns None if it could not be written.

    Written whole and replaced at the end, so an interrupted write leaves the
    previous settings rather than half of the new ones.
    """
    target = path(where)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".part")
        temporary.write_text(json.dumps(values, indent=2, sort_keys=True))
        temporary.replace(target)
        return target
    except (OSError, TypeError, ValueError):
        return None
