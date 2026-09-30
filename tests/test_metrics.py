"""The measure-scan-quality skill's metrics, where their answers were impossible.

`metrics.py` sits beside the skill rather than in the package, and nothing
tested it. These hold the parts that returned an answer they could not have
measured: a ceiling from a random share of 100% or more, a random share
read through a different filter from the total it is a share of, a window
that crashed when even, and 16-bit thresholds applied to 8-bit passes.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / ".claude" / "skills" / "measure-scan-quality" / "scripts"))

import metrics  # noqa: E402


def frame(seed=0, dtype=np.uint16, shape=(40, 60, 3)):
    rng = np.random.default_rng(seed)
    base = np.linspace(4000, 30000, shape[1])[None, :, None]
    return (base + rng.normal(0, 200, shape)).clip(0, 65535).astype(dtype)


def test_a_random_share_past_the_total_has_no_ceiling():
    """It clamped the fixed part to zero and promised the most improvement
    exactly where the measurement was worst."""
    with pytest.raises(ValueError, match="noise_split"):
        metrics.ceiling(1.118, 1.0, 9)


def test_an_honest_split_still_gives_its_ceiling():
    # fixed = sqrt(1 - 0.25); nine passes leave random / 3
    want = np.sqrt((0.5 / 3) ** 2 + 0.75) - 1.0
    assert metrics.ceiling(0.5, 1.0, 9) == pytest.approx(want)


def repeat_pair(random_dn, fixed_dn, seed=3, shape=(300, 400)):
    """Two passes of one frame, registered and matched in gain by
    construction: a smooth scene, a column pattern both passes share, and
    each pass's own white noise. Nothing leaks into their difference."""
    rng = np.random.default_rng(seed)
    h, w = shape
    scene = np.linspace(8000, 30000, w)[None, :] + rng.normal(0, fixed_dn, (1, w))

    def one():
        p = scene + rng.normal(0, random_dn, (h, w))
        return np.repeat(p[..., None], 3, axis=2).clip(0, 65535).astype(np.uint16)

    return one(), one(), np.ones(shape, bool)


def test_a_pair_that_is_mostly_random_noise_has_a_ceiling():
    """The case where averaging helps most was the one refused: the random
    part was read unfiltered against a high-passed total, so 100 DN of noise
    beside a 20 DN pattern came out at a share of 1.10 and `ceiling` told a
    perfectly registered pair to register itself."""
    a, b, mask = repeat_pair(random_dn=100, fixed_dn=20)
    rnd, total, share = metrics.noise_split(a, b, mask)
    assert share < 1.0, share
    reached = metrics.ceiling(rnd, total, 9)
    # Between all-fixed (nothing to gain) and all-random (1/3 of the noise).
    assert 1 / 3 - 1 < reached < 0


def test_white_noise_alone_is_all_random_not_more():
    """1/sqrt(1 - 1/5) = 1.118 is what the two filters used to read here."""
    a, b, mask = repeat_pair(random_dn=100, fixed_dn=0)
    _, _, share = metrics.noise_split(a, b, mask)
    assert share == pytest.approx(1.0, abs=0.02)


def test_a_mostly_fixed_pair_still_reads_mostly_fixed():
    a, b, mask = repeat_pair(random_dn=20, fixed_dn=100)
    _, _, share = metrics.noise_split(a, b, mask)
    # sqrt(0.8 * 20**2 / (0.8 * (20**2 + 100**2))), the fixed part white
    # along the row as the random part is.
    assert share == pytest.approx(20 / np.hypot(20, 100), rel=0.05)


@pytest.mark.parametrize("window", [24, 25])
def test_an_even_window_is_widened_rather_than_crashing(window):
    image = frame()
    dev = metrics.colour_deviation(image, window=window)
    assert dev.shape == (3, image.shape[1])
    # Equal to the last bit only by luck: np.convolve's summation order
    # follows how the arrays sit in memory, so two runs of the same numbers
    # differ in the last place about one time in three.
    assert np.allclose(dev, metrics.colour_deviation(image, window=25),
                       rtol=0, atol=1e-9)
    assert np.isfinite(metrics.fixed_pattern(image, 1, window=window))


@pytest.mark.parametrize("call", [
    lambda a: metrics.agreement_z(a, a, np.ones(a.shape[:2], bool)),
    lambda a: metrics.noise_split(a, a, np.ones(a.shape[:2], bool)),
    lambda a: metrics.dark_mask(a),
    lambda a: metrics.colour_deviation(a),
])
def test_an_8_bit_pass_is_refused(call):
    """Every threshold here is in 16-bit counts: on an 8-bit prescan the whole
    frame sits under the noise floor."""
    with pytest.raises(ValueError, match="8-bit"):
        call(frame(dtype=np.uint8))


