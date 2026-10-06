"""Holding a roll on a transport that remembers where it left the film.

`FakeRoll` (test_roll) scripts what each look returns. That proves the loop's
decisions, but not the thing that went wrong on 2026-10-03..05: a sub-frame
move stays on the strip through every whole-frame advance and the walk's
rewind, so each frame *arrives* displaced by what earlier moves left behind,
and the hold has to take the frame from wherever that is. Nineteen of 156
delivered frames sat more than 2.84 units off their sheet position, nine of
them 28-50 units, because the old budget refused to move a frame that arrived
further off than its own target allowed.

`TransportSim` is that transport. The film has a place, in units along the
strip; every look is the aperture's 428 columns of one wide synthetic strip at
that place; a sub-frame SLIDE moves it by the law (`param + 1.84` units, times
a delivery ratio), less any slack after a change of direction; a whole-frame
advance moves the frame counter and leaves the sub-frame offset exactly where
it was. The rolls below are the stored ones that failed, rebuilt in shape.
"""

import numpy as np
import pytest

from rps7200 import framing, protocol
from rps7200.direct import FULL_FRAME, METER_NONE, SLIDE_INIT, DirectScanner
from rps7200.session import Approved

from conftest import settings

#: Rows of a 300 dpi prescan, as the device returns them.
ROWS = 287


def _detail(width: int, seed: int) -> np.ndarray:
    """Picture detail with nothing that repeats: noise, blurred to its
    scale, as a float of unit spread."""
    rng = np.random.default_rng(seed)
    a = rng.normal(0.0, 1.0, (ROWS, width))
    for axis in (0, 1):
        # A box blur of 7, by cumulative sums: numpy only.
        c = np.cumsum(np.pad(a, [(4, 3) if i == axis else (0, 0)
                                 for i in range(2)]), axis=axis)
        hi = c.take(np.arange(7, c.shape[axis]), axis=axis)
        lo = c.take(np.arange(0, c.shape[axis] - 7), axis=axis)
        a = (hi - lo) / 7.0
    return (a - a.mean()) / a.std()


def _texture(width: int, seed: int) -> np.ndarray:
    """A strip with something to correlate on everywhere and nothing that
    repeats, at full contrast."""
    a = _detail(width, seed)
    a = (a - a.min()) / (a.max() - a.min())
    grey = (30 + 190 * a).astype(np.uint8)
    return np.repeat(grey[..., None], 3, axis=2)


def _grey(picture: np.ndarray) -> np.ndarray:
    return np.repeat(np.clip(picture, 0, 255).astype(np.uint8)[..., None], 3,
                     axis=2)


class TransportSim(DirectScanner):
    """A film transport with a memory.

    ``ratio`` scales every sub-frame move (1.013-1.059 measured); ``backlash``
    is the slack, in units, a change of direction takes up before the film
    follows (`protocol.BACKLASH_UNITS`, 3.9); ``jumps`` moves the film on the
    advance *into* a frame, as an advance landing a perforation off did on
    slide rolls (-44.9 units); ``inverted`` sends the film the other way.

    ``grain`` is ``(contrast, noise)``: low-contrast detail, and every pass
    -- the walk's references as much as the hold's looks -- with noise of its
    own, as a 300 dpi prescan of black and white film has.
    """

    COLUMNS = 428

    def __init__(self, frames, *, ratio=1.0, backlash=0.0, jumps=None,
                 inverted=False, seed=5, grain=None):
        self.verbose = False
        self.ratio, self.backlash = ratio, backlash
        self.jumps = dict(jumps or {})
        self.inverted = inverted
        self.pitch_px = framing.PITCH_UNITS * framing.COLUMNS_PER_UNIT
        self.margin = 300
        width = int(self.margin * 2 + self.pitch_px * (frames + 1)) + self.COLUMNS
        self.grain = grain
        self.noise = np.random.default_rng(seed + 1)
        self.strip = (_texture(width, seed) if grain is None
                      else 120.0 + grain[0] * _detail(width, seed))
        self.frames = frames
        self.at = 0
        #: Where the film sits along the strip, in units, beyond whole frames.
        #: Positive is the picture further along +x, as `measure_shift_mm`
        #: reads it.
        self.film = 0.0
        self.loaded = 1                  # a roll enters every frame forward
        self.slack = 0.0
        self.slid = []
        #: Where the film was, in units, when each frame was scanned.
        self.scanned_at = []
        self.reference = self._settings = settings(8000, 20000, 50000, 8000)

    # -- the picture
    def window(self, frame: int, film: float) -> np.ndarray:
        """What the aperture shows of `frame` with the film at `film`."""
        origin = self.margin + frame * self.pitch_px \
            - film * framing.COLUMNS_PER_UNIT
        x0 = int(round(origin))
        if self.grain is None:
            return self.strip[:, x0:x0 + self.COLUMNS].copy()
        noise = self.noise.normal(0.0, self.grain[1], (ROWS, self.COLUMNS))
        return _grey(self.strip[:, x0:x0 + self.COLUMNS] + noise)

    # -- the device
    def get_gain_offset(self):
        return self.reference

    def set_gain_offset(self, s, infrared=False):
        self._settings = s

    def position(self):
        return self.at

    def advance(self, steps=1, timeout=30.0, poll=0.5):
        if self.at + 1 >= self.frames:
            return None
        self.at += 1
        self.film += self.jumps.get(self.at, 0.0)
        self.loaded, self.slack = 1, 0.0
        return self.at

    def slide(self, action=SLIDE_INIT, param=0x16, value=0):
        self.slid.append((action, param, value))
        if action not in (0x00, 0x01):
            return
        way = 1 if action == 0x00 else -1
        if way != self.loaded:
            self.slack = self.backlash
            self.loaded = way
        travel = protocol.units_for_param(param) * self.ratio
        taken = min(travel, self.slack)
        self.slack -= taken
        self.film += (travel - taken) * way * (-1 if self.inverted else 1)

    def prescan(self, resolution=300, frame=None, keep_raw=False,
                shading=True, film="negative"):
        return self.window(self.at, self.film), None

    def auto_exposure(self, target=0.7, infrared=False, film="negative", **kw):
        return [1.0] * 3

    def scan(self, resolution=300, infrared=True, exposure_scale=1.0, **kw):
        self.scanned_at.append(self.film)
        n = 4 if infrared else 3
        return (np.zeros((4, 4, n), np.uint16),
                {"resolution_dpi": resolution,
                 "frame": list(kw.get("frame") or FULL_FRAME)})


