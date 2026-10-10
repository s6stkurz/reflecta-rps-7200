"""What the driver may make on disk, and whether a job's paths fit.

`rps7200/storage.py` holds three rules every writer asks before a job moves
anything: a configured folder may have its last folder made and never its
parents, the output folder is never made by a job, and on Windows a path is
no longer than 259 characters. Each test here is one of them, with nothing
on the bus.
"""

import os
import sys
import types
from pathlib import Path

import pytest

from rps7200 import storage


# -- make_root ---------------------------------------------------------------


def test_a_root_whose_parent_is_there_gets_its_last_folder(tmp_path):
    made = storage.make_root(tmp_path / "library")
    assert made == tmp_path / "library" and made.is_dir()
    # And again, for a folder already there: nothing to do, nothing raised.
    assert storage.make_root(tmp_path / "library") == made


def test_a_root_whose_parent_is_missing_is_refused_and_nothing_made(tmp_path):
    """A folder on a stick that is not plugged in was made again on C: by a
    `mkdir(parents=True)`, or failed after the pass. The first folder not
    there is named, and nothing is made."""
    gone = tmp_path / "gone"
    with pytest.raises(storage.DriveMissing) as refused:
        storage.make_root(gone / "scans" / "library")
    said = str(refused.value)
    assert f"{gone} is not there" in said
    assert "is the drive mounted?" in said
    assert not gone.exists()
    assert isinstance(refused.value, OSError), \
        "every caller that copes with a refused disk copes with this"


def test_there_is_no_way_to_ask_for_the_parents():
    """A typed option replayed from shell history, its drive letter now held
    by another stick, is the very case the rule is for."""
    import inspect

    assert list(inspect.signature(storage.make_root).parameters) == ["path"]


def test_a_root_under_a_file_says_so(tmp_path):
    (tmp_path / "blocker").write_bytes(b"")
    said = storage.missing_root(tmp_path / "blocker" / "library")
    assert said is not None and "is a file, not a folder" in said
    assert "is a file, not a folder" in storage.missing_root(
        tmp_path / "blocker")


@pytest.mark.skipif(sys.platform != "win32", reason="drive letters")
def test_a_root_on_a_drive_letter_nothing_holds_is_said():
    free = [c for c in "QRSTUVWXYZ" if not Path(f"{c}:/").exists()]
    if not free:
        pytest.skip("every drive letter here is in use")
    said = storage.missing_root(f"{free[0]}:/scans/library")
    assert said is not None and "is the drive mounted?" in said
    assert f"{free[0]}:" in said


# -- require_dir -------------------------------------------------------------


def test_the_output_folder_is_never_made(tmp_path):
    """The remembered output folder is iCloud's, and its parent is always
    there: making only its last folder would still have made it again on C:
    once iCloud took it away."""
    icloud = tmp_path / "iCloudDrive"
    with pytest.raises(storage.DriveMissing) as refused:
        storage.require_dir(icloud)
    assert "is the drive mounted?" in str(refused.value)
    assert "Choose the output folder again, or clear it" in str(refused.value)
    assert not icloud.exists()
    icloud.mkdir()
    assert storage.require_dir(icloud) == icloud


def test_a_folder_that_cannot_be_looked_at_is_refused_as_such(tmp_path,
                                                              monkeypatch):
    """Python 3.12 raises for a folder it may not look in -- access denied,
    a share dropped -- rather than saying no."""
    def denied(self):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(Path, "is_dir", denied)
    with pytest.raises(storage.DriveMissing, match="cannot be looked at"):
        storage.require_dir(tmp_path)
    assert "cannot be looked at" in storage.missing_root(tmp_path / "x")


# -- make_within -------------------------------------------------------------


def test_folders_below_a_root_are_made_as_deep_as_they_need(tmp_path):
    folder = storage.make_within(tmp_path / "rolls",
                                 tmp_path / "rolls" / "a" / "b")
    assert folder.is_dir()


def test_a_folder_outside_its_root_is_refused(tmp_path):
    with pytest.raises(ValueError, match="is not inside"):
        storage.make_within(tmp_path / "rolls", tmp_path / "elsewhere")
    assert not (tmp_path / "rolls").exists()


def test_below_a_root_whose_parent_is_missing_nothing_is_made(tmp_path):
    root = tmp_path / "gone" / "rolls"
    with pytest.raises(storage.DriveMissing):
        storage.make_within(root, root / "roll")
    assert not (tmp_path / "gone").exists()