def test_a_single_plane_is_refused_with_a_reason():
    with pytest.raises(ValueError, match="H, W, C"):
        metrics.dark_mask(frame()[..., 0])


def test_an_average_of_passes_is_still_welcome():
    """Floats on the 16-bit scale -- a mean of repeats, a merge -- are what
    the skill compares against, and must not be refused with the 8-bit case."""
    mean = (frame(1).astype(np.float64) + frame(2)) / 2
    assert metrics.dark_mask(mean).any()


def test_agreement_takes_the_noise_constants_from_their_one_home():
    """Retyped, they were a second home for two numbers and would drift. The
    bracket module that also held them is archived, so the skill keeps them,
    at the values the archive was measured with."""
    a, b = frame(1), frame(2)
    mask = np.ones(a.shape[:2], bool)
    assert (metrics.DEFAULT_ALPHA, metrics.DEFAULT_BETA) == (1.0, 4096.0)
    assert metrics.agreement_z(a, b, mask) == metrics.agreement_z(
        a, b, mask, alpha=metrics.DEFAULT_ALPHA, beta=metrics.DEFAULT_BETA)


def test_a_pair_a_little_apart_in_gain_is_matched_before_splitting():
    """Two passes 10% apart in exposure put 10% of every edge into their
    difference, and it read as random: the random share of a mostly fixed
    pair more than doubled, and the ceiling promised gains averaging cannot
    give."""
    a, b, mask = repeat_pair(random_dn=20, fixed_dn=600)
    brighter = (b.astype(np.float64) * 1.1).clip(0, 65535).astype(np.uint16)
    _, _, matched = metrics.noise_split(a, b, mask)
    _, _, share = metrics.noise_split(a, brighter, mask)
    assert share == pytest.approx(matched, abs=0.03), (share, matched)


def test_agreement_is_judged_where_neither_pass_is_at_the_rail():
    """The median ran over every masked pixel, clipped ones included, which
    disagree by construction: half the frame railed in the brighter pass
    read a consistent pair at 11 sigma."""
    rng = np.random.default_rng(4)
    h, w = 120, 160
    scene = np.linspace(3000, 45000, w)[None, :] * np.ones((h, 1))

    def expose(k):
        signal = scene * k
        p = signal + rng.normal(0, np.sqrt(signal + 4096))
        return np.repeat(p[..., None], 3, axis=2).clip(0, 65535).astype(
            np.uint16)

    a, b = expose(1.0), expose(3.0)
    railed = (b[..., 1] >= 65535).mean()
    assert railed > 0.4, "the premise: much of the brighter pass is railed"
    z = metrics.agreement_z(a, b, np.ones((h, w), bool))
    assert z < 1.5, z


def test_the_deviation_is_signed_and_channel_relative():
    """MES-13: CLAUDE.md makes signed, channel-relative deviation mandatory,
    and nothing held it: a regression to `np.abs`, which hides a violet and
    green pair, passed the suite. A green column 5% up and a blue one 5% down
    elsewhere must read that way round, against the other channels."""
    rng = np.random.default_rng(3)
    img = np.full((60, 120, 3), 20000.0) + rng.normal(0, 20, (60, 120, 3))
    img[:, 40, 1] *= 1.05
    img[:, 80, 2] *= 0.95
    dev = metrics.colour_deviation(img.clip(0, 65535).astype(np.uint16))
    assert dev[1, 40] > 2.0 and dev[0, 40] < -1.0 and dev[2, 40] < -1.0
    assert dev[2, 80] < -2.0 and dev[0, 80] > 1.0 and dev[1, 80] > 1.0
    assert np.allclose(dev.sum(axis=0), 0.0, atol=1e-9)


def test_a_sensor_column_is_what_two_frames_share():
    """The cross-frame test the skill names and nothing implemented: a
    defect sits at one sensor column whatever frame is in front of it;
    picture sits wherever the frame put it."""
    def frame_with(seed, defect_at, picture_at):
        rng = np.random.default_rng(seed)
        img = np.full((60, 120, 3), 20000.0) + rng.normal(0, 30, (60, 120, 3))
        img[:, defect_at, 2] *= 1.08            # a blue column on the sensor
        img[:, picture_at, 0] *= 1.08           # a red edge in the picture
        return img.clip(0, 65535).astype(np.uint16)

    a = frame_with(1, defect_at=40, picture_at=70)
    b = frame_with(2, defect_at=40, picture_at=95)
    shared = metrics.persistent_deviation(a, b)
    assert shared.shape == (3, 120)
    assert shared[2, 40] > 3.0
    assert abs(shared[0, 70]) < 0.5 and abs(shared[0, 95]) < 0.5
    with pytest.raises(ValueError, match="columns"):
        metrics.persistent_deviation(a, b[:, :100])
