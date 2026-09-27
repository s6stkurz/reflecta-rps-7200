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
import pytest  # noqa: E402

from rps7200 import settings as window_settings  # noqa: E402
from rps7200.direct import DirectScanner, RollFrame, Settings  # noqa: E402
from rps7200.usb_transport import CheckCondition  # noqa: E402


@pytest.fixture(autouse=True)
def _off_the_operators_own_state(monkeypatch, tmp_path_factory):
    """Keep every test off what the operator's own checkout remembers.

    Three variables decide where state outside a test's folder goes, and the
    suite inherits whatever the shell that runs it exported:

    - `RPS7200_SETTINGS` -- unset, the window reads and rewrites
      ``./gui-settings.json``. The window tests open rolls and close the
      window, each of which saves, and the checkout's own file was found
      holding rolls named "first" and "walk" and dozens of generated sheet
      keys: test folders, remembered as if the operator had opened them.
    - `RPS7200_DEBUG` -- CLAUDE.md tells Claude to export it always, and a test
      asserting that filing is off by default then failed in exactly that
      shell.
    - `RPS7200_DEBUG_ROOT` -- unset, a debug-filing test that completes a pass
      files into ``./library``, the operator's real one.

    So each test starts with filing off and both paths in a folder of its own.
    A folder apart from ``tmp_path``, because some tests list theirs.
    """
    isolated = tmp_path_factory.mktemp("operator")
    monkeypatch.setenv(window_settings.PATH_ENV,
                       str(isolated / "gui-settings.json"))
    monkeypatch.delenv(DirectScanner.DEBUG_ENV, raising=False)
    monkeypatch.setenv(DirectScanner.DEBUG_ROOT_ENV, str(isolated / "library"))

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
    deliberately implements only `command()`, and answers anything it was not
    given with nothing: a test that needs a whole pass to run uses
    `DeviceAtCommands`, and one about the transport's own retry and busy
    handling uses `FakeUsb`, which fakes libusb underneath a real `Transport`.
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


class FakeUsb:
    """libusb itself, scripted: the bridge's half of every transfer.

    Installed as `usb_transport._lib`, so `Transport` runs unchanged down to
    the ctypes buffers -- `_command`'s retry, busy and check-condition
    decisions, `_send_command`'s IEEE1284 select, `_read_payload`'s windows --
    and only the bus is imitated. Nothing of this was tested offline: every
    other test replaces `command()` whole, and the control plane was checked
    only on the hardware, only through INQUIRY and READ STATE, and only when
    someone opted in. A mistake there -- a command re-sent while the device
    is BUSY, a BUSY not drained after data-in -- is the class of fault this
    project associates with wedges.

    ``statuses`` answers each read of the status port, in order; running out
    fails the test, because the driver asked something the script did not
    expect. ``bulk`` is what the bulk endpoint delivers. ``transfers`` records
    every control transfer as ``(direction, request, port, length, payload)``
    -- the shapes `test_usbpcap` holds the vendor's captures to.
    """

    def __init__(self, statuses=(), bulk=b""):
        self.statuses = list(statuses)
        self.bulk = bytearray(bulk)
        self.transfers: list[tuple] = []
        self.bulk_reads: list[tuple[int, int]] = []
        self.halts_cleared = 0

    def transport(self, monkeypatch):
        """A real `Transport` on this bus, already open."""
        from rps7200 import usb_transport

        monkeypatch.setattr(usb_transport, "_lib", self)
        t = usb_transport.Transport()
        t._handle = object()          # opened; `open()` walks a device list
        return t

    # -- the libusb calls `Transport` makes ----------------------------------

    def libusb_init(self, ctx):
        return 0

    def libusb_exit(self, ctx):
        pass

    def libusb_release_interface(self, handle, interface):
        return 0

    def libusb_close(self, handle):
        pass

    def libusb_clear_halt(self, handle, endpoint):
        self.halts_cleared += 1
        return 0

    def libusb_control_transfer(self, handle, request_type, request, value,
                                index, buf, length, timeout):
        if request_type & 0x80:
            assert self.statuses, (
                "the driver read the status port more often than the script "
                f"answers; so far: {self.sequence()}")
            status = self.statuses.pop(0)
            buf[0] = status
            self.transfers.append(("in", request, value, length, bytes([status])))
        else:
            self.transfers.append(("out", request, value, length,
                                   bytes(buf[:length])))
        return length

    def libusb_bulk_transfer(self, handle, endpoint, buf, length, transferred,
                             timeout):
        import ctypes

        n = min(length, len(self.bulk))
        ctypes.memmove(buf, bytes(self.bulk[:n]), n)
        del self.bulk[:n]
        transferred._obj.value = n
        self.bulk_reads.append((length, n))
        return 0

    # -- reading it back ---------------------------------------------------

    def sequence(self) -> list:
        """The transfers as the bridge sees them: ``"select"`` for each
        IEEE1284 SCSI select, ``("byte", b)`` for each byte to the command
        port -- the command block, then any data out -- ``("status", s)``,
        and ``("length", n)`` for each bulk-length handshake."""
        from rps7200 import usb_transport as u

        out = []
        for direction, _request, port, _length, payload in self.transfers:
            if direction == "in":
                out.append(("status", payload[0]))
            elif port == u.PORT_SCSI_SIZE:
                out.append(("length", int.from_bytes(payload[4:8], "little")))
            elif port == u.PORT_SCSI_CMD:
                out.append(("byte", payload[0]))
            elif port == u.PORT_PAR_DATA and payload[0] == u.IEEE1284_SCSI:
                out.append("select")
        return out

    def shapes(self) -> set:
        """``(request_type, request, length)`` of every control transfer."""
        from rps7200 import usb_transport as u

        return {(u._REQUEST_TYPE_IN if d == "in" else u._REQUEST_TYPE_OUT,
                 request, length)
                for d, request, _port, length, _payload in self.transfers}


