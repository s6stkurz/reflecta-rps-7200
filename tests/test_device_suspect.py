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


def test_a_bracket_stopped_at_ctrl_c_files_the_passes_it_took(tmp_path, monkeypatch):
    """Stopped between passes, never inside one, and nothing scanned is lost:
    the passes used to be held in memory until the end and die with the
    exception."""
    from rps7200 import console
    from test_scan_tool import _filed, run_correcting

    monkeypatch.setattr(console.DeferredInterrupt, "requested", lambda self: True)
    _created, code = run_correcting(tmp_path, monkeypatch, "--bracket", "3")
    assert code == 130
    assert len(_filed(tmp_path)) == 1, "the pass taken before Ctrl-C was not filed"


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
