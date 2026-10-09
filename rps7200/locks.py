"""Who may write a library, a roll folder, a spool or a settings file while
another program has it.

Nothing stopped two of them. The window, `tools/scan_roll.py`, `tools/
library.py compact --write` and a second window could each rewrite the same
library entries, roll folder or remembered settings at the same moment: a
compaction swapping an entry's files under the window that was filing it, a
Rename of a roll scan_roll was scanning into, the last window to quit
writing its settings and sheet decisions over the first's. The scanner
allows one process, so only one can ever scan; any number could write.

So each of those is locked, and every refusal names who holds it. Stefan's
call, 2026-10-09: one window per library, rolls folder and settings file;
`compact --write`, the migrations, `duplicates --delete` and `file-spool`
refuse while any window is open on that library.

**Where.** The locks are files in a folder of their own per user
(`locks_dir`), never inside the folders they protect. Measured on Windows
before this was written: a folder cannot be renamed while another process
holds a file inside it open, so a lock kept in a roll folder would have
refused the very Rename it guards; `copytree` copies such a file into every
Duplicate; and a library that does not exist yet -- a fresh checkout, the
demo's first run -- or one on a read-only disk has nowhere to keep one.
`RPS7200_LOCKS` moves the folder, for the tests. Moved by hand to get past a
refusal, it would let two programs write over each other again.

**What a lock is.** One byte of a file, locked by the operating system
(`msvcrt.locking` on Windows, `flock` elsewhere), which frees it the moment
its process ends, however it ends: a crashed holder never leaves a lock
behind, and liveness is never judged by a process id -- Windows refuses to
open 108 of 219 of Stefan's own processes for that question, and ids are
reused. Shared and exclusive locks are built from that one exclusive lock:
a gate file, and one record per holding process, each held open and locked
by its holder (see `take`).

**Never stalls the device.** A lock is waited on for `WAIT_S` at most, and
none is taken inside a pass: before the scanner opens, between jobs before
the film moves, on the window's own thread before it hands a job over, or
after the scanner has closed. Every release is in a `finally`.

**Inside one process** a lock is counted, not refused: the window's own
checks decide what its threads may do to each other (`take`).
"""
from __future__ import annotations

import contextvars
import errno
import functools
import hashlib
import json
import os
import socket
import sys
import threading
import time
import unicodedata
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

#: Moves the lock folder: a test's own, so a suite never meets the window
#: Stefan has open. Not for getting past a refusal.
LOCKS_ENV = "RPS7200_LOCKS"

#: What is locked. A library: held shared by everything that files into it,
#: exclusive by the commands that rewrite or remove what is in it.
LIBRARY = "library"
#: A window's library, rolls folder and settings file, exclusive: one window
#: each.
WINDOW = "window"
#: A roll folder, exclusive, by whatever writes into it.
ROLL = "roll"
#: A roll folder, shared by whatever shows or reads it, exclusive by a
#: Rename or a Delete, after which there is nothing to show.
ROLL_VIEW = "roll-view"
#: A debug spool, exclusive, by the session spooling into it.
SPOOL = "spool"

#: The longest a lock is waited for. Another taker holds the gate for the
#: moment it takes to write a record; anything longer is a holder, and is
#: named rather than waited out.
WAIT_S = 0.5
_POLL_S = 0.02

#: The byte locked: far past anything a record or a marker holds, so their
#: text stays readable while they are held -- measured on Windows, where a
#: locked range cannot be read by anyone else.
_BYTE = 0x7FFFFFF0

#: How long the unlink of a record is retried: on Windows a file another
#: process has open -- a prober, for a moment -- refuses one.
_UNLINK_RETRY_S = (0.02, 0.05, 0.1)

_BINARY = getattr(os, "O_BINARY", 0)

