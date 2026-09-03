"""Scanning a whole roll, without a scanner attached.

Two things are load-bearing here and neither is guessable from the SANE source,
because the stock backend never drives this transport at all.

*The advance payload.* `SLIDE 04 01 00 01`, recovered from
`captures/600_ICE_FILM_STRIP_5.pcapng` -- CyberView walking a 5-frame strip.
This driver used to send `04 16 00 00`, with a zero where every observed advance
carried a 1.

*The confirmation.* `READ_STATE` byte 2 is the transport position, and it is the
only signal in any capture that says the film actually moved. It stepped
0 -> 1 -> 2 -> 3 -> 4 across that session's four advances and stayed put through
a session that never advanced. The reading taken immediately after the command
came back empty every time, so the poll has to survive a failed read rather than
read it as the end of the film.
"""

import numpy as np
import pytest

from rps7200.direct import (
    FULL_FRAME,
    METER_EACH,
    METER_NONE,
    METER_ONCE,
    SCSI_SLIDE,
    SLIDE_INIT,
    SLIDE_NEXT,
    NOMINAL_FRAME_WIDTH,
    DirectScanner,
    film_bounds,
    frame_contrast,
    registration,
)
from conftest import FakeTransport, settings


# --------------------------------------------------------------------------
# the transport commands
# --------------------------------------------------------------------------


def scanner_on(positions):
    s = DirectScanner(transport=FakeTransport(positions))
    s.verbose = False
    return s


def slides(transport):
    return [data for opcode, data in transport.sent if opcode == SCSI_SLIDE]


def test_the_advance_is_the_payload_the_vendor_sends():
    """04 01 00 01. The old 04 16 00 00 put a zero where the 1 has to be."""
    s = scanner_on([0, 1])
    s.advance(poll=0.0)
    assert slides(s.t) == [bytes([SLIDE_NEXT, 0x01, 0x00, 0x01])]


def test_slide_init_payload_is_unchanged():
    """Every scan sends this, so moving it would invalidate the library.

    `PROTOCOL_REVISION` marks entries as comparable only while the conversation
    with the device is identical; changing what a scan says would split every
    existing entry from every new one.
    """
    s = scanner_on([0])
    s.slide(SLIDE_INIT)
    assert slides(s.t) == [bytes([SLIDE_INIT, 0x16, 0x00, 0x00])]


def test_advance_waits_for_the_position_to_change():
    s = scanner_on([0, 0, 0, 1])
    assert s.advance(poll=0.0) == 1


def test_advance_survives_the_empty_read_that_follows_it():
    """The reading straight after the command came back empty in all four."""
    s = scanner_on([0, None, None, 1])
    assert s.advance(poll=0.0) == 1


def test_advance_that_never_moves_is_the_end_of_the_film():
    s = scanner_on([3, 3, 3, 3])
    assert s.advance(timeout=0.05, poll=0.0) is None


def test_read_state_reports_the_transport_position():
    s = scanner_on([4])
    assert s.read_state().position == 4


# --------------------------------------------------------------------------
# reading a prescan
# --------------------------------------------------------------------------


# Levels from a real 300 dpi prescan of a C-41 negative on this scanner: the
# empty aperture read 143/153/153 in R/G/B and the film 34/15/7. Getting that
# ratio right in the fixtures matters -- an earlier version had film at half the
# clear level, which is nothing like real film, and it hid a detector that was
# reporting a perfectly registered frame as 35 mm of drift.
CLEAR_LEVEL = 150
FILM_LEVELS = (34, 15, 7)


def blank(height=40, width=60, falloff=0.22):
    """An empty aperture: bright, with the lamp's falloff across the width.

    The falloff is ~22% centre to edge and is constant *down* each column, which
    is why it must not read as a picture.
    """
    profile = CLEAR_LEVEL * (1 - falloff * np.linspace(-1, 1, width) ** 2)
    return np.repeat(np.tile(profile, (height, 1))[:, :, None], 3, axis=2).astype(
        np.uint8
    )


