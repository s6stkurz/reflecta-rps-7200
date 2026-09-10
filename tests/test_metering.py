"""Metering tests: what the exposure loop does without the scanner attached.

The load-bearing piece is the film type. A colour negative is metered one
channel at a time, which takes the orange mask off before the ADC; every other
film keeps its balance, because there the cast is the picture and pulling the
channels apart removes it.
"""

import numpy as np
import pytest

from conftest import settings
from rps7200.bracket import CLIP_START, FULL_SCALE
from rps7200.direct import (
    BLUE_RGBI_HEADROOM,
    BLUE_RGBI_HEADROOM_UNMEASURED,
    EXPOSURE_TARGET,
    OVER_TARGET_TOLERANCE,
    FILM_BW,
    FILM_KODACHROME,
    FILM_NEGATIVE,
    FILM_POSITIVE,
    DirectScanner,
    blue_rgbi_headroom,
    locks_white_balance,
    supports_infrared,
)


class FakeScanner(DirectScanner):
    """A scanner whose passes are simulated from a per-channel transmission.

    Exposure is linear in integration time, as the sensor is, and clipped at
    full scale -- enough to exercise the metering loop end to end.
    """

    def __init__(self, transmission, base=(8000, 20000, 50000, 8000)):
        self.verbose = False
        self._settings = settings(*base)
        self.transmission = transmission
        self.passes = []

    def get_gain_offset(self):
        return self._settings

    def set_gain_offset(self, s, infrared=False):
        self._settings = s

    def scan(self, resolution=300, infrared=False, exposure_scale=1.0, **kw):
        settings = self._settings.scaled(exposure_scale)
        self._settings = settings          # SET GAIN OFFSET persists
        n = 4 if infrared else 3
        self.passes.append(list(settings.exposure[:n]))
        level = [
            min(1.0, settings.exposure[c] / 65535.0 * self.transmission[c])
            for c in range(n)
        ]
        return (np.full((8, 8, n), 0.0) + np.array(level) * 65535).astype(np.uint16), {}


def test_film_with_no_colour_record_is_metered_per_channel():
    """Only a slide and a Kodachrome have a cast that *is* the picture.

    Black and white was grouped with them and should not have been: silver
    halide has no dye layers, so holding its channels together preserves the
    film base and the sensor's response, not a photograph -- and it costs green
    and blue about half a stop each, measured.
    """
    assert locks_white_balance(FILM_NEGATIVE) is False
    assert locks_white_balance(FILM_BW) is False
    for film in (FILM_POSITIVE, FILM_KODACHROME):
        assert locks_white_balance(film) is True


def test_unknown_film_is_refused():
    with pytest.raises(ValueError, match="unknown film type"):
        locks_white_balance("colour-negative")


def test_a_slide_keeps_its_cast():
    """The whole point: a locked meter must not equalise the channels.

    Metered well below full scale so the 16-bit timer does not clamp a channel
    and confuse a clamp for a metering decision; the ceiling has its own test.
    """
    cast = (1.0, 0.6, 0.35)          # a warm slide
    s = FakeScanner(cast, base=(8000, 12000, 16000))
    scales = s.auto_exposure(target=0.4, film=FILM_POSITIVE, rounds=3)

    exposures = s.passes[-1]
    ratios = [e / exposures[0] for e in exposures]
    nominal = [8000 / 8000, 12000 / 8000, 16000 / 8000]
    assert ratios == pytest.approx(nominal, rel=0.02), (
        "a locked meter moved the channels apart, which takes the cast off"
    )
    assert len(set(round(v, 6) for v in scales[:3])) == 1


def test_a_negative_is_pulled_apart():
    """The orange mask must come off before the ADC, not after."""
    mask = (0.9, 0.7, 0.5)           # blue attenuated most, as a mask does
    s = FakeScanner(mask, base=(10000, 10000, 10000, 8000))
    s.auto_exposure(target=0.4, film=FILM_NEGATIVE, rounds=4)

    r, g, b = s.passes[-1][:3]
    assert b > g > r, "a negative was not metered per channel"


def test_locked_metering_never_clips_a_channel():
    s = FakeScanner((0.30, 0.85, 1.0), base=(9000, 9000, 9000))
    s.auto_exposure(target=0.8, film=FILM_BW, rounds=4)
    settings = s.get_gain_offset()
    levels = [
        settings.exposure[c] / 65535.0 * s.transmission[c] for c in range(3)
    ]
    assert max(levels) <= 1.0 + 1e-9
    assert max(levels) == pytest.approx(0.8, abs=0.1)


