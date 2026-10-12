"""What the window remembers, and what it must never let stop it.

The whole file is a convenience: losing it costs a few seconds of resetting
controls. So every test here is really the same test -- that nothing about
reading or writing it can keep the window from opening.
"""
import json
import os

import pytest

from rps7200 import settings


def test_a_missing_file_reads_as_a_fresh_install(tmp_path):
    values = settings.load(tmp_path / "not-there.json")
    assert set(values) == set(settings.SECTIONS)
    assert values["controls"] == {} and values["output"] == ""


@pytest.mark.parametrize("rubbish", [
    "{ not json at all",
    "[1, 2, 3]",                                 # valid JSON, wrong shape
    '"a string"',
    "",
])
def test_rubbish_reads_as_a_fresh_install(tmp_path, rubbish):
    """A file edited by hand into nonsense must cost the settings, nothing more."""
    path = tmp_path / "gui.json"
    path.write_text(rubbish, encoding="utf-8")
    assert settings.load(path)["controls"] == {}


def test_a_section_of_the_wrong_type_is_dropped_not_kept(tmp_path):
    path = tmp_path / "gui.json"
    path.write_text(json.dumps({"controls": "not a dict", "output": 17}),
                    encoding="utf-8")
    values = settings.load(path)
    assert values["controls"] == {}
    assert values["output"] == ""


def test_what_goes_in_comes_back(tmp_path):
    path = tmp_path / "gui.json"
    settings.save({"controls": {"dpi": "3600", "ir": True},
                   "film": {"stock": "Kodak Gold 200"},
                   "output": "/tmp/scans",
                   "window": {"geometry": "1280x860+0+0"},
                   "presets": {"a": {"dpi": "600"}}}, path)
    values = settings.load(path)
    assert values["controls"] == {"dpi": "3600", "ir": True}
    assert values["film"]["stock"] == "Kodak Gold 200"
    assert values["output"] == "/tmp/scans"
    assert values["presets"]["a"]["dpi"] == "600"


def test_a_key_the_window_no_longer_has_does_not_survive_for_ever(tmp_path):
    """Sections are filtered on load, so a control removed from the window does
    not leave its value in the file being read back at every launch."""
    path = tmp_path / "gui.json"
    path.write_text(json.dumps({"controls": {"dpi": "600"}, "gone": {"x": 1}}),
                    encoding="utf-8")
    assert "gone" not in settings.load(path)


def test_an_unwritable_place_is_reported_not_raised(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"not a directory")
    assert settings.save({"controls": {}}, blocker / "gui.json") is None


def test_something_unserialisable_is_reported_not_raised(tmp_path):
    assert settings.save({"controls": {"x": object()}}, tmp_path / "gui.json") is None


def test_an_interrupted_write_leaves_the_previous_settings(tmp_path):
    """Written whole and moved into place, so a crash midway keeps what was
    there rather than half of what was coming."""
    path = tmp_path / "gui.json"
    settings.save({"controls": {"dpi": "600"}}, path)
    settings.save({"controls": {"x": object()}}, path)   # fails
    assert settings.load(path)["controls"] == {"dpi": "600"}


def test_the_environment_moves_the_file(tmp_path, monkeypatch):
    """So a test, or a second checkout, never writes the real one."""
    monkeypatch.setenv(settings.PATH_ENV, str(tmp_path / "elsewhere.json"))
    assert settings.path() == tmp_path / "elsewhere.json"


def test_an_explicit_path_beats_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv(settings.PATH_ENV, str(tmp_path / "env.json"))
    assert settings.path(tmp_path / "asked.json") == tmp_path / "asked.json"


def test_no_test_writes_the_checkouts_own_file(tmp_path):
    """What the variable is for, made true: conftest moves the file for every
    test, so a test that saves without naming a path -- the window does, from
    `_remember` -- cannot reach the one the operator's window restores from.
    Nothing here sets the variable; the suite does. The first assert is the
    one that must fail, so a broken fixture stops here rather than saving."""
    assert settings.path() != settings.DEFAULT_PATH
    written = settings.save({"controls": {"dpi": "600"}})
    assert written is not None and written.resolve() != (
        settings.DEFAULT_PATH.resolve())


def test_the_default_is_beside_the_library(monkeypatch):
    monkeypatch.delenv(settings.PATH_ENV, raising=False)
    assert settings.path() == settings.DEFAULT_PATH
    assert not os.path.isabs(settings.DEFAULT_PATH)


