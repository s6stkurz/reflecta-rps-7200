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
    path.write_text(rubbish)
    assert settings.load(path)["controls"] == {}


def test_a_section_of_the_wrong_type_is_dropped_not_kept(tmp_path):
    path = tmp_path / "gui.json"
    path.write_text(json.dumps({"controls": "not a dict", "output": 17}))
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
    path.write_text(json.dumps({"controls": {"dpi": "600"}, "gone": {"x": 1}}))
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


def test_the_default_is_beside_the_library(monkeypatch):
    monkeypatch.delenv(settings.PATH_ENV, raising=False)
    assert settings.path() == settings.DEFAULT_PATH
    assert not os.path.isabs(settings.DEFAULT_PATH)
