"""Metering tests: what the exposure loop does without the scanner attached.

The load-bearing piece is the film type. A colour negative is metered one
channel at a time, which takes the orange mask off before the ADC; every other
film keeps its balance, because there the cast is the picture and pulling the
channels apart removes it.
"""

import numpy as np
import pytest

from conftest import settings
from rps7200.direct import (
    FILM_BW,
    FILM_KODACHROME,
    FILM_NEGATIVE,
    FILM_POSITIVE,
    DirectScanner,
    locks_white_balance,
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


def test_only_a_negative_is_metered_per_channel():
    assert locks_white_balance(FILM_NEGATIVE) is False
    for film in (FILM_POSITIVE, FILM_KODACHROME, FILM_BW):
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
    """Blue returns 2-3.7x brighter in RGBI at the same exposure, so a blue
    filling the range in RGB clips in RGBI."""
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
