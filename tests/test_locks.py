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
from conftest import HeldElsewhere, HolderRefused

from rps7200 import locks


def _records(kind, path):
    return sorted(locks.locks_dir().glob(f"{kind}-{locks.key(path)}.*.json"))


def _refused_by_me():
    """What another process is told when this one holds the lock: its
    refusal, naming this process -- never any other failure of it."""
    return pytest.raises(HolderRefused,
                         match=rf"is in use by .*\bpid {os.getpid()}\b")


def test_takes_inside_one_process_are_counted_and_kept_until_the_last(tmp_path):
    """Shared in shared, shared in exclusive and exclusive in exclusive count
    up; a shared hold made exclusive is exclusive while any exclusive take
    is held and shared again after -- seen from another process, which is
    the only place it shows."""
    library = tmp_path / "library"
    outer = locks.take(locks.LIBRARY, library, exclusive=False, what="outer")
    inner = locks.take(locks.LIBRARY, library, exclusive=False, what="inner")
    assert len(_records(locks.LIBRARY, library)) == 1, "one record a process"
    mine = locks.take(locks.LIBRARY, library, exclusive=True, what="upgrade")
    again = locks.take(locks.LIBRARY, library, exclusive=True, what="again")
    for held in (inner, again):
        held.release()
    # One exclusive take left: a shared take from another process is
    # refused at the gate, naming this one.
    with _refused_by_me():
        with HeldElsewhere(locks.LIBRARY, library, exclusive=False):
            pass
    mine.release()
    # Shared again with one shared take left: another shared take sits
    # beside it, an exclusive one is still refused.
    with HeldElsewhere(locks.LIBRARY, library, exclusive=False):
        pass
    with _refused_by_me():
        with HeldElsewhere(locks.LIBRARY, library, exclusive=True):
            pass
    outer.release()
    assert not _records(locks.LIBRARY, library), "the record went with it"
    with HeldElsewhere(locks.LIBRARY, library, exclusive=True):
        pass
    outer.release()                                      # twice is nothing


def test_an_exclusive_take_let_go_inside_a_shared_one_leaves_it_shared(
        tmp_path):
    """Taken exclusive first and shared inside it, the same: the roll a
    window shows was refused to every other window's view for as long as
    it was shown, once a Rename or a Delete here had taken it exclusive."""
    roll = tmp_path / "rolls" / "Gold-200"
    going = locks.take(locks.ROLL_VIEW, roll, exclusive=True, what="Rename")
    shown = locks.take(locks.ROLL_VIEW, roll, exclusive=False, what="sheet")
    with _refused_by_me():
        with HeldElsewhere(locks.ROLL_VIEW, roll, exclusive=False):
            pass
    going.release()
    with HeldElsewhere(locks.ROLL_VIEW, roll, exclusive=False) as other:
        assert {h["pid"] for h in locks.holders(locks.ROLL_VIEW, roll)} \
            == {os.getpid(), other.pid}
    # And made exclusive again from shared, against the other processes.
    going = locks.take(locks.ROLL_VIEW, roll, exclusive=True, what="Delete")
    with _refused_by_me():
        with HeldElsewhere(locks.ROLL_VIEW, roll, exclusive=False):
            pass
    going.release()
    shown.release()
    assert locks.holders(locks.ROLL_VIEW, roll) == []


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
    # And the other way: this one shared, theirs exclusive.
    with locks.take(locks.LIBRARY, library, exclusive=False):
        with _refused_by_me():
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


#: Two folders each on NTFS, one each on APFS: "ß" against "ss", and an NFC
#: "é" against an NFD one.
_FOLDED_APART = [("Straße", "Strasse"), ("caf\u00e9", "cafe\u0301")]


@pytest.mark.skipif(sys.platform != "win32",
                    reason="NTFS keeps these apart; elsewhere they are folded")
def test_folders_ntfs_keeps_apart_have_locks_of_their_own(tmp_path):
    """Folded past what `normcase` folds, each pair shared one lock: a roll
    beside its "ss" spelling was refused -- Rename, Delete, Duplicate, a
    roll -- in the name of the other folder's holder."""
    def keys():
        return [(locks.key(tmp_path / one), locks.key(tmp_path / other))
                for one, other in _FOLDED_APART]

    before = keys()
    for pair in _FOLDED_APART:
        for name in pair:
            (tmp_path / name).mkdir()
    assert len(list(tmp_path.iterdir())) == 4, "NTFS made two folders of one"
    for one, other in [*before, *keys()]:
        assert one != other
    # Case is still folded, past ASCII too, as NTFS folds it.
    assert locks.key(tmp_path / "CAF\u00c9") == locks.key(tmp_path / "caf\u00e9")
    with HeldElsewhere(locks.ROLL, tmp_path / "Straße",
                       what="tools/scan_roll.py") as other:
        locks.take(locks.ROLL, tmp_path / "Strasse", exclusive=True).release()
        with pytest.raises(locks.Refused, match=f"pid {other.pid}"):
            locks.take(locks.ROLL, tmp_path / "Straße", exclusive=True)


def test_elsewhere_normalisation_and_case_are_folded(monkeypatch, tmp_path):
    """APFS keeps neither apart, and `realpath` there writes neither as the
    disk stores it: unfolded, one folder spelled two ways had two locks."""
    monkeypatch.setattr(locks.sys, "platform", "darwin")
    for one, other in [*_FOLDED_APART, ("Roll", "roll")]:
        assert locks.key(tmp_path / one) == locks.key(tmp_path / other)


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