# `_try_lock` answers False only where another handle has the byte -- what
# each system says for that and nothing else -- and raises any other
# failure: a disk that locks nothing, NFS without its lock daemon or some
# FUSE, WebDAV and SMB mounts, which say ENOLCK, ENOTSUP or EINVAL. Taken
# for "held", a marker on such a disk read as a save writing it now, and
# `verify` named a process long gone as its writer and passed over the
# half-written entry.
if sys.platform == "win32":
    import msvcrt

    # LK_NBLCK over a byte another handle has: EACCES, measured here on
    # 2026-10-09 -- ERROR_LOCK_VIOLATION as the C runtime maps it.
    _HELD = frozenset({errno.EACCES, errno.EDEADLK})

    def _try_lock(fd: int) -> bool:
        os.lseek(fd, _BYTE, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError as exc:
            if exc.errno in _HELD:
                return False
            raise
        finally:
            os.lseek(fd, 0, os.SEEK_SET)

    def _unlock(fd: int) -> None:
        os.lseek(fd, _BYTE, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        finally:
            os.lseek(fd, 0, os.SEEK_SET)

    def _home() -> Path:
        return Path.home()
else:
    import fcntl
    import pwd

    # `flock`, never `lockf`: a POSIX record lock is dropped when *any*
    # descriptor of the file closes in the process, so a probe of one's own
    # record would have freed it. `flock` belongs to the open file, which a
    # second open does not share -- the same in one process as across two,
    # as on Windows.
    _HELD = frozenset({errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES})

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError as exc:
            if exc.errno in _HELD:
                return False
            raise

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)

    def _home() -> Path:
        """This user's home as the system records it for the user id, not
        as $HOME says: cron, ``sudo -u`` and ``env -i`` set it otherwise or
        not at all."""
        try:
            return Path(pwd.getpwuid(os.getuid()).pw_dir)
        except (KeyError, OSError):
            return Path.home()


def locks_dir() -> Path:
    """The folder the locks are kept in: `RPS7200_LOCKS`, or the user's own.

    %LOCALAPPDATA%\\rps7200\\locks on Windows, ~/Library/Application
    Support/rps7200/locks on macOS and ~/.local/state/rps7200/locks
    elsewhere. The same for the window and for every session Claude runs on
    this machine, which is what lets them see each other -- so chosen by who
    runs it, never by the environment it runs in: under $XDG_RUNTIME_DIR, a
    window started from the desktop and a `compact --write` started from
    cron, which has none, looked in two folders and never saw each other.
    Nor in a cache: macOS empties ~/Library/Caches under pressure, as
    cleaning tools empty ~/.cache, and on POSIX a held lock file deleted
    keeps its lock on a file nobody else can open, while the next taker
    makes a new one and holds that too.
    """
    moved = os.environ.get(LOCKS_ENV, "").strip()
    if moved:
        return Path(moved)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(
            _home() / "AppData" / "Local")
        return Path(base) / "rps7200" / "locks"
    if sys.platform == "darwin":
        return (_home() / "Library" / "Application Support" / "rps7200"
                / "locks")
    return _home() / ".local" / "state" / "rps7200" / "locks"


def key(path: str | Path) -> str:
    """One folder's or file's name for its locks, however it is spelled.

    Its full path, with every part that exists resolved -- links and
    junctions followed, short names written out -- and the rest as given,
    folded: the same for a relative and an absolute spelling, a trailing
    separator, another case and a link on the way, and the same before and
    after the folder, or any folder above it, is made. Keyed by the folder
    it is in -- its volume and file number -- a roll's folder changed key
    when the first roll made the rolls folder above it, and a program that
    had taken its lock before then was not seen by one that took it after.
    """
    path = Path(os.path.abspath(path))
    there, rest = path, []
    while there.parent != there:
        try:
            if there.exists():
                break
        except OSError:
            pass
        rest.append(there.name)
        there = there.parent
    try:
        found = os.path.realpath(there)
    except (OSError, ValueError):
        found = str(there)
    ident = unicodedata.normalize(
        "NFC", os.path.normcase(os.path.join(found, *reversed(rest))))
    return hashlib.sha1(ident.casefold().encode("utf-8", "surrogatepass")
                        ).hexdigest()[:16]


def program() -> str:
    """This program as a person would name it: ``tools/scan_roll.py``,
    ``pytest``, or ``python`` for a script given on the command line."""
    first = sys.argv[0] if sys.argv else ""
    if not first or first in ("-c", "-m"):
        return "python"
    path = Path(first)
    if path.parent.name == "tools":
        return f"tools/{path.name}"
    return path.stem if path.suffix.lower() == ".exe" else path.name


def _record_of(kind: str, path: Path, mode: str, what: str) -> dict[str, Any]:
    """What a record says of its holder, for the refusals that name it."""
    return {"lock": kind, "path": str(path), "mode": mode, "what": what,
            "program": program(), "pid": os.getpid(),
            "host": socket.gethostname(),
            "since_utc": datetime.now(timezone.utc).isoformat(
                timespec="seconds"),
            "cwd": os.getcwd(), "argv": list(sys.argv)}


def _since(text: Any) -> str | None:
    """A record's time as a person reads it: the hour today, the day too
    otherwise."""
    try:
        when = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    when = when.astimezone(timezone.utc)
    if when.date() == datetime.now(timezone.utc).date():
        return when.strftime("%H:%M UTC")
    return when.strftime("%Y-%m-%d %H:%M UTC")


def describe(holder: dict[str, Any]) -> str:
    """One holder, as a refusal names it: what it is, its program, its
    process, since when and in which checkout -- ``the window
    (tools/gui.py, pid 4242, since 11:02 UTC, in C:\\...)``."""
    what = str(holder.get("what") or "a process")
    said = []
    named = holder.get("program")
    if named and str(named) not in what:
        said.append(str(named))
    if holder.get("pid") is not None:
        said.append(f"pid {holder['pid']}")
    since = _since(holder.get("since_utc"))
    if since:
        said.append(f"since {since}")
    host = holder.get("host")
    if host and host != socket.gethostname():
        said.append(f"on {host}")
    if holder.get("cwd"):
        said.append(f"in {holder['cwd']}")
    return f"{what} ({', '.join(said)})" if said else what


def who(holders: Iterable[dict[str, Any]]) -> str:
    """Every holder, as one phrase."""
    return " and ".join(describe(h) for h in holders) or "another process"


class Refused(OSError):
    """A lock another process holds -- each holder in ``holders`` -- or, with
    none named, one that could not be taken at all.

    An OSError, so every caller that already copes with a folder refusing a
    write copes with this the same way: a failed job, a copy not written.
    ``kind`` and ``path`` say which lock it was.
    """

    def __init__(self, message: str, holders: Iterable[dict[str, Any]] = (),
                 kind: str = "", path: str | Path = ""):
        super().__init__(message)
        self.holders = list(holders)
        self.kind = kind
        self.path = str(path)

    def __str__(self) -> str:
        return str(self.args[0])

    @property
    def who(self) -> str:
        return who(self.holders)


class _Hold:
    """This process's hold on one lock: its record, the gate while it is
    exclusive, and how many takes each way it answers for."""

    def __init__(self) -> None:
        self.gate: int | None = None
        self.record: int | None = None
        self.record_path: Path | None = None
        self.shared = 0
        self.exclusive = 0


_holds: dict[tuple[str, str, str], _Hold] = {}
_holds_lock = threading.Lock()


class Held:
    """A lock taken by `take`: released by `release`, or by leaving a
    ``with``. Releasing twice is nothing.

    ``note`` is set, and nothing is held, where the lock could not be taken
    for a reason that is no holder -- a lock folder that cannot be written --
    and the caller was not one that must have it. Said once by the caller;
    the work goes on unguarded, as it did before there were locks.
    """

    def __init__(self, ident: tuple[str, str, str] | None, exclusive: bool,
                 note: str | None = None):
        self._ident = ident
        self._exclusive = exclusive
        self.note = note

    @property
    def held(self) -> bool:
        return self._ident is not None

    def release(self) -> None:
        ident, self._ident = self._ident, None
        if ident is not None:
            _release(ident, self._exclusive)

    def __enter__(self) -> Held:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def take(kind: str, path: str | Path, *, exclusive: bool,
         what: str | None = None, required: bool = False) -> Held:
    """Take the ``kind`` lock on ``path``, shared or exclusive. Never waits
    longer than `WAIT_S`.

    Raises `Refused`, naming them, where another process holds it in a way
    this cannot share. Where it cannot be taken for any other reason, a
    shared take hands back a `Held` that holds nothing and says why
    (`Held.note`) -- a lock folder that cannot be written is no reason to
    refuse a scan -- and an exclusive one ``required`` raises `Refused`
    naming nobody: the commands that rewrite a library must know they are
    alone, and do nothing when they cannot.

    How. Shared: the gate is locked, this process's record made and locked
    beside it, and the gate let go. Exclusive: the gate is locked and kept,
    every record probed -- one whose lock can be taken is a dead holder's,
    deleted there and then, and only ever by whoever holds the gate -- and
    any live one refuses; an exclusive holder writes a record too, so one
    refused at the gate can be named. A shared hold in this process is made
    exclusive the same way, against the other processes' records.

    Inside this process a lock already held is counted, not refused, from
    any thread; it is exclusive while any exclusive take is held, shared
    again once the last of those is released (`_release`). ``what``
    is how a refusal elsewhere names this holder: "the window",
    "tools/library.py compact --write"; this program's name unless given.
    """
    path = Path(path)
    what = what or program()
    folder = locks_dir()
    name = f"{kind}-{key(path)}"
    ident = (str(folder), kind, name)
    mode = "exclusive" if exclusive else "shared"
    with _holds_lock:
        hold = _holds.get(ident)
        fresh = hold is None
        try:
            if hold is None:
                hold = _Hold()
                folder.mkdir(parents=True, exist_ok=True)
                _take(folder, name, kind, path, hold, mode, what)
                _holds[ident] = hold
            elif exclusive and hold.gate is None:
                _upgrade(folder, name, kind, path, hold)
        except Refused:
            raise
        except OSError as exc:
            if fresh and hold is not None:
                _let_go(hold)
            said = f"could not take the {kind} lock on {path} ({exc})"
            if required:
                raise Refused(said, kind=kind, path=path) from exc
            return Held(None, exclusive, note=said)
        if exclusive:
            hold.exclusive += 1
        else:
            hold.shared += 1
    return Held(ident, exclusive)


def _take(folder: Path, name: str, kind: str, path: Path, hold: _Hold,
          mode: str, what: str) -> None:
    """A first take in this process: the gate, the records, a record."""
    gate = _gate(folder, name, kind, path)
    try:
        if mode == "exclusive":
            live = _sweep(folder, name, mine=None)
            if live:
                raise Refused(f"{path} is in use by {who(live)}", live,
                              kind=kind, path=path)
        hold.record, hold.record_path = _record(
            folder, name, _record_of(kind, path, mode, what))
    except BaseException:
        _unlock(gate)
        os.close(gate)
        raise
    if mode == "exclusive":
        hold.gate = gate
    else:
        _unlock(gate)
        os.close(gate)


def _upgrade(folder: Path, name: str, kind: str, path: Path,
             hold: _Hold) -> None:
    """Make this process's shared hold exclusive, where no other process
    holds the lock."""
    gate = _gate(folder, name, kind, path, mine=hold.record_path)
    try:
        live = _sweep(folder, name, mine=hold.record_path)
        if live:
            raise Refused(f"{path} is in use by {who(live)}", live,
                          kind=kind, path=path)
    except BaseException:
        _unlock(gate)
        os.close(gate)
        raise
    hold.gate = gate


def _gate(folder: Path, name: str, kind: str, path: Path,
          mine: Path | None = None) -> int:
    """The gate, locked: waited for `WAIT_S` at most, and its holders --
    all but ``mine``, this process's own record -- named when it is not let
    go by then."""
    fd = os.open(folder / f"{name}.gate", os.O_RDWR | os.O_CREAT | _BINARY)
    try:
        if _lock_fresh(fd):
            return fd
    except BaseException:
        os.close(fd)
        raise
    os.close(fd)
    live = _live(folder, name, mine)
    if live:
        raise Refused(f"{path} is in use by {who(live)}", live,
                      kind=kind, path=path)
    raise OSError(f"its gate stayed locked for {WAIT_S:g} s with no holder "
                  "to name")


def _lock_fresh(fd: int) -> bool:
    """Lock the file open on ``fd``, waiting `WAIT_S` at most; False where
    it is still held by then.

    The gate is held by another taker for as long as it writes a record. A
    record or a marker this process has just made is held by nobody but a
    prober (`_probe`), and by that only for the instant it asks: tried
    once, a take that met one went unguarded, and a save's marker was
    written plainly and reported -- by the `verify` that had asked -- as a
    save that did not finish.
    """
    deadline = time.monotonic() + WAIT_S
    while not _try_lock(fd):
        if time.monotonic() >= deadline:
            return False
        time.sleep(_POLL_S)
    return True


def _record(folder: Path, name: str, said: dict[str, Any]
            ) -> tuple[int, Path]:
    """This process's record, made, locked and written. No colon in its
    name: NTFS reads one as a stream's."""
    path = folder / f"{name}.{os.getpid()}-{time.time_ns()}.json"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | _BINARY)
    try:
        if not _lock_fresh(fd):
            raise OSError(f"{path.name} could not be locked")
        _write_all(fd, json.dumps(said, indent=1).encode("utf-8"))
    except BaseException:
        os.close(fd)
        _unlink(path)
        raise
    return fd, path


