"""What every key in the window does, and how it is allowed to be changed.

The table lives here rather than in `tools/gui.py` for the reason `settings.py`
does: it is data with rules about it, those rules are worth testing, and none of
them need Tk. What is in `gui.py` is the binding and the editor.

**No key drives the scanner.** Not scanning, not calibrating, not moving film.
`on_scan`, `on_prescan` and the transport buttons submit their job immediately,
with no confirmation -- they are behind buttons that have to be reached for, and
CLAUDE.md is explicit that presence is not permission. A slip on a keyboard is
not a decision to spend four minutes of hardware or to move somebody's negative,
and there is no undo for either. `stop` is the exception: `request_stop` is
cooperative, finishes the pass already running, and is always safe.

`test_shortcuts.py` holds that line as a test rather than a convention.

A binding is a Tk sequence string -- `"<Key-r>"`, `"<Left>"`, `"<Command-Key-s>"`
-- because that is what `widget.bind` takes and what a captured `<KeyPress>` can
be turned back into. An empty string means the action has no key.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

#: The modifier a platform expects for its own shortcuts. Command on a Mac,
#: Control everywhere else. Decided once, so a settings file written on one
#: machine does not read as a conflict on another.
ACCEL = "Command" if sys.platform == "darwin" else "Control"

#: The three windows that take keys. A sequence may be used once in each --
#: `<Left>` means something different and obvious in all three -- so conflicts
#: are only ever looked for within a scope.
SCOPES = ("window", "sheet", "adjuster")

SCOPE_NAMES = {
    "window": "The main window",
    "sheet": "The contact sheet",
    "adjuster": "The frame position window",
}


@dataclass(frozen=True)
class Action:
    """One thing a key can do."""

    id: str                                  # "rotate_right"
    scope: str                               # one of SCOPES
    label: str                               # what the editor shows
    default: str                             # a Tk sequence, or "" for none


ACTIONS: tuple[Action, ...] = (
    # -- the main window ---------------------------------------------------
    #
    # A letter or a digit always carries the platform's modifier, the way any
    # other application's do. Bare letters were quicker and they were wrong:
    # they collide with typing, they read as a private convention rather than
    # a shortcut, and a modified key can be pressed without first checking
    # where the focus is.
    #
    # Arrows, Home, End, Return, Space and Escape stay bare. Those are already
    # what they look like everywhere else, and a modifier on them is friction
    # for nothing.
    Action("previous_pass", "window", "Previous pass", "<Left>"),
    Action("next_pass", "window", "Next pass", "<Right>"),
    Action("first_pass", "window", "First pass", "<Home>"),
    Action("last_pass", "window", "Last pass", "<End>"),
    Action("rotate_right", "window", "Rotate right 90°", f"<{ACCEL}-Key-r>"),
    Action("rotate_left", "window", "Rotate left 90°", f"<{ACCEL}-Key-R>"),
    Action("rotate_180", "window", "Rotate 180°", f"<{ACCEL}-Key-u>"),
    Action("straighten", "window", "Straighten", f"<{ACCEL}-Key-0>"),
    Action("flip", "window", "Flip left-right", f"<{ACCEL}-Key-m>"),
    Action("save_as", "window", "Save as ...", f"<{ACCEL}-Key-s>"),
    Action("show_prescan", "window", "Show this pass's prescan",
           f"<{ACCEL}-Key-p>"),
    Action("delete_pass", "window", "Delete this pass", f"<{ACCEL}-BackSpace>"),
    # `equal` rather than `plus`: + needs Shift on most layouts, and ⌘= is
    # what a hand actually presses and what other applications accept.
    Action("zoom_in", "window", "Zoom in", f"<{ACCEL}-Key-equal>"),
    Action("zoom_out", "window", "Zoom out", f"<{ACCEL}-Key-minus>"),
    Action("zoom_fit", "window", "Fit the picture", f"<{ACCEL}-Key-f>"),
    Action("zoom_actual", "window", "One scanned pixel per screen pixel",
           f"<{ACCEL}-Key-1>"),
    Action("invert", "window", "Invert the picture", f"<{ACCEL}-Key-i>"),
    Action("channel_next", "window", "Next channel view", f"<{ACCEL}-Key-c>"),
    Action("channel_previous", "window", "Previous channel view",
           f"<{ACCEL}-Key-C>"),
    Action("contact_sheet", "window", "Open the contact sheet",
           f"<{ACCEL}-Key-k>"),
    Action("stop", "window", "Stop after this pass", "<Escape>"),
    Action("shortcuts", "window", "Edit these shortcuts", f"<{ACCEL}-Key-comma>"),

    # -- the contact sheet -------------------------------------------------
    #
    # The turns and the flip are the same keys as the main window's, because
    # they do the same thing to a picture and only the picture differs. Each
    # fires in its own window, so they do not collide.
    Action("sheet_left", "sheet", "Select the frame to the left", "<Left>"),
    Action("sheet_right", "sheet", "Select the frame to the right", "<Right>"),
    Action("sheet_up", "sheet", "Select the frame above", "<Up>"),
    Action("sheet_down", "sheet", "Select the frame below", "<Down>"),
    Action("sheet_toggle", "sheet", "Scan this frame, or do not", "<space>"),
    Action("sheet_adjust", "sheet", "Set where this frame sits", "<Return>"),
    Action("sheet_all", "sheet", "Scan every frame", f"<{ACCEL}-Key-a>"),
    Action("sheet_none", "sheet", "Scan none of them", f"<{ACCEL}-Key-n>"),
    Action("sheet_rotate_right", "sheet", "Rotate this frame right",
           f"<{ACCEL}-Key-r>"),
    Action("sheet_rotate_left", "sheet", "Rotate this frame left",
           f"<{ACCEL}-Key-R>"),
    Action("sheet_rotate_180", "sheet", "Rotate this frame 180°",
           f"<{ACCEL}-Key-u>"),
    Action("sheet_straighten", "sheet", "Straighten this frame",
           f"<{ACCEL}-Key-0>"),
    Action("sheet_flip", "sheet", "Flip this frame", f"<{ACCEL}-Key-m>"),
    Action("sheet_show", "sheet", "Show this frame in the preview",
           f"<{ACCEL}-Key-p>"),
    Action("sheet_close", "sheet", "Close the sheet", "<Escape>"),

    # -- the frame position window -----------------------------------------
    Action("adjust_left", "adjuster", "Move the film one step left", "<Left>"),
    Action("adjust_right", "adjuster", "Move the film one step right", "<Right>"),
    Action("adjust_accept", "adjuster", "Keep this frame and go to the next",
           "<Return>"),
    Action("adjust_previous", "adjuster", "Previous frame", "<Shift-Left>"),
    Action("adjust_next", "adjuster", "Next frame", "<Shift-Right>"),
    Action("adjust_centre", "adjuster", "Put it back where it was surveyed",
           f"<{ACCEL}-Key-c>"),
    Action("adjust_toggle", "adjuster", "Scan this frame, or do not", "<space>"),
    Action("adjust_close", "adjuster", "Close", "<Escape>"),
)

#: Actions that must never appear above, checked by a test rather than trusted
#: to a reading of the table. See the module docstring.
NEVER_BOUND = (
    "on_scan", "on_prescan", "on_roll", "on_calibrate", "ask_to_calibrate",
    "on_move_frames", "on_nudge", "on_abort", "on_scan_chosen",
)

_BY_ID = {action.id: action for action in ACTIONS}


def defaults() -> dict[str, str]:
    """Every action's key, as shipped."""
    return {action.id: action.default for action in ACTIONS}


