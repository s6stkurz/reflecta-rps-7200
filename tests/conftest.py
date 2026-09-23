"""Shared test setup.

``RPS7200_NO_TIFFFILE=1`` makes ``import tifffile`` fail for the whole run, so
the suite can be executed as it would be on a bare install::

    uv run pytest tests/ -q                       # tifffile present
    RPS7200_NO_TIFFFILE=1 uv run pytest tests/ -q # tifffile absent

Both must pass. ``tests/test_tiff.py`` monkeypatches ``_has_tifffile`` per test,
which is what makes the cross-implementation matrix possible, but a monkeypatch
only proves each call site picks the right branch. This proves the *package*
works with the dependency genuinely missing -- that no other module imports
tifffile behind the driver's back, and that the optional dependency really is
optional.
"""

import os
import sys
from importlib.abc import MetaPathFinder


class _BlockTifffile(MetaPathFinder):
    """Refuse tifffile the way an absent install does.

    ModuleNotFoundError rather than a bare ImportError, because that is what a
    missing package raises -- and ``pytest.importorskip`` re-raises anything
    else rather than skipping, on the grounds that a broken install should not
    look like an absent one.
    """

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "tifffile" or fullname.startswith("tifffile."):
            raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
        return None


if os.environ.get("RPS7200_NO_TIFFFILE"):
    sys.modules.pop("tifffile", None)
    sys.meta_path.insert(0, _BlockTifffile())


# ---------------------------------------------------------------------------
# Shared doubles
# ---------------------------------------------------------------------------
#
# Only what more than one module needs lives here. `FakeScanner`
# (test_metering) and `FakeRoll` (test_roll) stay beside their tests: each is
# tuned to the loop it exercises, and merging them would produce one double
# that models neither well.

import numpy as np  # noqa: E402

from rps7200.direct import DirectScanner, RollFrame, Settings  # noqa: E402
from rps7200.usb_transport import CheckCondition  # noqa: E402

#: The device's own power-on gain and offset, as READ GAIN/OFFSET reports them.
#: Shared so a test that cares about exposure does not have to restate the two
#: it does not care about.
DEVICE_GAIN = [40, 33, 21, 25]
DEVICE_OFFSET = [12, 10, 28, 10]


def settings(*exposure: int) -> Settings:
    """A `Settings` at ``exposure``, with the device's own gain and offset."""
    return Settings(
        exposure=list(exposure), gain=list(DEVICE_GAIN), offset=list(DEVICE_OFFSET)
    )


class FakeTransport:
    """Records commands, and answers READ_STATE from a scripted position.

    Stands in for `rps7200.usb_transport.Transport` wherever a test is about
    *which bytes the driver sends*, which is most of the protocol surface. It
    deliberately implements only `command()`: anything reaching further into the
    transport should be tested against `FakeUsb` instead, which fakes libusb.
    """

    def __init__(self, positions=(0,), replies=None):
        # One entry per READ_STATE; None means the read fails, which is what the
        # scanner does on the reading right after an advance.
        self.positions = list(positions)
        # opcode -> bytes, or opcode -> callable(command) -> bytes, for commands
        # a test needs an answer from. Anything unlisted answers empty.
        self.replies = dict(replies or {})
        self.sent = []
        self.states = 0

    def command(self, command, data=None, read_size=0, timeout_ms=0, max_wait_s=60.0):
        self.sent.append((command[0], bytes(data) if data else b""))
        if command[0] == 0xDD:  # READ_STATE
            i = min(self.states, len(self.positions) - 1)
            self.states += 1
            position = self.positions[i]
            if position is None:
                raise CheckCondition(0xDD)
            blob = bytearray(13)
            blob[2] = position
            return bytes(blob)
        reply = self.replies.get(command[0])
        if callable(reply):
            return reply(command)
        return reply if reply is not None else b""

    def payloads(self, opcode: int) -> list[bytes]:
        """Every data payload sent with ``opcode``, in order."""
        return [data for sent, data in self.sent if sent == opcode]


