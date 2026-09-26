"""Pass registration: does it find where a pass lies, and move it without harm.

The scene is a sum of random sinusoids, so a pass at any sub-pixel offset is
the same function evaluated at shifted coordinates -- the truth is exact and
owes nothing to the shifting code under test. `np.roll` would wrap content
round the edge and fight the window, which is why `test_uniformity.py` builds
its shifts from overlapping crops; this builds them from the coordinates.
"""

import sys
from pathlib import Path

# The archived modules are files beside this folder, not a package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "code"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from passes import (  # noqa: E402
    EDGE_MARGIN_PX,
    SEARCH_PX,
    band_residuals,
    register_passes,
    register_subpixel,
    shift_image,
)
from rps7200.uniformity import register  # noqa: E402

H, W = 256, 320


def texture(h=H, w=W, *, dy=0.0, dx=0.0, seed=7, level=9000.0, waves=160):
    """A grain-and-detail scene, sampled with its origin moved by (dy, dx).

    ``texture(dy=a, dx=b)[i, j]`` is the scene at ``(i + a, j + b)``, so with
    ``ref = texture()`` and ``mov = texture(dy=a, dx=b)``, ``ref[i, j]`` equals
    ``mov[i - a, j - b]`` -- `register`'s convention, with the answer (a, b).
    """
    rng = np.random.default_rng(seed)
    fy = rng.uniform(-0.3, 0.3, waves)
    fx = rng.uniform(-0.3, 0.3, waves)
    phase = rng.uniform(0, 2 * np.pi, waves)
    amp = rng.uniform(0.2, 1.0, waves) / np.sqrt(waves)
    y = np.arange(h)[:, None] + dy
    x = np.arange(w)[None, :] + dx
    field = np.zeros((h, w))
    for k in range(waves):
        field += amp[k] * np.cos(2 * np.pi * (fy[k] * y + fx[k] * x) + phase[k])
    base = level * (1.0 + 0.25 * field)
    return np.stack([base * c for c in (1.0, 0.8, 0.55)], axis=-1)


def as_pass(scene, *, exposure=1.0, noise=40.0, seed=0):
    rng = np.random.default_rng(seed)
    out = scene * exposure + rng.normal(0.0, noise, scene.shape)
    return np.clip(out, 0, 65535).astype(np.uint16)


# --- finding the shift ------------------------------------------------------

@pytest.mark.parametrize("dy,dx", [(0.3, -0.7), (-1.45, 0.2), (2.4, 0.6)])
def test_a_subpixel_shift_is_recovered(dy, dx):
    ref = as_pass(texture(), seed=1)
    mov = as_pass(texture(dy=dy, dx=dx), seed=2)
    s = register_subpixel(ref, mov)
    assert s.ok, s.reason
    # measured over twelve random shifts on this scene: unbiased to 0.006 px,
    # spread 0.03, worst 0.057 -- the texture here is far busier than film
    assert abs(s.dy - dy) < 0.08 and abs(s.dx - dx) < 0.08, s


def test_whole_pixel_shifts_agree_with_register_sign_and_all():
    """16 columns is the pass-to-pass offset recorded in TODO.md; 40 rows is
    the size of drift pyopticfilm measured on its own hardware. At 512 px,
    because `register` -- whole frame, no detail plane -- cannot lock onto a
    40-row shift of a frame only 256 rows tall; this module's registration
    can, and does so at 256 in the sub-pixel test's scene too."""
    ref = as_pass(texture(h=512, w=512), seed=1)
    mov = as_pass(texture(h=512, w=512, dy=40, dx=16), seed=2)
    s = register_subpixel(ref, mov)
    iy, ix, _ = register(ref, mov)
    assert (iy, ix) == (40, 16)
    assert abs(s.dy - iy) < 0.05 and abs(s.dx - ix) < 0.05, s


def test_a_longer_exposure_with_red_at_the_rail_still_registers():
    ref = as_pass(texture(), seed=1)
    mov = as_pass(texture(dy=-0.6, dx=1.3), exposure=7.0, seed=2)
    assert (mov[..., 0] == 65535).mean() > 0.3            # red is largely clipped
    s = register_subpixel(ref, mov)
    assert s.ok, s.reason
    assert abs(s.dy + 0.6) < 0.08 and abs(s.dx - 1.3) < 0.08, s


def test_a_column_pattern_both_passes_share_does_not_pull_the_shift_to_zero():
    """The sensor's residual column pattern sits at the same columns in every
    pass whatever the film did, so it is a perfect match at dx = 0."""
    rng = np.random.default_rng(3)
    pattern = 1.0 + 0.04 * rng.standard_normal(W)[None, :, None]
    ref = as_pass(texture() * pattern, seed=1)
    mov = as_pass(texture(dy=0.4, dx=2.3) * pattern, seed=2)
    s = register_subpixel(ref, mov)
    assert s.ok, s.reason
    assert abs(s.dx - 2.3) < 0.1 and abs(s.dy - 0.4) < 0.1, s