def test_the_contact_sheets_decisions_survive_a_round_trip(tmp_path):
    """Keyed by roll folder, because frame numbers only mean something within
    one strip. A section missing from `SECTIONS` is dropped on load without a
    word, so this is really a test that `sheet` is declared there."""
    path = tmp_path / "gui.json"
    settings.save({"sheet": {"roll-07": {"offsets": {"3": 1.25},
                                         "rotations": {"3": 0}}}}, path)
    stored = settings.load(path)["sheet"]
    assert stored["roll-07"]["offsets"] == {"3": 1.25}
    # Still strings here: JSON has no integer keys, and turning them back is
    # `ScannerGui._clean_sheet_state`'s job rather than this file's.
    assert stored["roll-07"]["rotations"] == {"3": 0}


def test_an_unreadable_file_is_kept_aside_not_written_over(tmp_path):
    """The window opens with defaults either way; but the next save wrote the
    whole file, and a sheet's unsaved decisions went with no word said."""
    target = tmp_path / "gui-settings.json"
    target.write_text('{"sheet": {"strip": [1, 2', encoding="utf-8")
    assert settings.load(target)["sheet"] == {}
    kept = list(tmp_path.glob("gui-settings.json.unreadable-*"))
    assert len(kept) == 1 and "strip" in kept[0].read_text(encoding="utf-8")


def test_a_save_waits_out_a_file_briefly_held_open(tmp_path, monkeypatch):
    """On Windows a replace fails outright while any handle on the target
    lacks FILE_SHARE_DELETE, which Defender and the indexer take on every new
    file -- and the window saves this file as a sheet's decisions are made."""
    from pathlib import Path

    real, refused = Path.replace, []

    def replace(self, target):
        if len(refused) < 2:
            refused.append(target)
            raise PermissionError(13, "held open")
        return real(self, target)

    monkeypatch.setattr(Path, "replace", replace)
    target = tmp_path / "gui-settings.json"
    assert settings.save({"output": "x"}, target) == target
    assert len(refused) == 2 and settings.load(target)["output"] == "x"


def test_a_save_that_fails_leaves_nothing_beside_the_file(tmp_path,
                                                          monkeypatch):
    """A replace refused for good -- a second window or an editor holding
    the file -- left `gui-settings.json.part` there for ever."""
    from pathlib import Path

    from rps7200 import session

    def refused(self, target):
        raise PermissionError(13, "held open")

    monkeypatch.setattr(session, "REPLACE_RETRY_S", ())
    monkeypatch.setattr(Path, "replace", refused)
    target = tmp_path / "gui-settings.json"
    assert settings.save({"output": "x"}, target) is None
    assert os.listdir(tmp_path) == []


def test_setting_an_unreadable_file_aside_is_said(tmp_path):
    """Kept aside, and then nothing said it: the window opened on its
    defaults, its presets and any sheet not yet commissioned gone from view,
    with no word of where they were."""
    said = []
    target = tmp_path / "gui-settings.json"
    target.write_text("[1, 2]", encoding="utf-8")
    settings.load(target, say=said.append)
    kept = list(tmp_path.glob("gui-settings.json.unreadable-*"))
    assert len(said) == 1 and kept[0].name in said[0]
    assert "defaults" in said[0]
    settings.load(tmp_path / "not-there.json", say=said.append)
    assert len(said) == 1, "a file that is not there is a fresh install"


def test_a_save_reaches_the_disk_before_it_is_renamed_in(tmp_path,
                                                         monkeypatch):
    """For a walk not yet commissioned this file is the only record of the
    contact sheet's decisions: renamed in unsynced, a power cut could leave
    the name over a file whose text never arrived. Its folder is synced
    once the new name is in."""
    from pathlib import Path

    from rps7200 import library

    order = []
    real = Path.replace
    monkeypatch.setattr(library, "_fsync", lambda fd: order.append("fsync"))
    monkeypatch.setattr(library, "_sync_dir",
                        lambda p: order.append(("folder", Path(p))))

    def replace(self, target):
        order.append("rename")
        return real(self, target)

    monkeypatch.setattr(Path, "replace", replace)
    target = tmp_path / "gui-settings.json"
    assert settings.save({"output": "x"}, target) == target
    assert order == ["fsync", "rename", ("folder", tmp_path)]


def test_a_save_that_fails_says_why(tmp_path, monkeypatch):
    """Quit asks before it goes on without the settings, and says why they
    would not go; `save` returned None and nothing more."""
    from pathlib import Path

    from rps7200 import session

    def refused(self, target):
        raise PermissionError(13, "held open")

    monkeypatch.setattr(session, "REPLACE_RETRY_S", ())
    monkeypatch.setattr(Path, "replace", refused)
    why: list[str] = []
    target = tmp_path / "gui-settings.json"
    assert settings.save({"output": "x"}, target, why=why) is None
    assert why == ["PermissionError: [Errno 13] held open"]
    assert os.listdir(tmp_path) == []
    why.clear()
    assert settings.save({"x": object()}, target, why=why) is None
    assert len(why) == 1 and why[0].startswith("TypeError:")


