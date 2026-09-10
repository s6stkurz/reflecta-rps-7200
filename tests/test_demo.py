"""The stand-in scanner, which the window is tried out against.

Two things matter here beyond "it returns an array". It must decode the
**raw bytes** a library entry stores rather than reading back the TIFF beside
them -- that is what makes trying the window out exercise the path that can
break -- and it must refuse what the hardware refuses, because a stand-in that
accepts a combination the device rejects teaches the window a shape that does
not exist.
"""


import json

import numpy as np
import pytest

from rps7200 import library, tiff
from rps7200.demo import DemoScanner
from rps7200.library import FilmNotes


def entry(root, channels=3, lines=6, width=8, film="negative", dpi=900,
          prescan=None):
    """A library entry with raw bytes, its TIFF, and nothing corrected.

    `prescan` stores a framing pass beside the scan, which is what makes an
    entry one a roll can walk.
    """
    tags = "RGBI"[:channels]
    rows, raw = [], bytearray()
    for _ in range(lines):
        for c in range(channels):
            row = np.arange(width, dtype="<u2") * (c + 1) * 100
            raw += tags[c].encode() * 2 + row.tobytes()
            rows.append(row)
    image = np.stack(
        [np.array(rows[c::channels]) for c in range(channels)], axis=-1
    ).astype(np.uint16)
    meta = {
        "resolution_dpi": dpi, "channels": channels, "film": film,
        "channel_order": list(tags), "width": width, "height": lines,
        "bytes_per_line": width * 2, "depth": 16,
    }
    return library.save(
        image, meta, root=root, film=FilmNotes(frame="demo"),
        prescan=prescan,
        raw=bytes(raw),
        raw_layout={"format": "index", "bytes_per_line": width * 2,
                    "line_stride": width * 2 + 2, "index_header": 2,
                    "width": width, "lines": lines, "channels": channels},
    ), image


def test_it_decodes_the_raw_bytes_not_the_tiff(tmp_path):
    """Proved by corrupting the TIFF: the pixels must still come back right."""
    path, truth = entry(tmp_path)
    tiff_path = path / "scan.tif"
    tiff_path.write_bytes(b"not a tiff at all")

    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    image, _ = s.scan(resolution=900, infrared=False)
    s.close()
    assert np.array_equal(image, truth), (
        "the demo read the TIFF; it must decode raw.bin.gz"
    )


def test_what_it_files_can_be_reconstructed(tmp_path):
    """The bytes it hands back have to be the bytes behind its pixels."""
    entry(tmp_path)
    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    image, meta = s.scan(resolution=900, infrared=False)
    capture = s.capture_record()
    s.close()

    assert capture["raw"] is not None
    out = library.save(image, meta, root=tmp_path / "out",
                       film=FilmNotes(), **capture)
    _, verdict = library.reconstruct(out)
    assert verdict.startswith("identical"), verdict


def test_reshaping_the_image_drops_the_bytes(tmp_path):
    """Asking a four-channel entry for RGB leaves bytes that describe four.

    Filing those would produce an entry whose raw decodes to a different
    picture, which is the one failure the library exists to make impossible.
    """
    entry(tmp_path, channels=4)
    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    image, _ = s.scan(resolution=900, infrared=False)
    capture = s.capture_record()
    s.close()
    assert image.shape[2] == 3
    assert capture["raw"] is None, "bytes describing four channels were kept"
    # The calibration is still worth keeping; only the bytes went.
    assert "reference" in capture


@pytest.mark.parametrize("film", ["bw", "kodachrome"])
def test_it_refuses_infrared_where_the_device_would(tmp_path, film):
    entry(tmp_path)
    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    with pytest.raises(ValueError, match="infrared is blind"):
        s.scan(resolution=900, infrared=True, film=film)
    with pytest.raises(ValueError, match="infrared is blind"):
        list(s.scan_roll(frames=1, infrared=True, film=film))
    s.close()


def test_it_allows_infrared_on_film_that_can_use_it(tmp_path):
    entry(tmp_path, channels=4)
    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    image, _ = s.scan(resolution=900, infrared=True, film="negative")
    s.close()
    assert image.shape[2] == 4


