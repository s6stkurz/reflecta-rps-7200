"""Shading correction: the two-phase parse and the two-point division.

The scanner returns raw pixels and hands back its own per-column response in
two phases -- unlit then lit -- in one pass. Getting the phases confused is the
failure that matters: averaging them together, as pieusb does, yields a
reference that corrects nothing.
"""

import numpy as np
import pytest

from rps7200.shading import (
    apply_shading,
    build_width_to_loc,
    calculate_shading,
)

PPL = 64
TAGS = {0: b"R", 1: b"G", 2: b"B", 3: b"I"}


def block(dark_level, light_level, channels=3, lines=20, seed=0, ppl=PPL):
    """A calibration block shaped like the device's: dark phase, then light."""
    rng = np.random.default_rng(seed)
    x = np.linspace(-1, 1, ppl)
    out = bytearray()
    truth = {}
    for phase, level in (("dark", dark_level), ("light", light_level)):
        for c in range(channels):
            shape = level * (1.0 - 0.15 * x**2)          # lamp falloff
            shape[ppl // 3] *= 0.90                       # a bad column
            truth[(phase, c)] = shape
        for _ in range(lines):
            for c in range(channels):
                noise = rng.normal(0, max(level * 0.002, 0.5), ppl)
                s = np.clip(truth[(phase, c)] + noise, 0, 65535).astype("<u2")
                out += TAGS[c] * 2 + s.tobytes()
    return bytes(out), truth


def test_the_two_phases_are_separated():
    data, truth = block(170, 47000)
    ref = calculate_shading(data, PPL)
    assert ref is not None and ref.two_point
    for c in range(3):
        assert ref.dark_mean[c] == pytest.approx(170 * 0.95, rel=0.05)
        assert ref.mean[c] == pytest.approx(47000 * 0.95, rel=0.05)
        # each phase recovered, not a blend of the two
        assert np.allclose(ref.dark[c], truth[("dark", c)], rtol=0.05, atol=3)
        assert np.allclose(ref.ref[c], truth[("light", c)], rtol=0.01)


def test_a_blend_would_have_been_wrong():
    """What averaging every line sharing a tag would have produced."""
    data, _ = block(170, 47000)
    ref = calculate_shading(data, PPL)
    blended = (ref.dark_mean[0] + ref.mean[0]) / 2
    assert abs(blended - ref.mean[0]) > 0.4 * ref.mean[0], (
        "a blended reference sits nowhere near the light level it must divide by"
    )


def test_one_phase_falls_back_to_a_single_point():
    data, _ = block(47000, 47000)      # no dark phase to find
    ref = calculate_shading(data, PPL)
    assert ref is not None and not ref.two_point
    assert ref.dark == {}


def test_two_point_removes_offset_and_gain():
    """A column defect that is part offset, part gain -- as a real one is."""
    data, truth = block(170, 47000)
    ref = calculate_shading(data, PPL)

    scene = np.linspace(2000, 40000, PPL)[None, :, None] * np.ones((32, 1, 3))
    raw = np.empty_like(scene)
    for c in range(3):
        gain = truth[("light", c)] / truth[("light", c)].mean()
        raw[..., c] = scene[..., c] * gain + truth[("dark", c)]
    raw = np.clip(raw, 0, 65535).astype(np.uint16)

    fixed, report = apply_shading(raw, ref)
    assert report["two_point"] == 1

    def worst(img):
        col = np.median(img[..., 1].astype(float), axis=0)
        sm = np.convolve(np.pad(col, 4, mode="reflect"), np.ones(9) / 9, "valid")
        return 100 * (np.abs(col - sm) / np.median(col))[6:-6].max()

    assert worst(fixed) < worst(raw) / 4, (
        f"defect not removed: {worst(raw):.2f}% -> {worst(fixed):.2f}%"
    )


def test_the_mask_places_the_columns():
    mask = bytearray([0x70]) * PPL
    for i in range(0, PPL, 2):
        mask[i] = 0x00
    loc = build_width_to_loc(bytes(mask), PPL // 2)
    assert loc.tolist() == list(range(0, PPL, 2))


def test_clipping_is_reported_per_channel():
    """A total buries the one signal worth having.

    Blue is the only channel on this scanner that ever clips, so a scalar sum
    over all of them cannot say which exposure to lower -- which is how a roll
    went out with 22% of its blue at the rail and the report reading only
    "983454 samples clipped".
    """
    data, truth = block(170, 47000)
    ref = calculate_shading(data, PPL)

    # Red and green comfortable, blue up against the ceiling.
    scene = np.zeros((32, PPL, 3))
    scene[..., 0] = 20000
    scene[..., 1] = 20000
    scene[..., 2] = 65000
    raw = np.clip(scene, 0, 65535).astype(np.uint16)

    _, report = apply_shading(raw, ref)
    per = report["clipped_per_channel"]

    assert len(per) == 3
    assert sum(per) == report["clipped"], "the per-channel counts must total"
    assert per[2] > 0, "blue was pushed over the ceiling and was not counted"
    assert per[0] == 0 and per[1] == 0, (
        f"only blue should have clipped, got {per}"
    )


def test_the_clipped_total_survives_for_stored_sidecars():
    """Every filed entry holds a scalar there; reading one back must not
    become a special case."""
    data, _ = block(170, 47000)
    ref = calculate_shading(data, PPL)
    raw = np.full((8, PPL, 3), 20000, dtype=np.uint16)
    _, report = apply_shading(raw, ref)
    assert isinstance(report["clipped"], int)
