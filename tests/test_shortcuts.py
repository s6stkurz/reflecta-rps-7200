"""The key table, and the rules that keep it usable.

Pure: no Tk, no window. What is checked here is that the table is coherent --
every action reachable, no two fighting over a key in the same window -- and the
one rule that is not a matter of taste: **no key drives the scanner**.
"""
import sys

import pytest

from rps7200 import shortcuts


def test_no_key_moves_film_or_calibrates():
    """There is no undo for a moved negative or a wedged device, so these have
    no key on any terms -- a confirmation is not enough where that is what is
    being risked.

    Starting a *pass* is different and does have keys, because each asks first
    and says what the run will cost. That half is checked in `test_gui.py`,
    against what the actions actually call rather than against these names."""
    ids = {a.id for a in shortcuts.ACTIONS}
    assert not ids & set(shortcuts.NEVER_BOUND)
    # The list is only useful if it names things that really exist, or it
    # quietly protects nothing.
    assert "on_nudge" in shortcuts.NEVER_BOUND
    assert "on_move_frames" in shortcuts.NEVER_BOUND
    assert "on_calibrate" in shortcuts.NEVER_BOUND
    assert "on_abort" in shortcuts.NEVER_BOUND, "abandoning a read costs a wedge"


def test_starting_a_pass_has_a_key_and_says_it_asks():
    """The keys that reach the hardware. Their labels carry "asks first",
    because the editor's list is where anyone learns what a key does and a key
    that starts a three-hour roll should not look like one that turns a
    picture."""
    for action_id in ("prescan", "scan", "roll"):
        action = shortcuts.action(action_id)
        assert action is not None, action_id
        assert action.default, f"{action_id} has no key"
        assert "asks" in action.label.lower(), action_id


def test_stop_is_the_one_scanner_key_and_it_is_safe():
    """`request_stop` is cooperative -- it finishes the pass already running
    rather than abandoning a read, which is the act that costs a power cycle."""
    assert shortcuts.action("stop") is not None
    assert shortcuts.action("stop").default == "<Escape>"
    assert shortcuts.action("abort") is None, "force abort is never a key"


def test_the_defaults_do_not_fight_each_other():
    assert shortcuts.conflicts(shortcuts.defaults()) == {}


def test_the_same_key_in_two_windows_is_not_a_conflict():
    """`<Left>` walks the filmstrip, moves the contact sheet's selection and
    steps the film in the position window. Each fires only in its own."""
    keys = shortcuts.defaults()
    left = [i for i, s in keys.items() if s == "<Left>"]
    assert len(left) == 3, left
    assert {shortcuts.scope_of(i) for i in left} == set(shortcuts.SCOPES)
    assert shortcuts.conflicts(keys) == {}


def test_every_action_belongs_to_a_window_that_exists():
    for action in shortcuts.ACTIONS:
        assert action.scope in shortcuts.SCOPES, action.id
        assert action.label, action.id


def test_no_two_actions_share_an_id():
    ids = [a.id for a in shortcuts.ACTIONS]
    assert len(ids) == len(set(ids))


# -- what gets remembered ----------------------------------------------------


def test_a_change_survives_and_the_rest_stay_shipped():
    keys = shortcuts.resolve({"rotate_right": "<Key-z>"})
    assert keys["rotate_right"] == "<Key-z>"
    assert keys["rotate_left"] == shortcuts.action("rotate_left").default


def test_an_action_that_no_longer_exists_is_dropped():
    """A settings file outlives a rename, and a key for something gone is not
    a reason to refuse to open."""
    keys = shortcuts.resolve({"rotate_widdershins": "<Key-z>"})
    assert "rotate_widdershins" not in keys
    assert keys == shortcuts.defaults()


def test_nonsense_in_the_file_costs_a_key_rather_than_the_window():
    """`gui-settings.json` is meant to be edited by hand."""
    assert shortcuts.resolve({"rotate_right": 7})["rotate_right"] == \
        shortcuts.action("rotate_right").default
    assert shortcuts.resolve(None) == shortcuts.defaults()


def test_only_the_changes_are_written_down():
    """Storing the whole table would freeze every key at whatever it was the
    day the file was first written, so a default improved later would never
    reach a machine that had already run the window once."""
    keys = shortcuts.resolve({"flip": "<Key-x>"})
    assert shortcuts.overrides_from(keys) == {"flip": "<Key-x>"}
    assert shortcuts.overrides_from(shortcuts.defaults()) == {}


def test_what_is_written_reads_back_as_what_was_set():
    keys = shortcuts.resolve({"flip": "<Key-x>", "invert": ""})
    assert shortcuts.resolve(shortcuts.overrides_from(keys)) == keys


def test_an_unbound_action_is_an_override_not_a_default():
    """Clearing a key has to survive a restart, or it comes back."""
    keys = shortcuts.resolve({"invert": ""})
    assert keys["invert"] == ""
    assert shortcuts.overrides_from(keys) == {"invert": ""}


# -- capturing a key ---------------------------------------------------------


@pytest.mark.parametrize("keysym, state, expected", [
    ("r", 0, "<Key-r>"),
    ("R", 0x0001, "<Key-R>"),          # the case is already in the keysym
    ("Left", 0, "<Left>"),
    ("Left", 0x0001, "<Shift-Left>"),  # but a named key needs it said
    ("s", 0x0004, "<Control-Key-s>"),
    ("space", 0, "<space>"),
    ("Return", 0, "<Return>"),
])
def test_a_captured_press_becomes_a_sequence_tk_would_accept(keysym, state,
                                                             expected):
    assert shortcuts.sequence_for(keysym, state) == expected


