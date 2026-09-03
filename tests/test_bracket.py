"""Bracket merge: does fusing exposures actually beat the passes it fuses.

Every test here answers a question that could stop the feature. The merge is
only worth its scan time if it beats the best single pass, and beats plain
averaging of the same number of passes -- so those are measured, not assumed.
"""

import numpy as np
import pytest

from rps7200.bracket import (
    CHANNEL_SPREAD_TAU,
    DEFAULT_ALPHA,
    DEFAULT_BETA,
    FULL_SCALE,
    confidence,
    fit_noise_params,
    merge_bracket,
)

RNG = np.random.default_rng(20260830)


def scene(h=64, w=96):
    """A frame spanning a wide density range, as a negative does."""
    y = np.arange(h)
    x = np.arange(w)
    ramp = np.exp(np.linspace(np.log(80.0), np.log(20000.0), w))[None, :]
    detail = 1.0 + 0.18 * np.sin(y / 3.0)[:, None] * np.cos(x / 5.0)[None, :]
    base = ramp * detail
    return np.stack([base * k for k in (1.0, 0.85, 0.6)], axis=-1)


def expose(truth, exposure, *, alpha=DEFAULT_ALPHA, beta=DEFAULT_BETA, rng=RNG):
    """One pass: linear in exposure, Poisson-Gaussian noise, clipped at the rail."""
    signal = truth * exposure
    noise = rng.normal(0.0, np.sqrt(alpha * np.maximum(signal, 0) + beta))
    return np.clip(signal + noise, 0, FULL_SCALE).astype(np.uint16)


def rms_vs_truth(frame, truth, exposure):
    """Error against the noise-free scene, in the reference's units."""
    return float(np.sqrt(np.mean((frame.astype(np.float64) / exposure - truth) ** 2)))


# --- 1. the N-way form must reduce to the pairwise one ----------------------

def pairwise_merge(short, long, r, alpha=DEFAULT_ALPHA, beta=DEFAULT_BETA):
    """The two-frame formula, written out independently of the N-way code.

    Includes the same no-confidence fallback the N-way merge applies, since that
    is a deliberate departure from pyopticfilm (which writes zero there) and is
    applied identically at every N. Without it this compares two different
    algorithms in the deep shadows, where neither pass carries signal.
    """
    a = short.astype(np.float32)
    b = long.astype(np.float32)
    xa, xb = a, b / r
    ca, cb = confidence(a), confidence(b)
    va = alpha * np.maximum(xa, 0.0) + beta
    vb = (alpha * np.maximum(b, 0.0) + beta) / (r * r)
    wa, wb = ca / np.maximum(va, 1e-12), cb / np.maximum(vb, 1e-12)
    ivw = (wa * xa + wb * xb) / np.maximum(wa + wb, 1e-12)
    return np.where((ca <= 1e-6) & (cb <= 1e-6), xa, ivw)


def test_two_frames_reduce_to_the_pairwise_formula():
    """The cheapest check that the generalisation is right."""
    truth = scene()
    short, long = expose(truth, 1.0), expose(truth, 2.5)
    merged, stats = merge_bracket([short, long], [1.0, 2.5])

    expected = pairwise_merge(short, long, 2.5)
    # Where the residual gate and the misalignment guard are inactive -- which
    # is everywhere for two well-registered frames -- the merge IS the IVW blend.
    assert stats.reference_fallback_fraction < 1e-6
    assert np.allclose(merged, np.clip(expected, 0, FULL_SCALE).astype(np.uint16), atol=1)


# --- 2. the merge must beat the passes it merges ----------------------------

def test_merge_beats_every_single_pass():
    truth = scene()
    exposures = [1.5, 4.0]
    frames = [expose(truth, e) for e in exposures]
    merged, _ = merge_bracket(frames, exposures)

    singles = [rms_vs_truth(f, truth, e) for f, e in zip(frames, exposures)]
    fused = rms_vs_truth(merged, truth, exposures[0])
    assert fused < min(singles), (
        f"merge {fused:.1f} did not beat the best single pass {min(singles):.1f}"
    )