@pytest.mark.parametrize("first", ["-", "-c", "-m", ""])
def test_a_script_read_from_stdin_is_named_python(monkeypatch, first):
    """``uv run python - <<EOF`` -- the shape of a throwaway scanner script
    -- has "-" for its name, and a maintainer it refused read "in use by -
    (pid ...)"."""
    monkeypatch.setattr(locks.sys, "argv", [first] if first else [])
    assert locks.program() == "python"
    said = locks._record_of(locks.ROLL, locks.Path("roll"), "exclusive",
                            locks.program())
    assert said["what"] == said["program"] == "python"
    assert locks.describe(said).startswith(f"python (pid {os.getpid()}, ")


# -- a disk that locks nothing, and a prober asking at the wrong moment ---------


def test_a_disk_that_locks_nothing_is_never_taken_for_a_holder(
        tmp_path, monkeypatch):
    """Only a byte another handle has is "held". Every other failure of the
    lock call -- NFS without its daemon, a mount that locks nothing -- was
    read as held as well, and a marker there as a save writing it now."""
    from conftest import a_disk_that_locks_nothing

    marker = tmp_path / "INCOMPLETE"
    marker.write_text('{"what": "tools/gui.py", "pid": 999}',
                      encoding="utf-8")
    a_disk_that_locks_nothing(monkeypatch)
    assert locks.file_held(marker) is None, "cannot be asked, so not held"
    with pytest.raises(OSError, match="Invalid argument"):
        locks.hold_file(tmp_path / "other")
    held = locks.take(locks.LIBRARY, tmp_path / "library", exclusive=False)
    assert not held.held and "could not take" in held.note
    with pytest.raises(locks.Refused, match="could not take") as refused:
        locks.take(locks.LIBRARY, tmp_path / "library", exclusive=True,
                   required=True)
    assert refused.value.holders == []
    assert not list(locks.locks_dir().glob("*.json")), "a record left behind"


def test_a_file_a_prober_has_for_an_instant_is_locked_all_the_same(tmp_path):
    """A probe takes the lock for the instant it asks. Met by a save making
    its marker, the marker went unlocked, and the `verify` that had asked
    called the save -- still writing -- one that did not finish."""
    marker = tmp_path / "INCOMPLETE"
    prober = locks.hold_file(marker)          # a probe, held a little long
    threading.Timer(0.1, prober.release).start()
    started = time.monotonic()
    hold = locks.hold_file(marker)
    try:
        assert 0.05 < time.monotonic() - started < locks.WAIT_S
        assert locks.file_held(marker) is True
    finally:
        hold.release()


def test_a_record_a_prober_met_as_it_was_made_is_held_all_the_same(
        tmp_path, monkeypatch):
    """A probe landing between a record's making and its locking: tried
    once, the take went unguarded, or refused a maintainer naming nobody."""
    real = locks._try_lock
    calls = []

    def probed_once(fd):
        calls.append(fd)
        if len(calls) == 2:              # the gate is first, the record next
            return False
        return real(fd)

    library = tmp_path / "library"
    with monkeypatch.context() as patched:
        patched.setattr(locks, "_try_lock", probed_once)
        held = locks.take(locks.LIBRARY, library, exclusive=True,
                          required=True)
    assert held.held and held.note is None
    assert len(calls) >= 3, "the record was not tried again"
    with _refused_by_me():
        with HeldElsewhere(locks.LIBRARY, library, exclusive=False):
            pass
    held.release()


def test_a_dead_record_another_prober_has_for_an_instant_is_dead(
        tmp_path, monkeypatch):
    """Two probes at once each see the other as a holder; seen held once, a
    crashed holder's record refused a maintainer in the name of a process
    that had gone. Held is believed when it is seen twice."""
    library = tmp_path / "library"
    other = HeldElsewhere(locks.LIBRARY, library, exclusive=False)
    with other:
        other.kill()
        [left] = _records(locks.LIBRARY, library)
        real = locks._try_lock
        seen = []

        def asked_at_once(at):
            def try_lock(fd):
                seen.append(fd)
                return False if len(seen) == at else real(fd)
            return try_lock

        with monkeypatch.context() as patched:
            patched.setattr(locks, "_try_lock", asked_at_once(1))
            assert locks.holders(locks.LIBRARY, library) == []
            # A maintainer's sweep: the gate first, the dead record next.
            seen.clear()
            patched.setattr(locks, "_try_lock", asked_at_once(2))
            held = locks.take(locks.LIBRARY, library, exclusive=True,
                              required=True, what="compact --write")
        assert held.held
        assert left not in _records(locks.LIBRARY, library), "not swept"
        held.release()
        # Held twice is held: a live record is still named.
        with HeldElsewhere(locks.LIBRARY, library, exclusive=False) as alive:
            assert [h["pid"] for h in locks.holders(locks.LIBRARY, library)] \
                == [alive.pid]


def test_the_lock_folder_is_the_users_whatever_the_environment_says(
        monkeypatch, tmp_path):
    """Under $XDG_RUNTIME_DIR, a window started from the desktop and a
    `compact --write` from cron -- which has none -- kept their locks in
    two folders and never saw each other. Nor in a cache, which cleaners
    empty under a lock held."""
    monkeypatch.delenv(locks.LOCKS_ENV)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    first = locks.locks_dir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    if sys.platform != "win32":
        monkeypatch.setenv("HOME", str(tmp_path / "elsewhere"))
    assert locks.locks_dir() == first
    assert not {"Caches", ".cache"} & set(first.parts)
    assert first.parts[-2:] == ("rps7200", "locks")