def test_scales_do_not_compound_across_rounds():
    """SET GAIN OFFSET persists on the device, so each round must restore the base.

    Without that, round three multiplies a base that round two already scaled
    and the pass comes back wildly over-exposed. The returned scale is relative
    to the original exposure, so the device must end up at exactly that.
    """
    base = (6000, 9000, 12000)
    s = FakeScanner((0.9, 0.7, 0.5), base=base + (8000,))
    scales = s.auto_exposure(target=0.4, film=FILM_NEGATIVE, rounds=3)

    final = s.get_gain_offset().exposure[:3]
    expected = [min(65535, round(b * v)) for b, v in zip(base, scales[:3])]
    assert final == pytest.approx(expected, rel=0.01), (
        f"exposure compounded across rounds: {final} vs {expected}"
    )


def test_a_channel_against_the_timer_ceiling_is_reported(capsys):
    """Blue starts near the top of the timer, so a negative can ask for more
    exposure than the hardware has left. That has to be said, not swallowed."""
    s = FakeScanner((1.0, 1.0, 0.05), base=(8000, 20000, 60000, 8000))
    s.verbose = True
    scales = s.auto_exposure(target=0.8, film=FILM_NEGATIVE, rounds=2)
    out = capsys.readouterr().out
    assert "held at the timer ceiling" in out
    assert "could not reach the target" in out
    # and the scale it returns must be one the device can actually apply
    assert 60000 * scales[2] <= 65535


def test_the_scale_cap_is_the_timer_not_a_fixed_number():
    """A fixed 8x cap used to stop blue short of what the hardware allows."""
    s = FakeScanner((1.0, 1.0, 0.02), base=(6506, 6506, 6506, 8000))
    scales = s.auto_exposure(target=0.8, film=FILM_NEGATIVE, rounds=3)
    assert scales[2] > 8.0, "blue was capped below the timer ceiling"
    assert 6506 * scales[2] == pytest.approx(65535, rel=0.01)


# --- metering for an infrared scan, without an infrared probe ----------------

def test_the_probe_is_never_infrared(monkeypatch):
    """An IR pass costs its own ~212 s floor per round; the vendor never does it."""
    s = FakeScanner((0.9, 0.7, 0.5), base=(10000, 10000, 10000, 8000))
    modes = []
    real = s.scan
    def spy(*a, **kw):
        modes.append(kw.get("infrared"))
        return real(*a, **kw)
    monkeypatch.setattr(s, "scan", spy)
    s.auto_exposure(target=0.4, film=FILM_NEGATIVE, infrared=True, rounds=2)
    assert modes and all(m is False for m in modes), (
        f"metering probed in infrared: {modes}"
    )


def test_blue_is_metered_lower_when_an_ir_scan_follows():
    """Blue returns about 5x brighter in RGBI at the same exposure, so a blue
    filling the range in RGB clips in RGBI.

    Pinned at 4.0 rather than the shipped constant because this test is about
    the mechanism -- blue moves, red and green do not -- and should not have to
    change when the measurement does.
    """
    t = (0.9, 0.7, 0.5)
    rgb = FakeScanner(t, base=(9000, 9000, 9000, 8000)).auto_exposure(
        target=0.6, film=FILM_NEGATIVE, infrared=False, rounds=2)
    ir = FakeScanner(t, base=(9000, 9000, 9000, 8000)).auto_exposure(
        target=0.6, film=FILM_NEGATIVE, infrared=True, rounds=2,
        infrared_blue_headroom=4.0)
    assert ir[0] == pytest.approx(rgb[0], rel=0.02), "red should not change"
    assert ir[1] == pytest.approx(rgb[1], rel=0.02), "green should not change"

    # Compare the level blue actually reaches, not the scale: in RGB the scale
    # runs into the 16-bit timer ceiling, so the scales are not proportional
    # even though the aim is.
    def level(scale):
        return min(1.0, 9000 * scale / 65535.0 * t[2])

    assert level(ir[2]) == pytest.approx(0.6 / 4.0, abs=0.03), (
        f"blue aimed at {level(ir[2]):.3f}, wanted {0.6/4:.3f}"
    )
    assert level(ir[2]) < level(rgb[2]) / 2, (
        "blue was not backed off for the infrared scan"
    )


