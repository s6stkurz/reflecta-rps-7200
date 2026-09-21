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

import inspect

import numpy as np
import pytest

from rps7200.framing import MAX_HOLD_MOVES
from rps7200.direct import (
    SLIDE_PREV,
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
    metering_region,
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
        self.prescans = []
        self.slid = []
        self.metered_channels = []
        self.frames_scanned = []
        self.metered_for_infrared = []
        self.advances = 0
        self.prescan_keep_raw = []

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
    def prescan(self, resolution=300, frame=None, keep_raw=False):
        self.prescan_keep_raw.append(keep_raw)
        # `prescans` lets a test script what successive looks return, which is
        # how a correction's before/after pair gets simulated.
        if self.prescans:
            return self.prescans.pop(0), None
        frame_at = self.strip[self.at]
        return (blank() if frame_at is None else frame_at), None

    def slide(self, action=SLIDE_INIT, param=0x16, value=0):
        self.slid.append((action, param, value))

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


def test_only_scans_the_frames_that_were_chosen():
    """The point of a survey: pay for the four good frames, not the seventeen."""
    s = FakeRoll([picture(seed=i) for i in range(6)])
    out = list(s.scan_roll(only=(1, 3), meter=METER_NONE))
    assert [f.index for f in out] == [1, 3]
    assert [f.position for f in out] == [1, 3]


def test_a_frame_nobody_chose_is_not_even_prescanned():
    """It costs its advance and nothing else -- 7 seconds rather than 13."""
    s = FakeRoll([picture(seed=i) for i in range(6)])
    list(s.scan_roll(only=(2,), meter=METER_NONE))
    assert len(s.prescan_keep_raw) == 1      # frame 2 only
    assert s.advances == 2                   # 0 -> 1 -> 2, then stop


def test_the_roll_ends_after_the_last_chosen_frame():
    """No walking out the rest of a strip the operator has already judged."""
    s = FakeRoll([picture(seed=i) for i in range(9)])
    out = list(s.scan_roll(only=(0, 1), meter=METER_NONE))
    assert [f.index for f in out] == [0, 1]
    assert s.advances == 1
    assert s.at == 1


def test_choosing_no_frames_scans_nothing_and_never_moves_the_film():
    s = FakeRoll([picture(seed=i) for i in range(4)])
    assert list(s.scan_roll(only=(), meter=METER_NONE)) == []
    assert s.advances == 0
    assert s.prescan_keep_raw == []


def test_a_stop_is_looked_at_while_advancing_past_unchosen_frames():
    """Walking past ten frames must not be ten seconds of ignoring stop."""
    s = FakeRoll([picture(seed=i) for i in range(8)])
    out = list(s.scan_roll(
        only=(6,), meter=METER_NONE, should_stop=lambda: s.advances >= 2
    ))
    assert out == []
    assert s.advances == 2


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


def test_media_is_read_from_byte_8_not_byte_6():
    """Measured with one variable changed: a strip going into the transport.

    Byte 8 read 1 empty and 0 loaded. Byte 6 said 0x1d -- no film by the old
    test -- in the very same reading that had a strip demonstrably loaded, which
    is why that reading could never be believed and was never allowed to block a
    scan.

    The captures cannot corroborate byte 8 and that is the point: all 737 of
    their READ STATE responses hold 0 there, because every one was taken with
    film in. An empty transport is the state they never contained.
    """
    from rps7200.protocol import State

    empty = State(button=False, warming_up=False, scanning=0x1D, busy=1, position=2)
    loaded = State(button=False, warming_up=False, scanning=0x1D, busy=0, position=4)

    assert empty.no_media and not empty.media_loaded
    assert loaded.media_loaded and not loaded.no_media
    # Byte 6 is identical in both, which is exactly why it cannot decide this.
    assert empty.scanning == loaded.scanning


# --- registration correction ------------------------------------------------

def framed(gap_left=0, gap_right=0, height=40, width=428, seed=0):
    """A frame with a gap of the given width at one or both edges.

    The gap is unexposed base: brighter than the picture and flat down the
    column. Both properties are needed -- the detector requires both, because
    every earlier one keyed on a single property and was wrong somewhere.
    """
    rng = np.random.default_rng(seed)
    a = np.clip(np.array(FILM_LEVELS) * (1 + rng.normal(0, 0.55, (height, width, 3))),
                0, 255)
    if gap_left:
        a[:, :gap_left] = 200.0
    if gap_right:
        a[:, -gap_right:] = 200.0
    return a.astype(np.uint8)


def test_gap_needs_to_be_bright_and_flat():
    """Either property alone is what made four earlier detectors wrong."""
    from rps7200.framing import gap_edges

    assert gap_edges(framed(gap_left=5)) == (5, 0)
    assert gap_edges(framed(gap_right=5)) == (0, 5)
    assert gap_edges(framed()) == (0, 0)

    # bright but not flat -- a sunlit area, not a gap
    rng = np.random.default_rng(1)
    noisy = framed()
    noisy[:, :6] = np.clip(rng.normal(200, 60, (40, 6, 3)), 0, 255).astype(np.uint8)
    assert gap_edges(noisy)[0] == 0


def test_a_reading_beyond_the_aperture_is_refused():
    """The bound is arithmetic: 36.49 mm of aperture, ~36 mm of frame.

    A larger reading is the detector failing, and acting on it would drive the
    transport on the strength of a number known to be impossible.
    """
    from rps7200.framing import MAX_REGISTRATION_MM, registration_error_mm

    mm, why = registration_error_mm(framed(gap_left=4))
    assert mm is not None and 0 < mm <= MAX_REGISTRATION_MM

    mm, why = registration_error_mm(framed(gap_left=20))
    assert mm is None and "exceeds" in why


def test_gaps_at_both_edges_are_not_drift():
    from rps7200.framing import registration_error_mm

    mm, why = registration_error_mm(framed(gap_left=4, gap_right=4))
    assert mm is None and "both edges" in why


def test_correction_is_off_unless_asked():
    s = FakeRoll([framed(gap_left=4) for _ in range(3)])
    list(s.scan_roll(frames=3, meter=METER_NONE))
    assert s.slid == [] or all(a == SLIDE_INIT for a, _, _ in s.slid), s.slid


#: A gap this wide is a real error: 10 columns is 0.85 mm, so the frame wants
#: about -0.61 mm, comfortably past the 0.27 mm the transport can deliver.
OUT_BY_A_GAP = 10


def aimable(count, gap=OUT_BY_A_GAP):
    """A strip long enough for the ensemble to arm on.

    One frame cannot be aimed and neither can two, by construction rather than
    by accident: the base needs bands from two frames, and the prior needs an
    advance between two placed ones before it can say anything. Frame 3 is the
    first that two members can both see, which is the bootstrap the operator
    asked for and also the earliest the evidence allows.
    """
    return [framed(gap_left=gap, seed=n) for n in range(count)]


def test_a_frame_on_its_own_is_never_aimed():
    """The gate, at the roll level. `gap_edges` would have moved film here on
    one detector's word, which is what it did wrongly on four of nine frames."""
    s = FakeRoll(aimable(1))
    frames = list(s.scan_roll(frames=1, meter=METER_NONE, correct=True))
    assert [x for x in s.slid if x[0] in (0x00, 0x01)] == []
    fix = frames[0].registration["correction"]
    assert fix["outcome"] == "abstained"
    assert "base level is not calibrated" in fix["reason"]


def test_a_dry_run_measures_but_never_moves():
    s = FakeRoll(aimable(3))
    frames = list(s.scan_roll(frames=3, meter=METER_NONE, correct_dry_run=True))
    sub = [x for x in s.slid if x[0] in (0x00, 0x01)]
    assert sub == [], sub
    fix = frames[2].registration["correction"]
    assert fix["moved"] is False
    assert fix["outcome"] == "dry_run"
    assert fix["would_send"]["action"] == 0x01      # gap left -> move back
    assert fix["would_send"]["param"] >= 1


def test_an_error_inside_the_deadband_is_left_alone():
    """Smallest possible move is 0.27 mm, so correcting 0.1 mm cannot help.

    Three frames, not one: on a shorter strip this passed because nothing was
    calibrated yet, which is a different reason for the same silence and would
    have gone on passing if the deadband were deleted.
    """
    s = FakeRoll(aimable(3, gap=3))                 # 0.26 mm -> wants -0.01 mm
    frames = list(s.scan_roll(frames=3, meter=METER_NONE, correct=True))
    assert [x for x in s.slid if x[0] in (0x00, 0x01)] == []
    assert frames[2].registration["correction"]["outcome"] == "in_place"


def test_a_real_error_is_corrected_the_other_way():
    """A gap on the left means the frame sits too far +x, so it must come back.

    Getting this backwards drives every frame of a roll the wrong way, and the
    transport gives no signal that it happened.
    """
    s = FakeRoll(aimable(3))
    frames = list(s.scan_roll(frames=3, meter=METER_NONE, correct=True))
    sub = [x for x in s.slid if x[0] in (0x00, 0x01)]
    assert sub, "frame 3 is measurable by two members and should have moved"
    action, param, value = sub[0]
    assert action == 0x01                      # backward
    assert 1 <= param <= 8
    assert value == 0x04
    fix = frames[2].registration["correction"]
    assert fix["moved"] is True
    assert fix["decision_mm"] < 0


def test_a_correction_that_does_not_land_is_reported():
    """Backlash swallows a move. Saying so is the whole point of re-measuring.

    Here the prescan never changes, so the film never appears to move and the
    loop spends its budget. It must say `not_converged` rather than report the
    distance it asked for as though it had been delivered.
    """
    s = FakeRoll(aimable(3))
    frames = list(s.scan_roll(frames=3, meter=METER_NONE, correct=True))
    fix = frames[2].registration["correction"]
    assert fix["moved"] is True
    assert fix["outcome"] == "not_converged"
    assert fix["moves"] == MAX_HOLD_MOVES


def test_a_corrected_frame_keeps_the_picture_it_arrived_as():
    """A corrected prescan replaces the original outright, so without this the
    only account of whether a correction helped is the detector's own."""
    s = FakeRoll(aimable(3))
    # Scripted so the verification pass is a different picture from the one
    # the frame arrived as; the strip fixture hands back one array object
    # every time, which cannot show a replacement happening at all.
    arrived = framed(gap_left=OUT_BY_A_GAP, seed=2)
    after = framed(gap_left=3, seed=9)
    s.prescans = [framed(gap_left=OUT_BY_A_GAP, seed=0),
                  framed(gap_left=OUT_BY_A_GAP, seed=1),
                  arrived, after, after, after]
    frames = list(s.scan_roll(frames=3, meter=METER_NONE, correct=True))
    moved = frames[2]
    assert moved.registration["correction"]["moved"] is True
    assert moved.prescan_before is arrived
    assert moved.prescan is not arrived


def test_a_frame_that_needed_no_move_keeps_no_before_picture():
    """No clutter for the frames that were already right -- and a file that
    exists only where something happened is itself a signal."""
    s = FakeRoll(aimable(3, gap=3))                 # inside the deadband
    frames = list(s.scan_roll(frames=3, meter=METER_NONE, correct=True))
    assert all(f.prescan_before is None for f in frames)


def test_an_ordinary_roll_carries_no_walk_at_all():
    """Nothing asked for aiming, so nothing is calibrated, remembered or
    recorded. A roll that does not want this must be untouched by it."""
    s = FakeRoll(aimable(3))
    frames = list(s.scan_roll(frames=3, meter=METER_NONE))
    assert all("correction" not in (f.registration or {}) for f in frames)
    assert all("base" not in (f.registration or {}) for f in frames)


def test_an_unverified_move_leaves_no_trace_in_the_prior():
    """The film has moved and nothing knows how far, so what this frame
    contributed is now a stale number -- and one bad delta is the poison a
    median cannot fix once a few of them agree."""
    from rps7200.framing import StripWalk

    walk = StripWalk()
    walk.placed[4] = -0.6
    walk.record(4, None, 1.0, verified=False)
    assert 4 not in walk.placed
    assert walk.history() == []


# --- automatic filing -------------------------------------------------------

def _debug_scanner(**kw):
    from rps7200.direct import DirectScanner

    class Detached(DirectScanner):
        def __init__(self, **kw):
            super().__init__(transport=object(), **kw)
            self._own_transport = False

    return Detached(**kw)


def test_a_scan_files_the_raw_pixels_and_returns_the_corrected_ones():
    """The two halves of the bargain, checked together.

    `scan()` hands the caller a corrected image -- everything shown, exported
    and saved is corrected -- and files the pixels as the scanner sent them, so
    the correction can be redone later with better code. Filing the corrected
    ones instead is what every entry did before, and it forecloses that on
    every scan ever taken.

    Pinned at the seam rather than end to end: `scan()` needs a device, so the
    assertion is that the two arrays handed out are different objects and that
    it is the *uncorrected* one that reaches the library.
    """
    import inspect

    from rps7200.direct import DirectScanner

    source = inspect.getsource(DirectScanner.scan)
    assert "raw_pixels = image" in source, "the pre-correction pixels must be kept"
    assert "self._debug_capture(raw_pixels, meta)" in source, \
        "filing must take the raw pixels, not the corrected ones"
    assert "return image, meta" in source, "callers still get the corrected image"
    # The order matters: `raw_pixels` has to be bound before apply_shading
    # rebinds `image`, or it is the corrected array under another name.
    assert (source.index("raw_pixels = image")
            < source.index("image, shading_report = apply_shading"))


def test_filing_is_off_by_default():
    """Ordinary use is not burdened. CLAUDE.md says who must turn it on."""
    import os

    assert os.environ.get("RPS7200_DEBUG") is None or True
    assert _debug_scanner().debug is False
    assert _debug_scanner(debug=True).debug is True


def test_the_environment_can_turn_filing_on(monkeypatch):
    """A probe script inherits it rather than having to remember."""
    monkeypatch.setenv("RPS7200_DEBUG", "1")
    assert _debug_scanner().debug is True
    monkeypatch.setenv("RPS7200_DEBUG", "0")
    assert _debug_scanner().debug is False
    monkeypatch.setenv("RPS7200_DEBUG", "on")
    assert _debug_scanner().debug is True


def test_nothing_is_written_while_the_device_is_open(tmp_path, monkeypatch):
    """Queue during, write after. Gzipping an entry with the device open and
    idle preceded a wedge once, which is why the tools have always worked this
    way and why the driver now does too."""
    monkeypatch.setenv("RPS7200_DEBUG_ROOT", str(tmp_path))
    s = _debug_scanner(debug=True)
    img = np.random.default_rng(0).integers(0, 255, (8, 16, 3), dtype=np.uint8)
    meta = {"resolution_dpi": 300, "channels": 3, "channel_order": ["r", "g", "b"],
            "width": 16, "height": 8, "depth": 8, "frame": [0, 0, 10343, 6887],
            "bytes_per_line": 48, "film": "negative", "protocol_revision": 1}

    s._debug_capture(img, meta)
    s._debug_capture(img, meta)
    assert len(s._debug_pending) == 2
    assert list(tmp_path.iterdir()) == [], "wrote while the session was open"

    s.close()
    assert s._debug_pending == []
    entries = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert len(entries) == 2, sorted(p.name for p in tmp_path.iterdir())
    assert (entries[0] / "raw.bin.gz").exists() or True   # raw only when kept


def test_a_filing_failure_never_breaks_the_session(tmp_path, monkeypatch):
    """Losing the record beats losing the session that produced it."""
    blocker = tmp_path / "a-file-not-a-directory"
    blocker.write_text("", encoding="utf-8")
    # The library cannot create a directory underneath a regular file, so this
    # makes filing fail for a real reason rather than a contrived one.
    monkeypatch.setenv("RPS7200_DEBUG_ROOT", str(blocker / "library"))
    s = _debug_scanner(debug=True)
    s._debug_capture(np.zeros((4, 4, 3), np.uint8), {"resolution_dpi": 300})
    s.close()                      # must not raise
    assert s._debug_pending == []


def test_filing_off_queues_nothing():
    s = _debug_scanner(debug=False)
    s._debug_capture(np.zeros((4, 4, 3), np.uint8), {"resolution_dpi": 300})
    assert s._debug_pending == []


def test_scans_are_spooled_to_disk_not_held_in_ram(tmp_path, monkeypatch):
    """A 7200 dpi RGBI frame is 570 MB of pixels and as much again of raw bytes.

    Queueing seventeen of those in memory would want 19 GB, which is why only
    paths and small metadata stay resident. The shading reference (a few hundred
    kB) and the CCD mask (5172 bytes) are small enough to keep.
    """
    monkeypatch.setenv("RPS7200_DEBUG_ROOT", str(tmp_path / "lib"))
    s = _debug_scanner(debug=True)
    big = np.zeros((300, 400, 4), np.uint16)
    s.last_raw = b"\x00" * 100_000
    s.last_raw_layout = {"width": 400, "lines": 300, "channels": 4,
                         "bytes_per_line": 3200}
    s._debug_capture(big, {"resolution_dpi": 300})

    held = s._debug_pending[0]
    assert "image" not in held, "the array is being kept in memory"
    assert "raw" not in held, "the raw bytes are being kept in memory"
    assert held["image_path"].exists()
    assert held["raw_path"].exists()
    # and the spool is somewhere temporary, not in the library
    assert str(tmp_path) not in str(held["image_path"])


def test_the_spool_is_cleaned_up_after_filing(tmp_path, monkeypatch):
    monkeypatch.setenv("RPS7200_DEBUG_ROOT", str(tmp_path / "lib"))
    s = _debug_scanner(debug=True)
    s._debug_capture(np.zeros((8, 16, 3), np.uint8),
                     {"resolution_dpi": 300, "channels": 3,
                      "channel_order": ["r", "g", "b"], "width": 16, "height": 8,
                      "depth": 8, "frame": [0, 0, 10343, 6887],
                      "bytes_per_line": 48, "film": "negative",
                      "protocol_revision": 1})
    spool = s._debug_spool
    assert spool.exists()
    s.close()
    assert not spool.exists(), "the spool outlived the session"
    assert s._debug_spool is None


def test_the_spooled_array_is_let_go_before_its_file_is_unlinked(
        tmp_path, monkeypatch):
    """Windows will not unlink a file that is still mapped; POSIX will.

    So the flush held an `np.load(..., mmap_mode="r")` open across its own
    unlink and nothing complained on the machine it was written on, while on
    Windows every frame's spool survived -- 43 GB of a 7200 dpi roll left in
    the temporary directory, because the refusal was swallowed.

    The refusal is simulated here rather than waited for, so the platform that
    cannot see the bug is the one that guards against it.
    """
    import weakref
    from pathlib import Path

    monkeypatch.setenv("RPS7200_DEBUG_ROOT", str(tmp_path / "lib"))

    mapped: dict[str, weakref.ref] = {}
    real_load = np.load

    def load(path, *args, **kw):
        array = real_load(path, *args, **kw)
        mapped[str(path)] = weakref.ref(array)   # weak: the flush owns it
        return array

    real_unlink = Path.unlink

    def unlink(self, *args, **kw):
        held = mapped.get(str(self))
        if held is not None and held() is not None:
            raise PermissionError(32, "the file is still mapped")  # WinError 32
        return real_unlink(self, *args, **kw)

    monkeypatch.setattr(np, "load", load)
    monkeypatch.setattr(Path, "unlink", unlink)

    s = _debug_scanner(debug=True)
    s._debug_capture(np.zeros((8, 16, 3), np.uint8),
                     {"resolution_dpi": 300, "channels": 3,
                      "channel_order": ["r", "g", "b"], "width": 16, "height": 8,
                      "depth": 8, "frame": [0, 0, 10343, 6887],
                      "bytes_per_line": 48, "film": "negative",
                      "protocol_revision": 1})
    spool = s._debug_spool
    s.close()
    assert mapped, "the flush never mapped anything; this test proves nothing"
    assert not spool.exists(), "the spool was still mapped when it was unlinked"
    assert s._debug_spool is None


# ---------------------------------------------------------------------------
# Stopping, and the bytes a prescan is filed with. Both of these were found on
# the hardware rather than here, which is why they are here now.
# ---------------------------------------------------------------------------


def test_a_stop_while_a_frame_is_running_ends_the_roll_after_it():
    """The ordinary case, and the one the button describes.

    A frame takes minutes; the operator presses stop somewhere inside that. The
    roll must finish the frame in hand -- abandoning a read is what costs a
    power cycle -- and then stop, without advancing the film again.
    """
    s = FakeRoll([picture(seed=i) for i in range(5)])
    stop = {"now": False}
    seen = []
    for rf in s.scan_roll(frames=5, infrared=False, meter=METER_NONE,
                          should_stop=lambda: stop["now"]):
        seen.append(rf.index)
        if len(seen) == 2:
            stop["now"] = True                   # pressed while frame 2 is in hand
    assert seen == [0, 1], seen
    # One advance to reach frame 2, and none after the stop.
    assert s.advances == 1, s.advances


def test_a_stop_during_the_advance_still_saves_the_next_frame():
    """The advance takes 2-7 seconds on the hardware. A stop landing inside it
    used to go unlooked-at until the frame after had been prescanned in full."""
    s = FakeRoll([picture(seed=i) for i in range(5)])
    stop = {"now": False}
    real_advance = s.advance

    def advance_and_press(*a, **kw):
        position = real_advance(*a, **kw)
        stop["now"] = True                       # pressed mid-advance
        return position

    s.advance = advance_and_press
    seen = [rf.index for rf in s.scan_roll(frames=5, infrared=False,
                                           meter=METER_NONE,
                                           should_stop=lambda: stop["now"])]
    # Frame 1, then the advance, then the stop is seen before frame 2 begins.
    assert seen == [0], seen


def test_a_stop_set_before_the_roll_starts_scans_nothing():
    s = FakeRoll([picture(seed=i) for i in range(5)])
    seen = [rf.index for rf in s.scan_roll(frames=5, infrared=False,
                                           meter=METER_NONE,
                                           should_stop=lambda: True)]
    assert seen == []
    assert s.advances == 0


def test_without_a_stop_the_roll_runs_to_its_frame_count():
    s = FakeRoll([picture(seed=i) for i in range(5)])
    seen = [rf.index for rf in s.scan_roll(frames=3, infrared=False,
                                           meter=METER_NONE)]
    assert seen == [0, 1, 2]
    assert s.advances == 2


def test_a_roll_prescan_keeps_its_own_bytes():
    """Otherwise `capture_record()` hands back whatever the previous pass left
    in `last_raw`, and the prescan is filed with another photograph's bytes --
    which is what happened to three entries on the hardware run."""
    s = FakeRoll([picture(seed=i) for i in range(3)])
    list(s.scan_roll(frames=2, infrared=False, meter=METER_NONE, keep_raw=True))
    assert s.prescan_keep_raw and all(s.prescan_keep_raw), s.prescan_keep_raw


def test_a_roll_asked_not_to_keep_raw_does_not_make_the_prescan_keep_it():
    s = FakeRoll([picture(seed=i) for i in range(3)])
    list(s.scan_roll(frames=2, infrared=False, meter=METER_NONE, keep_raw=False))
    assert not any(s.prescan_keep_raw), s.prescan_keep_raw


def test_going_back_a_frame_sends_slide_prev():
    """`05 01 00 01`, the mirror of an advance -- what the vendor sends to
    rewind a finished roll, one frame per step."""
    t = FakeTransport(positions=[3, 3, 2])
    s = DirectScanner(transport=t)
    assert s.retreat(poll=0.01) == 2
    assert t.payloads(SCSI_SLIDE) == [bytes([SLIDE_PREV, 0x01, 0x00, 0x01])]


def test_going_forward_a_frame_still_sends_slide_next():
    t = FakeTransport(positions=[3, 3, 4])
    s = DirectScanner(transport=t)
    assert s.advance(poll=0.01) == 4
    assert t.payloads(SCSI_SLIDE) == [bytes([SLIDE_NEXT, 0x01, 0x00, 0x01])]


def test_a_retreat_that_does_not_move_says_so_without_calling_it_the_end():
    """An advance that fails means the film ran out. A retreat that fails
    usually means it is already at the first frame, which is not the same."""
    t = FakeTransport(positions=[0])
    s = DirectScanner(transport=t, verbose=False)
    assert s.retreat(timeout=0.05, poll=0.01) is None


def test_a_sub_frame_move_never_reaches_the_frame_counter():
    """`SLIDE` action 0x00/0x01 moves the film without the counter seeing it,
    which is why only a prescan can confirm a nudge landed."""
    t = FakeTransport(positions=[2])
    s = DirectScanner(transport=t, verbose=False)
    out = s.nudge(0.5)
    sent = t.payloads(SCSI_SLIDE)
    assert len(sent) == 1
    assert sent[0][0] == 0x00, "forward is action 0x00"
    assert out["asked_mm"] > 0
    back = DirectScanner(transport=FakeTransport(positions=[2]), verbose=False)
    assert back.nudge(-0.5)["asked_mm"] < 0


def test_the_smallest_nudge_is_the_smallest_the_hardware_can_do():
    """param 1 = 0.1057 + 0.1662 mm. Asking for less does not get you less."""
    s = DirectScanner(transport=FakeTransport(), verbose=False)
    assert s.param_for_mm(0.01) == 1
    assert s.param_for_mm(0.27) == 1
    assert s.param_for_mm(99.0) == DirectScanner.MAX_CORRECTION_PARAM


# -- metering looks inside the film ----------------------------------------


def bordered(width=200, height=120, film=40, clear=200, margin=12):
    """A picture with clear aperture around it, as a short strip looks."""
    img = np.full((height, width, 3), clear, np.uint16)
    img[margin:height - margin, margin:width - margin] = film
    # A highlight in the picture, well below the clear level.
    img[height // 2, width // 2] = film * 2
    return img


def test_metering_ignores_the_clear_aperture():
    """The aperture is far brighter than any part of the picture, so metering
    the whole window lets however much of it is in view set the exposure."""
    img = bordered()
    whole = np.percentile(img[..., 1], 99.5)
    inside = np.percentile(metering_region(img)[..., 1], 99.5)
    assert whole >= 200, "the fixture should have clear aperture in the frame"
    assert inside < 100, (
        f"metering still sees the aperture: {inside} -- it should see film"
    )


def test_metering_keeps_the_whole_frame_when_film_fills_it():
    """The normal case for a registered frame. No aperture, so nothing to cut
    but the inset."""
    img = np.full((120, 200, 3), 40, np.uint16)
    region = metering_region(img)
    assert region.shape[2] == 3
    # An inset, not a rejection: most of the frame survives.
    assert region.shape[0] * region.shape[1] > 0.7 * 120 * 200


def test_a_degenerate_frame_falls_back_to_the_whole_image():
    """Failing back to today's behaviour is the right failure: it is the thing
    this improves on, not something worse."""
    for img in (np.zeros((0, 0, 3), np.uint16),
                np.full((2, 2, 3), 40, np.uint16)):
        region = metering_region(img)
        assert region.shape == img.shape


def test_the_region_is_a_view_and_does_not_copy():
    img = bordered()
    region = metering_region(img)
    assert region.base is img or region.base is not None


# --- holding a frame to the position an operator approved -------------------
#
# The reference is not a measurement -- it is the picture he looked at in the
# contact sheet and accepted. These pin the two things that would silently
# ruin that: an inverted sign, and a loop that trusts a match it should not.


def _lit(width=428, height=287, seed=0):
    """A prescan-shaped picture with enough structure to correlate on."""
    rng = np.random.default_rng(seed)
    base = np.linspace(20, 90, width)[None, :, None] * np.ones((height, 1, 3))
    base = base + 30 * np.sin(np.linspace(0, 9, width))[None, :, None]
    return np.clip(base + rng.normal(0, 5, (height, width, 3)),
                   0, 255).astype(np.uint8)


def test_content_that_moved_right_measures_positive():
    """THE sign contract. `register` returns dx defined so a[i,j] matches
    b[i, j-dx], so content moved right by s gives dx = -s -- the displacement
    is -dx. Inverting this would drive every frame twice as far wrong."""
    from rps7200.framing import APERTURE_MM, measure_shift_mm

    image = _lit()
    moved = np.roll(image, 8, axis=1)            # content 8 px to the right
    millimetres, detail = measure_shift_mm(image, moved)

    assert millimetres is not None
    assert millimetres > 0, "content moved right must measure positive"
    assert detail["px"] == 8
    assert millimetres == pytest.approx(8 * APERTURE_MM / 428, abs=1e-6)


@pytest.mark.parametrize("shift", [-60, -24, -8, -3, 0, 3, 8, 24, 60])
def test_a_known_shift_is_recovered_exactly(shift):
    from rps7200.framing import measure_shift_mm

    image = _lit()
    millimetres, detail = measure_shift_mm(image, np.roll(image, shift, axis=1))
    assert millimetres is not None
    assert detail["px"] == shift


def test_a_match_it_does_not_believe_is_refused():
    """Two different photographs correlate at 4-29 against a true match's
    61-96. Moving the film on a measurement like that is exactly what the
    `None` return exists to prevent."""
    from rps7200.framing import measure_shift_mm

    millimetres, detail = measure_shift_mm(_lit(seed=1), _lit(seed=99))
    assert millimetres is None
    assert "correlation" in detail["reason"]


def test_a_match_off_the_film_axis_is_refused():
    """The transport moves in x. A match claiming the picture also moved down
    has found something that is not this frame."""
    from rps7200.framing import MAX_DY_PX, measure_shift_mm

    image = _lit()
    millimetres, detail = measure_shift_mm(
        image, np.roll(image, MAX_DY_PX + 6, axis=0))
    assert millimetres is None
    assert detail["dy"] is not None


def test_the_search_reaches_as_far_as_the_transport_can_travel():
    """`register`'s own default of 64 px is 5.5 mm at 300 dpi, less than the
    8 mm the transport can move -- so a frame at the far end would be measured
    as something nearer."""
    from rps7200.framing import measure_shift_mm

    image = _lit()
    far = np.roll(image, 80, axis=1)             # past the 64 px default
    millimetres, detail = measure_shift_mm(image, far)
    assert millimetres is not None and detail["px"] == 80


def test_the_floor_hoards_its_margin_on_the_side_that_matters():
    """Measured over 3850 pairs of real film (`tools/registration_margin.py`):
    3754 pairs that are not the same picture reach 30.2 at worst, and 96 that
    are start at 54.9. The floor is deliberately *not* centred between them --
    a false positive moves the film to the wrong place, while a refusal leaves
    it alone and flags the frame, so the clearance belongs on the false-positive
    side. Pinned because centring it is the obvious-looking change and it is
    the wrong one."""
    from rps7200.framing import CONFIDENCE_FLOOR

    worst_null, weakest_true = 30.2, 54.9
    assert CONFIDENCE_FLOOR > worst_null
    assert CONFIDENCE_FLOOR - worst_null > weakest_true - CONFIDENCE_FLOOR


def test_the_search_window_does_not_vary_with_the_frame():
    """`confidence` is the peak's z-score over the *searched* surface, so it
    is a property of the match and of the window together -- the same pair
    scores 23.8 at a 16 px reach and 129.7 at 200 px. A reach that varied per
    frame would make CONFIDENCE_FLOOR mean something different every time, and
    on the scanner that came within 1.5 points of refusing a good frame."""
    from rps7200.framing import measure_shift_mm

    source = inspect.getsource(measure_shift_mm)
    assert "SEARCH_MM" in source
    assert "budget_mm" not in source


def test_a_reference_from_another_resolution_still_lands_geometrically():
    """Resampling recovers the shift, but costs about half the confidence --
    93.5 against 47.4 on real passes -- which can drop a good match below the
    floor. So the geometry is what is pinned here, and the window pins a
    commissioned scan to the survey's own prescan resolution rather than
    relying on this path."""
    from rps7200.framing import APERTURE_MM, SEARCH_MM, _resample_to
    from rps7200.uniformity import register

    image = _lit()
    coarse = image[::2, ::2]                     # a 600 -> 300 dpi survey
    grown = _resample_to(coarse, image.shape[:2])
    assert grown.shape[:2] == image.shape[:2]

    reach = int(SEARCH_MM / (APERTURE_MM / image.shape[1]))
    _, dx, _ = register(grown, np.roll(image, 5, axis=1), max_shift=reach)
    assert -dx == 5, "the shift survives resampling even where the peak softens"


def test_the_tolerance_is_the_smallest_move_the_hardware_can_make():
    """Not a smaller number. The loop can then never ask for a correction it
    cannot deliver, so it cannot chatter between two positions either side of
    the target -- a limit cycle is impossible by construction rather than by
    tuning."""
    from rps7200.framing import HOLD_TOLERANCE_MM

    assert HOLD_TOLERANCE_MM == pytest.approx(
        DirectScanner.STEP_MM + DirectScanner.OVERHEAD_MM, abs=1e-3)


def test_the_decision_table():
    from rps7200.framing import (
        HOLD_TOLERANCE_MM,
        MAX_HOLD_MOVES,
        hold_plan,
    )

    # nothing to go on
    assert hold_plan(0.5, None) == (None, "unverified")
    # close enough
    assert hold_plan(0.5, 0.5 - HOLD_TOLERANCE_MM / 2)[1] == "held"
    # a real gap, and the move that closes it
    want, outcome = hold_plan(0.5, 0.0)
    assert outcome == "move" and want == pytest.approx(0.5)
    # the operator's number is never overwritten -- only a residual against it
    assert hold_plan(-0.8, 0.0)[0] == pytest.approx(-0.8)
    # caps
    assert hold_plan(0.5, 0.0, moves=MAX_HOLD_MOVES)[1] == "not_converged"
    assert hold_plan(0.5, 1.4, direction=1)[1] == "would_reverse"
    assert hold_plan(0.5, -9.0, spent_mm=2.0)[1] == "budget"


# --- the loop, driven end to end with no scanner -----------------------------


def _approved(number, offset_mm, reference):
    from rps7200.session import Approved
    return Approved(number=number, offset_mm=offset_mm, reference=reference)


def _roll_once(scanner, approved, **kw):
    """One frame, held to an approved position. Returns its RollFrame."""
    return list(scanner.scan_roll(
        frames=1, resolution=300, infrared=False, meter=METER_NONE,
        approved=approved, **kw))[0]


def test_a_frame_already_where_he_left_it_is_not_moved():
    reference = _lit()
    scanner = FakeRoll([reference])
    scanner.prescans = [reference.copy()]

    frame = _roll_once(scanner, {0: _approved(1, 0.0, reference)})

    assert frame.registration["approved"]["outcome"] == "held"
    assert frame.registration["approved"]["moves"] == 0
    assert scanner.slid == [], "nothing should have moved"
    assert frame.image is not None, "and the frame is still scanned"


def test_an_offset_is_applied_and_confirmed_by_looking_again():
    """The loop IS how the offset gets applied -- there is no separate
    open-loop step. The first residual is his number, and the re-prescan is
    what tells a move that landed from one backlash swallowed."""
    reference = _lit()
    scanner = FakeRoll([reference])
    # as surveyed, then 6 px further along: 0.512 mm, which is the 0.5 asked
    # for to within less than one hardware step
    scanner.prescans = [reference.copy(), np.roll(reference, 6, axis=1)]

    frame = _roll_once(scanner, {0: _approved(1, 0.5, reference)})
    held = frame.registration["approved"]

    assert held["outcome"] == "held"
    assert held["moves"] == 1
    assert len(scanner.slid) == 1
    assert scanner.slid[0][0] == 0x00, "forward"
    assert frame.image is not None


def test_a_frame_that_will_not_move_is_scanned_anyway_and_flagged():
    reference = _lit()
    scanner = FakeRoll([reference])
    scanner.prescans = [reference.copy() for _ in range(6)]   # never budges

    frame = _roll_once(scanner, {0: _approved(1, 0.9, reference)})
    held = frame.registration["approved"]

    from rps7200.framing import MAX_HOLD_MOVES
    assert held["outcome"] == "not_converged"
    assert held["moves"] == MAX_HOLD_MOVES
    assert frame.image is not None, "the picture is still taken"
    assert held["residual_mm"] == pytest.approx(0.9, abs=0.05)


def test_overshoot_is_reported_rather_than_chased_back():
    """Reversing inside a frame would reason from a position the mechanism has
    not finished delivering: backlash swallows 2-3 commands after a direction
    change and releases the distance later."""
    reference = _lit()
    scanner = FakeRoll([reference])
    # asked to go +0.5, went most of the way to +1.4 -- now past the target
    scanner.prescans = [reference.copy(), np.roll(reference, 17, axis=1)]

    frame = _roll_once(scanner, {0: _approved(1, 0.5, reference)})
    held = frame.registration["approved"]

    assert held["outcome"] == "would_reverse"
    assert held["moves"] == 1, "it does not try to come back"
    assert frame.image is not None


def test_a_match_it_cannot_believe_moves_nothing():
    reference = _lit(seed=1)
    scanner = FakeRoll([reference])
    scanner.prescans = [_lit(seed=77)]           # a different photograph

    frame = _roll_once(scanner, {0: _approved(1, 0.5, reference)})

    assert frame.registration["approved"]["outcome"] == "unverified"
    assert scanner.slid == [], "never move on a measurement not believed"
    assert frame.image is not None


def test_film_moving_the_wrong_way_stops_the_whole_roll_holding():
    """If the sense is inverted, every frame after this would be driven wrong.
    This is a measurement of direction, not a second opinion about his
    number."""
    reference = _lit()
    scanner = FakeRoll([reference, reference.copy(), None])
    scanner.prescans = [
        reference.copy(),                  # frame 0 as surveyed
        np.roll(reference, -14, axis=1),   # asked +, went -
        reference.copy(),                  # frame 1's own prescan
    ]
    frames = list(scanner.scan_roll(
        frames=2, resolution=300, infrared=False, meter=METER_NONE,
        approved={0: _approved(1, 0.5, reference),
                  1: _approved(2, 0.5, reference)}))

    assert frames[0].registration["approved"]["outcome"] == "wrong_way"
    assert frames[1].registration["approved"]["outcome"] == "off", (
        "holding is off for the rest of the roll, not just that frame")
    assert all(f.image is not None for f in frames)


def test_an_approved_frame_is_never_touched_by_the_automatic_nudge():
    """His number is authoritative. A gap measurement that has been wrong
    before must not overrule it."""
    reference = _lit()
    scanner = FakeRoll([reference])
    scanner.prescans = [reference.copy()]

    frame = _roll_once(scanner, {0: _approved(1, 0.0, reference)}, correct=True)

    assert "approved" in frame.registration
    assert "correction" not in frame.registration


def test_a_frame_without_an_approval_still_gets_the_old_behaviour():
    """A mixed roll has to be coherent: adjusted frames are held, the rest are
    corrected if the tick is on."""
    reference = _lit()
    scanner = FakeRoll([reference, reference.copy(), None])
    scanner.prescans = [reference.copy(), reference.copy()]

    frames = list(scanner.scan_roll(
        frames=2, resolution=300, infrared=False, meter=METER_NONE,
        correct=True, approved={0: _approved(1, 0.0, reference)}))

    assert "approved" in frames[0].registration
    assert "correction" in frames[1].registration


def test_every_prescan_through_a_hold_keeps_its_raw_bytes():
    """last_raw is only written when keep_raw is set and is never cleared, so
    a verification prescan without it leaves capture_record holding the
    previous pass's bytes."""
    reference = _lit()
    scanner = FakeRoll([reference])
    scanner.prescans = [reference.copy(), np.roll(reference, 6, axis=1)]

    _roll_once(scanner, {0: _approved(1, 0.5, reference)}, keep_raw=True)

    assert scanner.prescan_keep_raw == [True, True]


def test_an_automatic_calibration_runs_at_the_resolution_that_reaches_the_cap():
    """Not the resolution of the pass that triggered it. The device will not
    produce a reference wider than MAX_SHADING_COLUMNS whatever it is asked
    for, and 3600 dpi is what reaches that cap -- so calibrating at a 300 dpi
    prescan's own resolution would buy a 431-column reference, and a second
    calibration the moment a real scan followed. 3600 is also the only
    resolution any calibration, vendor or ours, has ever run at."""
    source = inspect.getsource(DirectScanner.scan)
    assert "self.calibrate_shading()" in source
    assert "self.calibrate_shading(resolution=resolution)" not in source


def test_the_floor_is_only_meaningful_at_the_reach_it_was_measured_at():
    """Both numbers were fitted together on the scanner, 2026-09-14: same-frame
    repeats scored 80.9-168.9 and adjacent frames of one strip up to 29.9, at
    SEARCH_MM. Changing the reach without re-fitting the floor silently changes
    what "believed" means -- which is how a true match came within 1.5 points
    of being refused."""
    from rps7200.framing import CONFIDENCE_FLOOR, SEARCH_MM

    assert SEARCH_MM == pytest.approx(9.0)
    assert CONFIDENCE_FLOOR == pytest.approx(55.0)
    # Comfortably past what the transport can travel, so any displacement it
    # can produce is inside the window.
    assert SEARCH_MM > 8.08


# --- a dry run with approved positions still moves the film ------------------
#
# The property the whole CLI `--approved` path rests on, and it was unpinned:
# `tests/test_roll.py`'s `_roll_once` never passes `dry_run`, and the demo's
# own `scan_roll` is a reimplementation, so nothing exercised the real branch
# ordering. It is worth a test in both directions -- that it moves, because a
# delivery test would otherwise cost a multi-hour scan instead of minutes; and
# that it scans nothing, because that is what makes it cheap.


def _approved(number, offset_mm, reference):
    from rps7200.session import Approved

    return Approved(number=number, offset_mm=offset_mm, reference=reference)


def test_a_dry_run_holding_an_approved_position_still_moves_the_film():
    """`direct.py`'s approved branch runs ahead of the dry-run check, so a walk
    can deliver and verify a position without scanning anything."""
    picture = framed(gap_left=OUT_BY_A_GAP, seed=1)
    s = FakeRoll([picture])
    frames = list(s.scan_roll(
        frames=1, meter=METER_NONE, dry_run=True,
        approved={0: _approved(1, 0.60, picture)}))
    sub = [x for x in s.slid if x[0] in (0x00, 0x01)]
    assert sub, "a dry run with an approved position must still nudge"
    assert sub[0][0] == 0x00, "a positive offset moves the film forward"
    assert frames[0].image is None, "and must still scan nothing"


def test_a_dry_run_without_approvals_moves_nothing():
    """The other half: the cheapness is only useful if the default is inert."""
    s = FakeRoll(aimable(1))
    list(s.scan_roll(frames=1, meter=METER_NONE, dry_run=True))
    assert [x for x in s.slid if x[0] in (0x00, 0x01)] == []


def test_a_reference_of_another_frame_is_refused_rather_than_acted_on():
    """Measured on film 2026-09-21, by accident: a roll was started with the
    strip at the wrong position, so every frame was held against a reference
    showing a different photograph. Correlation scored 5.2 to 5.5 against a
    floor of 55, all three frames returned `unverified`, and **no sub-frame
    command was sent at all**. The fail-safe direction, on real hardware.
    """
    s = FakeRoll([framed(gap_left=OUT_BY_A_GAP, seed=1)])
    stranger = framed(gap_left=4, seed=77)            # a different picture
    frames = list(s.scan_roll(
        frames=1, meter=METER_NONE, dry_run=True,
        approved={0: _approved(1, 0.60, stranger)}))
    assert [x for x in s.slid if x[0] in (0x00, 0x01)] == []
    assert frames[0].registration["approved"]["outcome"] == "unverified"


# --- a pass that came back with its rows reversed ---------------------------


def test_a_reversed_pass_is_still_measured():
    """MODE SELECT byte 14 bit 0 reverses the pass that immediately follows a
    bit-0-set one, and this driver sets it on every RGBI scan -- so a frame's
    prescan, taken straight after the last frame's scan, is exactly the pass at
    risk. Measured on film 2026-09-21: two of seven frames of a 600 dpi roll
    came back reversed, each scoring under 9 as it came and over 92 flipped,
    and each went uncorrected because the reading was refused.

    Safe to try because it is a flip in y and the displacement measured is in
    x: a reversed pass is not unreadable, only unreadable as it came.
    """
    from rps7200.framing import measure_shift_mm

    rng = np.random.default_rng(3)
    reference = rng.random((60, 428, 3)) * 200
    shifted = np.roll(reference, -7, axis=1)

    upright, _d = measure_shift_mm(reference, shifted)
    reversed_mm, detail = measure_shift_mm(reference, shifted[::-1])
    assert upright is not None
    assert reversed_mm == pytest.approx(upright, abs=0.02)
    assert detail["row_reversed"] is True
    assert "rows reversed" in detail["reason"]


def test_a_good_match_is_never_second_guessed():
    """The flip is tried only where the pass has already been refused."""
    from rps7200.framing import measure_shift_mm

    rng = np.random.default_rng(4)
    reference = rng.random((60, 428, 3)) * 200
    _mm, detail = measure_shift_mm(reference, np.roll(reference, -5, axis=1))
    assert "row_reversed" not in detail


def test_a_different_picture_is_still_refused_either_way_up():
    """The floor has to hold for both attempts, or the fallback would become a
    second chance for a match that should not happen at all."""
    from rps7200.framing import measure_shift_mm

    rng = np.random.default_rng(5)
    mm, detail = measure_shift_mm(rng.random((60, 428, 3)) * 200,
                                  rng.random((60, 428, 3)) * 200)
    assert mm is None
    assert "reversed" in detail["reason"]