def _inquiry_answer() -> bytes:
    """An INQUIRY answer long enough for every offset `inquiry()` reads."""
    d = bytearray(120)
    d[4] = len(d) - 4
    d[8:16] = b"TESTDEV "
    d[16:32] = b"command double  "
    d[32:36] = b"0.00"
    d[36:38] = (7200).to_bytes(2, "little")
    d[40:42] = (10344).to_bytes(2, "little")
    d[42:44] = (6888).to_bytes(2, "little")
    d[44] = 0x10                                     # an infrared filter
    d[45] = 0x24                                     # 8 and 16 bits
    d[46] = 0x04                                     # the index format
    d[54:56] = (300).to_bytes(2, "little")
    return bytes(d)


class DeviceAtCommands:
    """The scanner itself, answering every command a pass sends, byte for byte.

    `FakeTransport` answers what a test tells it to and everything else with
    nothing, so `DirectScanner.scan()`, `prescan()` and `calibrate_shading()`
    never got past GET PARAMETERS in any test: the meta, raw pixels, raw bytes
    and command log every real entry is built from were checked by grepping
    the source. This answers at the level `Transport.command` speaks, so the
    driver's own code runs from the first READ STATE to the last line:

    - SET SCAN FRAME and MODE SELECT are parsed, and each pass is the picture
      they asked for: its width and lines from the frame at the resolution,
      three planes or four, eight bits or sixteen.
    - START SCAN makes that picture -- seeded, different for every pass -- and
      encodes it with `direction.encode_index`, bottom-up for the pass numbers
      in ``upward``, so GET PARAMETERS, the CCD mask (SCSI COPY) and every
      READ describe it. Each pass's first READ answers "not yet", as the
      device's first reads usually do.
    - A calibration (MODE SELECT's calibrate bit) is sixteen-bit lines, a dark
      phase and then a lit one, with a column falloff to correct, and ends on
      END OF DATA the way the device's does.
    - The calibration-info and shading-descriptor READs follow their prepare
      WRITE, as on the device.

    ``passes`` keeps every pass it served -- pixels, bytes and parameters -- so
    a test can hold what the driver filed against what the device sent. An
    opcode it does not know fails the test rather than answering empty: SET
    SCAN HEAD among them, and STOP SCAN, which the vendor never sends either.

    Closed, it refuses every command as `Transport` does once its handle is
    gone. ``on_read(device, n)`` is called before the ``n``th image READ of
    the whole session, so a test can act mid-pass -- a force abort, say.

    The film is a strip of ``last + 1`` frames, as `StripTransport` has it:
    SLIDE NEXT and PREV move ``position``, READ STATE byte 2 says where it is,
    and the READ STATE straight after a move is refused, as the device's is.
    """

    #: What READ GAIN/OFFSET reports: the device's own power-on values.
    EXPOSURE = (9604, 6506, 6506, 7745)

    def __init__(self, *, upward=(), seed=0, position=0, last=16,
                 on_read=None):
        self.last = last
        self._moved = False
        self.upward = set(upward)
        self.on_read = on_read
        self.reads = 0
        self.seed = seed
        self.position = position
        self.sent: list[tuple[int, bytes]] = []
        self.passes: list[dict] = []
        self.closed = False
        from rps7200.framing import FULL_FRAME
        from rps7200.protocol import DEPTH_8, ONE_PASS_COLOR
        self._frame = FULL_FRAME
        self._mode = {"resolution": 300, "passes": ONE_PASS_COLOR,
                      "depth": DEPTH_8, "calibrate": False}
        self._prepared = False
        self._sense = bytes(14)
        self._pass: dict | None = None
        self._served = 0
        self._waited = False

    def close(self):
        self.closed = True

    def _refuse(self, opcode, asc):
        sense = bytearray(14)
        sense[2], sense[12] = 0x05, asc
        self._sense = bytes(sense)
        raise CheckCondition(opcode)

    def command(self, command, data=None, read_size=0, timeout_ms=0,
                max_wait_s=60.0):
        from rps7200 import protocol as p

        from rps7200.usb_transport import UsbError

        if self.closed:
            raise UsbError("transport is not open")
        opcode = command[0]
        data = bytes(data) if data else b""
        self.sent.append((opcode, data))
        if opcode == p.SCSI_REQUEST_SENSE:
            sense, self._sense = self._sense, bytes(14)
            return sense
        if opcode == p.SCSI_SLIDE:
            if data[0] == p.SLIDE_NEXT and self.position < self.last:
                self.position += 1
                self._moved = True
            elif data[0] == p.SLIDE_PREV and self.position > 0:
                self.position -= 1
                self._moved = True
            return b""
        if opcode in (p.SCSI_TEST_UNIT_READY, p.SCSI_WRITE_GAIN_OFFSET,
                      p.SCSI_VENDOR_E7):
            return b""
        if opcode == p.SCSI_INQUIRY:
            return _inquiry_answer()[:read_size]
        if opcode == p.SCSI_READ_STATE:
            if self._moved:
                self._moved = False
                self._refuse(opcode, 0x00)
            state = bytearray(13)
            state[2] = self.position
            return bytes(state)
        if opcode == p.SCSI_READ_GAIN_OFFSET:
            d = bytearray(123)
            for offset, value in zip((60, 62, 64, 98), self.EXPOSURE):
                d[offset:offset + 2] = value.to_bytes(2, "little")
            d[66], d[67], d[68], d[100] = DEVICE_OFFSET
            d[72], d[73], d[74], d[102] = DEVICE_GAIN
            return bytes(d[:read_size])
        if opcode == p.SCSI_WRITE:
            sub = int.from_bytes(data[0:2], "little")
            if sub == p.SUB_CALIBRATION_INFO | 0x80:
                self._prepared = True
            elif sub == p.SUB_SCAN_FRAME:
                self._frame = tuple(int.from_bytes(data[i:i + 2], "little")
                                    for i in (6, 8, 10, 12))
            return b""
        if opcode == p.SCSI_MODE_SELECT:
            quality = int.from_bytes(data[9:11], "little")
            self._mode = {"resolution": int.from_bytes(data[2:4], "little"),
                          "passes": data[4], "depth": data[5],
                          "calibrate": bool(quality & p.QUALITY_CALIBRATE)}
            return b""
        if opcode == p.SCSI_SCAN:
            assert command[4] == 1, "STOP SCAN, which the vendor never sends"
            self._start()
            return b""
        if opcode == p.SCSI_COPY:
            return self._mask(read_size)
        if opcode == p.SCSI_PARAM:
            return self._parameters()
        if opcode == p.SCSI_READ:
            if self._prepared:
                self._prepared = False
                return self._descriptor() if read_size == 32 else bytes(read_size)
            return self._read(read_size)
        raise AssertionError(f"the driver sent {opcode:#04x}, which this "
                             "device double does not answer")

    def _width(self):
        x0, _, x1, _ = self._frame
        return round((x1 - x0 + 1) * self._mode["resolution"] / 7200)

    def _descriptor(self):
        d = bytearray(32)
        d[4], d[5] = 1, 6
        d[8:12] = bytes((0, 16, 16, 20))
        d[12:14] = (2 * self._width()).to_bytes(2, "little")
        return bytes(d)

    def _start(self):
        from rps7200.direction import encode_index
        from rps7200.protocol import DEPTH_8, ONE_PASS_RGBI

        number = len(self.passes)
        rng = np.random.default_rng(self.seed + number)
        width = self._width()
        upward = False
        if self._mode["calibrate"]:
            # Dark first, then lit, interleaved by channel throughout; the
            # levels are the device's own (~170 and ~47000), the falloff is
            # what shading exists to take out.
            falloff = 0.7 + 0.3 * np.cos(np.linspace(-1.2, 1.2, width))
            rows = [170.0] * 8 + [47000.0] * 12
            level = np.array(rows)[:, None, None] * falloff[None, :, None]
            noisy = level * (1 + rng.normal(0, 0.004, (len(rows), width, 3)))
            pixels = np.clip(noisy, 0, 65535).astype(np.uint16)
            channels, per_sample = 3, 2
        else:
            _, y0, _, y1 = self._frame
            lines = round((y1 - y0 + 1) * self._mode["resolution"] / 7200)
            channels = 4 if self._mode["passes"] == ONE_PASS_RGBI else 3
            eight = self._mode["depth"] == DEPTH_8
            dtype = np.uint8 if eight else np.uint16
            low, high = (5, 250) if eight else (500, 60000)
            pixels = rng.integers(low, high, (lines, width, channels),
                                  dtype=dtype)
            per_sample = 1 if eight else 2
            upward = number in self.upward
        self._pass = {
            "pixels": pixels, "blob": encode_index(pixels, reversed=upward),
            "width": width, "lines": pixels.shape[0], "channels": channels,
            "bytes_per_line": width * per_sample, "upward": upward,
            "resolution": self._mode["resolution"], "frame": self._frame,
            "calibrate": self._mode["calibrate"], "masks": [],
        }
        self.passes.append(self._pass)
        self._served = 0
        self._waited = False

    def _mask(self, size):
        from rps7200.shading import MASK_USED

        width = self._pass["width"] if self._pass else size
        mask = np.full(size, MASK_USED + 1, dtype=np.uint8)
        used = np.round(np.linspace(0, size - 1, min(width, size))).astype(int)
        mask[used] = MASK_USED
        answer = mask.tobytes()
        if self._pass is not None:
            self._pass["masks"].append(answer)
        return answer

    def _parameters(self):
        p = self._pass
        d = bytearray(18)
        d[0:2] = p["width"].to_bytes(2, "little")
        d[2:4] = p["lines"].to_bytes(2, "little")
        d[4:6] = p["bytes_per_line"].to_bytes(2, "little")
        d[6], d[7] = 3, 5                                 # filter offsets
        d[14:16] = p["lines"].to_bytes(2, "little")
        return bytes(d)

    def _read(self, read_size):
        from rps7200.protocol import ASC_END_OF_DATA, SCSI_READ
        from rps7200.usb_transport import NoDataYet

        if self._pass is None:
            self._refuse(SCSI_READ, ASC_END_OF_DATA)
        self.reads += 1
        if self.on_read is not None:
            self.on_read(self, self.reads)
            if self.closed:
                from rps7200.usb_transport import UsbError
                raise UsbError("transport closed under a read")
        if not self._waited:
            self._waited = True
            raise NoDataYet("not scanned that far yet")
        blob = self._pass["blob"]
        if self._served >= len(blob):
            self._refuse(SCSI_READ, ASC_END_OF_DATA)
        chunk = blob[self._served:self._served + read_size]
        self._served += len(chunk)
        return chunk