def test_headroom_of_one_leaves_blue_alone():
    t = (0.9, 0.7, 0.5)
    a = FakeScanner(t, base=(9000, 9000, 9000, 8000)).auto_exposure(
        target=0.6, film=FILM_NEGATIVE, infrared=True, rounds=2,
        infrared_blue_headroom=1.0)
    b = FakeScanner(t, base=(9000, 9000, 9000, 8000)).auto_exposure(
        target=0.6, film=FILM_NEGATIVE, infrared=False, rounds=2)
    assert a == pytest.approx(b, rel=0.02)


def test_two_rounds_by_default():
    """What the vendor takes: at most two prescans, then the scan."""
    s = FakeScanner((0.9, 0.7, 0.5), base=(10000, 10000, 10000, 8000))
    s.auto_exposure(target=0.4, film=FILM_NEGATIVE)
    assert len(s.passes) <= 2, f"took {len(s.passes)} prescans"


def test_a_zero_exposure_does_not_kill_metering():
    """The scanner reported exposure 0 just after re-enumerating, and metering
    died -- not in the arithmetic, which already guarded the division, but in the
    log line that exists to explain the guard.

    A channel has to be *limited* for that message to be reached, which is why a
    zero exposure is the case that triggers it: the fallback ceiling is 8x, and
    anything wanting more than that is held.
    """
    s = FakeScanner([0.5, 0.5, 0.5], base=(0, 0, 0, 0))
    scales = s.auto_exposure(target=0.7, infrared=False)
    assert len(scales) == 3
    assert all(v <= 8.0 for v in scales), scales


def test_scaling_by_one_still_returns_a_separate_object():
    """Returning self aliases the caller's settings to the device's."""
    base = settings(8000, 20000, 50000, 8000)
    same = base.scaled(1.0)
    assert same is not base
    assert same.exposure == base.exposure
    same.exposure[0] = 1
    assert base.exposure[0] == 8000, "editing the copy moved the original"


def test_scaling_by_one_does_not_share_the_gain_and_offset_lists():
    base = settings(8000, 20000, 50000, 8000)
    same = base.scaled(1.0)
    same.gain[0] = 99
    assert base.gain[0] != 99


@pytest.mark.parametrize(
    "scale, unity",
    [
        (1.0, True),
        (1, True),
        (2.0, False),
        ([1.0, 1.0, 1.0], True),
        ([1.0, 1.0, 1.0, 1.0], True),
        ([1.0, 2.0, 1.0], False),
        ([0.5, 0.5, 0.5], False),
    ],
)
def test_a_per_channel_scale_of_ones_asks_for_no_change(scale, unity):
    """`[1.0, 1.0, 1.0] != 1.0` is always True, so every metered scan reported
    itself as rescaled."""
    from rps7200.direct import _is_unity

    assert _is_unity(scale) is unity


# -- blue's RGBI headroom, and the evidence for the number ------------------

#: What blue actually does, measured from the one matched pair in the library:
#: 20260909T103542Z (RGB) and 20260909T104022Z_ir (RGBI), same frame 4.7 min
#: apart. See BLUE_RGBI_HEADROOM.
MEASURED_BLUE_RATIO = 4.98


def _blue_level_after_metering(headroom, target=EXPOSURE_TARGET):
    """Where blue lands in the RGBI scan when metering used ``headroom``.

    The probe is RGB, so it aims blue at ``target / headroom``; the scan that
    follows is RGBI, where blue is MEASURED_BLUE_RATIO brighter at the same
    exposure. Base exposures are the device's own reference, and blue's
    transmission is chosen so it can reach its aim without the timer clamping.
    """
    s = FakeScanner((0.9, 0.7, 0.5), base=(9604, 6506, 6506, 7745))
    scales = s.auto_exposure(
        target=target, film=FILM_NEGATIVE, infrared=True,
        infrared_blue_headroom=headroom, rounds=2,
    )
    aimed = min(1.0, 6506 * scales[2] / 65535.0 * 0.5)
    return aimed * MEASURED_BLUE_RATIO


