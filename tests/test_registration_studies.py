"""The registration studies read a walk the way the window does.

`tools/registration_margin.py` is the tool named for re-fitting
`CONFIDENCE_FLOOR`, and `tools/roll_registration_study.py` reads the same
cohort. Both read `rolls/*/prescan*.tif` as the files lay on disk: turned or
mirrored the way the operator viewed them, so x-axis measurements ran along
the frame's height or with left and right swapped -- and with each frame's
`prescanNN-before.tif` counted as a frame of its own.
"""
from __future__ import annotations

import json

import numpy as np
from conftest import load_tool

from rps7200 import preview, tiff
from rps7200.session import NUMBERING

margin = load_tool("registration_margin")


def picture(seed: int) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 255, (20, 32, 3),
                                                dtype=np.uint8)


def test_a_turned_walk_is_read_upright_and_its_before_pictures_left_out(tmp_path):
    folder = tmp_path / "roll"
    folder.mkdir()
    frames = {1: picture(1), 2: picture(2)}
    records = []
    for number, image in frames.items():
        # Written as the window writes a walk made turned 90 and mirrored.
        name = f"prescan{number:02d}.tif"
        tiff.write(str(folder / name), preview.orient(image, 90, True))
        tiff.write(str(folder / f"prescan{number:02d}-before.tif"),
                   preview.orient(picture(9), 90, True))
        records.append({"number": number, "prescan": name,
                        "prescan_rotation": 90, "prescan_flipped": True})
    (folder / "survey.json").write_text(json.dumps(
        {"numbering": NUMBERING, "frames": records}), encoding="utf-8")

    got = margin.cohort(folder, 300)
    assert len(got) == 2, [name for name, _ in got]
    for (_, image), original in zip(got, frames.values()):
        assert image.shape == original.shape
        assert np.array_equal(image, original.astype(np.float64))


def test_the_library_cohort_takes_only_corrected_passes(tmp_path):
    """It took every entry at the dpi through `library.corrected` and never
    asked what came back, so a deliberately raw ladder was compared against
    corrected frames -- measuring the shading, as its own comment warns."""
    from rps7200 import library
    from rps7200.direct import SHADING_SKIPPED_EXPLICIT
    from rps7200.shading import ShadingReference

    reference = ShadingReference(ref={c: np.full(32, 40000.0) for c in range(3)},
                                 mean={c: 40000.0 for c in range(3)},
                                 pixels_per_line=32)
    kept = library.save(picture(1), {"resolution_dpi": 300, "channels": 3},
                        root=tmp_path, reference=reference, ccd_mask=bytes(32))
    library.save(picture(2), {"resolution_dpi": 300, "channels": 3,
                              "shading_skipped": SHADING_SKIPPED_EXPLICIT},
                 root=tmp_path)
    library.save(picture(3), {"resolution_dpi": 300, "channels": 3,
                              "demo": True},
                 root=tmp_path, reference=reference, ccd_mask=bytes(32))
    assert [name for name, _ in margin.cohort(tmp_path, 300)] == [kept.name]


def test_a_folder_with_no_manifest_still_leaves_the_before_pictures_out(tmp_path):
    folder = tmp_path / "old"
    folder.mkdir()
    tiff.write(str(folder / "prescan01.tif"), picture(1))
    tiff.write(str(folder / "prescan01-before.tif"), picture(2))
    assert [name for name, _ in margin.cohort(folder, 300)] == ["prescan01.tif"]