def test_unrelated_frames_are_refused_not_guessed():
    a = as_pass(texture(seed=1), seed=1)
    b = as_pass(texture(seed=99), seed=2)
    s = register_subpixel(a, b)
    assert not s.ok
    assert "floor" in s.reason
    assert (s.dy, s.dx) == (0.0, 0.0)


def test_a_shift_beyond_the_search_is_refused():
    ref = as_pass(texture(h=H, w=W + 200), seed=1)[:, :W]
    mov = as_pass(texture(h=H, w=W + 200, dx=SEARCH_PX + 20), seed=2)[:, :W]
    assert not register_subpixel(ref, mov).ok


# --- moving a pass ----------------------------------------------------------

def test_a_fourier_shift_keeps_the_noise_it_had():
    """The reason for shifting by phase rather than by interpolation: a
    half-pixel bilinear shift would take ~30% off white noise, and a stack of
    such passes would read as quieter than stacking made it."""
    rng = np.random.default_rng(5)
    noise = rng.normal(0.0, 100.0, (H, W))
    for d in (0.5, 0.25, 1.7):
        moved, _ = shift_image(noise, d, -d)
        inner = (slice(8, -8),) * 2
        assert abs(moved[inner].std() / noise[inner].std() - 1.0) < 0.01, d


def test_shifting_there_and_back_returns_the_frame():
    """Away from the edge. Near it an exact interpolator needs data beyond the
    frame and gets a mirror instead -- see `EDGE_MARGIN_PX`."""
    img = as_pass(texture(), seed=1)
    moved, _ = shift_image(img, 1.3, -0.4)
    back, _ = shift_image(moved, -1.3, 0.4)
    inner = (slice(32, -32),) * 2
    err = back[inner].astype(float) - img[inner]
    assert np.sqrt(np.mean(err ** 2)) < 8          # the passes' noise is 40
    assert back.dtype == img.dtype


def test_a_shift_puts_the_pass_on_its_reference():
    ref = texture()
    mov = texture(dy=1.3, dx=-0.4)
    moved, valid = shift_image(mov, 1.3, -0.4)
    err = np.abs(moved - ref)[valid]
    assert err.max() < 0.005 * ref.mean()


def test_valid_is_false_exactly_where_the_shift_uncovered_the_frame():
    h, w = 120, 150
    img = np.ones((h, w, 3), dtype=np.uint16)
    _, valid = shift_image(img, 3.4, -2.0)
    rows = np.arange(h) - 3.4
    cols = np.arange(w) + 2.0
    want_y = (rows >= EDGE_MARGIN_PX) & (rows <= h - 1 - EDGE_MARGIN_PX)
    want_x = (cols >= EDGE_MARGIN_PX) & (cols <= w - 1 - EDGE_MARGIN_PX)
    assert np.array_equal(valid, want_y[:, None] & want_x[None, :])
    top = int(np.ceil(3.4 + EDGE_MARGIN_PX))
    assert not valid[:top].any() and valid[top, EDGE_MARGIN_PX:-EDGE_MARGIN_PX - 2].all()


def test_no_shift_leaves_everything_valid():
    img = as_pass(texture(), seed=1)
    moved, valid = shift_image(img, 0.0, 0.0)
    assert valid.all()
    assert np.array_equal(moved, img)


# --- a set of passes --------------------------------------------------------

def test_every_pass_is_put_on_the_first():
    truth = [(0.0, 0.0), (-0.9, 0.4), (-2.4, 0.7)]
    frames = [as_pass(texture(dy=dy, dx=dx), seed=i) for i, (dy, dx) in enumerate(truth)]
    aligned, valid, shifts = register_passes(frames)
    assert shifts[0].dy == 0.0 and valid[0].all() and aligned[0] is frames[0]
    ref = frames[0].astype(float)
    for (dy, dx), s, a, v in zip(truth[1:], shifts[1:], aligned[1:], valid[1:]):
        assert abs(s.dy - dy) < 0.05 and abs(s.dx - dx) < 0.05, s
        # what is left is the two passes' own noise, about sqrt(2) x 40
        assert (a.astype(float) - ref)[v].std() < 80


def test_a_pass_nobody_could_place_is_left_out_not_averaged_in():
    frames = [as_pass(texture(seed=1), seed=1), as_pass(texture(seed=99), seed=2)]
    aligned, valid, shifts = register_passes(frames)
    assert not shifts[1].ok
    assert not valid[1].any()


def test_passes_of_different_sizes_are_refused():
    with pytest.raises(ValueError, match="reference"):
        register_passes([np.zeros((64, 64, 3), np.uint16), np.zeros((64, 60, 3), np.uint16)])


def test_band_residuals_read_zero_once_a_rigid_shift_is_undone():
    ref = as_pass(texture(h=384), seed=1)
    mov = as_pass(texture(h=384, dy=-1.2, dx=0.5), seed=2)
    before = band_residuals(ref, mov, bands=4)
    assert all(abs(s.dy + 1.2) < 0.1 for _, s in before)
    aligned, _, _ = register_passes([ref, mov])
    after = band_residuals(ref, aligned[1], bands=4)
    assert [row for row, _ in after] == [48, 144, 240, 336]
    assert all(s.ok and abs(s.dy) < 0.1 and abs(s.dx) < 0.1 for _, s in after)