def resolve(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """The keys in force: the defaults, with the operator's changes over them.

    Ids that no longer exist are dropped rather than carried, so a settings
    file outlives a rename. Values that are not strings are ignored, because
    this file is meant to be edited by hand and a mistake in it should cost a
    key rather than the window.
    """
    keys = defaults()
    for action_id, sequence in (overrides or {}).items():
        if action_id in keys and isinstance(sequence, str):
            keys[action_id] = sequence
    return keys


def overrides_from(keys: dict[str, str]) -> dict[str, str]:
    """Only what differs from the defaults, for writing to the settings file.

    Storing the whole table would freeze every key at whatever it was the day
    the file was first written: a default improved later would never reach a
    machine that had already run the window once. What the operator changed is
    the only part that is theirs.
    """
    shipped = defaults()
    return {k: v for k, v in keys.items()
            if k in shipped and v != shipped[k]}


#: The modifiers that make a key unmistakably a shortcut rather than typing.
#: Shift is not one of them: Shift-R is a capital R.
_REAL_MODIFIERS = ("Control", "Command", "Alt", "Meta")


def is_modified(sequence: str) -> bool:
    """Whether this key could never be confused with typing.

    Which decides whether it is allowed to fire while a text field has the
    focus. ⌘S in the middle of typing a subject line is a save, and every
    other application treats it as one; a bare `s` is an `s`.
    """
    parts = sequence.strip("<>").split("-")[:-1]
    return any(p in _REAL_MODIFIERS for p in parts)


def scope_of(action_id: str) -> str:
    action = _BY_ID.get(action_id)
    return action.scope if action else ""


def action(action_id: str) -> Action | None:
    return _BY_ID.get(action_id)


def in_scope(keys: dict[str, str], scope: str) -> dict[str, str]:
    """`{sequence: action id}` for one window, skipping the unbound."""
    return {sequence: action_id
            for action_id, sequence in keys.items()
            if sequence and scope_of(action_id) == scope}


def conflicts(keys: dict[str, str]) -> dict[str, list[str]]:
    """Sequences claimed by more than one action **in the same scope**.

    Across scopes is not a conflict and is usually right: `<Left>` walks the
    filmstrip, moves the selection in the contact sheet, and steps the film in
    the position window, and each fires only in its own window.
    """
    seen: dict[tuple[str, str], list[str]] = {}
    for action_id, sequence in keys.items():
        if not sequence:
            continue
        seen.setdefault((scope_of(action_id), sequence), []).append(action_id)
    return {sequence: sorted(ids)
            for (_scope, sequence), ids in seen.items() if len(ids) > 1}


#: Modifier bits as Tk reports them in `event.state`, in the order a sequence
#: names them. Command is Mod1 on Aqua; Alt is Mod2 there and Mod1 on X11,
#: which is why only the two that are the same everywhere are offered besides
#: it -- a modifier that means different things on different machines is worse
#: than one that is missing.
_MODIFIERS = (
    (0x0004, "Control"),
    (0x0008, "Command") if sys.platform == "darwin" else (0x20000, "Alt"),
    (0x0001, "Shift"),
)

#: Keysyms that are only ever a modifier. Capturing one would make a shortcut
#: that can never fire.
_BARE = {
    "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
    "Meta_L", "Meta_R", "Super_L", "Super_R", "Caps_Lock", "Num_Lock",
    "Option_L", "Option_R", "Command_L", "Command_R",
}

#: Keysyms that name themselves rather than a character, so they are written
#: `<Left>` rather than `<Key-Left>`. Both forms work in Tk; one form in the
#: file keeps the editor's display and its conflict check honest.
_NAMED = {
    "Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next",
    "Return", "KP_Enter", "space", "BackSpace", "Delete", "Escape", "Tab",
    *(f"F{n}" for n in range(1, 21)),
}


def sequence_for(keysym: str, state: int = 0) -> str | None:
    """A bindable sequence from a captured key press, or None if unusable.

    None for a bare modifier: a shortcut that is only Shift can never fire, and
    accepting one would silently take the key away from whatever had it.
    """
    if not keysym or keysym in _BARE:
        return None
    parts = [name for bit, name in _MODIFIERS if state & bit]
    if keysym in _NAMED:
        # Shift is already in the keysym for a character, but not for a named
        # key: <Shift-Left> is a different sequence from <Left>.
        return f"<{'-'.join([*parts, keysym])}>"
    # A character carries its own case, so Shift would be named twice.
    parts = [p for p in parts if p != "Shift"]
    return f"<{'-'.join([*parts, 'Key', keysym])}>"


#: How a modifier reads to a person. A Mac shows symbols and no separators.
_SHOWN = (
    {"Command": "⌘", "Control": "⌃", "Shift": "⇧", "Alt": "⌥"}
    if sys.platform == "darwin" else
    {"Command": "Cmd", "Control": "Ctrl", "Shift": "Shift", "Alt": "Alt"}
)

#: And how a key does. Anything not here shows as itself.
_KEY_SHOWN = {
    "Left": "←", "Right": "→", "Up": "↑", "Down": "↓",
    "Return": "↩", "KP_Enter": "↩", "space": "Space",
    "BackSpace": "⌫", "Delete": "Del", "Escape": "Esc", "Tab": "Tab",
    "Prior": "PgUp", "Next": "PgDn", "plus": "+", "minus": "−",
    "equal": "=", "comma": ",", "period": ".", "slash": "/",
}


#: What Tk's own accelerator parser calls the keys that are not characters.
#: It matches these names and draws the glyph itself; a name it does not know
#: becomes the key equivalent verbatim and only its first character is drawn.
_TK_KEY_NAMES = {
    "Left": "Left", "Right": "Right", "Up": "Up", "Down": "Down",
    "Home": "Home", "End": "End", "Prior": "PageUp", "Next": "PageDown",
    "Return": "Return", "KP_Enter": "Enter", "space": "Space",
    "BackSpace": "Backspace", "Delete": "Delete", "Escape": "Escape",
    "Tab": "Tab",
}

#: Keysyms that name a character. Written as the character, because that is
#: what the menu should show and what Tk wants as the key equivalent.
_TK_KEY_CHARS = {
    "comma": ",", "period": ".", "minus": "-", "equal": "=", "plus": "+",
    "slash": "/", "backslash": "\\", "bracketleft": "[", "bracketright": "]",
    "semicolon": ";", "apostrophe": "'", "grave": "`",
}


def accelerator_text(sequence: str) -> str:
    """The same key in the form a Tk menu wants beside an item. "" for none.

    **Not** :func:`describe`, and the difference is not cosmetic. Tk parses
    this string itself: it looks for modifier *names* -- Command, Shift,
    Option, Control -- and for a key it recognises, and then draws the glyphs.
    Handing it a string that already holds ⌘ leaves it with no name it knows
    and a key equivalent several characters long, and it draws the first
    character and stops. The menu showed a lone ⌘ with no letter beside it.

    Joined with "-", which is the form Tk's own documentation uses. The one
    exception is the minus key itself, where that separator is also the key
    and "Command--" has no unambiguous reading; Tk splits on "+" as well, so
    that one is written "Command+-".
    """
    if not sequence:
        return ""
    parts = sequence.strip("<>").split("-")
    key = parts[-1]
    modifiers = [p for p in parts[:-1] if p != "Key"]
    # A capital letter carries its own Shift in the sequence; Tk needs it
    # named, or the glyph is missing from a shortcut that does need it held.
    if len(key) == 1 and key.isalpha() and key.isupper() and "Shift" not in modifiers:
        modifiers.append("Shift")
    name = _TK_KEY_NAMES.get(key) or _TK_KEY_CHARS.get(key) or (
        key.upper() if len(key) == 1 else key)
    # Control, Option, Shift, Command: the order the glyphs are read in.
    order = {"Control": 0, "Option": 1, "Alt": 1, "Shift": 2, "Command": 3}
    modifiers.sort(key=lambda m: order.get(m, 9))
    if sys.platform != "darwin":
        # Everywhere else Tk prints the string as it is given, so it has to be
        # the finished thing rather than something to be parsed.
        modifiers = ["Ctrl" if m == "Control" else m for m in modifiers]
    separator = "+" if name == "-" else "-"
    return separator.join([*modifiers, name])


def describe(sequence: str) -> str:
    """A sequence as it should read in the editor. "" for unbound."""
    if not sequence:
        return "—"
    parts = sequence.strip("<>").split("-")
    key = parts[-1]
    modifiers = [p for p in parts[:-1] if p != "Key"]
    shown = _KEY_SHOWN.get(key, key if len(key) > 1 else key.upper())
    if key.isalpha() and len(key) == 1 and key.isupper():
        modifiers = [*modifiers, "Shift"]
    joiner = "" if sys.platform == "darwin" else "+"
    return joiner.join([*(_SHOWN.get(m, m) for m in modifiers), shown])