def picture(height=40, width=60, seed=0):
    """Film with a picture on it: 5-20x darker than the empty aperture."""
    rng = np.random.default_rng(seed)
    varied = np.array(FILM_LEVELS) * (1 + rng.normal(0, 0.55, (height, width, 3)))
    return np.clip(varied, 0, 255).astype(np.uint8)


def test_blank_film_scores_no_contrast_despite_the_lamp_falloff():
    """The metric is column-wise for exactly this reason.

    Vignetting is ~22% across the sensor and constant down it. Measured across
    the width, empty film would read as a picture.
    """
    assert frame_contrast(blank()) < 0.001
    assert frame_contrast(picture()) > 0.1


def test_registration_reports_a_signed_offset_in_millimetres():
    marks = registration(picture())
    assert set(marks) >= {"x0", "x1", "offset", "offset_mm", "margin", "margin_mm"}
    assert isinstance(marks["offset"], int)


# --------------------------------------------------------------------------
# the roll loop
# --------------------------------------------------------------------------


class FakeRoll(DirectScanner):
    """A strip of pictures, driven with no USB underneath.

    ``strip`` is one entry per transport position: an image for a picture, None
    for clear film past the end.
    """

    def __init__(self, strip, base=(8000, 20000, 50000, 8000), fail_at=(),
                 echoes=False):
        self.verbose = False
        self.strip = list(strip)
        self.at = 0
        self.fail_at = set(fail_at)
        # `echoes` is the question this device answers in the negative. Across
        # 17 READ GAIN/OFFSET responses in the strip capture only bytes 66-68 --
        # the live R/G/B offsets -- ever change; the exposure fields hold the
        # same reference however different the value just written. The roll has
        # to be right either way, so both models are exercised.
        self.echoes = echoes
        self.reference = settings(*base)
        self._settings = self.reference
        self.exposures = []
        self.metered_channels = []
        self.frames_scanned = []
        self.metered_for_infrared = []
        self.advances = 0

    # -- the device
    def get_gain_offset(self):
        return self._settings if self.echoes else self.reference

    def set_gain_offset(self, s, infrared=False):
        self._settings = s

    def position(self):
        return self.at

    def advance(self, steps=1, timeout=30.0, poll=0.5):
        if self.at + 1 >= len(self.strip):
            return None
        self.at += 1
        self.advances += 1
        return self.at

    # -- the passes
    def prescan(self, resolution=300, frame=None):
        frame_at = self.strip[self.at]
        return (blank() if frame_at is None else frame_at), None

    def auto_exposure(self, target=0.7, infrared=False, film="negative", **kw):
        # The real one ALWAYS probes in RGB. `infrared` does not change the
        # probe; it says the scan that follows is RGBI, which is what gives
        # blue its headroom.
        self.metered_channels.append(3)
        self.metered_for_infrared.append(infrared)
        scales = [2.0] * 3
        # Metering leaves the device metered, as the real one does.
        self._settings = self._settings.scaled(scales)
        return scales

    def scan(self, resolution=300, infrared=True, exposure_scale=1.0, **kw):
        if self.at in self.fail_at:
            raise TimeoutError(f"pretend failure at position {self.at}")
        self.frames_scanned.append(tuple(kw.get("frame") or FULL_FRAME))
        settings = self.get_gain_offset().scaled(exposure_scale)
        self.set_gain_offset(settings)
        self.exposures.append(list(settings.exposure))
        n = 4 if infrared else 3
        return np.zeros((4, 4, n), np.uint16), {"resolution_dpi": resolution}


def test_the_first_picture_is_scanned_before_any_advance():
    """The film is already at picture 1; advancing first would skip it."""
    s = FakeRoll([picture(seed=i) for i in range(3)])
    out = list(s.scan_roll(frames=3, meter=METER_NONE))
    assert [f.index for f in out] == [0, 1, 2]
    assert [f.position for f in out] == [0, 1, 2]
    assert s.advances == 2          # between the three, not before the first


def test_a_roll_stops_when_the_window_holds_no_picture():
    """Clear film past the last frame ends the roll without being told how many."""
    s = FakeRoll([picture(seed=0), picture(seed=1), None, None])
    out = list(s.scan_roll(meter=METER_NONE))
    assert len(out) == 2