@pytest.mark.parametrize("keysym", ["Shift_L", "Control_R", "Command_L", ""])
def test_a_bare_modifier_is_refused(keysym):
    """A shortcut that is only Shift can never fire, and accepting one would
    silently take the key away from whatever had it."""
    assert shortcuts.sequence_for(keysym, 0) is None


def test_a_captured_sequence_survives_being_described():
    """The editor shows what it captured; it must not show something else."""
    for keysym, state in (("r", 0), ("Left", 0x0001), ("s", 0x0004)):
        sequence = shortcuts.sequence_for(keysym, state)
        assert shortcuts.describe(sequence), sequence


def test_every_default_can_be_described():
    for action in shortcuts.ACTIONS:
        assert shortcuts.describe(action.default) not in ("", None), action.id


def test_an_unbound_action_says_so_rather_than_showing_nothing():
    assert shortcuts.describe("") == "—"


#: Keys that are already what they look like everywhere else, and carry no
#: modifier for that reason.
BARE = {"<Left>", "<Right>", "<Up>", "<Down>", "<Home>", "<End>",
        "<Return>", "<space>", "<Escape>", "<Shift-Left>", "<Shift-Right>"}


def test_every_letter_and_digit_carries_the_platform_modifier():
    """Bare letters were quicker and they were wrong: they collide with
    typing, they read as a private convention rather than as a shortcut, and a
    modified key can be pressed without first checking where the focus is."""
    for action in shortcuts.ACTIONS:
        if action.default in BARE:
            continue
        assert shortcuts.is_modified(action.default), (
            f"{action.id} is {action.default}, which is neither a navigation "
            "key nor a modified one")


def test_navigation_keys_stay_bare():
    """A modifier on an arrow is friction for nothing."""
    for action in shortcuts.ACTIONS:
        if action.default in BARE:
            assert not shortcuts.is_modified(action.default), action.id


def test_the_same_picture_operation_is_the_same_key_in_both_windows():
    """A turn does the same thing to a picture in the filmstrip and in the
    contact sheet; only the picture differs. Two keys for that would be two
    things to remember about one idea."""
    keys = shortcuts.defaults()
    for what in ("rotate_right", "rotate_left", "rotate_180", "straighten",
                 "flip"):
        assert keys[what] == keys[f"sheet_{what}"], what


@pytest.mark.parametrize("sequence, modified", [
    ("<Key-r>", False),
    ("<Key-R>", False),               # Shift-R is a capital R, not a shortcut
    ("<Left>", False),
    ("<Shift-Left>", False),
    ("<Command-Key-r>", True),
    ("<Control-Key-s>", True),
    ("<Command-BackSpace>", True),
    ("", False),
])
def test_only_a_real_modifier_makes_a_key_unmistakable(sequence, modified):
    """Which decides whether it may fire while a text field has the focus."""
    assert shortcuts.is_modified(sequence) is modified


# -- what a Tk menu wants beside an item -------------------------------------


@pytest.mark.parametrize("sequence, expected", [
    ("<Command-Key-r>", "Command-R"),
    ("<Command-Key-R>", "Shift-Command-R"),
    ("<Control-Key-s>", "Control-S" if sys.platform == "darwin" else "Ctrl-S"),
    ("<Command-BackSpace>", "Command-Backspace"),
    ("<Left>", "Left"),
    ("<Shift-Left>", "Shift-Left"),
    ("<space>", "Space"),
    ("<Return>", "Return"),
    ("<Command-Key-comma>", "Command-,"),
    ("", ""),
])
def test_the_menu_form_is_what_tk_can_parse(sequence, expected):
    """Tk looks for modifier names and a key it knows, then draws the glyphs
    itself. A name it does not recognise becomes the key equivalent verbatim
    and only its first character is drawn."""
    assert shortcuts.accelerator_text(sequence) == expected


def test_the_menu_form_never_contains_a_glyph():
    """Which is the bug: given ⌘R, Tk found no modifier name, took the whole
    string as a one-character key equivalent, and drew a lone ⌘."""
    for action in shortcuts.ACTIONS:
        shown = shortcuts.accelerator_text(action.default)
        for glyph in "⌘⌃⌥⇧←→↑↓↩⌫−":
            assert glyph not in shown, f"{action.id}: {shown}"


def test_the_minus_key_is_not_mistaken_for_a_separator():
    """Everything else uses "-", the form Tk's own documentation uses. The
    minus key cannot: it is the separator as well, and "Command--" has no
    unambiguous reading. Tk splits on "+" too, so that one alone uses it."""
    assert shortcuts.accelerator_text("<Command-Key-minus>") == "Command+-"
    assert shortcuts.accelerator_text("<Command-Key-equal>") == "Command-="


def test_the_editor_still_shows_the_readable_form():
    """Two forms on purpose: the editor draws its own label and should read
    the way the keyboard does.

    A Mac reads that as a glyph; nothing else has one, so off Aqua the
    readable form is the word. What holds everywhere is that the two differ
    -- the menu's form is for Tk, the editor's is for a person.
    """
    readable = "⌘" if sys.platform == "darwin" else "Cmd"
    assert readable in shortcuts.describe("<Command-Key-r>")
    assert readable not in shortcuts.accelerator_text("<Command-Key-r>")
