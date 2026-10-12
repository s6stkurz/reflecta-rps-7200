"""What the driver says a manual exposure sends, before it sends anything.

The window showed "x50" while 65535 went to the device: `Settings.scaled`
clamps every channel to the 16-bit timer, and blue below its floor is raised
by the scanner without a word. `DirectScanner.manual_exposure` forecasts the
counts on this scanner's usual base (`BASE_EXPOSURE`) and names each limit.
That base is for display and the demo only; what a pass sends is the device's
own answer on the day, scaled -- which the last test here holds it to.
"""
import pytest

from rps7200.direct import (
    BLUE_EXPOSURE_FLOOR,
    BW_EXPOSURE_CAP,
    FILM_BW,
    DirectScanner,
)


def _by_channel(factors, film="negative"):
    return {name: (counts, limit) for name, counts, limit
            in DirectScanner.manual_exposure(factors, film)}


def test_the_usual_base_is_what_the_device_answers():
    """1353 of 1359 recorded READ GAIN/OFFSET replies."""
    assert DirectScanner.BASE_EXPOSURE == (9604, 6506, 6506, 7745)


def test_a_factor_past_the_timer_is_said_at_the_timers_top():
    said = _by_channel(50)
    assert {name: counts for name, (counts, _l) in said.items()} == {
        "R": 65535, "G": 65535, "B": 65535, "IR": 65535}
    assert said["R"][1] == "the timer's top, reached at x6.82"
    assert said["G"][1] == said["B"][1] == "the timer's top, reached at x10.07"
    assert said["IR"][1] == "the timer's top, reached at x8.46"


def test_blue_under_its_floor_is_said():
    """The scanner gives blue no less than about 3100 counts and takes a
    smaller number without a word."""
    counts, limit = _by_channel(0.3)["B"]
    assert counts == round(6506 * 0.3) < BLUE_EXPOSURE_FLOOR
    assert limit == (f"under blue's floor: the scanner gives it about "
                     f"{BLUE_EXPOSURE_FLOOR}")
    assert _by_channel(0.3)["R"][1] == "", "red has no floor"


@pytest.mark.parametrize("factor", [0.01, 0.0001])
def test_blue_under_the_fields_minimum_is_said_at_its_floor(factor):
    """Under x0.0154 blue's count is under the field's 100 too, and that was
    said first -- "B 100 (the shortest the field holds)" -- of a channel the
    scanner exposes at about 3100 whatever it is sent."""
    counts, limit = _by_channel(factor)["B"]
    assert counts == 100
    assert limit == (f"under blue's floor: the scanner gives it about "
                     f"{BLUE_EXPOSURE_FLOOR}")
    assert _by_channel(factor)["G"][1] == "the shortest the field holds"


def test_black_and_white_past_its_cap_is_said():
    """Metering keeps green and blue under `BW_EXPOSURE_CAP`, where its
    passes ran fast; a factor typed by hand is not held to it."""
    said = _by_channel([1.0, 9.6, 9.6], FILM_BW)
    for name, channel in (("G", 1), ("B", 2)):
        counts, limit = said[name]
        assert counts > BW_EXPOSURE_CAP[channel]
        assert limit.startswith(
            f"past black and white's {BW_EXPOSURE_CAP[channel]}")
    assert said["R"][1] == "", "red keeps its timer"
    assert _by_channel([1.0, 9.6, 9.6], "negative")["G"][1] == "", (
        "only black and white")


def test_one_factor_a_channel_leaves_the_rest_unchanged():
    said = DirectScanner.manual_exposure([2.0, 0.5, 1.5], "negative")
    assert [(name, counts) for name, counts, _l in said] == [
        ("R", 19208), ("G", 3253), ("B", 9759), ("IR", 7745)]
    assert DirectScanner.manual_exposure(1.0, "negative")[0] == ("R", 9604, "")


def test_the_usual_base_is_never_what_a_pass_sends(monkeypatch):
    """Display and demo only: a pass scales the device's own answer. With the
    constant made nonsense, a manual pass still sends the device's base."""
    from conftest import DeviceAtCommands, NoWaiting
    from rps7200 import direct

    monkeypatch.setattr(DirectScanner, "BASE_EXPOSURE", (1, 2, 3, 4))
    device = DeviceAtCommands()
    s = DirectScanner(transport=device, verbose=False, debug=False)
    waiting, direct.time = direct.time, NoWaiting()
    try:
        _image, meta = s.scan(resolution=300, infrared=True,
                              fast_infrared=True, shading=False,
                              auto_exposure=False, exposure_scale=2.0)
    finally:
        direct.time = waiting
    assert meta["exposure"] == [2 * e for e in DeviceAtCommands.EXPOSURE]


@pytest.mark.parametrize("factor", [0.0001, 100.0])
def test_the_field_is_said_at_both_its_ends(factor):
    """`Settings.scaled` holds every channel to 100..65535."""
    counts, limit = _by_channel(factor)["R"]
    assert counts in (100, 65535) and limit