def test_a_roll_stops_when_the_transport_will_not_move():
    s = FakeRoll([picture(seed=0)])
    out = list(s.scan_roll(frames=10, meter=METER_NONE))
    assert len(out) == 1


def test_a_roll_stops_at_the_frame_count():
    s = FakeRoll([picture(seed=i) for i in range(6)])
    out = list(s.scan_roll(frames=2, meter=METER_NONE))
    assert len(out) == 2


def test_skip_resumes_a_part_scanned_roll():
    s = FakeRoll([picture(seed=i) for i in range(6)])
    out = list(s.scan_roll(frames=2, skip=3, meter=METER_NONE))
    assert [f.index for f in out] == [3, 4]


def test_a_failed_frame_does_not_end_the_roll():
    """A roll takes hours; one bad frame must not cost the rest of it."""
    s = FakeRoll([picture(seed=i) for i in range(4)], fail_at={1})
    out = list(s.scan_roll(frames=4, meter=METER_NONE))
    assert [f.ok for f in out] == [True, False, True, True]
    assert "pretend failure" in out[1].error


def test_a_roll_gives_up_after_enough_consecutive_failures():
    s = FakeRoll([picture(seed=i) for i in range(6)], fail_at={0, 1, 2, 3, 4})
    out = list(s.scan_roll(frames=6, meter=METER_NONE, max_failures=3))
    assert len(out) == 3
    assert all(f.error for f in out)


@pytest.mark.parametrize("echoes", [False, True], ids=["measured", "if-it-echoed"])
def test_exposure_does_not_compound_across_a_roll(echoes):
    """Every frame is exposed from the reference, never from the frame before.

    On this device that comes free: READ GAIN/OFFSET returns a fixed reference,
    not a readback, so scaling it always gives base x scale whatever was written
    before. The roll must not *depend* on that, so the same assertion runs
    against a device that does echo -- where getting it wrong would walk the
    exposure x2, x4, x8 over three frames.
    """
    base = [8000, 20000, 50000, 8000]
    s = FakeRoll([picture(seed=i) for i in range(3)], base=tuple(base),
                 echoes=echoes)
    list(s.scan_roll(frames=3, meter=METER_EACH))
    assert len(s.exposures) == 3
    for got in s.exposures:
        assert got[:3] == [min(65535, round(b * 2.0)) for b in base[:3]]


def test_infrared_is_never_metered():
    """Only R, G and B are. The fourth channel keeps the device's own value.

    From the strip capture: across all 17 passes the infrared exposure is 7745 --
    in the 300 dpi RGB metering passes and the 600 dpi RGBI scans alike -- while
    R, G and B move freely between them. Metering it would also cost a full
    scan's time per round, since a four-channel pass has a ~212 s floor whatever
    the resolution.
    """
    base = [8000, 20000, 50000, 7745]
    s = FakeRoll([picture(seed=i) for i in range(3)], base=tuple(base))
    list(s.scan_roll(frames=3, meter=METER_EACH, infrared=True))

    assert all(e[3] == 7745 for e in s.exposures), s.exposures
    assert all(e[0] != base[0] for e in s.exposures)      # R was metered
    # The probe is three-channel, never four -- that is not what the flag does.
    assert s.metered_channels == [3, 3, 3], s.metered_channels
    # But an RGBI scan must still be metered *as* one, or blue loses the
    # headroom it needs and clips: measured, a roll metered without it put blue
    # at 10.07x on a 6506 base, pinning the 16-bit timer at 65535.
    assert s.metered_for_infrared == [True, True, True], s.metered_for_infrared


def test_metering_once_holds_the_first_frames_exposure():
    base = [8000, 20000, 50000, 8000]
    s = FakeRoll([picture(seed=i) for i in range(3)], base=tuple(base))
    list(s.scan_roll(frames=3, meter=METER_ONCE))
    assert all(e == s.exposures[0] for e in s.exposures)