def _hold_roll(sim, walk, targets, *, start=None):
    """Walk references taken with the film at `walk[k]` for frame k, then a
    roll from frame 1 held to `targets`. The film starts where the walk left
    it -- its last offset, the carry-over -- unless `start` says otherwise.
    Returns the frames and each one's error at scan time, in units."""
    references = [sim.window(k, walk[k]) for k in range(len(walk))]
    sim.film = walk[-1] if start is None else start
    approved = {k: Approved(number=k + 1, offset_mm=t * protocol.MM_PER_UNIT,
                            reference=references[k])
                for k, t in enumerate(targets)}
    frames = list(sim.scan_roll(frames=len(targets), resolution=300,
                                infrared=False, meter=METER_NONE,
                                approved=approved))
    errors = [sim.scanned_at[k] - (walk[k] + targets[k])
              for k in range(len(frames))]
    return frames, errors


def _outcomes(frames):
    return [f.registration["approved"]["outcome"] for f in frames]


def test_the_simulated_strip_measures_like_a_real_one():
    """Before trusting a roll on it: a look a known distance along reads that
    distance, well above the floor -- the simulator is not the thing failing."""
    sim = TransportSim(3)
    for units in (-40.0, -3.0, 0.0, 7.0, 29.0):
        mm, detail = framing.measure_shift_mm(sim.window(1, 0.0),
                                              sim.window(1, units))
        assert mm is not None, detail
        assert mm / protocol.MM_PER_UNIT == pytest.approx(units, abs=0.9)
        assert detail["confidence"] > 2 * framing.CONFIDENCE_FLOOR


def test_the_2026_10_05_195753_roll_now_reaches_every_position():
    """The walk aimed frame 6 by +24.8 units, so the strip came back to frame
    1 carrying it; frame 1 was set to +30.8 and held there, and frames 2-5,
    set to 0, then arrived about +29 off. The old budget refused all three,
    holding switched off, and the rest of the roll was scanned where it
    landed. Every frame is placed now."""
    walk = [0.0] * 5 + [24.84]
    targets = [30.84, 0.0, 0.0, 0.0, 0.0, 0.0]
    frames, errors = _hold_roll(TransportSim(6, ratio=1.013, backlash=3.9),
                                walk, targets)
    assert _outcomes(frames) == ["held"] * 6, _outcomes(frames)
    assert max(abs(e) for e in errors) < framing.SMALLEST_MOVE + 0.5, errors


def test_the_2026_10_05_204523_roll_backward_after_forward_advances():
    """The walk aimed frames 4 and 5 back, by 28.2 and 3.2 units, so frame 1
    arrived 31.4 units back with every frame set to 0; a backward correction
    on a transport the advance left loaded forward pays the backlash first."""
    walk = [0.0, 0.0, 0.0, -28.2, -31.4, -31.4]
    targets = [0.0] * 6
    frames, errors = _hold_roll(TransportSim(6, ratio=1.013, backlash=3.9),
                                walk, targets)
    outcomes = _outcomes(frames)
    assert not {"budget", "off", "unverified"} & set(outcomes), outcomes
    held = [e for e, o in zip(errors, outcomes) if o == "held"]
    assert held and max(abs(e) for e in held) < framing.SMALLEST_MOVE + 0.5
    # Whatever did not quite arrive is reported, and is close.
    assert max(abs(e) for e in errors) < 2 * framing.SMALLEST_MOVE, errors