class FilmOnFrame:
    """A film transport for a scanner double: the counter, and whole frames.

    Mixed in ahead of `DirectScanner` (``class X(FilmOnFrame, DirectScanner)``)
    so these win over the real methods, which would reach for a USB transport
    the double does not have. A roll now asks where the film is before it
    moves anything, the window and the roll tool alike, so a double with no
    transport at all is a scanner that will not say -- and every roll refuses.

    ``at`` is the counter, 0-based: frame 1 of the strip by default.
    ``last`` is where the strip ends. ``moves`` is every whole-frame command,
    in order, so a test can say which way the film went.
    """

    at = 0
    last = 16
    moves: list | None = None
    #: How many times the lamp was waited for. A roll does before it asks the
    #: counter anything, as `session.seek` does of the real scanner.
    warmed = 0

    def wait_warm(self, timeout=300.0, poll=5.0):
        self.warmed += 1

    def position(self):
        return self.at

    def advance(self, steps=1, **kw):
        self._moved("advance", steps)
        if self.at >= self.last:
            return None
        self.at += 1
        return self.at

    def retreat(self, steps=1, **kw):
        self._moved("retreat", steps)
        if self.at <= 0:
            return None
        self.at -= 1
        return self.at

    def _moved(self, way, steps):
        # Made on first use: the doubles this mixes into skip
        # `DirectScanner.__init__`, which would open a transport.
        if self.moves is None:
            self.moves = []
        self.moves.append((way, steps))


class StripScanner(FilmOnFrame):
    """A scanner on a strip, for driving `ScanSession` end to end.

    Its roll is `DirectScanner.scan_roll`'s loop in miniature and nothing
    kinder: the first frame is **wherever the film is**, the film advances
    between frames and past the ones not chosen, and the roll ends at its
    count, after its last chosen frame, or where the strip runs out. The index
    counts from ``first_index`` and ``skip`` advances first, as the driver's
    does. The doubles that put frame ``i`` at position ``i`` whatever the
    transport said are how a roll that started on frame 11 and called it 1
    passed every test, so this one does not.

    ``sticks_at`` stops a rewind there. ``silent`` is a transport that never
    says where the film is. ``rolls`` records what each roll was asked, and how
    many whole-frame moves had happened before it began.
    """

    def __init__(self, at=0, last=16, sticks_at=None, silent=False):
        self.at, self.last = at, last
        self.sticks_at, self.silent = sticks_at, silent
        self.moves = []
        self.rolls = []
        self.log_hook = None
        self.progress_hook = None

    def open(self):
        return self

    def close(self):
        pass

    def inquiry(self, refresh=False):
        return "STRIP  test double"

    def capture_record(self):
        return {"reference": None, "ccd_mask": None, "raw": None,
                "raw_layout": None}

    def position(self):
        return None if self.silent else self.at

    def retreat(self, steps=1, **kw):
        if self.sticks_at is not None and self.at <= self.sticks_at:
            self._moved("retreat", steps)
            return None
        return super().retreat(steps, **kw)

    def prescan(self, resolution=300, keep_raw=False, film="negative", **kw):
        return self._picture(self.at, np.uint8), None

    def scan(self, resolution=1800, infrared=True, **kw):
        return self._picture(self.at, np.uint16), {
            "resolution_dpi": resolution, "channel_order": list("RGB")}

    @staticmethod
    def _picture(at, dtype):
        # Different at every position, so a test can tell frames apart.
        return np.full((6, 8, 3), 10 + at, dtype=dtype)

    def scan_roll(self, frames=None, resolution=1800, infrared=True,
                  dry_run=False, skip=0, only=None, first_index=0,
                  should_stop=None, **kw):
        self.rolls.append({"first_index": first_index, "skip": skip,
                           "only": only, "frames": frames,
                           "moves_before": len(self.moves), "at": self.at})
        index = first_index
        for _ in range(skip):
            if self.advance() is None:
                return
            index += 1
        # The driver's own end rule, so this double cannot drift from it the
        # way the demo's retyped copy did.
        finished = DirectScanner.roll_ends(first_index, skip, frames, only)
        while not finished(index):
            if only is None or index in only:
                prescan, _ = self.prescan()
                image, meta = ((None, {}) if dry_run
                               else self.scan(resolution, infrared))
                yield RollFrame(index=index, position=self.at, image=image,
                                meta=meta, prescan=prescan, registration={})
            index += 1
            if finished(index):
                return
            if self.advance() is None:
                return