def test_more_exposures_help_more():
    """The direction TobbyTravel measured: 5 brackets beat 2."""
    truth = scene()
    results = {}
    for n in (2, 5, 9):
        exposures = list(np.geomspace(1.5, 6.0, n))
        frames = [expose(truth, e) for e in exposures]
        merged, _ = merge_bracket(frames, exposures)
        results[n] = rms_vs_truth(merged, truth, exposures[0])
    assert results[5] < results[2], f"5 brackets ({results[5]:.1f}) not better than 2 ({results[2]:.1f})"
    assert results[9] <= results[5] * 1.02, f"9 brackets regressed: {results}"


def test_merge_beats_averaging_the_same_number_of_passes():
    """The honest comparison against plain multi-sampling."""
    truth = scene()
    exposures = list(np.geomspace(1.5, 6.0, 5))
    frames = [expose(truth, e) for e in exposures]
    merged, _ = merge_bracket(frames, exposures)

    # the same budget spent on repeats at one exposure
    mid = float(np.sqrt(1.5 * 6.0))
    repeats = [expose(truth, mid) for _ in exposures]
    averaged = np.mean([f.astype(np.float64) for f in repeats], axis=0)

    assert rms_vs_truth(merged, truth, exposures[0]) < rms_vs_truth(averaged, truth, mid)


# --- 3. degenerate cases ----------------------------------------------------

def test_identical_passes_do_not_amplify_noise():
    truth = scene()
    frames = [expose(truth, 1.0) for _ in range(4)]
    merged, _ = merge_bracket(frames, [1.0] * 4)
    single = rms_vs_truth(frames[0], truth, 1.0)
    # four identical passes are four independent noise draws: averaging them
    # should help, and must certainly not hurt
    assert rms_vs_truth(merged, truth, 1.0) <= single * 1.05


def test_a_fully_clipped_pass_is_ignored_not_averaged_in():
    truth = scene()
    good = expose(truth, 2.0)
    blown = np.full_like(good, int(FULL_SCALE))
    merged, _ = merge_bracket([good, blown], [2.0, 40.0])
    assert rms_vs_truth(merged, truth, 2.0) < rms_vs_truth(good, truth, 2.0) * 1.5


def test_an_empty_pass_is_ignored():
    truth = scene()
    good = expose(truth, 2.0)
    dead = np.zeros_like(good)
    merged, _ = merge_bracket([good, dead], [2.0, 4.0])
    assert rms_vs_truth(merged, truth, 2.0) < rms_vs_truth(good, truth, 2.0) * 1.5


@pytest.mark.parametrize("frames,exposures,match", [
    (1, 1, "at least 2"),
    (2, 3, "exposures"),
])
def test_bad_input_is_refused(frames, exposures, match):
    f = [np.zeros((4, 4, 3), np.uint16)] * frames
    with pytest.raises(ValueError, match=match):
        merge_bracket(f, [1.0] * exposures)


def test_non_positive_exposure_is_refused():
    f = [np.zeros((4, 4, 3), np.uint16)] * 2
    with pytest.raises(ValueError, match="positive"):
        merge_bracket(f, [1.0, 0.0])


# --- 4. misregistration must not become colour fringes ----------------------

@pytest.mark.parametrize("shift", [1, 4, 16])
def test_a_shifted_pass_does_not_produce_colour_fringes(shift):
    """16 columns is the real pass-to-pass offset recorded in TODO.md."""
    truth = scene()
    a = expose(truth, 2.0)
    b = np.roll(expose(truth, 6.0), shift, axis=1)
    merged, stats = merge_bracket([a, b], [2.0, 6.0])

    def worst_fringe(img):
        f = img.astype(np.float64)
        chroma = f - f.mean(axis=2, keepdims=True)
        return float(np.abs(chroma[:, 20:-20]).max())

    # the guard should keep the fused chroma near what the reference alone shows
    assert worst_fringe(merged) < worst_fringe(a) * 1.6, (
        f"shift {shift} produced fringes: {worst_fringe(merged):.0f} vs "
        f"{worst_fringe(a):.0f} in the reference alone"
    )


# --- the noise model --------------------------------------------------------