def test_an_entry_with_no_bytes_falls_back_rather_than_failing(tmp_path):
    """A library full of older entries must still drive the window."""
    path, _ = entry(tmp_path)
    (path / "raw.bin.gz").unlink()
    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    image, _ = s.scan(resolution=900, infrared=False)
    s.close()
    assert image.ndim == 3 and image.size > 0


def test_it_shows_the_film_that_was_asked_for(tmp_path):
    """Setting the film to black and white and being shown a colour negative
    through its green channel demonstrates nothing.

    The library has real black and white scans; the demo has to reach for one.
    """
    entry(tmp_path, film="negative", dpi=1800)
    entry(tmp_path, film="bw", dpi=1800)

    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    for film in ("negative", "bw"):
        source = s._source_for(film)
        assert source is not None, film
        record = json.loads((source / "scan.json").read_text())
        assert record["scan"]["film"] == film, (
            f"asked for {film}, was shown {record['scan']['film']}"
        )
    s.close()


def test_the_prescan_and_the_scan_are_the_same_picture(tmp_path):
    """Two different pictures either side of a framing pass would make the
    frame the operator lined up meaningless.

    They are normally at different resolutions, so the check is that they come
    from one entry and agree once sized alike -- not that the arrays match.
    """
    entry(tmp_path, film="negative", dpi=900)
    entry(tmp_path, film="bw", dpi=900)
    entry(tmp_path, film="bw", dpi=1800, lines=12, width=16)

    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    chosen = s._source_for("bw")
    pre, _ = s.prescan(resolution=300, film="bw")
    scan, meta = s.scan(resolution=900, infrared=False, film="bw")
    assert s._source_for("bw") == chosen, "the picture changed under us"
    assert meta["film"] == "bw"
    # Same resolution as the prescan, so the two can be compared at all.
    same = s.scan(resolution=300, infrared=False, film="bw")[0]
    s.close()
    assert np.array_equal(pre[..., 1], same[..., 1]), (
        "the prescan and the scan are different photographs"
    )


def test_a_film_with_nothing_stored_still_drives_the_window(tmp_path):
    """A library with no slide in it must not stop the demo working."""
    entry(tmp_path, film="negative")
    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    image, meta = s.scan(resolution=900, infrared=False, film="positive")
    s.close()
    assert image.ndim == 3 and image.size > 0
    assert meta["film"] == "positive"


def test_a_pass_comes_back_at_the_resolution_it_asked_for(tmp_path):
    """A pass reported as 900 dpi that hands back 1800 dpi pixels is a
    stand-in lying about the one thing the window sizes everything from -- the
    estimate, the zoom and the crop are then all off by a factor.
    """
    entry(tmp_path, film="bw", dpi=900, lines=8, width=12)
    entry(tmp_path, film="bw", dpi=1800, lines=16, width=24)

    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    for dpi, shape in ((900, (8, 12)), (1800, (16, 24))):
        image, _ = s.scan(resolution=dpi, infrared=False, film="bw")
        assert image.shape[:2] == shape, f"{dpi} dpi gave {image.shape}"
    s.close()


def test_a_resolution_with_nothing_stored_is_resized_to_fit(tmp_path):
    """The nearest entry, scaled, rather than the wrong shape."""
    entry(tmp_path, film="bw", dpi=900, lines=8, width=12)

    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    image, _ = s.scan(resolution=1800, infrared=False, film="bw")
    capture = s.capture_record()
    s.close()
    assert image.shape[:2] == (16, 24), image.shape
    assert capture["raw"] is None, (
        "bytes from a 900 dpi pass were kept against resized 1800 dpi pixels"
    )


def test_a_prescan_is_always_three_channels(tmp_path):
    """The real one sets passes=0x80 and 8-bit, so a four-channel prescan is a
    shape the window would never see from the device."""
    entry(tmp_path, film="bw", channels=4)
    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    image, _ = s.prescan(resolution=900, film="bw")
    s.close()
    assert image.ndim == 3 and image.shape[2] == 3, image.shape


