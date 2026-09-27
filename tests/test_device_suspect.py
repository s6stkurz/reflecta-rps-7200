"""A pass abandoned mid-read leaves the scanner mid-scan; nothing drives it again.

The documented recovery from an abandoned read is a power cycle. What the
driver used to do instead was carry on: a roll advanced the film and started
the next frame's scan into a device that was no longer listening, and a
session ran the next queued job. `DirectScanner.suspect` is the one flag that
stops all of it, and these are the places it has to hold.
"""
from __future__ import annotations

import numpy as np
import pytest

from rps7200.direct import DirectScanner
from rps7200.protocol import SLIDE_NEXT, DeviceSuspect, ScanParameters
from rps7200.usb_transport import UsbError

from test_roll import METER_NONE, FakeRoll, picture


class Pass(DirectScanner):
    """Just enough of a device for `_read_pass`, with the read scripted."""

    def __init__(self, read):
        super().__init__(transport=object(), debug=False)
        self._own_transport = False
        self._read = read
        self.settled = 0

    def start_scan(self, *a, **kw):
        self._refuse_if_suspect("a scan")

    def wait_ready(self, *a, **kw):
        pass

    def get_ccd_mask(self, size):
        return b"\x01" * 4

    def get_parameters(self):
        return ScanParameters(width=4, lines=2, bytes_per_line=8,
                              filter_offset1=0, filter_offset2=0, available_lines=2)

    def read_planes(self, params, channels, keep_raw=False, idle_timeout=None):
        self.idle_timeout = idle_timeout
        return self._read(self)

    def finish_scan(self, polls=3):
        self.settled += 1


def _lost_mid_read(s):
    raise UsbError("LIBUSB_ERROR_TIMEOUT")


def _fails_after_every_line(s):
    s._read_complete = True                      # what read_planes sets
    raise ValueError("decoded to an unexpected shape")


def test_a_read_lost_part_way_marks_the_device():
    s = Pass(_lost_mid_read)
    with pytest.raises(UsbError):
        s._read_pass(3, keep_raw=False, resolution=1800)
    assert s.suspect is not None and "LIBUSB_ERROR_TIMEOUT" in s.suspect


def test_a_host_failure_after_the_last_line_does_not():
    """Every byte was in: the device has nothing outstanding."""
    s = Pass(_fails_after_every_line)
    with pytest.raises(ValueError):
        s._read_pass(3, keep_raw=False, resolution=1800)
    assert s.suspect is None


def test_an_interrupted_read_marks_it_too():
    """Ctrl-C in the middle of a read is an abandoned read like any other."""
    def interrupted(s):
        raise KeyboardInterrupt

    s = Pass(interrupted)
    with pytest.raises(KeyboardInterrupt):
        s._read_pass(3, keep_raw=False, resolution=1800)
    assert s.suspect is not None


def test_a_suspect_device_is_not_driven_again():
    s = Pass(_lost_mid_read)
    with pytest.raises(UsbError):
        s._read_pass(3, keep_raw=False, resolution=1800)
    with pytest.raises(DeviceSuspect, match="power-cycle"):
        s._read_pass(3, keep_raw=False, resolution=1800)
    with pytest.raises(DeviceSuspect):
        DirectScanner.slide(s, SLIDE_NEXT)
    with pytest.raises(DeviceSuspect):
        DirectScanner.calibrate_shading(s)


def test_a_roll_ends_on_the_frame_that_left_the_device_suspect():
    """One lost frame used to become a lost roll: the loop advanced the film
    and started the next scan into a device that was no longer listening."""

    class Lost(FakeRoll):
        def scan(self, *a, **kw):
            if self.at == 1:
                self._mark_suspect("UsbError during a 1800 dpi pass")
                raise UsbError("LIBUSB_ERROR_TIMEOUT")
            return super().scan(*a, **kw)

    s = Lost([picture(seed=i) for i in range(4)])
    frames = []
    with pytest.raises(DeviceSuspect):
        for frame in s.scan_roll(frames=4, meter=METER_NONE, max_failures=3):
            frames.append(frame)
    assert [f.ok for f in frames] == [True, False]
    assert s.advances == 1, "the film moved on after the device was lost"


