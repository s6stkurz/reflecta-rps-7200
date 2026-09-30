"""READ STATE answered empty, where the driver polls it.

READ STATE issued right after a film move "came back empty every time"
(`DirectScanner._whole_frames`): the transport raises `NoDataYet`, a
`UsbError`. `position()` takes that as "not yet". Elsewhere it was taken as
a failure, or as an answer.
"""
from __future__ import annotations

import pytest

from conftest import FakeTransport, NoWaiting
from rps7200 import direct
from rps7200.direct import DirectScanner
from rps7200.protocol import SCSI_READ_GAIN_OFFSET, SCSI_READ_STATE
from rps7200.usb_transport import NoDataYet


class EmptyFirst(FakeTransport):
    """Answers the first `empty` READ STATEs with nothing at all."""

    def __init__(self, *args, empty=1, **kwargs):
        super().__init__(*args, **kwargs)
        self.empty = empty

    def command(self, command, *args, **kwargs):
        if command[0] == SCSI_READ_STATE and self.empty:
            self.empty -= 1
            self.sent.append((command[0], b""))
            raise NoDataYet("scanner has no data ready (of 13 bytes)")
        return super().command(command, *args, **kwargs)


def test_an_empty_read_state_before_a_pass_is_polled_past(monkeypatch):
    """The pass's opening polls took it as a failure: a hold's prescan 0.4 s
    after its nudge failed the frame over a status query the next poll would
    have answered."""
    monkeypatch.setattr(direct, "time", NoWaiting())

    class Reached(Exception):
        pass

    def reached(*a, **k):
        raise Reached

    s = DirectScanner(transport=EmptyFirst(
        replies={SCSI_READ_GAIN_OFFSET: bytes(123)}), debug=False)
    s._own_transport = False
    s._read_pass = reached
    with pytest.raises(Reached):
        s.scan(resolution=300, infrared=False, shading=False)


def test_an_empty_read_state_does_not_cost_a_calibration(monkeypatch):
    """The calibration's opening polls are the same, and a failure there
    costs the 3-4 minutes a calibration takes."""
    from test_calibration_end import DARK, LIT, CalibratingTransport

    class Empty(CalibratingTransport):
        empty = 1

        def command(self, command, *args, **kwargs):
            if command[0] == SCSI_READ_STATE and self.empty:
                self.empty = 0
                raise NoDataYet("scanner has no data ready (of 13 bytes)")
            return super().command(command, *args, **kwargs)

    monkeypatch.setattr(direct, "time", NoWaiting())
    s = DirectScanner(transport=Empty(DARK + LIT), debug=False)
    s._own_transport = False
    assert s.calibrate_shading()["reference"] is not None


def test_a_move_counts_only_once_the_counter_leaves_where_it_was(monkeypatch):
    """With the READ STATE before the move unanswered, the first poll to
    answer counted as the move whatever it said: the counter lags the command
    by 1.6-6.2 s, so the old position read a second later came back as the
    new one, and a roll prescanned a transport still moving."""
    monkeypatch.setattr(direct, "time", NoWaiting())
    # Before the move: unanswered, then 4. After it: still 4, then 5.
    t = FakeTransport(positions=[None, 4, 4, 5])
    s = DirectScanner(transport=t, debug=False)
    s._own_transport = False
    assert s.advance() == 5


def test_a_move_of_several_frames_is_one_command_a_frame(monkeypatch):
    """The step count went into the value byte, which moves nothing more --
    `04 01 00 02` moved one frame -- and the move reported success on the
    first change, where the demo moved every frame asked for."""
    from rps7200.protocol import SCSI_SLIDE

    monkeypatch.setattr(direct, "time", NoWaiting())
    t = FakeTransport(positions=[0, 1, 1, 2])
    s = DirectScanner(transport=t, debug=False)
    s._own_transport = False
    assert s.advance(steps=2) == 2
    assert t.payloads(SCSI_SLIDE) == [bytes([0x04, 0x01, 0x00, 0x01])] * 2
    with pytest.raises(ValueError):
        s.retreat(steps=0)


def test_a_read_answered_with_no_data_is_not_counted_as_lines():
    """The transport returns nothing for a READ answered OK with no data
    phase, and read_planes counted the lines as read: a pass could reach its
    declared count early and be taken as complete with lines still on the
    device. It is a refused read now, and the pass is not whole."""
    from rps7200.protocol import SCSI_READ, ScanReadError

    s = DirectScanner(transport=FakeTransport(replies={SCSI_READ: bytes(0)}),
                      debug=False)
    s._own_transport = False
    with pytest.raises(ScanReadError, match="returned 0 bytes"):
        s.read_lines(4, 100, retries=1)