def _held(monkeypatch, target, times):
    """``target`` refused to reads ``times`` times -- for good with None --
    as Defender, the indexer or a backup holds a file a moment on Windows.
    Returns the count of reads refused."""
    from pathlib import Path

    from rps7200 import session

    monkeypatch.setattr(session, "REPLACE_RETRY_S", (0.0, 0.0, 0.0))
    real, refused = Path.read_text, []

    def read_text(self, *a, **k):
        if self == target and (times is None or len(refused) < times):
            refused.append(1)
            raise PermissionError(13, "held by another process", str(self))
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", read_text)
    return refused


def test_a_file_held_a_moment_is_read_again(tmp_path, monkeypatch):
    """CSA-16: any error but a missing file was taken for a corrupt one at
    once, and a valid file held a moment was moved aside -- the window then
    opened on its defaults and its next save wrote them over the place."""
    target = tmp_path / "gui-settings.json"
    settings.save({"presets": {"mine": {"dpi": "3600"}}}, target)
    refused = _held(monkeypatch, target, 2)
    said, unread = [], []
    stored = settings.load(target, say=said.append, unread=unread)
    assert stored["presets"] == {"mine": {"dpi": "3600"}}
    assert len(refused) == 2 and said == [] and unread == []
    assert list(tmp_path.glob("*.unreadable-*")) == []


def test_a_file_held_for_good_is_left_where_it_is(tmp_path, monkeypatch):
    """Still held after the retries: not moved aside -- it is no worse than
    it was -- and named in ``unread``, which the window takes as "write
    nothing over it until it can be read"."""
    target = tmp_path / "gui-settings.json"
    settings.save({"presets": {"mine": {"dpi": "3600"}}}, target)
    before = target.read_bytes()
    refused = _held(monkeypatch, target, None)
    said, unread = [], []
    stored = settings.load(target, say=said.append, unread=unread)
    assert stored["presets"] == {}
    assert len(refused) == 4, "asked once and again after each wait"
    assert len(unread) == 1 and "left as it is" in unread[0]
    assert "PermissionError" in unread[0]
    assert len(said) == 1 and "defaults" in said[0]
    assert target.read_bytes() == before
    assert list(tmp_path.glob("*.unreadable-*")) == []


def test_a_file_that_is_not_utf8_is_set_aside(tmp_path):
    """Read, and nonsense: moved aside at once, as invalid JSON is."""
    target = tmp_path / "gui-settings.json"
    target.write_bytes(b'{"output": "\xff\xfe"}')
    unread: list[str] = []
    assert settings.load(target, unread=unread)["output"] == ""
    assert unread == [] and not target.exists()
    assert len(list(tmp_path.glob("gui-settings.json.unreadable-*"))) == 1


def test_two_files_set_aside_in_one_second_are_both_kept(tmp_path,
                                                         monkeypatch):
    """Named to the second, the second aside took the first's name, and the
    replace put it over the first: the one copy kept was lost. Named in UTC
    now, with -2 for a name already taken."""
    import time

    monkeypatch.setattr(time, "gmtime",
                        lambda *a: time.struct_time((2026, 10, 10, 12, 0, 0,
                                                     5, 283, 0)))
    target = tmp_path / "gui-settings.json"
    for text in ("[1]", "[2]"):
        target.write_text(text, encoding="utf-8")
        settings.load(target)
    kept = sorted(p.name for p in tmp_path.glob("gui-settings.json.*"))
    assert kept == ["gui-settings.json.unreadable-20261010T120000Z",
                    "gui-settings.json.unreadable-20261010T120000Z-2"]
    assert {(tmp_path / name).read_text(encoding="utf-8")
            for name in kept} == {"[1]", "[2]"}


def test_the_roll_continue_offers_is_kept_as_a_name(tmp_path):
    """`last_roll`, the roll of strips the Roll panel offers to continue
    after a restart: a folder's name, never a mapping, and nothing where the
    file holds something else there."""
    path = tmp_path / "gui.json"
    assert settings.load(path)["last_roll"] == ""
    settings.save({"last_roll": "Gold200"}, path)
    assert settings.load(path)["last_roll"] == "Gold200"
    path.write_text(json.dumps({"last_roll": {"not": "a name"}}),
                    encoding="utf-8")
    assert settings.load(path)["last_roll"] == ""
