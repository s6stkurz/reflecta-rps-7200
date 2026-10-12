"""What the driver may make on disk, and whether a job's paths fit.

`rps7200/storage.py` holds the rules every writer asks before a job moves
anything: a configured folder may have its last folder made and never its
parents, a default one is made only in the driver's main checkout, the
output folder is never made by a job, and on Windows a path is no longer
than 259 characters. Each test here is one of them, with nothing on the bus.
"""

import os
import sys
import types
from pathlib import Path

import pytest

from rps7200 import storage

#: The real test, before `conftest` answers it for every test: the ones
#: below put it back.
STARTED_IN_CHECKOUT = storage.started_in_checkout


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


# -- default folders: made only in the main checkout --------------------------


def _repository(tmp_path):
    """A main checkout, a linked worktree of it laid out as git lays one out,
    a copy with no `.git`, and a folder that is none of them."""
    main = tmp_path / "driver"
    kept = main / ".git" / "worktrees" / "wt"
    kept.mkdir(parents=True)
    (kept / "commondir").write_text("../..\n", encoding="utf-8")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {kept.as_posix()}\n",
                                   encoding="utf-8")
    (kept / "gitdir").write_text(f"{(worktree / '.git').as_posix()}\n",
                                 encoding="utf-8")
    copy = tmp_path / "copy"
    copy.mkdir()
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "tools").mkdir(parents=True)
    (main / "tools").mkdir()
    return types.SimpleNamespace(main=main, worktree=worktree, copy=copy,
                                 elsewhere=elsewhere)


@pytest.fixture
def code_in(monkeypatch):
    """Run the code as if it lived in a folder of the test's choosing, with
    the real `started_in_checkout` (`conftest` answers it for every test)."""
    monkeypatch.setattr(storage, "started_in_checkout", STARTED_IN_CHECKOUT)

    def place(folder):
        monkeypatch.setattr(storage, "checkout", lambda: Path(folder))
    return place


def test_a_worktree_names_its_main_checkout_and_is_not_it(tmp_path):
    """A worktree's `.git` is a file naming the repository's own: the main
    checkout is the folder that holds that. A clone and a copy with no
    `.git` at all are each their own."""
    repo = _repository(tmp_path)
    assert storage.main_checkout(repo.main) == repo.main
    assert storage.main_checkout(repo.worktree) == repo.main
    assert storage.main_checkout(repo.copy) == repo.copy
    # As git writes it on some machines: a gitdir relative to the worktree.
    (repo.worktree / ".git").write_text(
        "gitdir: ../driver/.git/worktrees/wt\n", encoding="utf-8")
    assert storage.main_checkout(repo.worktree) == repo.main
    # Nothing readable named: no main checkout to send anyone to.
    (repo.worktree / ".git").write_text("not git\n", encoding="utf-8")
    assert storage.main_checkout(repo.worktree) is None


def test_the_checkout_this_suite_runs_in_is_read_from_its_own_git():
    """The real one, whichever this is: the main checkout holds `.git` as a
    folder; a worktree's names it; a copy with none is its own."""
    code, main = storage.checkout(), storage.main_checkout()
    assert (code / "rps7200" / "storage.py").is_file()
    assert main is not None
    if (code / ".git").is_file():
        assert main != code and (main / ".git").is_dir()
    else:
        assert main == code


def test_started_in_the_checkout_and_nowhere_else(tmp_path, monkeypatch,
                                                  code_in):
    """Its own folder by the disk's identity; never a folder inside it --
    `tools/`, which a double-click starts a program in -- nor a worktree."""
    repo = _repository(tmp_path)
    code_in(repo.main)
    for where, expected in ((repo.main, True), (repo.main / "tools", False),
                            (repo.elsewhere, False), (repo.worktree, False)):
        monkeypatch.chdir(where)
        assert storage.started_in_checkout() is expected, where
    # The worktree's own code, started in the main checkout: started there.
    code_in(repo.worktree)
    monkeypatch.chdir(repo.main)
    assert storage.started_in_checkout() is True
    monkeypatch.chdir(repo.worktree)
    assert storage.started_in_checkout() is False


