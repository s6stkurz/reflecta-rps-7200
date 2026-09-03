"""Finding and removing the sensor's bad columns.

Two properties decide whether this works at all, and neither is obvious.

*Channels are measured separately.* This is a trilinear CCD, so a bad element
produces a defect in one colour. Pooling the channels hides exactly the defects
that matter, and scaling all three to fix one tints the column instead of
repairing it.

*Position comes from the sensor, strength from the picture.* A defect's
magnitude moves with exposure, so a flat locates it but cannot say how strong it
is in this scan.
"""

import numpy as np
import pytest

from rps7200.defects import (
    column_defect_sigma,
    destripe,
    dilate_defects,
    find_column_defects,
    flat_defect_sigma,
    resample_reference,
)

WIDTH, HEIGHT = 400, 120
FULL = (0, 0, 10343, 6887)


def picture(seed=0, level=20000, height=HEIGHT, width=WIDTH):
    """Something with real vertical structure, so a detector can be fooled."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:height, 0:width]
    base = level * (0.6 + 0.4 * np.sin(x / 37.0) * np.cos(y / 23.0))
    base += rng.normal(0, level * 0.01, base.shape)
    return np.clip(np.repeat(base[..., None], 3, axis=2), 0, 65535).astype(np.uint16)


def with_stripe(image, column, channel, strength=0.90, width=1):
    out = image.copy()
    out[:, column : column + width, channel] = (
        out[:, column : column + width, channel] * strength
    ).astype(np.uint16)
    return out


# --- detection --------------------------------------------------------------


def test_a_single_channel_stripe_is_found_in_that_channel_only():
    """Flagging all three would tint the column rather than repair it."""
    image = with_stripe(picture(), 150, 1)
    mask = find_column_defects(image, sigma=4.0)
    assert mask[150, 1]
    assert not mask[150, 0] and not mask[150, 2]


def test_a_clean_frame_flags_almost_nothing():
    mask = find_column_defects(picture(seed=5), sigma=4.0)
    assert mask.sum() <= 0.02 * mask.size, f"{mask.sum()} of {mask.size} flagged"


def test_picture_content_scores_far_below_a_real_defect():
    """A defect reads wrong in every row; vertical detail does not.

    The frame is split into bands and the median across them kept, so an edge
    present in only some bands collapses towards zero while a defect survives.
    It does not collapse to *nothing* -- a hard edge in a third of the frame
    still scores a few sigma -- so the property worth holding is the separation,
    not an absolute floor.
    """
    edge = picture(seed=1)
    edge[: HEIGHT // 3, 200:, :] = (edge[: HEIGHT // 3, 200:, :] * 0.5).astype(np.uint16)
    edge_sigma = column_defect_sigma(edge)[197:204, 1].max()

    defect_sigma = column_defect_sigma(with_stripe(picture(seed=1), 150, 1))[150, 1]

    assert defect_sigma > 4 * edge_sigma, (
        f"a real defect scored {defect_sigma:.1f} against picture content's "
        f"{edge_sigma:.1f}; they must not be comparable"
    )


def test_a_stronger_defect_scores_higher():
    weak = column_defect_sigma(with_stripe(picture(), 150, 1, strength=0.97))
    strong = column_defect_sigma(with_stripe(picture(), 150, 1, strength=0.80))
    assert strong[150, 1] > weak[150, 1]


def flat(seed=0, level=30000, noise=150):
    """A flat with the sensor noise a real one has."""
    rng = np.random.default_rng(seed)
    return np.clip(
        rng.normal(level, noise, (40, WIDTH, 3)), 0, 65535
    ).astype(np.uint16)


def test_flat_defect_sigma_measures_per_channel():
    f = flat()
    f[:, 77, 2] = (f[:, 77, 2] * 0.9).astype(np.uint16)
    sigma = flat_defect_sigma(f)
    assert sigma[77, 2] > 4.0
    assert sigma[77, 0] < 4.0 and sigma[77, 1] < 4.0


def test_a_noiseless_flat_reports_nothing():
    """The scale is the flat's own noise, so a synthetic flat with none has no
    scale to measure against. Worth pinning: it makes a noiseless test fixture
    silently report a clean sensor."""
    f = np.full((40, WIDTH, 3), 30000, np.uint16)
    f[:, 77, 2] = 27000
    assert flat_defect_sigma(f).max() == 0.0


def test_a_dead_flat_channel_does_not_divide_by_zero():
    flat = np.zeros((40, WIDTH, 3), np.uint16)
    flat[..., 0] = 30000
    sigma = flat_defect_sigma(flat)       # must not raise
    assert np.isfinite(sigma).all()


# --- dilation ---------------------------------------------------------------


def test_dilation_widens_within_a_channel_never_across():
    mask = np.zeros((10, 3), bool)
    mask[5, 1] = True
    out = dilate_defects(mask, by=2)
    assert out[3:8, 1].all()
    assert not out[:, 0].any() and not out[:, 2].any()


def test_dilation_by_zero_changes_nothing():
    mask = np.zeros((10, 3), bool)
    mask[5, 1] = True
    assert np.array_equal(dilate_defects(mask, by=0), mask)


def test_dilating_an_empty_mask_is_a_no_op():
    mask = np.zeros((10, 3), bool)
    assert not dilate_defects(mask, by=3).any()


# --- repair -----------------------------------------------------------------


def test_destripe_removes_the_stripe_it_is_told_about():
    clean = picture()
    striped = with_stripe(clean, 150, 1, strength=0.90)
    mask = np.zeros((WIDTH, 3), bool)
    mask[150, 1] = True

    before = abs(float(striped[:, 150, 1].mean()) - float(clean[:, 150, 1].mean()))
    fixed = destripe(striped, mask)
    after = abs(float(fixed[:, 150, 1].mean()) - float(clean[:, 150, 1].mean()))
    assert after < 0.25 * before, f"{before:.0f} -> {after:.0f}"


def test_destripe_leaves_the_other_channels_alone():
    """Scaling all three to fix one tints the column."""
    striped = with_stripe(picture(), 150, 1)
    mask = np.zeros((WIDTH, 3), bool)
    mask[150, 1] = True
    fixed = destripe(striped, mask)
    assert np.array_equal(fixed[:, 150, 0], striped[:, 150, 0])
    assert np.array_equal(fixed[:, 150, 2], striped[:, 150, 2])


def test_destripe_leaves_columns_it_was_not_told_about():
    image = picture()
    mask = np.zeros((WIDTH, 3), bool)
    mask[150, 1] = True
    fixed = destripe(image, mask)
    untouched = np.r_[0:140, 160:WIDTH]
    assert np.array_equal(fixed[:, untouched, :], image[:, untouched, :])


def test_a_run_too_wide_to_be_a_sensor_defect_is_left_alone():
    """Interpolating across it would flatten the picture instead."""
    image = picture()
    mask = np.zeros((WIDTH, 3), bool)
    mask[100:250, 1] = True
    assert np.array_equal(destripe(image, mask, max_run=96), image)


def test_a_run_against_the_frame_edge_is_left_alone():
    """That is the film border, and it has no good data on one side."""
    image = picture()
    mask = np.zeros((WIDTH, 3), bool)
    mask[0:3, 1] = True
    fixed = destripe(image, mask, margin=10, dilate=0)
    assert np.array_equal(fixed[:, 0:3, 1], image[:, 0:3, 1])


def test_a_one_dimensional_mask_applies_to_every_channel():
    image = picture()
    mask = np.zeros(WIDTH, bool)
    mask[150] = True
    assert destripe(image, mask).shape == image.shape


def test_an_empty_mask_returns_the_image_unchanged():
    image = picture()
    assert destripe(image, np.zeros((WIDTH, 3), bool)) is image


def test_a_mask_of_the_wrong_width_is_ignored_not_misapplied():
    image = picture()
    assert destripe(image, np.ones((WIDTH + 7, 3), bool)) is image


def test_the_dtype_survives_the_repair():
    image = picture()
    mask = np.zeros((WIDTH, 3), bool)
    mask[150, 1] = True
    assert destripe(image, mask).dtype == image.dtype


# --- column mapping ---------------------------------------------------------


def test_a_reference_maps_onto_a_scan_of_the_same_frame():
    ref = np.linspace(0, 1, 100)[:, None]
    out = resample_reference(ref, FULL, 100, FULL)
    assert np.allclose(out[:, 0], ref[:, 0])


def test_columns_are_matched_by_position_not_by_width_ratio():
    """A half-width scan of the left half must map to the reference's left half."""
    ref = np.linspace(0.0, 1.0, 200)[:, None]
    half = (FULL[0], FULL[1], (FULL[0] + FULL[2]) // 2, FULL[3])
    out = resample_reference(ref, FULL, 100, half)
    assert out[0, 0] == pytest.approx(0.0, abs=0.02)
    assert out[-1, 0] == pytest.approx(0.5, abs=0.02)


def test_every_channel_is_mapped():
    ref = np.stack([np.linspace(0, 1, 50)] * 3, axis=-1)
    assert resample_reference(ref, FULL, 25, FULL).shape == (25, 3)


# --- the film's own edge is not a defect ------------------------------------


def frame_with_film_edge(edge=30, clear=40000, film=16000, height=HEIGHT, width=WIDTH):
    """A prescan's shape: clear aperture, a step, then film.

    The step reads identically in every row, which is exactly the signature a
    column defect has -- so consistency down the frame cannot tell them apart.
    """
    rng = np.random.default_rng(7)
    out = np.empty((height, width, 3), np.float64)
    for c, tint in enumerate((1.00, 0.99, 0.75)):     # the film's own cast
        col = np.where(np.arange(width) < edge, clear, film) * tint
        out[..., c] = col[None, :] + rng.normal(0, 60, (height, width))
    return np.clip(out, 0, 65535).astype(np.uint16)


def test_a_run_spanning_the_film_edge_is_refused():
    """Interpolating across it replaces the edge with a straight line."""
    image = frame_with_film_edge()
    mask = np.zeros((WIDTH, 3), bool)
    mask[11:44, :] = True                              # a run straddling the edge
    assert np.array_equal(destripe(image, mask, dilate=0), image)


def test_the_refusal_does_not_depend_on_reaching_column_zero():
    """The bug this guards: whether a run happened to touch column 0 decided
    whether that channel was flattened, so one channel kept the real edge and
    the others did not -- and the frame ended in a coloured fringe."""
    image = frame_with_film_edge()
    mask = np.zeros((WIDTH, 3), bool)
    mask[0:44, 1] = True       # reaches column 0: the old frame-edge guard fired
    mask[11:44, 0] = True      # does not reach it: the old code flattened this
    mask[6:51, 2] = True       # nor this
    fixed = destripe(image, mask, dilate=0)
    assert np.array_equal(fixed, image), "every channel must be left alone alike"


def test_the_edge_stays_a_step_rather_than_becoming_a_ramp():
    image = frame_with_film_edge(edge=30)
    mask = np.zeros((WIDTH, 3), bool)
    mask[11:44, 0] = True
    prof = np.median(destripe(image, mask, dilate=0)[..., 0], axis=0)
    # A straight interpolation would make consecutive differences equal; a step
    # keeps almost all of the change in one place.
    steps = np.abs(np.diff(prof[11:44]))
    assert steps.max() > 20 * np.median(steps)


def test_no_coloured_fringe_survives_at_the_edge():
    """The property the eye actually sees: no channel departs from the others."""
    image = frame_with_film_edge()
    mask = np.zeros((WIDTH, 3), bool)
    mask[0:44, 1] = True
    mask[11:44, 0] = True
    mask[6:51, 2] = True
    fixed = destripe(image, mask, dilate=0).astype(np.float64)

    # each channel's departure from the mean of the three, signed and relative
    prof = np.median(fixed, axis=0)
    ratio = prof / np.maximum(np.median(image.astype(np.float64), axis=0), 1)
    spread = (ratio - ratio.mean(axis=1, keepdims=True))[:60]
    assert np.abs(spread).max() < 0.01, (
        f"channels were rescaled differently across the edge by up to "
        f"{100 * np.abs(spread).max():.1f}%"
    )


def test_a_real_defect_on_a_smooth_profile_is_still_corrected():
    """The guard must refuse edges without refusing the defects it exists for."""
    clean = picture()
    striped = with_stripe(clean, 150, 1, strength=0.90)
    mask = np.zeros((WIDTH, 3), bool)
    mask[150, 1] = True
    fixed = destripe(striped, mask)
    before = abs(float(striped[:, 150, 1].mean()) - float(clean[:, 150, 1].mean()))
    after = abs(float(fixed[:, 150, 1].mean()) - float(clean[:, 150, 1].mean()))
    assert after < 0.25 * before


def test_a_defect_beside_the_edge_but_not_spanning_it_is_still_corrected():
    image = frame_with_film_edge(edge=30)
    image[:, 60, 1] = (image[:, 60, 1] * 0.9).astype(np.uint16)
    mask = np.zeros((WIDTH, 3), bool)
    mask[60, 1] = True
    fixed = destripe(image, mask, dilate=0)
    assert float(fixed[:, 60, 1].mean()) > float(image[:, 60, 1].mean()) * 1.05