def _write_all(fd: int, data: bytes) -> None:
    os.lseek(fd, 0, os.SEEK_SET)
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def _records(folder: Path, name: str) -> list[Path]:
    try:
        return sorted(folder.glob(f"{name}.*.json"))
    except OSError:
        return []


def _probe(path: Path) -> bool | None:
    """Whether ``path`` is held: True, False, or None when it cannot be
    asked -- gone, being removed, or on a disk that locks nothing.

    Asking takes the lock for an instant, so another prober asking at the
    same moment is seen as a holder: held is believed only when it is seen
    twice, `_POLL_S` apart. Once was enough to take a dead holder's record
    for a live one, and to refuse a maintainer in the name of a process
    that had gone. The file is closed in between, so a holder letting go
    can remove it meanwhile -- which Windows refuses while it is open.
    """
    for attempt in range(2):
        if attempt:
            time.sleep(_POLL_S)
        try:
            fd = os.open(path, os.O_RDONLY | _BINARY)
        except OSError:
            return None
        try:
            if _try_lock(fd):
                _unlock(fd)
                return False
        except OSError:
            return None
        finally:
            os.close(fd)
    return True


def _read(path: Path) -> dict[str, Any]:
    """A live record's text; its process id from its name where the text
    cannot be read, which is still enough to name it."""
    try:
        said = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(said, dict):
            return said
    except (OSError, ValueError):
        pass
    pid = path.name.rsplit(".", 2)[-2].split("-")[0] if "." in path.name else ""
    return {"what": "a process", "pid": int(pid) if pid.isdigit() else None}


