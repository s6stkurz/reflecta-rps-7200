"""The windowed payload read, which is why this transport exists at all.

The scanner stops delivering after 32 KB and then waits, so the length
handshake is repeated per window rather than announced once. Two failure modes
matter and they look alike from inside the loop:

An empty read *before* anything arrives is the scanner saying "not scanned that
far yet" -- its normal answer, on most reads. An empty read *after* part of a
payload has been delivered is a pause in an answer already in progress. Treating
the second as the first drops bytes the device has already handed over while it
still believes it is answering, so the retried READ resumes into the middle of
the previous one and every line after it is misaligned. It is also abandoning a
read mid-scan, which costs a power cycle.
"""

import ctypes
import sys

import pytest

from conftest import FakeTransport
from rps7200 import usb_transport
from rps7200.direct import DirectScanner
from rps7200.protocol import INDEX_HEADER, untagged_lines
from rps7200.usb_transport import (
    BULK_CHUNK,
    LIBUSB_ERROR_NO_DEVICE,
    LIBUSB_ERROR_PIPE,
    LIBUSB_ERROR_TIMEOUT,
    MAX_BREAKS_PER_PAYLOAD,
    NoDataYet,
    PARTIAL_READ_POLL_S,
    Transport,
    TransferBroken,
    UsbError,
    _landed,
    _SENTINEL,
)


class ScriptedTransport(Transport):
    """A transport whose bulk endpoint answers from a script.

    Subclasses rather than fakes libusb: `_read_payload` is the code under test
    and everything below it -- the handshake and the bulk call -- is exactly
    what a test wants to replace.

    A script entry is the bytes one `_bulk_read_into` call delivers, or
    ``("break", n)``: the bus delivers ``n`` bytes and then breaks the read.
    The scanner's stream carries on from byte ``n`` either way, which is the
    premise the resume rests on -- it still holds the rest of its answer.
    """

    def __init__(self, script, payload=None):
        # No libusb_init: nothing here touches the bus.
        self.verbose = False
        self.max_window = 0x8000
        self.script = list(script)      # bytes per _bulk_read_into call
        self.payload = payload or (lambda i: 0xAB)
        self.announced = []
        self.reads = 0
        self.filled = 0
        self.last_breaks = 0

    def _announce_length(self, size):
        self.announced.append(size)

    def _bulk_read_into(self, view, timeout_ms):
        self.reads += 1
        step = self.script.pop(0) if self.script else 0
        broke = isinstance(step, tuple)
        n = min(step[1] if broke else step, len(view))
        for i in range(n):
            view[i] = self.payload(self.filled + i) & 0xFF
        self.filled += n
        if broke:
            raise TransferBroken(f"broke after {n}", n)
        return n


@pytest.fixture(autouse=True)
def _virtual_clock(monkeypatch):
    """Run the stall loop on a clock the test advances, not on the wall.

    The loop waits up to two minutes for a paused payload to resume. Stubbing
    only `sleep` would spin for those two real minutes; the clock has to move
    with it, and moving it in `sleep` keeps the relationship the code assumes.
    """
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    monkeypatch.setattr("rps7200.usb_transport.time.sleep", sleep)
    monkeypatch.setattr("rps7200.usb_transport.time.monotonic", lambda: now[0])
    return now


# --- the happy paths --------------------------------------------------------


def test_a_payload_arriving_in_one_read_comes_back_whole():
    t = ScriptedTransport([64])
    assert t._read_payload(64, 1000) == bytes([0xAB] * 64)
    assert t.announced == [64]


def test_a_payload_split_across_reads_is_reassembled_in_order():
    t = ScriptedTransport([10, 20, 34], payload=lambda i: i)
    got = t._read_payload(64, 1000)
    assert got == bytes(i & 0xFF for i in range(64))


def test_a_payload_larger_than_a_window_is_announced_per_window():
    """The scanner stops after a window; each one needs its own handshake."""
    size = 0x8000 * 2 + 100
    t = ScriptedTransport([BULK_CHUNK] * 100)
    t._read_payload(size, 1000)
    assert t.announced == [0x8000, 0x8000, 100]
    assert sum(t.announced) == size


# --- an empty read before anything arrives ----------------------------------


def test_no_data_at_all_is_reported_as_not_yet():
    t = ScriptedTransport([0])
    with pytest.raises(NoDataYet):
        t._read_payload(64, 1000)