def strip_picture(place: int) -> np.ndarray:
    """A 300 dpi-ish negative, different at every place on a strip.

    The levels are the film's own from a real prescan (34/15/7), with enough
    grain that `frame_contrast` reads it as a picture rather than clear film.
    """
    rng = np.random.default_rng(place + 1)
    varied = np.array((34, 15, 7)) * (1 + rng.normal(0, 0.55, (40, 60, 3)))
    return np.clip(varied, 0, 255).astype(np.uint8)


class StripTransport:
    """A strip under the transport, answering at the level the driver speaks.

    `SLIDE_NEXT` and `SLIDE_PREV` move the film, and READ_STATE byte 2 says
    where it is -- empty on the read straight after a move, as the device's
    is -- so `DirectScanner`'s own advance, retreat, wait and position run on
    it unchanged. Nothing above the transport is imitated.

    ``double_steps`` are the places whose advance moves the film two: the one
    failure a roll's own count cannot see, and the counter can.
    ``goes_back`` maps a place to where its next advance lands instead, once
    -- a counter that reads behind the count, which nothing has seen here.

    ``warm_at`` is when the lamp is warm, on ``clock`` (a `NoWaiting`). Until
    then every command but REQUEST SENSE is refused, and the sense says NOT
    READY -- what the device does for its first ~80 s, READ_STATE included,
    by `DirectScanner.wait_warm`'s docstring rather than by a measurement.
    ``while_warming`` is every opcode sent before then.
    """

    def __init__(self, at=0, last=16, double_steps=(), warm_at=0.0,
                 clock=None, goes_back=None):
        self.at, self.last = at, last
        self.double_steps = set(double_steps)
        self.goes_back = dict(goes_back or {})
        self.warm_at, self.clock = warm_at, clock
        self.sent = []
        self.while_warming = []
        self.closed = False
        self._empty = 0

    def warming(self):
        return self.clock is not None and self.clock.now < self.warm_at

    def command(self, command, data=None, read_size=0, timeout_ms=0,
                max_wait_s=60.0):
        from rps7200.protocol import (SCSI_READ_STATE, SCSI_REQUEST_SENSE,
                                      SCSI_SLIDE, SLIDE_NEXT, SLIDE_PREV)

        opcode = command[0]
        self.sent.append((opcode, bytes(data) if data else b""))
        if self.warming():
            self.while_warming.append(opcode)
            if opcode == SCSI_REQUEST_SENSE:
                sense = bytearray(14)
                sense[2], sense[12] = 0x02, 0x04          # NOT READY
                return bytes(sense)
            raise CheckCondition(opcode)
        if opcode == SCSI_REQUEST_SENSE:
            return bytes(14)
        if opcode == SCSI_SLIDE and data:
            if data[0] == SLIDE_NEXT and self.at in self.goes_back:
                self.at = self.goes_back.pop(self.at)
            elif data[0] == SLIDE_NEXT and self.at < self.last:
                step = 2 if self.at in self.double_steps else 1
                self.at = min(self.last, self.at + step)
            elif data[0] == SLIDE_PREV and self.at > 0:
                self.at -= 1
            self._empty = 1
            return b""
        if opcode == SCSI_READ_STATE:
            if self._empty:
                self._empty -= 1
                raise CheckCondition(opcode)
            blob = bytearray(13)
            blob[2] = self.at
            return bytes(blob)
        return b""

    def close(self):
        self.closed = True


