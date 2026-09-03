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

import pytest

from rps7200.usb_transport import (
    BULK_CHUNK,
    NoDataYet,
    PARTIAL_READ_POLL_S,
    Transport,
    UsbError,
)


class ScriptedTransport(Transport):
    """A transport whose bulk endpoint answers from a script.

    Subclasses rather than fakes libusb: `_read_payload` is the code under test
    and everything below it -- the handshake and the bulk call -- is exactly
    what a test wants to replace.
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

    def _announce_length(self, size):
        self.announced.append(size)

    def _bulk_read_into(self, view, timeout_ms):
        self.reads += 1
        n = self.script.pop(0) if self.script else 0
        n = min(n, len(view))
        for i in range(n):
            view[i] = self.payload(self.filled + i) & 0xFF
        self.filled += n
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