def test_the_shipped_blue_headroom_keeps_blue_out_of_the_clipping_knee():
    """The reason the constant moved from 4.0 to 5.2.

    A CCD goes non-linear before it saturates, which is why bracket.py stops
    trusting a sample at CLIP_START (0.80 of full scale). Metering must land
    blue below that in the scan it is metering *for*, not merely in the probe.
    """
    landed = _blue_level_after_metering(BLUE_RGBI_HEADROOM)
    assert landed < CLIP_START / FULL_SCALE, (
        f"blue lands at {landed:.0%} of full scale in the RGBI scan, past the "
        f"{CLIP_START / FULL_SCALE:.0%} knee where a sample stops being trusted"
    )


def test_the_old_blue_headroom_overshot_the_knee():
    """The defect this replaced, kept as a test so it cannot come back quietly.

    4.0 against a true ratio of ~5 put blue at ~87% of full scale where
    metering aimed for 70% -- and measured on the delivered files it was worse
    still, 88-96%, because the probe itself lands a little high.
    """
    landed = _blue_level_after_metering(4.0)
    assert landed > CLIP_START / FULL_SCALE, (
        "4.0 is being asserted to overshoot, but it did not -- if the measured "
        "ratio has been revised, revise this test with it"
    )
    assert landed > _blue_level_after_metering(BLUE_RGBI_HEADROOM)


def test_blue_headroom_defaults_to_the_measurement():
    """The default is the measured value, not a caller's guess."""
    s = FakeScanner((0.9, 0.7, 0.5), base=(9604, 6506, 6506, 7745))
    explicit = FakeScanner((0.9, 0.7, 0.5), base=(9604, 6506, 6506, 7745))
    assert s.auto_exposure(
        target=0.7, film=FILM_NEGATIVE, infrared=True,
    ) == pytest.approx(explicit.auto_exposure(
        target=0.7, film=FILM_NEGATIVE, infrared=True,
        infrared_blue_headroom=BLUE_RGBI_HEADROOM,
    ))


# -- what the probe measured is kept ---------------------------------------


def test_metering_records_what_each_round_measured():
    """Metering is the one step whose inputs are otherwise thrown away.

    Without this the probe's own error and blue's RGBI ratio cannot be told
    apart afterwards, which is exactly the position the 4.0 constant left us in.
    """
    s = FakeScanner((0.9, 0.7, 0.5), base=(9604, 6506, 6506, 7745))
    scales = s.auto_exposure(target=0.7, film=FILM_NEGATIVE, infrared=True)

    m = s.last_metering
    assert m is not None
    assert m["target"] == 0.7
    assert m["infrared"] is True
    assert m["blue_headroom"] == BLUE_RGBI_HEADROOM
    assert m["base_exposure"] == [9604, 6506, 6506, 7745]
    assert m["scales"] == pytest.approx(scales, abs=1e-4)
    assert m["rounds"], "no round was recorded"
    for probe in m["rounds"]:
        assert len(probe["levels"]) == 3
        assert len(probe["targets"]) == 3
        # Blue's target is the one that moves, and the record has to show it.
        assert probe["targets"][2] == pytest.approx(0.7 / BLUE_RGBI_HEADROOM, abs=1e-3)


def test_metering_telemetry_is_cleared_before_each_run():
    """A run that raises must not leave the last frame's numbers behind."""
    s = FakeScanner((0.9, 0.7, 0.5), base=(9604, 6506, 6506, 7745))
    s.auto_exposure(target=0.7, film=FILM_NEGATIVE)
    assert s.last_metering is not None

    boom = FakeScanner((0.9, 0.7, 0.5), base=(9604, 6506, 6506, 7745))
    boom.last_metering = s.last_metering

    def explode(*a, **kw):
        raise RuntimeError("the probe failed")

    boom.scan = explode
    with pytest.raises(RuntimeError):
        boom.auto_exposure(target=0.7, film=FILM_NEGATIVE)
    assert boom.last_metering is None, "stale metering survived a failed run"


# -- a clipped channel is re-measured, everything else is not ---------------


def test_a_clipped_channel_buys_one_more_round():
    """nkscan's rule: a level at full scale says only that it is somewhere
    above, so the retreat applied there is a guess and worth confirming.

    Everything else is one proportional step on a linear sensor.
    """
    s = FakeScanner((20.0, 20.0, 20.0), base=(60000, 60000, 60000, 8000))
    s.auto_exposure(target=0.4, film=FILM_NEGATIVE, rounds=2)
    assert len(s.passes) == 3, (
        f"a channel still clipped after two rounds took {len(s.passes)} probes"
    )