class ScannerOnStrip(DirectScanner):
    """The real driver on a `StripTransport`, with its passes stood in for.

    Only the passes are replaced -- a prescan is `strip_picture` of the place
    the film is on, so a test can say which picture was filed under which
    number. Every transport command and every roll decision is the driver's.
    ``held`` records each approved position the roll reached for, as
    ``(index, the Approved's number)``.
    """

    def __init__(self, at=0, last=16, double_steps=(), warm_at=0.0,
                 clock=None, goes_back=None):
        super().__init__(
            transport=StripTransport(at, last, double_steps, warm_at, clock,
                                     goes_back),
            verbose=False, debug=False)
        self.held = []
        self.logged = []
        self.log_hook = self.logged.append

    def open(self):
        return self

    def close(self):
        pass

    def inquiry(self, refresh=False):
        return "STRIP  transport-level double"

    def capture_record(self):
        return {"reference": None, "ccd_mask": None, "raw": None,
                "raw_layout": None}

    def get_gain_offset(self):
        return settings(9604, 6506, 6506, 7745)

    def set_gain_offset(self, s, infrared=False):
        pass

    def prescan(self, resolution=300, frame=None, keep_raw=False, **kw):
        self.last_scan_meta = {"resolution_dpi": resolution,
                               "channel_order": ["R", "G", "B"]}
        return strip_picture(self.t.at), None

    def _hold_to_approved(self, index, image, prescan_resolution, approved,
                          **kw):
        self.held.append((index, approved.number))
        return {"outcome": "held", "moves": 0, "prescan": None}


class NoWaiting:
    """`time` for the driver, with the waiting taken out.

    `_whole_frames` sleeps between READ_STATEs and `_query` after an empty
    one; on a double those are only wall-clock. The clock still moves, by what
    each sleep asked for, so a timeout is reached exactly as it would be.
    """

    def __init__(self):
        import time as real
        self._real = real
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds

    def monotonic(self):
        return self.now

    def __getattr__(self, name):
        return getattr(self._real, name)


#: `roll.json` as 8a9ba17 -- what `main` ran until the roll learned where the
#: film is -- wrote it across a "Start at N, same roll name" resume: its own
#: `ScanSession`, run twice into roll "r", each run counting `skip` from
#: wherever the film then was and merging its frames into the file the other
#: left. Produced by running 8a9ba17's package, not typed from its code; only
#: the keys a reader looks at are kept. ``(frames, start_at)`` of each run,
#: and where the film was -> ``(number, transport_position)``:
#:
#:   tied           (3, 1) on 0, then (3, 4) where the first stopped
#:                  (1,0) (2,1) (3,2) (4,5) (5,6) (6,7)
#:   second-longer  (2, 1) on 0, then (3, 3) where the first stopped
#:                  (1,0) (2,1) (3,3) (4,4) (5,5)
#:   rewound        (3, 1) on 5, then (3, 4) wound back to 3
#:                  (1,5) (2,6) (3,7) (4,6) (5,7) (6,8)
#:   reinserted     (3, 1) on 5, then (3, 4) with the strip put in again, on 0
#:                  (1,5) (2,6) (3,7) (4,3) (5,4) (6,5)
#:   one-frame      (3, 1) on 5, then (1, 4) wound back to 3
#:                  (1,5) (2,6) (3,7) (4,6)
#:
#: Two shifts in one file, legitimately: tied in the first, the second run's
#: the commoner in the next. In the last three the runs overlap on the strip,
#: because the film went back between them -- four prev-slides, or the strip
#: taken out and put back, which resets the counter to 0 -- so strip frames 7
#: and 8, then 6, then 7 were each scanned twice, under two numbers.
RESUMED_BY_8A9BA17 = {
    # name: (start_at and frames of the last run, (number, position) pairs)
    "tied": (4, 3, [(1, 0), (2, 1), (3, 2), (4, 5), (5, 6), (6, 7)]),
    "second-longer": (3, 3, [(1, 0), (2, 1), (3, 3), (4, 4), (5, 5)]),
    "rewound": (4, 3, [(1, 5), (2, 6), (3, 7), (4, 6), (5, 7), (6, 8)]),
    "reinserted": (4, 3, [(1, 5), (2, 6), (3, 7), (4, 3), (5, 4), (6, 5)]),
    "one-frame": (4, 1, [(1, 5), (2, 6), (3, 7), (4, 6)]),
}