def test_a_frame_an_advance_left_a_perforation_off_is_corrected():
    """2026-10-03-221354: after frame 1's hold the advance into frame 2 came
    up about 45 units short -- one perforation -- and three frames were scanned
    there before holding switched off."""
    sim = TransportSim(5, jumps={1: -44.9})
    frames, errors = _hold_roll(sim, [0.0] * 5, [-5.84, -5.84, -4.84,
                                                 -7.84, -5.84])
    assert _outcomes(frames) == ["held"] * 5, _outcomes(frames)
    assert max(abs(e) for e in errors) < framing.SMALLEST_MOVE + 0.5, errors
    assert frames[1].registration["approved"]["arrived_units"] == \
        pytest.approx(-44.9 - 5.84, abs=1.5), "where the jump left it"


def test_the_hardware_tests_positions_are_all_reached():
    """The positions the hardware test sets on a walked strip: arrivals of
    +-30-40 units the old budget refused, a backward move straight after a
    forward one, and +84.8 units -- past the old 84.7-unit search reach, inside
    the new one -- on a transport that delivers 5% long with the backlash
    measured, as the worst measured ratio did."""
    targets = [29.84, 0.0, -39.84, 0.0, 84.84, 0.0]
    frames, errors = _hold_roll(TransportSim(6, ratio=1.05, backlash=3.9),
                                [0.0] * 6, targets)
    assert _outcomes(frames) == ["held"] * 6, _outcomes(frames)
    assert max(abs(e) for e in errors) < framing.SMALLEST_MOVE + 0.5, errors


def test_the_hardware_rolls_positions_are_reached_on_grainy_film():
    """2026-10-06's 600 dpi roll, black and white: frames 1-3 held; 4-6 were
    measured right and refused -- 50.3, 41.4 and 27.5 against a floor of 55 --
    and frame 4 was scanned 27 units, frame 6 84 units from where they were
    set. Grain of its own on every pass dilutes every match, and a frame far
    out shows film its reference never saw, which counted against a match
    scored on the whole pictures. Scored on the part both show, smoothed, they
    are all placed -- and the frame after the far one is exactly the look the
    whole pictures would have refused."""
    targets = [29.84, 0.0, -39.84, 0.0, 84.84, 0.0]
    frames, errors = _hold_roll(
        TransportSim(6, ratio=1.05, backlash=3.9, grain=(15.0, 10.0)),
        [0.0] * 6, targets)
    assert _outcomes(frames) == ["held"] * 6, _outcomes(frames)
    assert max(abs(e) for e in errors) < framing.SMALLEST_MOVE + 0.5, errors
    arrival = frames[5].registration["approved"]["history"][0]
    assert abs(arrival["px"]) > 95, "the frame after the far one arrives far"
    assert arrival["confidence_whole"] < framing.CONFIDENCE_FLOOR, arrival
    assert arrival["confidence"] >= framing.CONFIDENCE_FLOOR


def test_a_match_three_rows_off_the_axis_is_believed():
    """A walk's first prescan sits up to 3.1 rows off the frames after it,
    and a pass read the other way about 2: six good slide matches at dy 3 were
    refused by a 2-row gate, two of them with an offset never applied."""
    sim = TransportSim(2)
    reference = sim.window(1, 0.0)
    look = np.roll(sim.window(1, 10.0), 3, axis=0)
    mm, detail = framing.measure_shift_mm(reference, look)
    assert mm is not None, detail
    assert abs(detail["dy"]) == 3
    assert mm / protocol.MM_PER_UNIT == pytest.approx(10.0, abs=0.9)


def test_a_slipping_transport_switches_holding_off_after_three_frames():
    """Moves sent and the film not following, frame after frame: the setup,
    not the frames. The rest of the roll is scanned unplaced, and says so."""
    frames, _ = _hold_roll(TransportSim(5, ratio=0.0), [0.0] * 5,
                           [9.84] * 5)
    assert _outcomes(frames) == ["no_effect"] * 3 + ["off"] * 2


def test_a_transport_running_backwards_stops_holding_at_once():
    frames, _ = _hold_roll(TransportSim(3, inverted=True), [0.0] * 3,
                           [9.84, 9.84, 9.84])
    assert _outcomes(frames) == ["wrong_way", "off", "off"]
    assert frames[0].registration["approved"]["roll_abort"]
