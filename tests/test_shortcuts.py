"""The key table, and the rules that keep it usable.

Pure: no Tk, no window. What is checked here is that the table is coherent --
every action reachable, no two fighting over a key in the same window -- and the
one rule that is not a matter of taste: **no key drives the scanner**.
"""
import pytest

from rps7200 import shortcuts


def test_no_key_drives_the_scanner():
    """A slip on the keyboard is not a decision to spend four minutes of
    hardware or to move somebody's negative, and there is no undo for either.
    `on_scan`, `on_prescan` and the transport buttons submit immediately with
    no confirmation -- they are behind buttons that have to be reached for."""
    ids = {a.id for a in shortcuts.ACTIONS}
    assert not ids & set(shortcuts.NEVER_BOUND)
    # The list is only useful if it names things that really exist, or it
    # quietly protects nothing.
    assert "on_scan" in shortcuts.NEVER_BOUND
    assert "on_nudge" in shortcuts.NEVER_BOUND
    # What each action actually *calls* is the thing that matters, and the
    # dispatch table is in the window -- so that half is checked in
    # test_gui.py, against the methods rather than against these names.


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


def test_the_window_is_the_only_scope_with_an_accelerator_default():
    """The two extra windows are transient and reached with the hands already
    on the picture; a modifier there is friction for nothing."""
    for action in shortcuts.ACTIONS:
        if action.scope != "window":
            assert shortcuts.ACCEL not in action.default, action.id
