"""The three comparison files Stefan judges by eye.

CLAUDE.md makes `1_nothing_done.tif`, `2_corrected.tif` and
`3_corrected_inverted.tif` the check after any change to the scan or
correction path, and his read the authoritative one. They were built from an
arbitrary TIFF with `destripe`, a correction nothing delivered runs -- so a
regression in `apply_shading` left all three unchanged. What these hold the
tool to is that the files are the delivered path itself: the entry's decode,
`library.corrected`, and its inversion, as written to disk.
"""

import numpy as np
import pytest

from conftest import load_tool
from rps7200 import library, tiff
from rps7200.direct import CHANNEL_ORDER, SHADING_SKIPPED_EXPLICIT
from rps7200.library import FilmNotes
from rps7200.shading import ShadingReference

pytest.importorskip("PIL")
make_comparison = load_tool("make_comparison")

W, H = 64, 40


def entry(tmp_path, **meta_extra):
    """One filed pass with a reference that visibly changes its pixels."""
    rng = np.random.default_rng(3)
    base = np.linspace(8000, 30000, W)[None, :, None] * np.array([1.0, 0.7, 0.4])
    image = (base + rng.normal(0, 300, (H, W, 3))).clip(0, 65535).astype(np.uint16)
    reference = ShadingReference(
        ref={c: np.linspace(26000, 34000, W) for c in range(3)},
        mean={c: 30000.0 for c in range(3)},
        pixels_per_line=W,
    )
    meta = {
        "resolution_dpi": 600, "channels": 3,
        "channel_order": list(CHANNEL_ORDER[:3]),
        "width": W, "height": H, "depth": 16,
        **meta_extra,
    }
    return library.save(image, meta, root=tmp_path / "lib", film=FilmNotes(),
                        reference=reference)


def run(tmp_path, name):
    return make_comparison.main([
        name, "--library", str(tmp_path / "lib"),
        "--out", str(tmp_path / "out"), "--previews", str(tmp_path / "previews"),
    ])


def written(tmp_path, n):
    return tiff.read(str(tmp_path / "out" / make_comparison.NAMES[n]))


def test_the_files_are_the_decode_and_the_delivered_correction(tmp_path):
    path = entry(tmp_path)
    assert run(tmp_path, path.name) == 0

    raw, _ = library.load(path)
    corrected, info = library.corrected(path)
    assert info["corrected"] == "applied"
    assert not np.array_equal(raw, corrected), "the fixture must correct something"

    assert np.array_equal(written(tmp_path, 0), raw)
    assert np.array_equal(written(tmp_path, 1), corrected)
    assert np.array_equal(written(tmp_path, 2), make_comparison.invert(corrected))


def test_a_change_to_the_shipped_correction_reaches_the_files(tmp_path, monkeypatch):
    """The failure this replaced: a regression in the correction every
    delivered file gets, and three comparison files that could not show it."""
    path = entry(tmp_path)
    assert run(tmp_path, path.name) == 0
    before = written(tmp_path, 1)

    def regressed(image, reference, mask=None):
        return np.zeros_like(image), {"columns": 0, "width": image.shape[1],
                                      "clipped": 0}

    monkeypatch.setattr(library, "apply_shading", regressed)
    assert run(tmp_path, path.name) == 0
    assert not np.array_equal(written(tmp_path, 1), before)
    assert not written(tmp_path, 1).any()


def test_an_entry_is_found_by_its_path_too(tmp_path):
    path = entry(tmp_path)
    code = make_comparison.main([str(path), "--out", str(tmp_path / "out"),
                                 "--previews", str(tmp_path / "previews")])
    assert code == 0
    assert (tmp_path / "out" / "2_corrected.tif").exists()


def test_the_previews_are_written_where_none_existed(tmp_path):
    """`previews/` is gitignored, so a fresh checkout has none, and the tool
    raised there -- after the three TIFFs were already on disk."""
    path = entry(tmp_path)
    assert not (tmp_path / "previews").exists()
    assert run(tmp_path, path.name) == 0
    assert (tmp_path / "previews" / "cmp_before.png").exists()
    assert (tmp_path / "previews" / "cmp_after.png").exists()


def test_an_entry_that_cannot_be_corrected_is_refused(tmp_path):
    """A raw pair would show no difference and read as a correction that does
    nothing. Nothing is written rather than something misleading."""
    path = entry(tmp_path, shading_skipped=SHADING_SKIPPED_EXPLICIT)
    assert run(tmp_path, path.name) == 1
    assert not (tmp_path / "out").exists() or not any((tmp_path / "out").iterdir())


def test_an_unknown_entry_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        run(tmp_path, "20990101T000000Z_nothing_600dpi")


def test_the_colour_measure_leaves_infrared_out():
    """Channel-relative means relative to the colours. An IR plane in the
    mean across channels tinted every column it differed in."""
    rng = np.random.default_rng(7)
    rgb = rng.normal(20000, 50, (30, 60, 3))
    ir = rgb[..., :1].copy()
    ir[:, 30] += 8000                       # a feature in the infrared alone
    four = np.concatenate([rgb, ir], axis=-1).astype(np.uint16)
    assert make_comparison.worst_colour(four) == pytest.approx(
        make_comparison.worst_colour(four[..., :3]))