def scanner_at_commands(monkeypatch, *, debug=False, **device):
    """`DirectScanner` itself on a `DeviceAtCommands`, with the waiting out.

    Returns ``(scanner, device)``. Every method is the driver's own; only the
    clock is `NoWaiting`, because a calibration waits ten silent seconds and
    every read polls.
    """
    from rps7200 import direct

    monkeypatch.setattr(direct, "time", NoWaiting())
    device_ = DeviceAtCommands(**device)
    return DirectScanner(transport=device_, verbose=False, debug=debug), device_


def tool_on_device(tool, monkeypatch, **device) -> list:
    """Put the driver itself, on a `DeviceAtCommands`, under a tool's `main()`.

    The tools build their own scanner (`DirectScanner(verbose=..., debug=None)`),
    so the class is replaced by one that is the driver in every method and
    only opens onto the double instead of the bus. Returns the devices, one
    per scanner the tool made, filled in as it runs.
    """
    from rps7200 import direct

    monkeypatch.setattr(direct, "time", NoWaiting())
    devices = []

    class OnTheDevice(DirectScanner):
        def __init__(self, verbose=False, debug=None, **kw):
            devices.append(DeviceAtCommands(**device))
            super().__init__(transport=devices[-1], verbose=False, debug=debug)

    monkeypatch.setattr(tool, "DirectScanner", OnTheDevice)
    return devices


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


def windows_mkdir(monkeypatch):
    """Make `os.mkdir` answer as Windows does, on any runner.

    Under a *file*, Windows reports a missing folder as FileNotFoundError, and
    `Path.mkdir(parents=True)` then fails on the file with FileExistsError --
    naming the file, for a folder that does not exist. Linux says
    NotADirectoryError about the folder itself. Code tested only on Linux
    never meets the Windows answer, and it hung CI there once. Returns the
    list of paths asked for, and refuses to be asked without end.
    """
    from pathlib import Path

    real_mkdir = os.mkdir
    calls = []

    def mkdir(path, *args, **kwargs):
        calls.append(path)
        assert len(calls) < 50, "mkdir retried without end"
        p = Path(path)
        if any(parent.is_file() for parent in p.parents):
            raise FileNotFoundError(2, "The system cannot find the path "
                                    "specified", str(path))
        if p.is_file():
            raise FileExistsError(183, "Cannot create a file when that file "
                                  "already exists", str(path))
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(os, "mkdir", mkdir)
    return calls