def test_a_default_folder_is_made_only_in_the_main_checkout(
        tmp_path, monkeypatch, code_in):
    """GUI2-A2, Stefan's call 2026-10-12. Started in the checkout, a default
    folder not there yet may be made, its last folder only; anywhere else it
    is refused in one sentence naming each folder in full, where this was
    started, the checkout and the flags -- and nothing is made."""
    repo = _repository(tmp_path)
    code_in(repo.main)
    defaults = [("library", "--library"), ("rolls", "--rolls"),
                (Path("calibration"), "--reference")]
    monkeypatch.chdir(repo.main)
    assert storage.default_refused(defaults) is None
    monkeypatch.chdir(repo.main / "tools")
    said = storage.default_refused(defaults)
    here = repo.main / "tools"
    assert said is not None and "\n" not in said
    assert (f"{here / 'library'}, {here / 'rolls'} and "
            f"{here / 'calibration'} are not there") in said
    assert f"made only in the driver's own folder, {repo.main} --" in said
    assert f"not in {here}, where this was started" in said
    assert said.endswith(f"Start it in {repo.main}, or give --library, "
                         "--rolls and --reference")
    assert "worktree" not in said
    assert sorted(os.listdir(here)) == [], "nothing was made"


def test_a_default_folder_that_is_there_is_used_wherever(tmp_path,
                                                         monkeypatch, code_in):
    repo = _repository(tmp_path)
    code_in(repo.main)
    monkeypatch.chdir(repo.elsewhere)
    (repo.elsewhere / "library").mkdir()
    assert storage.default_refused([("library", "--library")]) is None
    said = storage.default_refused([("library", "--library"),
                                    ("rolls", "--rolls")])
    assert said is not None and f"{repo.elsewhere / 'rolls'} is not" in said
    assert "library " not in said and "--library" not in said


def test_a_worktree_makes_no_default_folder_of_its_own(tmp_path, monkeypatch,
                                                       code_in):
    """`make run` in a worktree runs there: its library would be empty, and
    `make verify` on the shared one would never see what it filed."""
    repo = _repository(tmp_path)
    code_in(repo.worktree)
    monkeypatch.chdir(repo.worktree)
    said = storage.default_refused([("library", "--library")])
    assert said is not None
    assert (f"made only in the driver's own folder, {repo.main} "
            f"({repo.worktree} is a worktree of it)") in said
    assert said.endswith(f"Start it in {repo.main}, or give --library")
    assert not (repo.worktree / "library").exists()
    # Its code, started in the main checkout, uses the folders there.
    monkeypatch.chdir(repo.main)
    assert storage.default_refused([("library", "--library")]) is None


def test_what_a_default_folder_cannot_be_is_said_anywhere(tmp_path,
                                                          monkeypatch,
                                                          code_in):
    """In the checkout too: a file where the library goes is no folder."""
    repo = _repository(tmp_path)
    code_in(repo.main)
    monkeypatch.chdir(repo.main)
    (repo.main / "library").write_bytes(b"")
    assert "is a file, not a folder" in storage.default_refused(
        [("library", "--library")])


def test_an_absolute_default_and_a_variable_to_set(tmp_path, monkeypatch,
                                                   code_in):
    """A path that names its own place is not resolved against the start
    folder. And a default no flag can name -- a script's library -- is
    said as the variable that moves it."""
    repo = _repository(tmp_path)
    code_in(repo.main)
    monkeypatch.chdir(repo.elsewhere)
    assert storage.default_refused(
        [(repo.elsewhere / "library", "--library")]) is None
    said = storage.default_refused([("library", "RPS7200_DEBUG_ROOT"),
                                    ("rolls", "--out")])
    assert said.endswith("or give --out or set RPS7200_DEBUG_ROOT")


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