def test_not_yet_is_raised_before_any_byte_is_consumed():
    """Nothing was delivered, so the caller may safely re-issue the READ."""
    t = ScriptedTransport([0])
    with pytest.raises(NoDataYet):
        t._read_payload(64, 1000)
    assert t.filled == 0


# --- an empty read after part of the payload --------------------------------


def test_a_pause_mid_payload_is_waited_out_not_abandoned():
    """The bytes already delivered must survive the pause."""
    t = ScriptedTransport([10, 0, 0, 54], payload=lambda i: i)
    got = t._read_payload(64, 1000)
    assert got == bytes(i & 0xFF for i in range(64))


def test_a_pause_mid_payload_does_not_raise_not_yet():
    """NoDataYet makes read_planes re-issue the READ, desynchronising the stream."""
    t = ScriptedTransport([10] + [0] * 3 + [54])
    t._read_payload(64, 1000)          # must not raise


def test_a_pause_that_never_resumes_is_a_hard_error_not_not_yet():
    t = ScriptedTransport([10])        # then empty for ever
    with pytest.raises(UsbError) as exc:
        t._read_payload(64, 1000)
    assert not isinstance(exc.value, NoDataYet)
    assert "mid-payload" in str(exc.value)
    assert "10 of 64" in str(exc.value)