def test_an_ordinary_failed_frame_still_does_not_end_the_roll():
    """A failure that left nothing outstanding keeps its old meaning."""
    s = FakeRoll([picture(seed=i) for i in range(4)], fail_at={1})
    out = list(s.scan_roll(frames=4, meter=METER_NONE))
    assert [f.ok for f in out] == [True, False, True, True]
    assert s.suspect is None


def test_a_stand_in_that_never_ran_the_constructor_is_not_suspect():
    s = DirectScanner.__new__(DirectScanner)
    assert s.suspect is None
    s._refuse_if_suspect("anything")         # does not raise


def test_the_pass_that_was_read_is_returned_whole():
    s = Pass(lambda s: np.zeros((2, 4, 3), np.uint16))
    image, params, mask = s._read_pass(3, keep_raw=False, resolution=1800)
    assert image.shape == (2, 4, 3) and params.width == 4 and mask == b"\x01" * 4
    assert s.settled == 1


# -- Ctrl-C in the tools ---------------------------------------------------


def test_the_first_ctrl_c_asks_and_the_second_insists():
    from rps7200.console import DeferredInterrupt

    said = []
    interrupt = DeferredInterrupt(say=said.append)
    assert not interrupt.requested()
    interrupt._handler(2, None)
    assert interrupt.requested() and said, "the first Ctrl-C must only ask"
    with pytest.raises(KeyboardInterrupt):
        interrupt._handler(2, None)


def test_the_handler_is_put_back_afterwards():
    import signal

    from rps7200.console import DeferredInterrupt

    before = signal.getsignal(signal.SIGINT)
    with DeferredInterrupt(say=lambda m: None) as interrupt:
        assert signal.getsignal(signal.SIGINT) == interrupt._handler
    assert signal.getsignal(signal.SIGINT) == before


@pytest.mark.parametrize("name", ["SIGTERM", "SIGHUP", "SIGBREAK"])
def test_the_other_ways_to_be_told_to_stop_are_taken_as_ctrl_c(name):
    """`kill`, and the terminal closing, ended a tool at once -- inside its
    read, with its queued frames unfiled -- where Ctrl-C had stopped doing
    that. Those a platform does not have are simply not there."""
    import signal

    from rps7200.console import DeferredInterrupt

    signum = getattr(signal, name, None)
    if signum is None:
        pytest.skip(f"no {name} here")
    before = signal.getsignal(signum)
    asked = []
    with DeferredInterrupt(say=lambda m: None,
                           on_request=lambda: asked.append(1)) as interrupt:
        assert signal.getsignal(signum) == interrupt._handler
        interrupt._handler(signum, None)
        assert interrupt.requested() and asked == [1]
    assert signal.getsignal(signum) == before


@pytest.mark.parametrize("name", ["SIGHUP", "SIGTERM"])
def test_a_signal_the_process_was_started_ignoring_stays_ignored(name):
    """`nohup` sets SIGHUP to ignore, for exactly the long runs CLAUDE.md
    says to background; taking it over made the terminal closing end the
    roll after the frame in flight -- what nohup was there to prevent."""
    import signal

    from rps7200.console import DeferredInterrupt

    signum = getattr(signal, name, None)
    if signum is None:
        pytest.skip(f"no {name} here")
    ctrl_c = signal.getsignal(signal.SIGINT)
    before = signal.signal(signum, signal.SIG_IGN)
    try:
        with DeferredInterrupt(say=lambda m: None) as interrupt:
            assert signal.getsignal(signum) is signal.SIG_IGN
            if ctrl_c is not signal.SIG_IGN:       # the rest still taken
                assert signal.getsignal(signal.SIGINT) == interrupt._handler
        assert signal.getsignal(signum) is signal.SIG_IGN
    finally:
        signal.signal(signum, before)


def test_a_terminal_that_has_gone_does_not_turn_the_request_into_an_error():
    """SIGHUP means stderr may be gone too, and the handler runs in the
    middle of whatever the tool was doing -- a read, most likely."""
    from rps7200.console import DeferredInterrupt

    def say(message):
        raise OSError(5, "Input/output error")

    interrupt = DeferredInterrupt(say=say)
    interrupt._handler(1, None)                    # does not raise
    assert interrupt.requested()