def test_a_folder_named_through_a_link_is_inside_its_root(tmp_path):
    """A rolls folder that is a link or a junction names each roll two ways,
    and the window finds a sheet's by the one with the link followed. Asked
    as written alone, that roll was not inside its rolls, and its
    approved.json was refused."""
    from conftest import folder_link

    real = tmp_path / "data" / "rolls"
    real.mkdir(parents=True)
    rolls = tmp_path / "rolls"
    folder_link(rolls, real)
    assert storage.inside(real / "roll", rolls)
    assert storage.inside(rolls / "roll", real)
    assert storage.make_within(rolls, real / "roll").is_dir()
    assert (rolls / "roll").is_dir()
    assert not storage.inside(tmp_path / "data", rolls)
    with pytest.raises(ValueError, match="is not inside"):
        storage.make_within(rolls, tmp_path / "data" / "other")


# -- check_space -------------------------------------------------------------


def _free(monkeypatch, free):
    import collections
    import shutil

    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage",
                        lambda path: usage(10 ** 13, 0, free))


def test_the_reserve_is_kept_beyond_what_a_job_needs(tmp_path, monkeypatch):
    """Stefan's call: refused below once what a job writes plus 2 GB."""
    monkeypatch.setattr(storage, "SPACE_RESERVE", 2 * 10**9)
    _free(monkeypatch, 3 * 10**9)
    assert storage.check_space([(tmp_path, 10**9)]) == ([], [])
    (short,), unknown = storage.check_space([(tmp_path, 10**9 + 1)])
    assert unknown == [] and "3.0 GB free" in short
    assert "2 GB is kept free" in short
    # Warned below twice it.
    (tight,), _ = storage.check_space([(tmp_path, 10**9)], factor=2.0)
    assert "3.0 GB free" in tight


def test_places_on_one_disk_are_added_together(tmp_path, monkeypatch):
    """The library and the rolls folder side by side share one disk's room,
    and each is named once."""
    _free(monkeypatch, 10 * 10**9)
    (short,), _ = storage.check_space([(tmp_path / "a", 6 * 10**9),
                                       (tmp_path / "b", 6 * 10**9),
                                       (tmp_path / "a", 10**9)])
    assert f"holding {tmp_path / 'a'} and {tmp_path / 'b'}, where" in short
    assert "can need up to 13.0 GB" in short


def test_a_folder_not_made_yet_is_counted_on_its_parents_disk(tmp_path):
    assert storage.nearest_existing(tmp_path / "x" / "y") == tmp_path


# -- longest_too_long --------------------------------------------------------


def _windows(monkeypatch):
    monkeypatch.setattr(storage, "sys", types.SimpleNamespace(platform="win32"))


def test_a_path_past_259_characters_is_said_on_windows(tmp_path, monkeypatch):
    """Refused whatever Windows' long-path setting: Explorer and NegPy do
    not read past it, however Python writes it."""
    _windows(monkeypatch)
    base = str(Path(tmp_path).absolute())
    fits = Path(base + os.sep + "x" * (259 - len(base) - 1))
    over = Path(base + os.sep + "y" * (260 - len(base) - 1))
    assert len(str(fits)) == 259 and len(str(over)) == 260
    assert storage.longest_too_long([fits]) == []
    (said,) = storage.longest_too_long([fits, over])
    assert "260 characters" in said and "259" in said
    assert str(over.parent) in said


def test_nothing_is_said_of_a_long_path_anywhere_else(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "sys", types.SimpleNamespace(platform="linux"))
    assert storage.longest_too_long([tmp_path / ("z" * 300)]) == []


def test_same_disk_asks_the_nearest_folders_there_and_never_guesses(
        tmp_path, monkeypatch):
    """Duplicate counts what the writer has queued on its own disk only
    (`same_disk`): a folder not made yet is on its parent's disk, and a
    place whose disk cannot be read shares room with nothing."""
    (tmp_path / "a").mkdir()
    assert storage.same_disk(tmp_path / "a", tmp_path / "not-yet" / "made")
    real = storage._device

    def device(where):
        if Path(where) == tmp_path / "a":
            raise OSError(5, "the share has gone")
        return real(where)

    monkeypatch.setattr(storage, "_device", device)
    assert not storage.same_disk(tmp_path / "a", tmp_path)