def test_a_pause_between_windows_still_counts_as_started():
    """Byte 1 of window 2 is not 'nothing arrived': window 1 already landed."""
    size = 0x8000 + 64
    t = ScriptedTransport([BULK_CHUNK] * (0x8000 // BULK_CHUNK) + [0, 64])
    t._read_payload(size, 1000)        # must not raise NoDataYet


def test_the_stall_clock_restarts_when_data_resumes():
    """A long read of many short pauses must not add them up into a failure."""
    script = [2]                       # data first: the payload has started
    for _ in range(19):
        script += [0, 0, 2]
    t = ScriptedTransport(script)
    assert len(t._read_payload(40, 1000)) == 40


def test_poll_interval_is_short_enough_to_keep_up():
    """The vendor software retries an empty read about every 20 ms."""
    assert PARTIAL_READ_POLL_S <= 0.05


# --- the caller's patience --------------------------------------------------


def test_a_pause_is_waited_out_as_long_as_the_caller_waits_for_the_read():
    """An untied infrared pass holds the device ~220 s. Its read waits that
    long for a READ answered "not yet", and passes the same patience down as
    the bulk timeout -- but a pause part way through a payload was given up
    at a flat 120 s, an abandoned read."""
    pause = int(150 / PARTIAL_READ_POLL_S)            # 150 s without a byte
    t = ScriptedTransport([10] + [0] * pause + [54])
    assert len(t._read_payload(64, 287_000)) == 64

    t = ScriptedTransport([10] + [0] * pause + [54])
    with pytest.raises(UsbError, match="mid-payload"):
        t._read_payload(64, 30_000)                   # the default command's


def test_a_long_payload_does_not_spend_the_wait_that_follows_it(monkeypatch):
    """One deadline, set at the READ's start, also bounded the BUSY the device
    answers with after the payload. A payload that paused long enough to spend
    it -- which the pass's own bulk timeout now allows -- was followed by
    "stayed busy" at the first BUSY: every byte read, and the pass failed."""
    from rps7200 import usb_transport
    from rps7200.usb_transport import UsbStatus

    clock = [1000.0]
    monkeypatch.setattr(usb_transport.time, "monotonic", lambda: clock[0])

    class Paused(Transport):
        def __init__(self):
            self.verbose = False
            self.status = [UsbStatus.BUSY, UsbStatus.BUSY, UsbStatus.OK]

        def _send_command(self, command):
            return UsbStatus.READ

        def _read_payload(self, size, timeout_ms):
            clock[0] += 290.0 + 60.0          # a long pause, then the rest
            return b"\xab" * size

        def _control_in(self):
            return self.status.pop(0)

    t = Paused()
    got = t._command(b"\x08" + b"\x00" * 5, None, 64, 287_000, 300.0)
    assert got == b"\xab" * 64 and t.status == []

    class StaysBusy(Paused):                  # still given up, on its own count
        def _control_in(self):
            clock[0] += 10.0
            return UsbStatus.BUSY

    with pytest.raises(UsbError, match="stayed busy"):
        StaysBusy()._command(b"\x08" + b"\x00" * 5, None, 64, 287_000, 300.0)


def test_a_pass_hands_its_patience_to_the_bulk_read():
    """The bulk transfer timed out at 120 s whatever the pass: a device that
    stays silent through an untied infrared pass's floor, rather than
    answering "not yet", was abandoned mid-read."""
    import numpy as np

    from rps7200.direct import DirectScanner
    from rps7200.protocol import ScanParameters

    params = ScanParameters(width=4, lines=2, bytes_per_line=8,
                            filter_offset1=0, filter_offset2=0,
                            available_lines=2)
    lines = b"".join(tag * 2 + np.arange(4, dtype="<u2").tobytes()
                     for tag in (b"R", b"G", b"B") * 2)

    class Reads:
        def __init__(self):
            self.timeouts = []

        def command(self, command, data=None, read_size=0, timeout_ms=0,
                    max_wait_s=60.0):
            self.timeouts.append(timeout_ms)
            return lines[:read_size]

    s = DirectScanner(transport=Reads(), debug=False)
    s._own_transport = False
    s.read_planes(params, 3, idle_timeout=DirectScanner.UNTIED_INFRARED_IDLE_S)
    assert s.t.timeouts == [int(DirectScanner.UNTIED_INFRARED_IDLE_S * 1000)]


# --- the window a probe may set ---------------------------------------------


@pytest.mark.parametrize("raw", ["0", "-5", "32k", "65536"])
def test_a_window_the_device_cannot_serve_is_not_used(raw, capsys):
    """0 or less announced an empty window for ever with a READ pending, and
    above 32 KB the device stops after 32 KB: both hang or abandon a read."""
    from rps7200.usb_transport import DEVICE_WINDOW, _window_from

    assert _window_from(raw) == DEVICE_WINDOW
    assert "RPS7200_MAX_WINDOW" in capsys.readouterr().err


def test_a_smaller_window_is_still_a_probers_choice():
    from rps7200.usb_transport import DEVICE_WINDOW, _window_from

    assert _window_from("16384") == 16384
    assert _window_from(None) == _window_from("") == DEVICE_WINDOW


def test_a_window_that_is_not_a_number_does_not_break_the_import():
    """It raised at import of usb_transport, which direct imports -- so
    offline decoding broke over a setting for the bus."""
    import os
    import subprocess

    env = dict(os.environ, RPS7200_MAX_WINDOW="32k")
    done = subprocess.run([sys.executable, "-c", "import rps7200.direct"],
                          env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr


def test_a_transport_refuses_a_window_before_touching_libusb():
    with pytest.raises(ValueError, match="max_window"):
        Transport(max_window=0)


def test_a_failed_bulk_read_says_it_cleared_the_halt(monkeypatch):
    """CLEAR_FEATURE(ENDPOINT_HALT) is not one of the vendor's shapes and no
    command log records it, so the error that follows it has to say so."""
    import ctypes

    from rps7200 import usb_transport

    cleared = []

    class Lib:
        def libusb_bulk_transfer(self, handle, ep, buf, size, done, timeout):
            return usb_transport.LIBUSB_ERROR_TIMEOUT      # and nothing read

        def libusb_clear_halt(self, handle, ep):
            cleared.append(ep)
            return 0

        def libusb_error_name(self, code):
            return b"LIBUSB_ERROR_TIMEOUT"

    monkeypatch.setattr(usb_transport, "_lib", Lib())
    t = Transport.__new__(Transport)
    t.verbose, t._handle, t.bulk_in_ep = False, ctypes.c_void_p(1), 0x81
    with pytest.raises(UsbError, match="halt was cleared"):
        t._bulk_read_into(memoryview(bytearray(64)), 1000)
    assert cleared == [0x81]


def test_a_clear_libusb_refused_is_not_reported_as_cleared(monkeypatch):
    """`clear_halt` swallowed libusb's answer, so a refused clear read "the
    endpoint's halt was cleared" -- wrong in the one message a wedge is
    investigated from afterwards."""
    import ctypes

    from rps7200 import usb_transport

    names = {usb_transport.LIBUSB_ERROR_TIMEOUT: b"LIBUSB_ERROR_TIMEOUT",
             usb_transport.LIBUSB_ERROR_NO_DEVICE: b"LIBUSB_ERROR_NO_DEVICE"}

    class Lib:
        def libusb_bulk_transfer(self, handle, ep, buf, size, done, timeout):
            return usb_transport.LIBUSB_ERROR_TIMEOUT      # and nothing read

        def libusb_clear_halt(self, handle, ep):
            return usb_transport.LIBUSB_ERROR_NO_DEVICE

        def libusb_error_name(self, code):
            return names[code]

    monkeypatch.setattr(usb_transport, "_lib", Lib())
    t = Transport.__new__(Transport)
    t.verbose, t._handle, t.bulk_in_ep = False, ctypes.c_void_p(1), 0x81
    with pytest.raises(UsbError) as refused:
        t._bulk_read_into(memoryview(bytearray(64)), 1000)
    assert "was cleared" not in str(refused.value)
    assert "failed too: LIBUSB_ERROR_NO_DEVICE" in str(refused.value)

    t._handle = None                                   # nothing to send it on
    assert t.clear_halt() is None


def test_nothing_here_offers_an_ieee1284_reset():
    """The vendor never sends one, and one left the scanner working for a
    single session and wedged for the next. `open(reset=True)` and a public
    `reset()` invited exactly the cleanup handler that would send it."""
    import inspect

    from rps7200 import usb_transport

    assert not hasattr(Transport, "reset")
    assert "reset" not in inspect.signature(Transport.open).parameters
    assert "ieee_command(IEEE1284_RESET)" not in inspect.getsource(usb_transport)


# -- a device another program holds ------------------------------------------


def _unopened(monkeypatch, claim_rc=0):
    """A `Transport` whose open finds the scanner on the bus and, as asked,
    cannot open it or cannot claim it."""
    from rps7200 import usb_transport

    names = {usb_transport.LIBUSB_ERROR_ACCESS: b"LIBUSB_ERROR_ACCESS",
             usb_transport.LIBUSB_ERROR_BUSY: b"LIBUSB_ERROR_BUSY"}

    class Lib:
        def libusb_open_device_with_vid_pid(self, ctx, vid, pid):
            return None if claim_rc == 0 else 1

        def libusb_claim_interface(self, handle, interface):
            return claim_rc

        def libusb_close(self, handle):
            pass

        def libusb_error_name(self, code):
            return names.get(code, b"LIBUSB_ERROR_OTHER")

    monkeypatch.setattr(usb_transport, "_lib", Lib())
    t = Transport.__new__(Transport)
    t.verbose, t._handle, t._ctx = False, None, None
    monkeypatch.setattr(t, "_on_the_bus", lambda: True)
    monkeypatch.setattr(t, "_discover_endpoints", lambda: None)
    return t


def test_on_windows_a_scanner_in_use_is_not_sent_to_zadig(monkeypatch):
    """WinUSB allows one handle, so a second opener -- check_scanner run
    beside the window, a python left by a killed one -- got NULL and was
    told to replace the driver with Zadig: under the process holding the
    scanner, possibly mid-roll (PLAT-04). libusb's own answer, ACCESS, is
    asked for now and says which it is."""
    from rps7200 import usb_transport
    from rps7200.usb_transport import ScannerNotFound

    monkeypatch.setattr(usb_transport.sys, "platform", "win32")
    t = _unopened(monkeypatch)
    monkeypatch.setattr(t, "_open_rc",
                        lambda: usb_transport.LIBUSB_ERROR_ACCESS)
    with pytest.raises(ScannerNotFound) as held:
        t._raw_open()
    assert "another program has the scanner open" in str(held.value)
    assert "Zadig" not in str(held.value)

    # Anything else there is still the driver binding.
    monkeypatch.setattr(t, "_open_rc", lambda: -12)       # NOT_SUPPORTED
    with pytest.raises(ScannerNotFound, match="Zadig"):
        t._raw_open()


def test_a_claim_refused_because_the_scanner_is_held_says_so(monkeypatch):
    """On macOS an exclusive open elsewhere fails at the claim, where the
    window showed a bare 'could not claim interface 0: LIBUSB_ERROR_ACCESS'
    and the advice sat on a branch this case never reaches."""
    from rps7200 import usb_transport

    for rc in (usb_transport.LIBUSB_ERROR_ACCESS,
               usb_transport.LIBUSB_ERROR_BUSY):
        t = _unopened(monkeypatch, claim_rc=rc)
        with pytest.raises(UsbError, match="another program has the scanner"):
            t._raw_open()


def test_the_open_is_asked_again_alone_to_hear_why_it_failed(monkeypatch):
    """`_open_rc` walks the device list as `_on_the_bus` does and opens the
    scanner itself, for the error `libusb_open_device_with_vid_pid` keeps to
    itself -- and frees the list, and opens nothing else."""
    import ctypes

    from rps7200 import usb_transport

    other, scanner = 11, 22
    devices = (usb_transport._dev_p * 2)(other, scanner)
    opened, freed = [], []

    class Lib:
        def libusb_get_device_list(self, ctx, out):
            ctypes.memmove(ctypes.addressof(out._obj),
                           ctypes.byref(ctypes.c_void_p(
                               ctypes.addressof(devices))),
                           ctypes.sizeof(ctypes.c_void_p))
            return 2

        def libusb_get_device_descriptor(self, dev, out):
            out._obj.idVendor = usb_transport.VENDOR_ID
            out._obj.idProduct = (usb_transport.PRODUCT_ID if dev == scanner
                                  else 0)
            return 0

        def libusb_open(self, dev, handle):
            opened.append(dev)
            return usb_transport.LIBUSB_ERROR_ACCESS

        def libusb_free_device_list(self, devs, unref):
            freed.append(unref)

    monkeypatch.setattr(usb_transport, "_lib", Lib())
    t = Transport.__new__(Transport)
    t._ctx = None
    assert t._open_rc() == usb_transport.LIBUSB_ERROR_ACCESS
    assert opened == [scanner]
    assert freed == [1]


def test_a_libusb_that_will_not_load_does_not_hide_one_that_will(monkeypatch):
    """The loader returned the first candidate that existed, so a copy that
    would not load -- an Intel libusb in /usr/local on Apple Silicon, ahead of
    Homebrew's in /opt/homebrew -- ended the search (PLAT-05)."""
    from rps7200 import usb_transport

    broken, working = "/usr/local/lib/libusb-1.0.dylib", "/opt/homebrew/lib/x"
    monkeypatch.delenv("LIBUSB_PATH", raising=False)
    monkeypatch.setattr(usb_transport, "_LIBUSB_PATHS", (broken, working))
    monkeypatch.setattr(usb_transport.os.path, "exists",
                        lambda p: p in (broken, working))
    tried = []

    def dll(path):
        tried.append(path)
        if path == broken:
            raise OSError("incompatible architecture (have 'x86_64', "
                          "need 'arm64')")
        return "loaded " + path

    monkeypatch.setattr(usb_transport, "_dll", dll)
    assert usb_transport._load_libusb() == "loaded " + working
    assert tried == [broken, working]

    # And with none that loads, the refusals are in the message.
    monkeypatch.setattr(usb_transport, "_LIBUSB_PATHS", (broken,))
    monkeypatch.setattr(usb_transport.ctypes.util, "find_library",
                        lambda name: None)
    monkeypatch.setattr(usb_transport, "_bundled", lambda: None)
    with pytest.raises(OSError, match="incompatible architecture"):
        usb_transport._load_libusb()


def test_a_libusb_path_that_will_not_load_is_refused_not_passed_over(
        monkeypatch):
    """Falling through from every refusal took LIBUSB_PATH with it: a build
    named there to be tried was skipped without a word and another copy
    loaded in its place. The override is the one chosen, so its refusal is
    the answer."""
    from rps7200 import usb_transport

    chosen, working = "/home/me/libusb-test/libusb-1.0.so", "/usr/lib/x"
    monkeypatch.setenv("LIBUSB_PATH", chosen)
    monkeypatch.setattr(usb_transport, "_LIBUSB_PATHS", (working,))
    monkeypatch.setattr(usb_transport.os.path, "exists",
                        lambda p: p in (chosen, working))
    tried = []

    def dll(path):
        tried.append(path)
        if path == chosen:
            raise OSError("wrong ELF class: ELFCLASS32")
        return "loaded " + path

    monkeypatch.setattr(usb_transport, "_dll", dll)
    with pytest.raises(OSError, match="LIBUSB_PATH=.*ELFCLASS32"):
        usb_transport._load_libusb()
    assert tried == [chosen]


# --- a read the bus breaks part-way -----------------------------------------
#
# Recorded 2026-09-27: 97,174 clean reads, then one the host controller gave up
# on after 12288 of its 16384 bytes, with no STALL from the scanner. The driver
# cleared the endpoint and sent its next command, and the scanner took the
# preamble and one command byte, then nothing more: it was still holding the
# rest of its answer. So the rest is read instead.


def position(i):
    """A payload in which every byte says where it belongs."""
    return (i * 7 + i // 251) & 0xFF


def test_a_broken_read_is_read_on_from_where_its_data_stopped():
    t = ScriptedTransport([BULK_CHUNK, ("break", 12288), BULK_CHUNK], payload=position)
    got = t._read_payload(0x8000, 1000)
    assert got == bytes(position(i) for i in range(0x8000))
    assert t.last_breaks == 1


def test_reading_on_announces_no_new_window():
    """The window announced before the break is the one still being delivered."""
    t = ScriptedTransport([BULK_CHUNK, ("break", 512), BULK_CHUNK])
    t._read_payload(0x8000, 1000)
    assert t.announced == [0x8000]


def test_a_break_across_windows_keeps_every_later_window_in_step():
    size = 0x8000 * 2 + 100
    script = [("break", 4096), BULK_CHUNK, BULK_CHUNK, BULK_CHUNK, BULK_CHUNK, 100]
    t = ScriptedTransport(script, payload=position)
    assert t._read_payload(size, 1000) == bytes(position(i) for i in range(size))
    assert t.announced == [0x8000, 0x8000, 100]


def test_a_break_before_anything_landed_is_still_an_answer_begun():
    """NoDataYet would make the caller send the READ again into a half-sent answer."""
    t = ScriptedTransport([("break", 0), 0, 0, 64], payload=position)
    assert t._read_payload(64, 1000) == bytes(position(i) for i in range(64))


def test_a_payload_that_keeps_breaking_is_given_up_on():
    t = ScriptedTransport([("break", 512)] * (MAX_BREAKS_PER_PAYLOAD + 1) + [BULK_CHUNK] * 4)
    with pytest.raises(TransferBroken):
        t._read_payload(0x8000, 1000)
    assert t.last_breaks == MAX_BREAKS_PER_PAYLOAD + 1


def test_an_unbroken_payload_reports_no_breaks():
    t = ScriptedTransport([64])
    t.last_breaks = 5                  # left from an earlier command
    t._read_payload(64, 1000)
    assert t.last_breaks == 0


# --- how much of a broken read landed ----------------------------------------


def sentinel_buffer(size=BULK_CHUNK):
    return bytearray(_SENTINEL[:size])


def test_the_packets_that_arrived_are_counted_from_the_buffer():
    buf = sentinel_buffer()
    buf[:24 * 512] = bytes(24 * 512)             # all zeros: still data
    assert _landed(memoryview(buf), 512) == 12288


def test_nothing_landed_counts_nothing_and_everything_counts_everything():
    assert _landed(memoryview(sentinel_buffer()), 512) == 0
    assert _landed(memoryview(bytearray(b"\xff" * BULK_CHUNK)), 512) == BULK_CHUNK


def test_a_packet_touched_by_a_single_byte_counts_as_arrived():
    """A corrupted packet can leave bytes behind: the one way to count too many,
    and why a resumed read's lines are checked afterwards."""
    buf = sentinel_buffer()
    buf[:512] = bytes(512)
    buf[512] ^= 0x01
    assert _landed(memoryview(buf), 512) == 1024


def test_a_short_last_packet_is_counted_whole():
    buf = sentinel_buffer(1000)
    buf[:1000] = bytes(1000)
    assert _landed(memoryview(buf), 512) == 1000


# --- the bulk call itself, against a stand-in for libusb ---------------------


class FakeLibusb:
    """What `_bulk_read_into` asks of libusb, and nothing else."""

    def __init__(self, delivered, rc, reported=0, halt_rc=0):
        self.delivered, self.rc, self.reported, self.halt_rc = (
            delivered, rc, reported, halt_rc)
        self.halts = 0

    def libusb_bulk_transfer(self, handle, ep, buf, length, transferred, timeout):
        for i in range(self.delivered):
            buf[i] = position(i)
        transferred._obj.value = self.reported
        return self.rc

    def libusb_clear_halt(self, handle, ep):
        self.halts += 1
        return self.halt_rc

    def libusb_error_name(self, code):
        return {LIBUSB_ERROR_PIPE: b"LIBUSB_ERROR_PIPE",
                LIBUSB_ERROR_TIMEOUT: b"LIBUSB_ERROR_TIMEOUT",
                LIBUSB_ERROR_NO_DEVICE: b"LIBUSB_ERROR_NO_DEVICE"}.get(code, b"?")


def opened():
    t = Transport.__new__(Transport)
    t.verbose = False
    t._handle = ctypes.c_void_p(1)
    t.bulk_in_ep = 0x81
    t.max_packet_size = 512
    t.last_breaks = 0
    return t


def test_windows_reporting_zero_bytes_still_resumes_from_what_arrived(monkeypatch):
    """libusb said 0; the buffer holds 24 packets. The buffer is believed."""
    lib = FakeLibusb(delivered=12288, rc=LIBUSB_ERROR_PIPE, reported=0)
    monkeypatch.setattr(usb_transport, "_lib", lib)
    buf = bytearray(BULK_CHUNK)
    with pytest.raises(TransferBroken) as exc:
        opened()._bulk_read_into(memoryview(buf), 1000)
    assert exc.value.landed == 12288
    assert lib.halts == 1
    assert buf[:12288] == bytes(position(i) for i in range(12288))


def test_a_break_whose_halt_would_not_clear_is_not_read_on(monkeypatch):
    """An endpoint still halted refuses the read that would go on."""
    monkeypatch.setattr(usb_transport, "_lib", FakeLibusb(
        delivered=12288, rc=LIBUSB_ERROR_PIPE, halt_rc=LIBUSB_ERROR_TIMEOUT))
    with pytest.raises(UsbError) as exc:
        opened()._bulk_read_into(memoryview(bytearray(BULK_CHUNK)), 1000)
    assert not isinstance(exc.value, TransferBroken)


def test_a_scanner_that_left_the_bus_is_not_resumed(monkeypatch):
    """Recorded once: the endpoint reset answered DEVICE_GONE and the scanner
    came back as a new device. There is nothing left to read on from."""
    monkeypatch.setattr(usb_transport, "_lib", FakeLibusb(
        delivered=14848, rc=LIBUSB_ERROR_PIPE, halt_rc=LIBUSB_ERROR_NO_DEVICE))
    with pytest.raises(UsbError) as exc:
        opened()._bulk_read_into(memoryview(bytearray(BULK_CHUNK)), 1000)
    assert not isinstance(exc.value, TransferBroken)
    assert "dropped off the USB bus" in str(exc.value)


def test_a_timeout_with_nothing_delivered_is_not_a_break(monkeypatch):
    monkeypatch.setattr(usb_transport, "_lib", FakeLibusb(
        delivered=0, rc=LIBUSB_ERROR_TIMEOUT))
    with pytest.raises(UsbError) as exc:
        opened()._bulk_read_into(memoryview(bytearray(BULK_CHUNK)), 1000)
    assert not isinstance(exc.value, TransferBroken)


def test_a_clean_read_returns_its_count_and_its_bytes(monkeypatch):
    monkeypatch.setattr(usb_transport, "_lib", FakeLibusb(
        delivered=BULK_CHUNK, rc=0, reported=BULK_CHUNK))
    buf = bytearray(BULK_CHUNK)
    assert opened()._bulk_read_into(memoryview(buf), 1000) == BULK_CHUNK
    assert buf == bytes(position(i) for i in range(BULK_CHUNK))


# --- checking a resumed read's lines ------------------------------------------

STRIDE = 2 * 40 + INDEX_HEADER


def index_lines(count):
    out = bytearray()
    for k in range(count):
        out += b"RGB"[k % 3:k % 3 + 1] + b"\x00" + bytes(position(i) for i in range(STRIDE - 2))
    return bytes(out)


def test_lines_in_place_all_start_with_a_colour_letter():
    assert untagged_lines(index_lines(30), STRIDE) == 0


def test_a_slip_shows_at_nearly_every_line_after_it():
    data = index_lines(30)
    slipped = data[:5 * STRIDE] + data[5 * STRIDE + 7:] + bytes(7)
    assert untagged_lines(slipped, STRIDE) >= 20


def test_a_resumed_read_says_whether_its_lines_are_in_place():
    class Resumed(FakeTransport):
        last_breaks = 1

        def command(self, command, data=None, read_size=0, **kw):
            return payload

    said = []
    s = DirectScanner(transport=Resumed())
    s._log = said.append
    payload = index_lines(4)
    assert s.read_lines(4, STRIDE) == payload
    assert any("all 4 lines in place" in line for line in said)

    payload = index_lines(4)[3:] + bytes(3)
    said.clear()
    s.read_lines(4, STRIDE)
    assert any("out of place" in line for line in said)