def _live(folder: Path, name: str, mine: Path | None = None
          ) -> list[dict[str, Any]]:
    """Every live record but ``mine``, read-only."""
    return [_read(p) for p in _records(folder, name)
            if p != mine and _probe(p)]


def _sweep(folder: Path, name: str, mine: Path | None
           ) -> list[dict[str, Any]]:
    """Every live record but ``mine``, deleting each dead one. Only ever
    called holding the gate, so no record is being made meanwhile."""
    live = []
    for path in _records(folder, name):
        if path == mine:
            continue
        held = _probe(path)
        if held:
            live.append(_read(path))
        elif held is False:
            _unlink(path)
    return live


def _unlink(path: Path) -> None:
    """Remove a record, patient with a prober holding it for a moment; one
    that will not go is a dead record, swept by the next exclusive take."""
    for wait in (*_UNLINK_RETRY_S, None):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if wait is None:
                return
            time.sleep(wait)
        except OSError:
            return


def _let_go(hold: _Hold) -> None:
    """Unlock and close what ``hold`` has, its record first. Gate files are
    never removed: one is shared by every taker of that lock."""
    if hold.record is not None:
        try:
            _unlock(hold.record)
        except OSError:
            pass
        os.close(hold.record)
        hold.record = None
    if hold.record_path is not None:
        _unlink(hold.record_path)
        hold.record_path = None
    if hold.gate is not None:
        try:
            _unlock(hold.gate)
        except OSError:
            pass
        os.close(hold.gate)
        hold.gate = None


