"""The rotation method, checked against fields it is supposed to recover.

Every test here is synthetic and offline: a known field is multiplied into a
random target, the four insertions are generated, and the solver is asked to
give the field back. That is the only way to test this honestly -- on real
scans there is no ground truth, which is the whole reason the study exists.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pytest

from rps7200 import uniformity as un
from rps7200.uniformity import AS_IS, MIRROR_X, MIRROR_Y, ROT180

#: Monotonic enough that only the staircase test can reject it.
ORIENTATION_MONOTONIC_ENOUGH = 0.97


# -- fixtures ---------------------------------------------------------------


def coords(h: int, w: int) -> tuple[np.ndarray, np.ndarray]:
    """Normalised (u, v) grids, matching Field.evaluate."""
    v, u = np.meshgrid(np.linspace(-1, 1, h), np.linspace(-1, 1, w), indexing="ij")
    return u, v


def known_field(h: int, w: int, pp=0.0, mp=0.0, pm=0.0, mm=0.0) -> np.ndarray:
    """A log-space field with a chosen amount of each parity component.

    ``pp`` is a centred radial bowl -- the shape a lens vignette actually takes,
    and the one rotation cannot see. The others are the lowest-order surface
    with the right parity in each axis.
    """
    u, v = coords(h, w)
    return (
        pp * (u**2 + v**2)      # even, even
        + mp * u                 # odd in x
        + pm * v                 # odd in y
        + mm * u * v             # odd in both
    )


def patchy_target(h: int, w: int, seed: int = 0, patch: int = 30) -> np.ndarray:
    """A target of flat patches with hard borders, like an IT8 grid.

    Flat interiors are what the block filter is meant to keep and hard borders
    are what it is meant to throw away, so a smooth random field would not
    exercise it at all.

    ``patch`` is deliberately not a multiple of the block size. A patch grid
    aligned to the block grid would let every block tile neatly inside a patch,
    none would straddle a border, and the rejection this fixture exists to
    exercise would never fire.
    """
    rng = np.random.default_rng(seed)
    ny, nx = h // patch + 1, w // patch + 1
    tiles = rng.uniform(0.15, 1.0, size=(ny, nx, 3))
    return np.repeat(np.repeat(tiles, patch, axis=0), patch, axis=1)[:h, :w]


def render(field: np.ndarray, target: np.ndarray, scale: float = 30000.0) -> np.ndarray:
    """Linear 16-bit pixels for a field and a target."""
    out = target * np.exp(field)[:, :, None] * scale
    return np.clip(out, 0, 65535).astype(np.uint16)


def four_passes(h, w, seed=0, patch=30, **components) -> dict[str, np.ndarray]:
    """The same target imaged through the same field, inserted four ways.

    The target is transformed, the field is not -- that is the physics. The
    field belongs to the machine and stays put while the film moves.
    """
    s = known_field(h, w, **components)
    target = patchy_target(h, w, seed=seed, patch=patch)
    return {
        name: render(s, un.apply_orientation(target, name)) for name in un.ORIENTATIONS
    }


def difference(passes: dict[str, np.ndarray], orientation: str, dpi: int = 600):
    """Un-rotate a pass back to target coordinates and difference it against as-is.

    Each orientation is an involution, so applying it again is the un-rotate.
    """
    a = un.apply_orientation(passes[orientation], orientation)
    b = passes[AS_IS]
    samples = un.block_ratios(a, b, dpi=dpi)
    return un.fit_field(samples).evaluate(*a.shape[:2])


# -- orientation, read off the target ---------------------------------------


def greyscale_target(h: int, w: int, edge: str, rising: bool,
                     steps: int = 24, smooth: bool = False) -> np.ndarray:
    """A target with a greyscale row along one edge.

    The row is a **staircase** of discrete plateaus, because that is what an
    IT8's GS row is -- 24 patches, not a gradient. ``smooth=True`` replaces it
    with a continuous ramp, which is what a photograph's sky looks like and
    which the detector has to refuse.
    """
    rng = np.random.default_rng(7)
    img = rng.uniform(0.3, 0.9, size=(h, w, 3))
    if smooth:
        ramp = np.linspace(0.05, 1.0, w)
    else:
        ramp = np.repeat(np.linspace(0.05, 1.0, steps), w // steps + 1)[:w]
    if not rising:
        ramp = ramp[::-1]
    # Inset from the frame edge, as a real target is: measured on the actual
    # IT8, the greyscale row sits at y=459 of 574 with target border below it,
    # not flush against the scan boundary. A row hard against the edge cannot
    # be windowed cleanly and reads a much weaker margin.
    band = max(2, h // 12)
    inset = max(2, h // 14)
    rows = (slice(h - inset - band, h - inset) if edge == "bottom"
            else slice(inset, inset + band))
    img[rows, :, :] = ramp[None, :, None]
    return (img * 40000).astype(np.uint16)


@pytest.mark.parametrize("edge", ["top", "bottom"])
@pytest.mark.parametrize("rising", [True, False])
def test_orientation_signature_reads_the_greyscale_row(edge, rising):
    sig = un.orientation_signature(greyscale_target(240, 320, edge, rising))
    assert sig.edge == edge
    assert sig.rising is rising
    assert sig.confident


def test_the_four_insertions_are_distinguishable():
    """Every insertion must map to a different name, or the study is meaningless.

    A collision here means two orientations are indistinguishable from the
    pixels, which is exactly the failure that swaps d_fx with d_fy and returns
    a confident wrong decomposition.
    """
    base = greyscale_target(240, 320, "bottom", True)
    reference = un.orientation_signature(base)
    seen = {}
    for name in un.ORIENTATIONS:
        sig = un.orientation_signature(un.apply_orientation(base, name))
        seen[name] = un.classify_relative(sig, reference)
    assert sorted(seen.values()) == sorted(un.ORIENTATIONS)
    assert seen[AS_IS] == AS_IS


def test_a_featureless_image_is_not_confident():
    """Clear film has no greyscale row, so it must decline rather than guess."""
    rng = np.random.default_rng(3)
    flat = (rng.normal(30000, 40, size=(240, 320, 3))).astype(np.uint16)
    assert not un.orientation_signature(flat).confident


def test_a_smooth_gradient_is_refused():
    """A picture's gradient is monotonic too, and must not be mistaken for GS.

    This is not hypothetical. Run against four real film negatives in this
    repo's library -- photographs, with no greyscale row anywhere in them --
    the most monotonic band still scored 0.81 to 0.93, so monotonicity alone
    would have confidently assigned two of them an orientation. What separates
    them is that a greyscale row is a staircase and a gradient is not.
    """
    smooth = greyscale_target(240, 320, "bottom", True, smooth=True)
    sig = un.orientation_signature(smooth)
    assert sig.confidence > ORIENTATION_MONOTONIC_ENOUGH   # it *is* monotonic
    assert sig.step_fraction > un.MAX_STEP_FRACTION        # but it is not stepped
    assert not sig.confident

    stepped = greyscale_target(240, 320, "bottom", True)
    assert un.orientation_signature(stepped).confident


def test_channel_report_flags_a_dead_plane():
    img = greyscale_target(240, 320, "bottom", True)
    sig = un.orientation_signature(img)
    assert all(un.channel_report(img, sig)["live"])

    img[:, :, 2] = 100
    assert un.channel_report(img, sig)["live"] == [True, True, False]


def greyscale_it8(h: int, w: int, seed: int = 0, patch: int = 26) -> np.ndarray:
    """A patch grid with a 24-step greyscale row inset from the bottom edge.

    Close enough to an IT8 for both halves of the machinery: flat patch
    interiors for the block filter, and a staircase ramp for the orientation
    check.

    ``patch`` defaults so that ~22 patches span the width, as on a real IT8.
    That matters more than it looks: with only ~10 patches across, a row of
    random values has a real chance of being monotonic by luck, and a spurious
    ramp then competes with the greyscale row for the orientation margin. With
    22 the chance correlation falls to ~1/sqrt(21) and the real row wins
    cleanly -- which is what is observed on the actual target.
    """
    rng = np.random.default_rng(seed)
    ny, nx = h // patch + 1, w // patch + 1
    tiles = rng.uniform(0.15, 1.0, size=(ny, nx, 3))

    # Interleave each row high-low-high-low so no colour row is monotonic by
    # accident. A real IT8's colour rows are not ramps; only the greyscale row
    # is, and the orientation margin depends on nothing else competing with it.
    # Left random, a row of ~20 values is monotonic-looking often enough to
    # swamp the real row in some orientations but not others -- which shows up
    # as a margin that depends on which way the target was inserted.
    for r in range(ny):
        for c in range(3):
            ordered = np.sort(tiles[r, :, c])
            tiles[r, :, c] = np.reshape(
                np.stack([ordered[::-1][: (nx + 1) // 2],
                          np.pad(ordered[: nx // 2],
                                 (0, (nx + 1) // 2 - nx // 2))], axis=1),
                -1)[:nx]

    img = np.repeat(np.repeat(tiles, patch, axis=0), patch, axis=1)[:h, :w]
    ramp = np.repeat(np.linspace(0.05, 1.0, 24), w // 24 + 1)[:w]
    band = max(4, h // 12)
    inset = max(2, h // 14)                 # as on the real target -- see above
    img[h - inset - band : h - inset, :, :] = ramp[None, :, None]
    return img


# -- registration -----------------------------------------------------------


def test_register_recovers_a_known_shift():
    """The scanner has been seen to shift 16 columns between identical passes.

    Two overlapping crops of one larger scene, which is what a real shift is --
    not ``np.roll``. A circular roll wraps content around the frame edge, and
    the Hann window in :func:`register` is applied after the shift, so a rolled
    pair is not the transformation the correlator is built for.
    """
    rng = np.random.default_rng(1)
    scene = (rng.uniform(0.2, 1.0, size=(40, 52)) * 40000).astype(np.uint16)
    scene = np.repeat(np.repeat(scene, 8, 0), 8, 1)          # 320 x 416, blocky

    a = scene[32:232, 32:292]
    b = scene[27:227, 48:308]          # 5 rows up, 16 columns right

    dy, dx, confidence = un.register(a, b)
    assert (abs(dy), abs(dx)) == (5, 16)
    assert confidence > 3.0

    # The contract that actually matters, and the one a sign slip would break:
    # register and align together must bring the two frames into agreement.
    ca, cb = un.align(a, b, dy, dx)
    assert ca.shape == cb.shape
    assert np.array_equal(ca, cb)


def test_align_crops_to_the_overlap():
    a = np.zeros((100, 120))
    b = np.zeros((100, 120))
    ca, cb = un.align(a, b, 4, -6)
    assert ca.shape == cb.shape == (96, 114)


# -- the target-cancelling core ---------------------------------------------


def test_block_ratios_reject_patch_borders():
    """Blocks straddling a border must be dropped, not averaged.

    A border under even a one-pixel misalignment produces a ratio no amount of
    later smoothing would survive, which is why the filter is on within-block
    variance rather than on the fit residual.
    """
    h, w = 240, 240
    image = render(np.zeros((h, w)), patchy_target(h, w, patch=30))
    samples = un.block_ratios(image, image, dpi=600)

    # 12px blocks on a 30px patch grid: interiors survive, straddlers do not.
    assert 0.5 < samples.fraction_kept < 0.8
    assert np.allclose(samples.values, 0.0, atol=1e-9)

    # And an aligned grid is the case that would silently keep everything, which
    # is why the fixture's patch size is not a multiple of the block size.
    aligned = render(np.zeros((h, w)), patchy_target(h, w, patch=24))
    assert un.block_ratios(aligned, aligned, dpi=600).fraction_kept == 1.0


def test_the_target_cancels_regardless_of_what_it_is():
    """The claim the whole method rests on: t drops out of the difference."""
    h, w = 240, 240
    s = known_field(h, w, mp=0.06)
    for seed in (0, 1, 2):
        target = patchy_target(h, w, seed=seed)
        a = render(s, target)
        b = render(np.zeros((h, w)), target)
        fitted = un.fit_field(un.block_ratios(a, b, dpi=600)).evaluate(h, w)
        # Recovered field must be s, whatever the target was.
        assert np.abs(fitted[:, :, 0] - s).max() < 0.01


def test_a_narrow_unshaded_edge_is_rejected_without_help():
    """The real 600 dpi case: 2 unshaded columns of 862, inside a 12px block.

    Measured here: the flatness filter alone drops every block containing them,
    so the artefact contributes nothing at all. `trailing` is defence in depth
    for this case, not the thing that saves it.
    """
    h, w = 240, 240
    image = render(np.zeros((h, w)), patchy_target(h, w))
    broken = image.copy()
    broken[:, -2:] = 60000

    samples = un.block_ratios(image, broken, dpi=600)
    assert np.abs(samples.values).max() < 1e-9
    assert samples.kept < samples.total          # the edge blocks were dropped


def test_a_wide_unshaded_edge_needs_trailing():
    """Wider than a block, and a whole block sits inside it: flat, and wrong.

    This is the case the flatness filter cannot see, because the artefact is
    uniform. It is why `trailing` exists.
    """
    h, w = 240, 240
    image = render(np.zeros((h, w)), patchy_target(h, w))
    broken = image.copy()
    broken[:, -24:] = (broken[:, -24:] * 1.3).clip(0, 65535).astype(np.uint16)

    dirty = un.block_ratios(image, broken, dpi=600)
    clean = un.block_ratios(image, broken, dpi=600, trailing=24)
    assert np.abs(dirty.values).max() > 0.2
    assert np.abs(clean.values).max() < 1e-9


# -- symmetry ---------------------------------------------------------------


def test_decompose_partitions_exactly():
    rng = np.random.default_rng(5)
    surface = rng.normal(size=(64, 80))
    parts = un.decompose(surface)
    assert np.allclose(sum(parts.values()), surface)
    assert np.allclose(parts["mp"], -parts["mp"][:, ::-1])
    assert np.allclose(parts["pm"], -parts["pm"][::-1])
    assert np.allclose(parts["pp"], parts["pp"][:, ::-1])


def test_solve_components_recovers_the_three_odd_parts():
    """The closed form, against a field built from known components."""
    h, w = 96, 120
    truth = {"mp": 0.05, "pm": 0.03, "mm": 0.02}
    s = known_field(h, w, **truth)
    parts = un.decompose(s)

    d = {
        MIRROR_X: un.apply_orientation(s, MIRROR_X) - s,
        MIRROR_Y: un.apply_orientation(s, MIRROR_Y) - s,
        ROT180: un.apply_orientation(s, ROT180) - s,
    }
    solved = un.solve_components(d[MIRROR_X], d[MIRROR_Y], d[ROT180])

    for name in ("mp", "pm", "mm"):
        assert np.abs(solved[name] - parts[name]).max() < 1e-9


def test_the_even_component_is_reported_unmeasured_not_zero():
    """The blind spot, pinned in place.

    A centred radial vignette is even under every operation in the group, so it
    cancels in each difference exactly as the target does. Returning zero would
    report the blind spot as a measurement -- the most damaging thing this
    module could do, because it would read as "no vignette".
    """
    h, w = 96, 120
    s = known_field(h, w, pp=0.20)               # a large, purely even bowl

    d = {
        o: un.apply_orientation(s, o) - s for o in (MIRROR_X, MIRROR_Y, ROT180)
    }
    # Every difference is identically zero: the field is invisible to rotation.
    for name, diff in d.items():
        assert np.abs(diff).max() < 1e-12, name

    solved = un.solve_components(d[MIRROR_X], d[MIRROR_Y], d[ROT180])
    assert solved["pp"] is None
    for name in ("mp", "pm", "mm"):
        assert np.abs(solved[name]).max() < 1e-12


def test_parity_residual_is_zero_for_a_clean_difference():
    h, w = 96, 120
    s = known_field(h, w, mp=0.05, pm=0.03, mm=0.02)
    for orientation in (MIRROR_X, MIRROR_Y, ROT180):
        diff = un.apply_orientation(s, orientation) - s
        assert un.parity_residual(diff, orientation) < 1e-9


def test_parity_residual_catches_a_swapped_label():
    """Mislabelling turned-over as turned-over-180 must show up as wrong parity."""
    h, w = 96, 120
    s = known_field(h, w, mp=0.05, pm=0.03)
    d_mirror_x = un.apply_orientation(s, MIRROR_X) - s
    assert un.parity_residual(d_mirror_x, MIRROR_X) < 1e-9
    assert un.parity_residual(d_mirror_x, MIRROR_Y) > 0.5


# -- end to end -------------------------------------------------------------


def test_full_recovery_from_four_synthetic_passes():
    """The whole chain: four insertions in, three components out."""
    h, w = 288, 288
    truth = {"mp": 0.05, "pm": 0.03, "mm": 0.02}
    passes = four_passes(h, w, **truth, pp=0.10)   # plus an invisible bowl
    expected = un.decompose(known_field(h, w, **truth))

    solved = un.solve_components(
        difference(passes, MIRROR_X),
        difference(passes, MIRROR_Y),
        difference(passes, ROT180),
    )
    for name in ("mp", "pm", "mm"):
        got = solved[name][:, :, 0]
        assert np.abs(got - expected[name]).max() < 0.01, name
    assert solved["pp"] is None


def test_a_uniform_scanner_returns_nothing():
    """No field in, no field out -- the false-positive check."""
    h, w = 288, 288
    passes = four_passes(h, w)
    for orientation in (MIRROR_X, MIRROR_Y, ROT180):
        got = difference(passes, orientation)
        assert un.peak_to_peak_percent(got) < 0.5


def test_the_fit_is_resolution_independent():
    """600 and 1800 dpi must describe the same field, so passes 10-11 compare."""
    truth = {"mp": 0.05, "pm": 0.03}
    # A patch is a fixed physical size, so it covers 3x the pixels at 3x the dpi.
    low = four_passes(240, 240, patch=30, **truth)
    high = four_passes(720, 720, patch=90, **truth)

    a = un.fit_field(un.block_ratios(
        un.apply_orientation(low[ROT180], ROT180), low[AS_IS], dpi=600))
    b = un.fit_field(un.block_ratios(
        un.apply_orientation(high[ROT180], ROT180), high[AS_IS], dpi=1800))

    assert np.abs(a.evaluate(120, 120) - b.evaluate(120, 120)).max() < 0.01


def test_peak_to_peak_is_a_brightness_ratio():
    surface = np.array([[0.0, np.log(1.25)]])
    assert un.peak_to_peak_percent(surface) == pytest.approx(25.0)


def test_log_image_floors_at_one():
    """Zero counts happen in dense film; they must not become -inf."""
    assert np.isfinite(un.log_image(np.zeros((4, 4), dtype=np.uint16))).all()


# -- the tool, end to end ---------------------------------------------------


def build_entry(root, name, image, orientation, dpi=600, tag="vignette-study"):
    """Write a library entry the way tools/uniformity.py expects to read one."""
    import gzip
    from rps7200 import tiff

    entry = root / name
    entry.mkdir(parents=True)
    h, w, c = image.shape
    bpl = w * 2
    blob = bytearray()
    for y in range(h):
        for ci, letter in enumerate("RGBI"[:c]):
            blob += bytes([ord(letter), ord(letter)])
            blob += image[y, :, ci].astype("<u2").tobytes()
    with gzip.open(entry / "raw.bin.gz", "wb") as fh:
        fh.write(bytes(blob))
    tiff.write(str(entry / "scan.tif"), image, resolution=dpi)
    (entry / "scan.json").write_text(json.dumps({
        "id": name,
        "tags": [tag],
        "film": {"stock": "IT8", "subject": f"IT8 {orientation}", "frame": "", "notes": ""},
        "scan": {"resolution_dpi": dpi, "channels": c, "width": w, "height": h,
                 "depth": 16, "frame": [0, 0, 10343, 6887]},
        "raw": {"file": "raw.bin.gz", "layout": {
            "format": "index", "bytes_per_line": bpl, "line_stride": bpl + 2,
            "index_header": 2, "width": w, "lines": h, "channels": c,
            "byte_order": "little", "lines_received": h * c}},
        "calibration": {},
    }, indent=2))
    return entry


def test_analyse_end_to_end(tmp_path, capsys, monkeypatch):
    """The whole tool: entries in, verdict out, with no scanner attached."""
    import importlib
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parent.parent / "tools"))
    tool = importlib.import_module("uniformity")

    h = w = 576                     # 22 patches of 26 px, as on a real IT8
    truth = {"mp": 0.06, "pm": 0.04}
    s_field = known_field(h, w, **truth)

    root = tmp_path / "library"
    for i, name in enumerate(un.ORIENTATIONS):
        img = render(s_field, un.apply_orientation(greyscale_it8(h, w), name))
        build_entry(root, f"2026_{i}_{name}", img, name)
    # a second as-is pass, so there is a repeat floor to judge against
    build_entry(root, "2026_9_repeat",
                render(s_field, greyscale_it8(h, w)), AS_IS)

    args = argparse.Namespace(library=str(root), tag="vignette-study", out=None)
    assert tool.cmd_analyse(args) == 0

    out = capsys.readouterr().out
    assert "orientation, re-derived from the greyscale row" in out
    assert "MISMATCH" not in out
    assert "NOT MEASURABLE by rotation" in out
    assert "a field is present above the floor" in out


def test_analyse_refuses_when_the_set_is_not_a_permutation(tmp_path, capsys, monkeypatch):
    """A wrong orientation must stop the run, not quietly produce an answer."""
    import importlib
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parent.parent / "tools"))
    tool = importlib.import_module("uniformity")

    h = w = 576
    root = tmp_path / "library"
    base = greyscale_it8(h, w)
    for name in un.ORIENTATIONS:
        # Label the 180 pass as a mirror: now three passes claim to be mirrors
        # and none claims 180, so the set cannot be a permutation.
        claimed = MIRROR_X if name == ROT180 else name
        build_entry(root, f"2026_{name}",
                    render(np.zeros((h, w)), un.apply_orientation(base, name)),
                    claimed)

    args = argparse.Namespace(library=str(root), tag="vignette-study", out=None)
    assert tool.cmd_analyse(args) == 1
    out = capsys.readouterr()
    assert "refusing to decompose" in out.err
    assert "not as the two mirrors" in out.err


def test_analyse_refuses_when_a_repeat_pass_moved(tmp_path, capsys, monkeypatch):
    """A repeat pass that is not as-is means handling disturbed the target.

    The repeat passes set the threshold every component is judged against, so a
    repeat that moved silently inflates the floor and hides a real field.
    """
    import importlib
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parent.parent / "tools"))
    tool = importlib.import_module("uniformity")

    h = w = 576
    root = tmp_path / "library"
    base = greyscale_it8(h, w)
    for name in un.ORIENTATIONS:
        build_entry(root, f"2026_{name}",
                    render(np.zeros((h, w)), un.apply_orientation(base, name)), name)
    # A "repeat" that is actually rotated.
    build_entry(root, "2026_bad_repeat",
                render(np.zeros((h, w)), un.apply_orientation(base, ROT180)), AS_IS)

    args = argparse.Namespace(library=str(root), tag="vignette-study", out=None)
    assert tool.cmd_analyse(args) == 1
    err = capsys.readouterr().err
    assert "claimed as-is but reads as" in err
    assert "the target moved" in err


def test_a_flip_about_the_other_axis_is_accepted(tmp_path, capsys, monkeypatch):
    """Turning a slide over can give either mirror, and both are valid.

    Which mirror a front-to-back flip produces depends on whether it was about
    the vertical or the horizontal axis. The label cannot know, so the set is
    required to contain *the two mirrors* and the assignment is taken from the
    image. Observed on the real target: the operator's flip produced the
    top-bottom mirror while the label assumed left-right.
    """
    import importlib
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parent.parent / "tools"))
    tool = importlib.import_module("uniformity")

    h = w = 576
    root = tmp_path / "library"
    base = greyscale_it8(h, w)
    swap = {MIRROR_X: MIRROR_Y, MIRROR_Y: MIRROR_X}
    for name in un.ORIENTATIONS:
        build_entry(root, f"2026_{name}",
                    render(known_field(h, w, mp=0.05), un.apply_orientation(base, name)),
                    swap.get(name, name))          # mirror labels swapped
    build_entry(root, "2026_repeat",
                render(known_field(h, w, mp=0.05), base), AS_IS)

    args = argparse.Namespace(library=str(root), tag="vignette-study", out=None)
    assert tool.cmd_analyse(args) == 0             # accepted, not refused
    out = capsys.readouterr().out
    assert "produced the *other* mirror" in out
