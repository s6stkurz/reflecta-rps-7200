"""The locks between processes: `rps7200.locks`.

Inside one process a lock is counted; across two, a shared one sits beside
a shared one and an exclusive one beside nothing, and every refusal names
who holds it. Another process is a real one (`HeldElsewhere`), because the
operating system's lock is the whole of the mechanism.
"""

import json
import os
import stat
import sys
import threading
import time

import pytest
from conftest import HeldElsewhere

from rps7200 import locks


def _records(kind, path):
    return sorted(locks.locks_dir().glob(f"{kind}-{locks.key(path)}.*.json"))


def test_takes_inside_one_process_are_counted_and_kept_until_the_last(tmp_path):
    """Shared in shared, shared in exclusive and exclusive in exclusive count
    up; a shared hold made exclusive stays exclusive until every take is let
    go -- seen from another process, which is the only place it shows."""
    library = tmp_path / "library"
    outer = locks.take(locks.LIBRARY, library, exclusive=False, what="outer")
    inner = locks.take(locks.LIBRARY, library, exclusive=False, what="inner")
    assert len(_records(locks.LIBRARY, library)) == 1, "one record a process"
    mine = locks.take(locks.LIBRARY, library, exclusive=True, what="upgrade")
    again = locks.take(locks.LIBRARY, library, exclusive=True, what="again")
    for held in (inner, mine, again):
        held.release()
    # One shared take left, and the hold is still the exclusive one: a
    # shared take from another process is refused at the gate.
    with pytest.raises(AssertionError, match="did not take"):
        with HeldElsewhere(locks.LIBRARY, library, exclusive=False):
            pass
    outer.release()
    assert not _records(locks.LIBRARY, library), "the record went with it"
    with HeldElsewhere(locks.LIBRARY, library, exclusive=False):
        pass
    outer.release()                                      # twice is nothing


def test_another_thread_re_enters(tmp_path):
    """The window's threads share its locks; its own checks decide what each
    may do."""
    roll = tmp_path / "rolls" / "Gold-200"
    held = locks.take(locks.ROLL, roll, exclusive=True, what="the window")
    took = []

    def other():
        with locks.take(locks.ROLL, roll, exclusive=True, what="a thread"):
            took.append(True)

    thread = threading.Thread(target=other)
    thread.start()
    thread.join(5)
    assert took == [True]
    assert len(locks.holders(locks.ROLL, roll)) == 1
    held.release()
    assert locks.holders(locks.ROLL, roll) == []


def test_an_exclusive_holder_elsewhere_refuses_and_is_named(tmp_path):
    library = tmp_path / "library"
    with HeldElsewhere(locks.LIBRARY, library, exclusive=True,
                       what="tools/library.py compact --write") as other:
        for exclusive in (False, True):
            started = time.monotonic()
            with pytest.raises(locks.Refused) as refused:
                locks.take(locks.LIBRARY, library, exclusive=exclusive)
            assert time.monotonic() - started < locks.WAIT_S + 1.0
            said = str(refused.value)
            assert "tools/library.py compact --write" in said
            assert f"pid {other.pid}" in said and "since " in said
            assert refused.value.kind == locks.LIBRARY
            assert [h["pid"] for h in refused.value.holders] == [other.pid]
    # Gone with it.
    locks.take(locks.LIBRARY, library, exclusive=True).release()


def test_shared_sits_beside_shared_and_exclusive_beside_nothing(tmp_path):
    library = tmp_path / "library"
    with HeldElsewhere(locks.LIBRARY, library, exclusive=False,
                       what="the window") as window:
        mine = locks.take(locks.LIBRARY, library, exclusive=False)
        assert {h["pid"] for h in locks.holders(locks.LIBRARY, library)} \
            == {window.pid, os.getpid()}
        # This process's shared hold cannot become exclusive beside it.
        with pytest.raises(locks.Refused, match=f"pid {window.pid}"):
            locks.take(locks.LIBRARY, library, exclusive=True)
        mine.release()
        with pytest.raises(locks.Refused, match="the window"):
            locks.take(locks.LIBRARY, library, exclusive=True,
                       required=True)
    # And the other way: this one exclusive, theirs shared and exclusive.
    with locks.take(locks.LIBRARY, library, exclusive=False):
        with pytest.raises(AssertionError, match="did not take"):
            with HeldElsewhere(locks.LIBRARY, library, exclusive=True):
                pass


def test_a_holder_that_crashed_holds_nothing(tmp_path):
    """Killed, it lets go at once; its record is left behind, passed over by
    `holders` -- which deletes nothing -- and swept by the next exclusive
    take, which alone holds the gate."""
    library = tmp_path / "library"
    other = HeldElsewhere(locks.LIBRARY, library, exclusive=False)
    with other:
        other.kill()
        left = _records(locks.LIBRARY, library)
        assert len(left) == 1, "a crash leaves its record"
        assert locks.holders(locks.LIBRARY, library) == []
        assert _records(locks.LIBRARY, library) == left, "holders deleted it"
        started = time.monotonic()
        held = locks.take(locks.LIBRARY, library, exclusive=True)
        assert time.monotonic() - started < locks.WAIT_S
        assert left[0] not in _records(locks.LIBRARY, library), "not swept"
        held.release()