def test_an_unclipped_meter_stops_at_the_normal_rounds():
    s = FakeScanner((0.9, 0.7, 0.5), base=(9604, 6506, 6506, 7745))
    s.auto_exposure(target=0.4, film=FILM_NEGATIVE, rounds=2)
    assert len(s.passes) == 2, (
        f"nothing clipped, so the extra round should not have been spent "
        f"({len(s.passes)} probes)"
    )


def test_the_extra_round_can_be_refused():
    s = FakeScanner((20.0, 20.0, 20.0), base=(60000, 60000, 60000, 8000))
    s.auto_exposure(target=0.4, film=FILM_NEGATIVE, rounds=2, max_rounds=2)
    assert len(s.passes) == 2


def test_blue_lands_just_below_the_others_at_any_target():
    """The headroom divisor is a *ratio*, so blue tracks the target rather
    than being pinned to one level.

    BLUE_RGBI_HEADROOM sits slightly above the measured ratio, so blue lands a
    little under wherever red and green land -- deliberately, and by the same
    proportion whatever the target is.
    """
    for target in (0.60, EXPOSURE_TARGET, 0.90):
        landed = _blue_level_after_metering(BLUE_RGBI_HEADROOM, target=target)
        assert landed < target, f"blue overshot red and green at target {target}"
        assert landed > target * 0.85, (
            f"blue is {landed / target:.0%} of the target at {target}, which is "
            f"further down than the safety bias intends"
        )


def test_the_shipped_target_is_the_measured_one():
    """Pinned so a change to it is a deliberate act with evidence behind it."""
    assert EXPOSURE_TARGET == 0.80
    from rps7200.bracket import CLIP_START, FULL_SCALE
    assert EXPOSURE_TARGET <= CLIP_START / FULL_SCALE, (
        "metering must not aim above the level bracket.py stops trusting"
    )


# -- a locked film stays locked against the timer ceiling -------------------


def test_the_ceiling_does_not_break_the_lock():
    """The channels' ceilings are not equal, and that used to put a cast on
    exactly the films the lock exists to protect.

    Red's base exposure is 9604 against 6506 for green and blue, so its ceiling
    is x6.82 where theirs is x10.07. Clamping each channel on its own left red
    45% below the other two on any locked film that wanted more than x6.82 --
    measured on a real B&W scan as scales [6.824, 6.905, ...] where the lock
    promised one number.
    """
    for film in (FILM_POSITIVE, FILM_KODACHROME):
        s = FakeScanner((0.55, 0.60, 0.58), base=(9604, 6506, 6506, 7745))
        scales = s.auto_exposure(target=0.8, film=film, rounds=2)
        assert scales[0] == pytest.approx(scales[1], rel=0.01), (
            f"{film}: red {scales[0]:.3f} against green {scales[1]:.3f} -- "
            f"the lock was broken by the ceiling"
        )
        assert scales[1] == pytest.approx(scales[2], rel=0.01), film


def test_a_held_back_locked_meter_is_reported(capsys):
    """Under-exposing the whole scan is the right trade, but it has to be said:
    the operator can lower nothing else to get the range back."""
    s = FakeScanner((0.55, 0.60, 0.58), base=(9604, 6506, 6506, 7745))
    s.verbose = True
    s.auto_exposure(target=0.8, film=FILM_POSITIVE, rounds=2)
    out = capsys.readouterr().out
    assert "locked metering held" in out, out


def test_the_lock_survives_the_infrared_blue_headroom():
    """Blue is metered lower for an RGBI pass, and on a locked film that is
    still the right thing: blue comes back ~5x brighter there, so a blue held
    down by the same factor lands *in proportion* with red and green rather
    than out of it. What must not happen is red and green drifting apart.
    """
    s = FakeScanner((0.55, 0.60, 0.58), base=(9604, 6506, 6506, 7745))
    scales = s.auto_exposure(target=0.8, film=FILM_POSITIVE, infrared=True,
                             rounds=2)
    assert scales[0] == pytest.approx(scales[1], rel=0.01)
    assert scales[2] == pytest.approx(
        scales[1] / blue_rgbi_headroom(FILM_POSITIVE), rel=0.01)