def test_a_dry_run_prescans_and_advances_but_never_scans():
    s = FakeRoll([picture(seed=i) for i in range(4)])
    out = list(s.scan_roll(frames=4, dry_run=True))
    assert s.exposures == []
    assert s.advances == 3
    assert all(f.prescan is not None and f.image is None for f in out)
    assert all("offset_mm" in f.registration for f in out)


def test_an_unknown_meter_mode_is_refused():
    s = FakeRoll([picture()])
    with pytest.raises(ValueError, match="unknown meter mode"):
        list(s.scan_roll(meter="auto"))


def test_a_drifted_frame_shows_up_as_a_narrower_picture():
    """A frame hanging outside the aperture cannot be seen; a short one can.

    The prescan only covers the transport window, so a picture that has drifted
    partly out of it is simply not there to be detected. What is detectable is
    that what remains is narrower than a whole frame -- the vendor's fifth strip
    frame measured 8472 units against 10079 for the four before it.
    """
    whole = registration(picture(width=600))
    assert whole["shortfall"] < 0.05 * NOMINAL_FRAME_WIDTH

    # A picture occupying only the right two thirds of the window: the rest of
    # the frame never reached the sensor.
    drifted = picture(width=600)
    drifted[:, :200] = blank(height=40, width=600)[:, :200]
    marks = registration(drifted)
    assert marks["shortfall"] > 0.2 * NOMINAL_FRAME_WIDTH
    assert marks["offset"] > 0        # sits right of centre: under-advanced


def test_a_full_window_frame_is_not_reported_as_drift():
    """The failure this replaced, reproduced.

    The shape below is the one that catches a frame-finder out: a bright empty
    strip, a violent step at the film's edge, then a whole frame that is dark
    and nearly flat. Anything keying on how much a column *varies* picks the
    border, because the border varies far more than the picture does -- the film
    edge is not square to the sensor, so its columns hold clear on some rows and
    film on others.

    film_bounds keys on level instead, which does not depend on picture content,
    and has to report this frame as covering the window rather than as a sliver
    of drift.
    """
    height, width, edge = 40, 428, 9
    frame = picture(height=height, width=width, seed=3)
    clear = blank(height=height, width=width)
    # The film edge is not square to the sensor, so the boundary columns hold
    # clear on some rows and film on others. That is where the variance rule's
    # peak comes from: those columns read std 36-59 against the picture's 1-10.
    rng = np.random.default_rng(0)
    for row in range(height):
        cut = edge + int(rng.integers(0, 4))
        frame[row, :cut] = clear[row, :cut]


    # The border columns are the highest-variance thing in the frame, which is
    # exactly the trap: they must not be mistaken for where the picture stops.
    grey = frame.astype(float).mean(axis=2)
    assert grey.std(axis=0)[:edge + 4].max() > 3 * grey.std(axis=0)[edge + 8:].max()

    # The film covers all but the strip, so nothing is missing.
    level = registration(frame)
    assert level["shortfall"] < 0.05 * NOMINAL_FRAME_WIDTH


def test_film_bounds_returns_the_whole_window_when_film_fills_it():
    """No empty aperture in view is the normal case, not a failure."""
    assert film_bounds(picture(width=200)) == FULL_FRAME


def test_the_roll_never_crops():
    """Every frame is scanned at the full transport window. No exceptions.

    A crop computed from a prescan cannot be undone: the pixels outside it were
    never read. The vendor does crop -- to 10080 x 6745 against the window's
    10344 x 6888 -- and on the strip capture's drifted fifth picture that cost
    it 1607 units, 5.7 mm of picture, unrecoverably. Scanning the whole window
    keeps that decision on the host, where it can be revisited.

    `registration` is recorded so a badly positioned frame can be *found*. It
    must never be wired to `set_scan_frame`.
    """
    s = FakeRoll([picture(seed=i) for i in range(3)])
    list(s.scan_roll(frames=3, meter=METER_NONE))
    assert s.frames_scanned, "no frames recorded"
    for got in s.frames_scanned:
        assert got == FULL_FRAME, f"scanned {got}, not the full window"