def test_a_record_is_readable_while_held_and_its_name_has_no_colon(tmp_path):
    """NTFS reads a colon in a name as a stream; and a refusal elsewhere has
    to read what the record says while its holder has it locked."""
    roll = tmp_path / "rolls" / "Gold-200"
    with HeldElsewhere(locks.ROLL, roll, what="tools/scan_roll.py") as other:
        records = _records(locks.ROLL, roll)
        assert records and all(":" not in p.name for p in records)
        said = json.loads(records[0].read_text(encoding="utf-8"))
        assert said["pid"] == other.pid and said["what"] == "tools/scan_roll.py"
        assert said["mode"] == "exclusive" and said["lock"] == locks.ROLL
        assert locks.holders(locks.ROLL, roll)[0]["pid"] == other.pid


def test_a_folder_has_one_key_however_it_is_spelled(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "Library"
    spellings = [folder, "Library", str(folder) + os.sep, tmp_path / "library",
                 tmp_path / "x" / ".." / "Library"]
    before = {locks.key(s) for s in spellings}
    folder.mkdir()
    after = {locks.key(s) for s in spellings}
    assert len(before) == 1 and before == after
    assert locks.key(tmp_path / "rolls") != locks.key(folder)


def test_a_rolls_key_holds_across_the_rolls_folder_being_made(tmp_path):
    """The first roll makes the rolls folder: a lock taken on a roll before
    then has to be the lock taken on it after, or a scan_roll that started
    first is not seen by the window that starts second."""
    roll = tmp_path / "rolls" / "Gold-200"
    before = locks.key(roll)
    roll.mkdir(parents=True)
    assert locks.key(roll) == before
    with HeldElsewhere(locks.ROLL, tmp_path / "rolls2" / "x") as other:
        (tmp_path / "rolls2").mkdir()
        with pytest.raises(locks.Refused, match=f"pid {other.pid}"):
            locks.take(locks.ROLL, tmp_path / "rolls2" / "x", exclusive=True)


def test_a_link_on_the_way_is_followed(tmp_path):
    """A rolls folder that is a link or a junction: one folder, two names."""
    import subprocess

    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "linked"
    try:
        os.symlink(real, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        if sys.platform != "win32":
            pytest.skip("no symbolic links here")
        # A junction needs no privilege, where a symbolic link does.
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(real)],
                       check=False, capture_output=True)
        if not link.is_dir():
            pytest.skip("neither a link nor a junction can be made here")
    assert locks.key(link / "roll") == locks.key(real / "roll")


def test_the_window_compares_folders_folded_on_macos(monkeypatch, tmp_path):
    """APFS folds case as Windows does; `normcase` folds only on Windows."""
    from conftest import load_tool

    gui = load_tool("gui")
    monkeypatch.setattr(gui.sys, "platform", "darwin")
    assert gui._folder_key(tmp_path / "Roll") == gui._folder_key(
        tmp_path / "roll")


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                    reason="a folder's mode does not refuse a write here")
def test_a_lock_folder_that_cannot_be_written(tmp_path, monkeypatch):
    """A shared take never refuses for it, and says why; an exclusive one
    that must have the lock refuses, naming nobody."""
    folder = tmp_path / "locks"
    folder.mkdir()
    folder.chmod(stat.S_IRUSR | stat.S_IXUSR)
    monkeypatch.setenv(locks.LOCKS_ENV, str(folder))
    try:
        held = locks.take(locks.LIBRARY, tmp_path / "library",
                          exclusive=False)
        assert not held.held and "could not take" in held.note
        with pytest.raises(locks.Refused, match="could not take") as refused:
            locks.take(locks.LIBRARY, tmp_path / "library", exclusive=True,
                       required=True)
        assert refused.value.holders == []
    finally:
        folder.chmod(stat.S_IRWXU)


def test_a_lock_folder_that_is_a_file(tmp_path, monkeypatch):
    """The same, on every platform: nothing can be made there."""
    blocked = tmp_path / "locks"
    blocked.write_text("not a folder", encoding="utf-8")
    monkeypatch.setenv(locks.LOCKS_ENV, str(blocked / "inside"))
    held = locks.take(locks.LIBRARY, tmp_path / "library", exclusive=False)
    assert not held.held and "could not take" in held.note
    held.release()
    with pytest.raises(locks.Refused, match="could not take"):
        locks.take(locks.LIBRARY, tmp_path / "library", exclusive=True,
                   required=True)


def test_a_file_hold_is_seen_by_another_handle_and_let_go(tmp_path):
    """An INCOMPLETE marker's lock: seen held from a second handle in the
    same process as from another, readable meanwhile, free once let go --
    and `letting_go` lets go of what a failure left held."""
    marker = tmp_path / "INCOMPLETE"
    hold = locks.hold_file(marker)
    os.write(hold.fd, b'{"what": "a save"}')
    assert locks.file_held(marker) is True
    assert json.loads(marker.read_text(encoding="utf-8")) == {"what": "a save"}
    hold.release()
    assert locks.file_held(marker) is False
    marker.unlink()
    assert locks.file_held(marker) is None

    @locks.letting_go
    def fails():
        locks.hold_file(marker)
        assert locks.file_held(marker) is True
        raise OSError("disk full")

    with pytest.raises(OSError, match="disk full"):
        fails()
    assert locks.file_held(marker) is False
    marker.unlink()


def test_describe_names_a_holder_as_a_person_reads_it():
    said = locks.describe({"what": "the window", "program": "tools/gui.py",
                           "pid": 4242, "since_utc": "2020-01-02T11:02:00+00:00",
                           "cwd": "C:\\repo"})
    assert said == ("the window (tools/gui.py, pid 4242, since 2020-01-02 "
                    "11:02 UTC, in C:\\repo)")
    assert locks.describe({"what": "tools/scan_roll.py",
                           "program": "tools/scan_roll.py", "pid": 1}) == \
        "tools/scan_roll.py (pid 1)"