def test_a_negative_is_still_metered_per_channel():
    """The fix must not reach films that are deliberately not locked."""
    s = FakeScanner((0.55, 0.60, 0.58), base=(9604, 6506, 6506, 7745))
    scales = s.auto_exposure(target=0.8, film=FILM_NEGATIVE, rounds=2)
    assert scales[1] > scales[0] * 1.2, (
        "a negative was moved as one group; the mask stays in the blue record"
    )


def test_the_blue_headroom_is_not_one_number_for_every_film():
    """It was, and that put 34% of a B&W scan's blue channel at the rail.

    Measured 4.98-5.02 on a colour negative and ~9.6 on black and white, each
    from a matched pair with red and green confirming the mode was the only
    variable. A film with no measurement of its own has to fail safe -- too
    much headroom only darkens a noise-limited channel, too little destroys it.
    """
    assert blue_rgbi_headroom(FILM_NEGATIVE) == BLUE_RGBI_HEADROOM
    assert blue_rgbi_headroom(FILM_BW) > BLUE_RGBI_HEADROOM
    assert blue_rgbi_headroom("something nobody has measured") == (
        BLUE_RGBI_HEADROOM_UNMEASURED
    )
    for film in (FILM_BW, FILM_POSITIVE, FILM_KODACHROME):
        assert blue_rgbi_headroom(film) >= BLUE_RGBI_HEADROOM, film


def test_a_bw_scan_no_longer_blows_its_blue_channel():
    """The scan this came from: film=bw, RGBI, blue 34% at the rail.

    Modelled with the ratio actually measured on that film, 9.6 -- not the 5.2
    the metering assumed.
    """
    measured_bw_ratio = 9.6
    s = FakeScanner((0.55, 0.60, 0.58), base=(9604, 6506, 6506, 7745))
    # scan() now refuses this combination outright; auto_exposure is tested on
    # its own so the headroom table stays honest if that guard ever moves.
    scales = s.auto_exposure(target=EXPOSURE_TARGET, film=FILM_BW, infrared=True)
    landed = min(1.0, 6506 * scales[2] / 65535.0 * 0.58) * measured_bw_ratio
    assert landed < 1.0, f"blue still clips, landing at {landed:.0%}"
    assert landed < CLIP_START / FULL_SCALE, (
        f"blue lands at {landed:.0%}, past the knee bracket.py stops trusting"
    )


# -- infrared is refused on film that absorbs it ---------------------------


def test_infrared_is_blind_to_silver_bw_and_kodachrome():
    assert supports_infrared(FILM_NEGATIVE) is True
    assert supports_infrared(FILM_POSITIVE) is True
    assert supports_infrared(FILM_BW) is False
    assert supports_infrared(FILM_KODACHROME) is False


def test_an_unknown_film_is_refused_here_too():
    with pytest.raises(ValueError, match="unknown film type"):
        supports_infrared("colour-negative")


@pytest.mark.parametrize("film", [FILM_BW, FILM_KODACHROME])
def test_a_scan_refuses_infrared_on_blind_film(film):
    """Not a warning. The pass costs its ~212 s floor and returns a plane
    holding the photograph -- measured at +0.97 correlation with green on a
    B&W frame -- and it drags blue's metering with it, which is what put 34%
    of that scan's blue channel at the rail.
    """
    s = FakeScanner((0.55, 0.60, 0.58))
    with pytest.raises(ValueError, match="infrared is blind"):
        DirectScanner.scan(s, infrared=True, film=film)


@pytest.mark.parametrize("film", [FILM_NEGATIVE, FILM_POSITIVE])
def test_infrared_is_left_alone_on_film_that_can_use_it(film):
    """The guard must not reach film whose dyes are transparent to infrared."""
    s = FakeScanner((0.55, 0.60, 0.58))
    # It gets past the guard; FakeScanner has no device, so it fails later.
    with pytest.raises(Exception) as exc:
        DirectScanner.scan(s, infrared=True, film=film)
    assert "infrared is blind" not in str(exc.value)


# -- the acceptance band is asymmetric ------------------------------------