def test_noise_params_are_recovered_from_flats():
    alpha, beta = 0.7, 2500.0
    flats = []
    for level in (2000, 12000, 30000, 50000):
        flat = np.full((128, 128), float(level))
        flat = flat + RNG.normal(0, np.sqrt(alpha * level + beta), flat.shape)
        flats.append(np.clip(flat, 0, FULL_SCALE))
    got_alpha, got_beta = fit_noise_params(flats)
    assert got_alpha == pytest.approx(alpha, rel=0.35), f"alpha {got_alpha}"
    assert got_beta == pytest.approx(beta, rel=0.8), f"beta {got_beta}"


def test_too_few_flats_falls_back_to_the_defaults():
    assert fit_noise_params([]) == (DEFAULT_ALPHA, DEFAULT_BETA)


# --- the ladder -------------------------------------------------------------

class FakeLadder:
    """Just enough DirectScanner to exercise bracket_ladder."""
    from rps7200.direct import DirectScanner
    bracket_ladder = DirectScanner.bracket_ladder
    MIN_BRACKET_PASSES = DirectScanner.MIN_BRACKET_PASSES
    MAX_BRACKET_PASSES = DirectScanner.MAX_BRACKET_PASSES

    def __init__(self, exposure=(9604, 6506, 6506, 7745)):
        from rps7200.direct import Settings
        self.verbose = False
        self._settings = Settings(exposure=list(exposure), gain=[39, 33, 21, 21],
                                  offset=[12, 11, 30, 11])

    def _log(self, msg): pass
    def get_gain_offset(self): return self._settings


def test_the_ladder_never_exceeds_the_16_bit_timer():
    """Past 65535 the timer wraps and the pass comes back darker, not brighter."""
    s = FakeLadder()
    metered = [2.88, 6.03, 2.60]
    ladder = s.bracket_ladder(metered, passes=9, stops=2.0)
    base = s.get_gain_offset().exposure
    for k in ladder:
        for c in range(3):
            assert base[c] * metered[c] * k <= 65535 + 1e-6, (
                f"x{k:.3f} would put channel {'RGB'[c]} at "
                f"{base[c] * metered[c] * k:.0f}"
            )


def test_the_ladder_is_geometric_and_ascending():
    s = FakeLadder()
    ladder = s.bracket_ladder([2.88, 6.03, 2.60], passes=5, stops=2.0)
    assert len(ladder) == 5
    assert all(b > a for a, b in zip(ladder, ladder[1:])), ladder
    steps = [b / a for a, b in zip(ladder, ladder[1:])]
    assert max(steps) - min(steps) < 1e-6, f"not geometric: {steps}"
    assert ladder[-1] / ladder[0] == pytest.approx(4.0), "2 stops is a factor of 4"


def test_no_two_passes_land_on_the_same_exposure():
    s = FakeLadder()
    base = s.get_gain_offset().exposure
    metered = [2.88, 6.03, 2.60]
    ladder = s.bracket_ladder(metered, passes=9, stops=2.0)
    applied = [
        tuple(int(max(100, min(65535, round(base[c] * metered[c] * k)))) for c in range(3))
        for k in ladder
    ]
    assert len(set(applied)) == len(applied), f"duplicate exposures: {applied}"


@pytest.mark.parametrize("n", [0, 1, 10, 20])
def test_bracket_size_is_refused_outside_2_to_9(n):
    s = FakeLadder()
    with pytest.raises(ValueError, match="2 to 9 passes"):
        s.bracket_ladder([1.0, 1.0, 1.0], passes=n)


def test_a_metered_exposure_already_at_the_rail_still_gives_a_ladder():
    """Blue reaches the ceiling on some negatives; the bracket must go down."""
    s = FakeLadder()
    ladder = s.bracket_ladder([2.88, 10.07, 10.07], passes=3, stops=1.0)
    # green sits at 65515 of 65535, so there is essentially nowhere up to go
    assert ladder[-1] == pytest.approx(1.0, abs=0.01)
    assert ladder[0] == pytest.approx(0.5, abs=0.01)
    assert ladder[-1] / ladder[0] == pytest.approx(2.0, rel=1e-6)