def test_the_shape_comes_from_the_library_not_from_a_ratio(tmp_path):
    """The widths the device reports -- 428, 860, 1292, 2584, 5172 for 300 to
    3600 dpi -- are not a constant multiple of the resolution. It rounds its
    own way, so the shape is looked up rather than derived, and any film's
    entry at that resolution will do: the frame is the same size whatever is
    in it.
    """
    entry(tmp_path, film="bw", dpi=1800, lines=20, width=30)
    entry(tmp_path, film="negative", dpi=900, lines=9, width=14)  # odd on purpose

    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    assert s._shape_for(900) == (9, 14)
    image, _ = s.scan(resolution=900, infrared=False, film="bw")
    s.close()
    assert image.shape[:2] == (9, 14), (
        f"a ratio would have given {(10, 15)}, the device gives (9, 14)"
    )


# -- what a roll walks, which is what a contact sheet shows -----------------


def _walkable(root, name, seed, film="negative"):
    """An entry with a prescan in it, of a known film."""
    rng = np.random.default_rng(seed)
    picture = (rng.random((48, 72, 3)) * 40000 + seed * 900).astype(np.uint16)
    return entry(root, channels=3, lines=6, width=8, film=film,
                 prescan=picture)[0]


def test_a_roll_walks_a_different_picture_every_frame(tmp_path):
    """A roll used to hand back one picture for every frame. On a contact sheet
    that is six identical thumbnails, where a wrong pick and a right one look
    exactly alike."""
    for n in range(4):
        _walkable(tmp_path, f"e{n}", seed=n + 1)
    with DemoScanner(root=tmp_path, speed=100000.0) as s:
        frames = list(s.scan_roll(frames=4, dry_run=True))
    assert len({f.prescan.tobytes() for f in frames}) == 4


def test_the_frames_are_measured_not_invented(tmp_path):
    """The captions under a contact sheet have to come from the pictures, or
    they say the same thing about every frame and mean nothing."""
    for n in range(4):
        _walkable(tmp_path, f"e{n}", seed=n + 1)
    with DemoScanner(root=tmp_path, speed=100000.0) as s:
        frames = list(s.scan_roll(frames=4, dry_run=True))
    assert len({f.registration["contrast"] for f in frames}) > 1
    for f in frames:
        assert "offset_mm" in f.registration and "shortfall_mm" in f.registration


def test_each_frame_shows_the_entry_it_walked_to(tmp_path):
    """Frames differ from each other; a frame does not differ from itself. The
    prescan and the scan of one frame both come from that frame's entry, which
    is what the fixed pair does for a single pass."""
    for n in range(3):
        _walkable(tmp_path, f"e{n}", seed=n + 1)
    with DemoScanner(root=tmp_path, speed=100000.0) as s:
        walked = s._strip_for("negative")
        frames = list(s.scan_roll(frames=3, infrared=False))
        # Dropped when the roll ends, or every later single pass would be
        # served the last frame of the last roll instead of its own film.
        assert s._frame_source is None
    for f, source in zip(frames, walked, strict=True):
        stored = tiff.read(str(source / "prescan.tif"))
        assert np.array_equal(f.prescan, stored)
        assert f.image is not None


def test_a_roll_walks_the_film_it_was_asked_for(tmp_path):
    """Being shown colour negatives for a black and white roll is no more a
    demonstration than it is for a single pass."""
    for n in range(2):
        _walkable(tmp_path, f"c{n}", seed=n + 1, film="negative")
    for n in range(2):
        _walkable(tmp_path, f"b{n}", seed=n + 10, film="bw")
    with DemoScanner(root=tmp_path, speed=100000.0) as s:
        walked = s._strip_for("bw")
    films = set()
    for path in walked:
        films.add((json.loads((path / "scan.json").read_text())
                   ["scan"]).get("film"))
    assert films == {"bw"}


def test_the_choice_a_sheet_makes_reaches_the_roll(tmp_path):
    """`only` is what the contact sheet's ticks become."""
    for n in range(5):
        _walkable(tmp_path, f"e{n}", seed=n + 1)
    with DemoScanner(root=tmp_path, speed=100000.0) as s:
        frames = list(s.scan_roll(frames=5, only=(0, 3), dry_run=True))
    assert [f.index for f in frames] == [0, 3]


def test_a_library_with_no_prescans_still_walks_a_strip(tmp_path):
    """Test cards, seeded per frame, so the sheet is readable on a machine with
    an empty library rather than being one picture six times."""
    with DemoScanner(root=tmp_path, speed=100000.0) as s:
        frames = list(s.scan_roll(frames=3, dry_run=True))
    assert len({f.prescan.tobytes() for f in frames}) == 3
