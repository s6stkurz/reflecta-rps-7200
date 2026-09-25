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
    assert np.array_equal(dev, metrics.colour_deviation(image, window=25))
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


def test_agreement_takes_the_bracket_modules_noise_constants():
    """Retyped, they were a second home for two numbers and would drift."""
    from rps7200.bracket import DEFAULT_ALPHA, DEFAULT_BETA

    a, b = frame(1), frame(2)
    mask = np.ones(a.shape[:2], bool)
    assert metrics.agreement_z(a, b, mask) == metrics.agreement_z(
        a, b, mask, alpha=DEFAULT_ALPHA, beta=DEFAULT_BETA)