def _release(ident: tuple[str, str, str], exclusive: bool) -> None:
    with _holds_lock:
        hold = _holds.get(ident)
        if hold is None:
            return
        if exclusive:
            hold.exclusive -= 1
        else:
            hold.shared -= 1
        if hold.shared + hold.exclusive > 0:
            if hold.exclusive == 0 and hold.gate is not None:
                # The last exclusive take let go, shared ones left: shared
                # again, its record kept and its gate let go. Kept
                # exclusive until every take was released, a roll this
                # window showed stayed refused to every other window for as
                # long as it was shown, named as "being renamed or deleted".
                gate, hold.gate = hold.gate, None
                try:
                    _unlock(gate)
                except OSError:
                    pass
                os.close(gate)
            return
        del _holds[ident]
        _let_go(hold)


def release_all() -> None:
    """Let go of every lock this process holds: for a test that ends with
    some still taken, as the end of a process lets go of them all."""
    with _holds_lock:
        holds = list(_holds.values())
        _holds.clear()
        for hold in holds:
            _let_go(hold)


def holders(kind: str, path: str | Path) -> list[dict[str, Any]]:
    """Every live holder of the ``kind`` lock on ``path``, this process
    included, as their records say. Read-only: a dead record is passed
    over, never deleted, here."""
    return _live(locks_dir(), f"{kind}-{key(path)}")