def test_a_bracket_stopped_at_ctrl_c_files_the_passes_it_took(tmp_path, monkeypatch):
    """Stopped between passes, never inside one, and nothing scanned is lost:
    the passes used to be held in memory until the end and die with the
    exception."""
    from test_scan_tool import _filed, run_correcting

    passes = _ctrl_c_after_passes(monkeypatch, 1)
    _created, code = run_correcting(tmp_path, monkeypatch, "--bracket", "3")
    assert code == 130
    assert len(passes) == 1, "a pass started after the Ctrl-C"
    assert len(_filed(tmp_path)) == 1, "the pass taken before Ctrl-C was not filed"


def _ctrl_c_after_passes(monkeypatch, n):
    """Ctrl-C pressed during pass `n`: asked for from then on. Returns the
    passes that started, as `FakeCorrectingScanner` saw them."""
    from test_scan_tool import FakeCorrectingScanner

    from rps7200 import console

    started = []
    real = FakeCorrectingScanner.scan

    def scan(self, **kw):
        started.append(kw)
        return real(self, **kw)

    monkeypatch.setattr(FakeCorrectingScanner, "scan", scan)
    monkeypatch.setattr(console.DeferredInterrupt, "requested",
                        lambda self: len(started) >= n)
    return started


def test_a_ctrl_c_during_the_calibration_starts_no_pass(tmp_path, monkeypatch):
    """The calibration ran on, then the full pass started anyway, after the
    operator had been told "stopping" -- which invites the second Ctrl-C,
    the one that abandons a read."""
    from test_scan_tool import FakeCorrectingScanner, run_correcting

    from rps7200 import console

    asked = []
    monkeypatch.setattr(FakeCorrectingScanner, "ensure_shading",
                        lambda self, *a, **k: asked.append(1) or {
                            "action": "calibrated", "summary": "calibrated"})
    monkeypatch.setattr(console.DeferredInterrupt, "requested",
                        lambda self: bool(asked))
    created, code = run_correcting(tmp_path, monkeypatch)
    assert code == 130
    assert created[0].scans == []


def test_a_ctrl_c_during_metering_starts_no_further_pass(tmp_path, monkeypatch):
    """Metering's probes are passes, and go through `scan` as the frame does;
    a stop asked for during the first is taken before the second."""
    from test_scan_tool import FakeCorrectingScanner, run_correcting

    class Metering(FakeCorrectingScanner):
        def scan(self, **kw):
            if kw.pop("auto_exposure", False):
                # As the driver's does: each round a scan of its own.
                for _ in range(2):
                    self.scan(resolution=300, infrared=False)
            return super().scan(**kw)

    passes = _ctrl_c_after_passes(monkeypatch, 1)
    _created, code = run_correcting(tmp_path, monkeypatch, "--auto-exposure",
                                    scanner=Metering)
    assert code == 130
    assert len(passes) == 1, "metering went on, and the frame after it"


def test_a_bracket_filed_nowhere_still_stops_at_ctrl_c(tmp_path, monkeypatch):
    """The only check sat behind `--library`: with --no-library every pass of
    the bracket was taken."""
    from test_scan_tool import run_correcting

    passes = _ctrl_c_after_passes(monkeypatch, 1)
    _created, code = run_correcting(tmp_path, monkeypatch, "--bracket", "3",
                                    "--no-library")
    assert code == 130
    assert len(passes) == 1