def test_metering_will_not_stop_above_the_target():
    """Landing under costs noise; landing over clips, and that is not
    recoverable. The band either side of the target is not symmetric.

    This was `abs(level - target) <= tolerance`. At the old target of 0.70 that
    accepted 0.78 and was harmless; raising the target to 0.80 moved the top of
    the band to 0.88, past the knee bracket.py stops trusting -- and a real B&W
    frame landed at 87% with samples at the rail.
    """
    # A film bright enough that one proportional step overshoots.
    s = FakeScanner((0.9, 0.9, 0.9), base=(9604, 6506, 6506, 7745))
    scales = s.auto_exposure(target=EXPOSURE_TARGET, film=FILM_NEGATIVE,
                             rounds=3)
    for c, base in enumerate((9604, 6506, 6506)):
        landed = min(1.0, base * scales[c] / 65535.0 * 0.9)
        assert landed <= EXPOSURE_TARGET + OVER_TARGET_TOLERANCE + 0.01, (
            f"{'RGB'[c]} settled at {landed:.0%}, above the target"
        )


def test_the_band_above_the_target_stays_under_the_knee():
    from rps7200.bracket import CLIP_START, FULL_SCALE
    assert EXPOSURE_TARGET + OVER_TARGET_TOLERANCE <= CLIP_START / FULL_SCALE + 0.02
    assert OVER_TARGET_TOLERANCE < 0.08, (
        "the band above the target must be tighter than the one below it"
    )


# -- metering looks inside the film ----------------------------------------


class BorderedScanner(FakeScanner):
    """A scanner whose passes have clear aperture around the picture.

    That is what a short strip, or a frame that has drifted, looks like from
    the transport: the film does not fill the window, and what is left is bare
    lit aperture -- far brighter than any part of a negative.
    """

    def scan(self, resolution=300, infrared=False, exposure_scale=1.0, **kw):
        image, meta = super().scan(resolution, infrared, exposure_scale, **kw)
        h, w = image.shape[:2]
        out = np.full_like(image, 65535)          # aperture: the rail
        m = max(1, h // 5), max(1, w // 5)
        out[m[0]:h - m[0], m[1]:w - m[1]] = image[m[0]:h - m[0], m[1]:w - m[1]]
        return out, meta


def test_the_aperture_does_not_set_the_exposure():
    """Metering takes a high percentile, so anything brighter than the picture
    decides it. Measured on real prescans, an aperture in view read the 99.5th
    percentile 5.9-11.1% high and the scan came out that much short.

    The test is that the two scanners agree: the same film should be metered
    the same whether or not there is aperture beside it.
    """
    transmission = (0.55, 0.60, 0.58)
    base = (9604, 6506, 6506, 7745)

    filled = FakeScanner(transmission, base=base).auto_exposure(
        target=EXPOSURE_TARGET, film=FILM_NEGATIVE)
    bordered = BorderedScanner(transmission, base=base).auto_exposure(
        target=EXPOSURE_TARGET, film=FILM_NEGATIVE)

    assert bordered == pytest.approx(filled, rel=0.05), (
        f"aperture in view changed the exposure: {bordered} against {filled}"
    )


def test_without_the_crop_the_aperture_would_have_won():
    """Guards the guard: if metering_region ever stopped cropping, the test
    above would still pass for the wrong reason -- so check the aperture really
    is bright enough to have taken over."""
    s = BorderedScanner((0.55, 0.60, 0.58), base=(9604, 6506, 6506, 7745))
    image, _ = s.scan(exposure_scale=1.0)
    assert np.percentile(image[..., 1], 99.5) >= 65000, (
        "the fixture's aperture is not at the rail; it would not skew anything"
    )


def test_the_film_is_located_once_while_it_is_still_dark():
    """The subtle half, and the reason this is not detected per round.

    `film_bounds` needs the aperture at least CLEAR_RATIO brighter than the
    median. Metering's whole job is to brighten the film until it is nearly as
    bright as the aperture, so by the round that settles the exposure the
    contrast the detector depends on is gone. Detecting each round would work
    on the first pass and quietly stop working on the one that matters.
    """
    from rps7200.framing import FULL_FRAME, film_bounds

    s = BorderedScanner((0.55, 0.60, 0.58), base=(9604, 6506, 6506, 7745))
    dark, _ = s.scan(exposure_scale=1.0)
    bright, _ = s.scan(exposure_scale=8.0)

    assert film_bounds(dark) != FULL_FRAME, "the fixture has no visible edge"
    assert film_bounds(bright) == FULL_FRAME, (
        "the fixture does not reproduce the contrast collapse this guards"
    )