# -- a lock on one file of the data's own --------------------------------------
#
# An entry's INCOMPLETE marker is locked by the save writing it, for as long
# as it writes: `verify` tells an entry being written now from one a crash or
# a failed save left half written by whether that lock is free -- per save,
# never per process, since a window whose save raised lives on for days.


class FileHold:
    """One file held open and locked (`hold_file`), its descriptor ``fd``
    for writing through. Released by `release`, or leaving a ``with``;
    twice is nothing. One holding nothing (``fd`` None) is the file written
    without a lock where none could be taken."""

    def __init__(self, fd: int | None, path: Path):
        self.fd = fd
        self.path = path

    def release(self) -> None:
        fd, self.fd = self.fd, None
        if fd is None:
            return
        try:
            _unlock(fd)
        except OSError:
            pass
        os.close(fd)

    def __enter__(self) -> FileHold:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()

    def __del__(self) -> None:
        # A hold nobody keeps can be let go by nobody: let go here, rather
        # than leave a marker held -- "being written now" -- for as long as
        # the process lives.
        try:
            self.release()
        except Exception:                                 # noqa: BLE001
            pass


#: The file holds made inside a call `letting_go` wraps, for it to let go.
_file_holds: contextvars.ContextVar[list[FileHold] | None] = (
    contextvars.ContextVar("file_holds", default=None))


def hold_file(path: str | Path) -> FileHold:
    """Open ``path`` -- made where it is not there -- and lock it.

    Raises OSError where it cannot be locked: still held by another writer
    after `WAIT_S` -- a prober holds it for an instant, and is waited out
    (`_lock_fresh`) -- or on a disk that locks nothing. The file's text is
    untouched, and readable by everyone while it is held. Inside a call
    `letting_go` wraps, the hold is let go when that call ends, however it
    ends.
    """
    path = Path(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | _BINARY)
    try:
        locked = _lock_fresh(fd)
    except BaseException:
        os.close(fd)
        raise
    if not locked:
        os.close(fd)
        raise OSError(f"{path} is held by another writer")
    hold = FileHold(fd, path)
    made = _file_holds.get()
    if made is not None:
        made.append(hold)
    return hold


def file_held(path: str | Path) -> bool | None:
    """Whether a writer holds ``path`` now (`hold_file`): None where it
    cannot be asked -- not there, not to be opened, or on a disk that
    locks nothing, where no writer can have held it either."""
    return _probe(Path(path))


_P = ParamSpec("_P")
_R = TypeVar("_R")


def letting_go(function: Callable[_P, _R]) -> Callable[_P, _R]:
    """``function``, with every file it locks (`hold_file`) let go when it
    ends, however it ends.

    A wrapper, as `direct._keeps_what_a_failed_pass_left` is, so every way
    out is covered without the function being re-indented around a handler.
    The function lets go itself where it must -- a marker is unlocked and
    closed before it is removed, which Windows refuses while it is open --
    and this lets go of what a failure left held: the marker stays, free,
    and `verify` reports it as the save that did not finish it is.
    """
    @functools.wraps(function)
    def run(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        made: list[FileHold] = []
        token = _file_holds.set(made)
        try:
            return function(*args, **kwargs)
        finally:
            _file_holds.reset(token)
            for hold in made:
                hold.release()

    return run