def test_a_failure_part_way_through_a_bracket_files_what_came_before(
        tmp_path, monkeypatch):
    from test_scan_tool import FakeCorrectingScanner, _filed, run_correcting

    real = FakeCorrectingScanner.scan
    calls = {"n": 0}

    def scan(self, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise UsbError("LIBUSB_ERROR_TIMEOUT")
        return real(self, *a, **kw)

    monkeypatch.setattr(FakeCorrectingScanner, "scan", scan)
    _created, code = run_correcting(tmp_path, monkeypatch, "--bracket", "3")
    assert code == 1
    assert len(_filed(tmp_path)) == 1


# -- the read's patience -----------------------------------------------------


def test_an_untied_infrared_read_waits_past_the_infrared_floor():
    """An untied IR pass holds the device ~220 s however few lines were asked
    for. The read gave up after 120 s without data -- short of that floor, the
    combination that wedged the device once as a 60 s timeout -- while
    INFRARED_FLOOR_S, documented as guarding it, guarded nothing."""
    untied = DirectScanner.read_idle_s(infrared=True, fast_infrared=False)
    assert untied > DirectScanner.INFRARED_FLOOR_S
    assert untied >= 227.0, "the top of the measured 212-227 s range"
    assert DirectScanner.read_idle_s(infrared=True, fast_infrared=True) \
        == DirectScanner.READ_IDLE_S
    assert DirectScanner.read_idle_s(infrared=False, fast_infrared=False) \
        == DirectScanner.READ_IDLE_S


def test_the_session_estimate_and_the_read_share_one_floor():
    from rps7200 import session

    assert session.INFRARED_FLOOR_S == DirectScanner.INFRARED_FLOOR_S


def test_the_pass_is_read_with_the_patience_its_mode_needs():
    s = Pass(lambda s: np.zeros((2, 4, 3), np.uint16))
    s._read_pass(3, keep_raw=False, resolution=300,
                 idle_timeout=DirectScanner.read_idle_s(True, False))
    assert s.idle_timeout == DirectScanner.UNTIED_INFRARED_IDLE_S


def test_a_resolution_that_cannot_be_corrected_is_known_before_opening():
    assert DirectScanner.correctable_at(3600)
    assert not DirectScanner.correctable_at(7200)


# -- starting a pass ---------------------------------------------------------


def _scan_answered(raise_):
    from conftest import FakeTransport
    from rps7200.protocol import SCSI_SCAN

    def scan(command):
        raise raise_

    s = DirectScanner(transport=FakeTransport(replies={SCSI_SCAN: scan}),
                      debug=False)
    s._own_transport = False
    return s


def test_a_start_that_was_not_answered_marks_the_device():
    """The SCAN went out and its status read timed out: the device may be
    scanning, and a roll used to count the frame an ordinary failure, move
    the film and start the next pass into it."""
    s = _scan_answered(UsbError("status read failed: LIBUSB_ERROR_TIMEOUT"))
    with pytest.raises(UsbError):
        s.start_scan()
    assert s.suspect is not None and "started" in s.suspect
    with pytest.raises(DeviceSuspect):
        DirectScanner.slide(s, SLIDE_NEXT)


def test_a_start_the_device_refused_does_not():
    """A refusal is an answer: nothing is running, so nothing is suspect."""
    from rps7200.protocol import CalibrationRequired
    from rps7200.usb_transport import CheckCondition

    s = _scan_answered(CheckCondition(0x1B))
    with pytest.raises(CalibrationRequired):
        s.start_scan(retries=1)
    assert s.suspect is None


# -- nothing reaches a suspect device --------------------------------------


def _suspect():
    from conftest import FakeTransport, settings

    s = DirectScanner(transport=FakeTransport(), debug=False)
    s._own_transport = False
    s.suspect = "UsbError during a 1800 dpi pass"
    return s, settings(9604, 6506, 6506, 7745)


@pytest.mark.parametrize("attempt", [
    lambda s, g: s.scan(resolution=300, infrared=False, shading=False),
    lambda s, g: s.prescan(shading=False),
    lambda s, g: s.auto_exposure(shading=False),
    lambda s, g: s.set_gain_offset(g),
], ids=["scan", "prescan", "metering", "gain write"])
def test_a_suspect_device_is_sent_nothing_that_configures_a_pass(attempt):
    """A pass used to send READ STATE, the lamp wait, the exposure and
    highlight ladders, the frame, gain and offset and MODE SELECT to a
    device still in an abandoned one, and only then be refused at SLIDE
    INIT."""
    s, g = _suspect()
    with pytest.raises(DeviceSuspect, match="power-cycle"):
        attempt(s, g)
    assert s.t.sent == [], [hex(op) for op, _ in s.t.sent]


def test_a_session_does_not_start_a_job_on_a_suspect_scanner(tmp_path):
    """The window kept queueing Scans after a lost read, and each started --
    a roll made its folder and manifest first -- before the driver refused."""
    from test_session import FakeScanner, kinds, run

    from rps7200.session import Roll, Scan

    for job in (Scan(resolution=1800, infrared=False), Roll(frames=2)):
        scanner = FakeScanner()
        scanner.suspect = "UsbError during a 1800 dpi pass"
        _, _, events = run(job, tmp_path, scanner=scanner)
        failed = kinds(events, "failed")
        assert failed and "DeviceSuspect" in failed[0].text
        assert "power-cycle" in failed[0].text
        assert scanner.calls == []