def resumed_by_8a9ba17(which: str = "tied") -> dict:
    """A fresh copy of one of `RESUMED_BY_8A9BA17`, as the file held it."""
    start_at, count, frames = RESUMED_BY_8A9BA17[which]
    return {
        "roll": "r", "dry_run": False, "start_at": start_at, "only": None,
        "wanted": None,
        "settings": {"frames": count, "start_at": start_at, "only": None},
        "frames": [{"number": n, "index": n - 1, "transport_position": p,
                    "registration": {}, "error": None, "done": True}
                   for n, p in frames],
    }


def frame_of(value=0, shape=(4, 4, 3), dtype=np.uint16) -> np.ndarray:
    """A constant frame, for tests that only care about shape and dtype."""
    return np.full(shape, value, dtype=dtype)


def load_tool(name: str):
    """Import a script from ``tools/`` as a module.

    ``tools/`` is a directory of scripts, not a package -- each one puts the
    repo root on ``sys.path`` and runs. Tests still have to reach the functions
    inside them, and this is the only way in that does not turn the scripts into
    something they are not.

    A tool that needs Tk -- the window -- skips the test where this Python has
    none, as `test_gui.py` does for itself. GitHub's macOS runner is such a
    Python, and a test that only borrows one pure function from the window
    failed there rather than skipping.
    """
    import importlib.util
    from pathlib import Path

    import pytest

    path = Path(__file__).resolve().parent.parent / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"tools_{name}", path)
    assert spec and spec.loader, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except ModuleNotFoundError as exc:
        if exc.name in ("tkinter", "_tkinter"):
            pytest.skip(f"tools/{name}.py needs Tk, and this Python has none")
        raise
    return module


#: The unexposed base of a C-41 negative as a 300 dpi prescan reads it, in
#: 8-bit counts: the orange mask, R/G ~2.2 and B/G ~0.53 on every roll the
#: frame-edge study measured.
C41_BASE = (66.0, 30.0, 16.0)


def negative_prescan(left: float = 0.0, right: float = 0.0, *, seed: int = 0,
                     rows: int = 286, width: int = 428, outer_left: float | None = None,
                     noise: float = 1.0) -> np.ndarray:
    """A synthetic 300 dpi C-41 prescan: picture, and base where asked. uint8.

    ``left`` / ``right`` columns of unexposed base at each border, each ending in
    a straight full-height edge at a fractional column. ``outer_left`` puts the
    *neighbouring* frame's picture back beyond the left gap: columns
    ``[0, outer_left)`` are picture again, so the gap is a band.

    The picture is what a negative is and a flat grey block is not: denser than
    base in every channel, colour varying, textured at several scales -- the
    properties the detectors in `tools/frame_edges` read. Built from numpy's
    seeded generator only, so a test's frame is the same every run.
    """
    rng = np.random.default_rng(seed)
    base = np.array(C41_BASE, dtype=np.float64)

    def smooth(scale: int) -> np.ndarray:
        coarse = rng.random((rows // scale + 2, width // scale + 2, 3))
        big = np.kron(coarse, np.ones((scale, scale, 1)))[:rows, :width]
        return big

    texture = 0.5 * smooth(40) + 0.3 * smooth(12) + 0.2 * smooth(4)
    transmission = 0.15 + 0.6 * texture              # 0.15..0.75 of base, per channel
    picture = base * transmission
    img = picture.copy()
    cols = np.arange(width, dtype=np.float64)[None, :, None]

    def coverage(start: float, stop: float) -> np.ndarray:
        """Fraction of each column inside [start, stop): partial columns at the ends."""
        return np.clip(np.minimum(cols + 1, stop) - np.maximum(cols, start), 0.0, 1.0)

    if left > 0:
        lo = 0.0 if outer_left is None else float(outer_left)
        f = coverage(lo, float(left))
        img = img * (1 - f) + base * f
    if right > 0:
        f = coverage(float(width - right), float(width))
        img = img * (1 - f) + base * f
    img = img + rng.normal(0.0, noise, img.shape)
    return np.clip(np.round(img), 0, 255).astype(np.uint8)
