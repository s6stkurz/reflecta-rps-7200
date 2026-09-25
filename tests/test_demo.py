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
from rps7200.export import to_8bit
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


@pytest.fixture(autouse=True)
def calibrated(monkeypatch):
    """Every stand-in here has been calibrated, as the window insists on first.

    These tests are about decoding, framing and rolls. The refusal an
    uncalibrated pass gets is its own test below, which undoes this.
    """
    monkeypatch.setattr(DemoScanner, "_calibrated", True, raising=False)


def test_an_uncalibrated_pass_is_refused_as_the_real_one_refuses_it(
        tmp_path, monkeypatch):
    """The real scanner used to calibrate inside such a pass, and now refuses
    it with `DirectScanner.uncalibrated` -- which the stand-in raises too,
    rather than a retyped copy of its words."""
    from rps7200.direct import DirectScanner
    from rps7200.protocol import ShadingUnavailable

    monkeypatch.setattr(DemoScanner, "_calibrated", False, raising=False)
    entry(tmp_path)
    s = DemoScanner(tmp_path, speed=1e9)
    with pytest.raises(ShadingUnavailable) as refused:
        s.scan(resolution=900, infrared=False)
    assert str(refused.value) == str(DirectScanner.uncalibrated())
    with pytest.raises(ShadingUnavailable):
        s.prescan()
    s.ensure_shading(None)
    s.scan(resolution=900, infrared=False)       # calibrated: it scans


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
    _, meta = s.scan(resolution=900, infrared=False, keep_raw=True)
    capture = s.capture_record()
    s.close()

    assert capture["raw"] is not None
    out = library.save(s.last_pixels_raw, meta, root=tmp_path / "out",
                       film=FilmNotes(), **capture)
    _, verdict = library.reconstruct(out)
    assert verdict.startswith("identical"), verdict


def test_bytes_are_kept_only_when_the_pass_keeps_them(tmp_path):
    """As on the real one: `keep_raw` decides, and a pass that did not keep
    its bytes hands over none -- not the previous pass's."""
    entry(tmp_path)
    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    s.scan(resolution=900, infrared=False, keep_raw=True)
    s.scan(resolution=900, infrared=False)
    s.close()
    assert s.capture_record()["raw"] is None
    assert s.last_raw is None


def test_reshaping_the_image_hands_over_the_reshaped_pass(tmp_path):
    """Asking a four-channel entry for RGB is a three-channel pass.

    The stored bytes describe four, and filing them would make an entry whose
    raw decodes to a different picture -- the one failure the library exists
    to make impossible. So the pass hands over bytes of its own, three planes
    wide, which decode to exactly what it holds.
    """
    entry(tmp_path, channels=4)
    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    image, meta = s.scan(resolution=900, infrared=False, keep_raw=True)
    capture = s.capture_record()
    s.close()
    assert image.shape[2] == 3
    assert capture["raw_layout"]["channels"] == 3
    out = library.save(s.last_pixels_raw, meta, root=tmp_path / "out",
                       film=FilmNotes(), **capture)
    assert library.reconstruct(out)[1].startswith("identical")


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
        record = json.loads((source / "scan.json").read_text(encoding="utf-8"))
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
    # Same resolution as the prescan, so the two can be compared at all -- and
    # the same depth: a prescan is 8-bit, as the device takes it.
    same = s.scan(resolution=300, infrared=False, film="bw")[0]
    s.close()
    assert np.array_equal(pre[..., 1], to_8bit(same)[..., 1]), (
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
    image, _ = s.scan(resolution=1800, infrared=False, film="bw",
                      keep_raw=True)
    capture = s.capture_record()
    s.close()
    assert image.shape[:2] == (16, 24), image.shape
    layout = capture["raw_layout"]
    assert (layout["lines"], layout["width"]) == (16, 24), (
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


# -- the stand-in must not drift from what it stands in for -----------------
#
# The demo is how this driver is judged when the scanner is off. A demo that
# diverges does not fail loudly: it reports something plausible and wrong, and
# the fix then asked for damages the real path. `nudge` was typed out here once
# and kept a cap of param 8 and a 1.57-unit ramp after the driver moved to 87
# and 1.84 -- so a frame set 38 units out held in one command on the hardware
# and came back `not_converged` in the demo.


def test_the_demo_has_every_attribute_the_borrowed_methods_reach_for():
    """`_hold_to_approved` and `_aim_frame` are the real ones, bound onto this
    class. They reach through `self` for things `DirectScanner` has, and a
    missing one is an `AttributeError` in the middle of a roll rather than at
    import. `_aim_frame`'s dry run did exactly that: it wanted `param_for_mm`
    and the stand-in did not have it."""
    from rps7200.demo import DemoScanner

    for name in ("param_for_mm", "STEP_MM", "OVERHEAD_MM",
                 "MAX_CORRECTION_PARAM", "HOLD_SETTLE_S",
                 "HOLD_GIVE_UP_FRAMES", "nudge", "prescan", "_log"):
        assert hasattr(DemoScanner, name), name


def test_the_transport_law_is_the_drivers_own_and_not_a_copy():
    """One home for the snapping, which is why `param_for_mm` is a
    staticmethod. Equal values are not enough -- these must be the same
    objects, or the next time one moves the other stays where it was."""
    from rps7200.demo import DemoScanner
    from rps7200.direct import DirectScanner

    assert DemoScanner.param_for_mm is DirectScanner.param_for_mm
    assert DemoScanner.STEP_MM is DirectScanner.STEP_MM
    assert DemoScanner.OVERHEAD_MM is DirectScanner.OVERHEAD_MM
    assert DemoScanner.MAX_CORRECTION_PARAM is DirectScanner.MAX_CORRECTION_PARAM


def test_when_a_roll_ends_is_the_drivers_decision_and_not_a_copy():
    """The demo's roll loop retyped the driver's end-of-roll rule, and the
    copy drifted three ways -- a six-frame default, the end of `only`
    ignored, walking past its own strip -- before it was retyped again. It
    is taken now, as `param_for_mm` is, and so is what a frame is numbered
    when the counter disagrees with the count."""
    from rps7200.demo import DemoScanner
    from rps7200.direct import DirectScanner

    assert DemoScanner.roll_ends is DirectScanner.roll_ends
    assert DemoScanner.place_on_strip is DirectScanner.place_on_strip


def test_the_demo_answers_everything_the_seek_asks_of_the_scanner():
    """`session.seek` runs above the seam and asks the scanner to wait for
    its lamp before anything else. The demo answers it as the real one does
    once warm, so the demo runs the seek with no branch of its own."""
    from rps7200.demo import DemoScanner
    from rps7200.direct import DirectScanner

    for name in ("wait_warm", "position", "advance", "retreat"):
        assert callable(getattr(DirectScanner, name)), name
        assert callable(getattr(DemoScanner, name, None)), name
    assert DemoScanner("library").wait_warm() is None


def test_the_demo_roll_logs_distances_in_the_transports_units(tmp_path):
    """As the real loop does. It said 'offset +0.00 mm' where the scanner's
    own log says units, in lines a person reads beside each other."""
    lines = []
    with DemoScanner(root=tmp_path, speed=1e9) as s:
        s.log_hook = lines.append
        list(s.scan_roll(frames=2, dry_run=True))
    measured = [line for line in lines if "contrast" in line]
    assert len(measured) == 2
    for line in measured:
        assert " mm" not in line, line
        assert "units" in line, line
    assert measured[0].startswith("frame 1:"), "counted from 1, as shown"


def test_a_nudge_picks_the_same_param_the_scanner_would():
    """The distance the operator asks for becomes the same byte either way.

    38 units is the case that exposed the drift: one command on the hardware,
    and the stale copy capped it at param 8 and gave up after three moves.
    """
    from rps7200.demo import DemoScanner
    from rps7200.direct import DirectScanner

    demo = DemoScanner("library")
    for units in (1, 3, 8, 20, 38, 87, 200):
        millimetres = units * DirectScanner.STEP_MM
        assert (demo.param_for_mm(millimetres)
                == DirectScanner.param_for_mm(millimetres)), units


def test_a_nudge_answers_with_everything_the_hold_loop_reads():
    """`_hold_to_approved` reads `clamped` to say a command fell short, and
    `asked_mm` to know what it spent. The copy returned neither, so the
    shortfall warning was unreachable at any distance."""
    from rps7200.demo import DemoScanner
    from rps7200.direct import DirectScanner

    got = DemoScanner("library").nudge(38 * DirectScanner.STEP_MM)
    for key in ("param", "forward", "asked_mm", "requested_mm",
                "clamped", "short_mm"):
        assert key in got, key


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
        assert s._rolling is None
    for f, source in zip(frames, walked, strict=True):
        stored = tiff.read(str(source / "prescan.tif"))
        # at the prescan's own depth, 8 bits, whatever was stored
        assert np.array_equal(f.prescan, to_8bit(stored))
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
        films.add((json.loads((path / "scan.json").read_text(encoding="utf-8"))
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


# --- a roll starts where the film is, in the demo as on the hardware --------
#
# The demo's roll used to put the film straight on frame `skip + i` whatever
# it was on. The real one starts where the film is -- which is how a roll from
# frame 10 was numbered 1 on the scanner while the demo, asked the same thing,
# got it right and hid it. The stand-in now moves its film with its own
# `advance`, so `session.seek` above the seam has something true to work on.


class _Counting(DemoScanner):
    """The demo, counting its own whole-frame moves."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.advances = self.retreats = 0

    def advance(self, *a, **kw):
        self.advances += 1
        return super().advance(*a, **kw)

    def retreat(self, *a, **kw):
        self.retreats += 1
        return super().retreat(*a, **kw)


def _on_frame(s, position):
    for _ in range(position):
        s.advance()
    assert s.position() == position


def test_a_demo_roll_starts_where_its_film_is(tmp_path):
    with DemoScanner(root=tmp_path, speed=1e9) as s:
        _on_frame(s, 5)
        frames = list(s.scan_roll(frames=2, dry_run=True, first_index=5))
    assert [f.position for f in frames] == [5, 6]
    assert [f.index for f in frames] == [5, 6]


def _end_of_strip(tmp_path):
    """Where the demo's own `advance` stops, found by asking it."""
    with DemoScanner(root=tmp_path, speed=1e9) as s:
        while s.advance() is not None:
            pass
        return s.position()


def test_a_demo_roll_with_no_count_runs_to_the_end_of_its_strip(tmp_path):
    """As the real one does, which has no count to stop at either. The demo's
    used to stop after six whatever the strip held."""
    end = _end_of_strip(tmp_path)
    with DemoScanner(root=tmp_path, speed=1e9) as s:
        _on_frame(s, end - 2)
        frames = list(s.scan_roll(dry_run=True, first_index=end - 2))
    assert [f.position for f in frames] == [end - 2, end - 1, end]


def test_a_demo_roll_never_walks_past_the_end_of_its_strip(tmp_path):
    """It used to hand back frames at positions its own `advance` would have
    refused to reach."""
    end = _end_of_strip(tmp_path)
    with DemoScanner(root=tmp_path, speed=1e9) as s:
        frames = list(s.scan_roll(frames=end + 5, dry_run=True))
    assert len(frames) == end + 1
    assert frames[-1].position == end


def test_a_demo_roll_ends_after_its_last_chosen_frame(tmp_path):
    """Not walked on to the end of the strip past frames nobody chose."""
    with DemoScanner(root=tmp_path, speed=1e9) as s:
        frames = list(s.scan_roll(only=(1, 3), dry_run=True))
        assert s.position() == 3
    assert [f.index for f in frames] == [1, 3]


def test_a_demo_roll_numbers_its_frames_by_where_its_film_is(tmp_path):
    """The driver's `place_on_strip`, called by the demo's own loop -- which
    nothing checked, only that the demo had it. Told it starts on 2 with its
    film on 5, it files the pictures under 5 and 6 as the scanner would,
    where counting would have filed the pictures on 8 and 9 there."""
    with DemoScanner(root=tmp_path, speed=1e9) as s:
        _on_frame(s, 5)
        frames = list(s.scan_roll(only=(5, 6), first_index=2, dry_run=True))
    assert [(f.index, f.position) for f in frames] == [(5, 5), (6, 6)]


def test_a_demo_roll_behind_its_count_ends_as_the_drivers_does(tmp_path):
    """Film on 0, told it starts on 5, two frames: the counter reads behind
    the count, so the roll ends before it takes anything and says which
    frames it would have gone back over. Following it, the demo walked seven
    frames, 0 to 6, for two asked."""
    lines = []
    with DemoScanner(root=tmp_path, speed=1e9) as s:
        s.log_hook = lines.append
        frames = list(s.scan_roll(frames=2, first_index=5, dry_run=True))
        assert s.position() == 0, "nothing moved"
    assert frames == []
    assert any("went back over frames 1, 2, 3, 4, 5" in line
               for line in lines), lines


def test_the_demo_is_wound_back_by_the_sessions_own_seek(tmp_path):
    """The reported case, run through the whole of the real software with the
    demo standing where the scanner stands: nine frames on, a roll from frame
    1 winds back with the demo's own `retreat` and walks 1, 2, 3."""
    import time

    from rps7200.session import Roll, ScanSession

    demo = _Counting(root=tmp_path / "lib", speed=1e9)
    demo.open()
    _on_frame(demo, 9)
    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(tmp_path / "rolls"),
                    open_scanner=lambda: demo, verbose=False)
    s.start()
    s.submit(Roll(frames=3, start_at=1, dry_run=True, name="walk"))
    s.shutdown()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if any(e.kind == "closed" for e in s.poll()):
            break
        time.sleep(0.01)
    s.join(timeout=5)
    survey = json.loads((tmp_path / "rolls" / "walk" / "survey.json")
                        .read_text(encoding="utf-8"))
    assert [(f["number"], f["transport_position"])
            for f in survey["frames"]] == [(1, 0), (2, 1), (3, 2)]
    assert demo.retreats == 9


# --- the stand-in has to behave like the thing it stands in for -------------
#
# Two real bugs reached the scanner because the demo diverged from it: the
# hold loop was imitated rather than run, so nothing the loop actually does
# was ever exercised without hardware.


def test_the_demo_runs_the_real_hold_loop_not_an_imitation():
    """Bound from DirectScanner, so it cannot drift from the code it stands in
    for. An imitation can never disagree with itself."""
    from rps7200.demo import DemoScanner
    from rps7200.direct import DirectScanner

    assert DemoScanner._hold_to_approved is DirectScanner._hold_to_approved


def test_nudging_the_demo_actually_moves_the_film():
    """It used to report a move and hand back an identical picture, so a loop
    that looked again after moving learned nothing and could only ever be
    pretended at."""
    from rps7200.demo import DemoScanner

    scanner = DemoScanner("library", speed=1e9)
    before = scanner._film_mm
    scanner.nudge(0.5)
    assert scanner._film_mm > before


def test_the_demo_converges_on_an_approved_position():
    from rps7200.demo import DemoScanner
    from rps7200.session import Approved

    scanner = DemoScanner("library", speed=1e9)
    scanner.open()
    if not scanner._entries:
        # The test card the demo falls back to has nothing to correlate,
        # so registration can only ever say "unverified". Same reason
        # test_tiff skips without scans/: the data is not in a checkout.
        scanner.close()
        pytest.skip("no library entries in this checkout to register against")
    references = {rf.index: rf.prescan for rf in scanner.scan_roll(
        frames=2, resolution=300, infrared=False, dry_run=True)}
    scanner.close()

    scanner = DemoScanner("library", speed=1e9)
    scanner.open()
    held = {}
    approved = {0: Approved(1, 0.5, reference=references[0]),
                1: Approved(2, 0.0, reference=references[1])}
    for rf in scanner.scan_roll(frames=2, resolution=300, infrared=False,
                                dry_run=True, approved=approved):
        held[rf.index] = rf.registration["approved"]
    scanner.close()

    assert held[0]["outcome"] == "held"
    assert held[0]["moves"] == 1, "an offset should cost exactly one move"
    assert held[0]["final_mm"] == pytest.approx(0.5, abs=0.05)
    assert held[1]["outcome"] == "held"
    assert held[1]["moves"] == 0, "no offset asked for, so nothing to do"


def test_one_frame_is_made_to_miss_on_purpose():
    """A flag nobody has ever seen fire is a flag nobody trusts. The demo has
    a frame whose transport slips, so `not_converged` and the end-of-roll
    warning can be watched rather than taken on faith."""
    from rps7200.demo import DemoScanner
    from rps7200.session import Approved

    scanner = DemoScanner("library", speed=1e9)
    scanner.open()
    if not scanner._entries:
        # The test card the demo falls back to has nothing to correlate,
        # so registration can only ever say "unverified". Same reason
        # test_tiff skips without scans/: the data is not in a checkout.
        scanner.close()
        pytest.skip("no library entries in this checkout to register against")
    slipping = scanner._slipping_index
    references = {rf.index: rf.prescan for rf in scanner.scan_roll(
        frames=slipping + 1, resolution=300, infrared=False, dry_run=True)}
    scanner.close()

    scanner = DemoScanner("library", speed=1e9)
    scanner.open()
    out = {}
    for rf in scanner.scan_roll(
            frames=slipping + 1, resolution=300, infrared=False, dry_run=True,
            approved={slipping: Approved(slipping + 1, 0.8,
                                         reference=references[slipping])}):
        if rf.index == slipping:
            out = rf.registration["approved"]
    scanner.close()

    assert out["outcome"] == "not_converged"
    assert out["moves"] == 3, "it tries, and stops at the cap"


# --- the carriage: a pass after an RGBI scan comes back bottom-up ------------


def picture_entry(root, channels=4, lines=10, width=8):
    """An entry whose rows differ, so reading it bottom-up would show."""
    from rps7200.direction import encode_index

    rng = np.random.default_rng(3)
    image = rng.integers(0, 65535, (lines, width, channels), dtype=np.uint16)
    meta = {"resolution_dpi": 900, "channels": channels, "film": "negative",
            "channel_order": list("RGBI"[:channels]), "width": width,
            "height": lines, "bytes_per_line": width * 2, "depth": 16}
    path = library.save(
        image, meta, root=root, film=FilmNotes(frame="demo"),
        raw=encode_index(image),
        raw_layout={"format": "index", "bytes_per_line": width * 2,
                    "line_stride": width * 2 + 2, "index_header": 2,
                    "width": width, "lines": lines, "channels": channels})
    return path, image


def _read(meta):
    return (meta.get("read_direction") or {}).get("direction")


def test_the_pass_after_an_rgbi_scan_is_read_bottom_up_and_shown_upright(tmp_path):
    _, truth = picture_entry(tmp_path)
    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    first, meta = s.scan(resolution=900, infrared=True)
    second, again = s.scan(resolution=900, infrared=True)
    third, last = s.scan(resolution=900, infrared=True)
    s.close()
    assert [_read(m) for m in (meta, again, last)] == ["forward", "reversed", "forward"]
    for image in (first, second, third):
        assert np.array_equal(image, truth), "every pass is shown upright"
    assert again["carriage_state"]["far_end"] is True


def test_an_rgb_pass_leaves_the_carriage_at_home(tmp_path):
    picture_entry(tmp_path)
    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    s.scan(resolution=900, infrared=False)
    s.prescan(film="negative")
    assert _read(s.last_scan_meta) == "forward"
    s.scan(resolution=900, infrared=True)
    s.prescan(film="negative")
    assert _read(s.last_scan_meta) == "reversed", "the prescan after an RGBI scan"
    s.prescan(film="negative")
    assert _read(s.last_scan_meta) == "forward", "and the one after that is home"
    s.close()


def test_the_carriage_takes_its_rule_from_the_driver(tmp_path, monkeypatch):
    """`DirectScanner.byte14_for` decides whether a pass leaves the carriage
    at the far end; the demo asks it rather than keeping its own copy."""
    from rps7200.direct import DirectScanner

    picture_entry(tmp_path)
    monkeypatch.setattr(DirectScanner, "byte14_for", staticmethod(lambda passes: 0x10))
    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    s.scan(resolution=900, infrared=True)
    s.prescan(film="negative")
    s.close()
    assert _read(s.last_scan_meta) == "forward"


def test_a_pass_read_bottom_up_files_bytes_that_agree_with_its_record(tmp_path):
    """The stored bytes are reversed the way the carriage reverses them, so
    the entry decodes to what was shown and its record matches its tags."""
    picture_entry(tmp_path)
    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    s.scan(resolution=900, infrared=True)
    _, meta = s.scan(resolution=900, infrared=True, keep_raw=True)
    capture = s.capture_record()
    s.close()
    assert _read(meta) == "reversed" and capture["raw"] is not None
    out = library.save(s.last_pixels_raw, meta, root=tmp_path / "out",
                       film=FilmNotes(), **capture)
    _, verdict = library.reconstruct(out)
    assert verdict.startswith("identical"), verdict


# -- a second roll in one session reads other pictures ------------------------


def _shown(frames):
    """Which picture each frame showed, by its prescan's bytes."""
    return [f.prescan.tobytes() for f in frames]


def test_the_first_roll_is_the_one_it_always_was(tmp_path):
    for n in range(5):
        _walkable(tmp_path, f"e{n}", seed=n + 1)
    with DemoScanner(root=tmp_path, speed=100000.0, seed=1) as a:
        first = _shown(a.scan_roll(frames=5, dry_run=True))
    with DemoScanner(root=tmp_path, speed=100000.0, seed=2) as b:
        again = _shown(b.scan_roll(frames=5, dry_run=True))
    assert first == again, "a fresh session opens on the library's own strip"


def test_a_second_roll_reads_a_new_strip_and_fresh_pictures_first(tmp_path):
    """Stefan: another roll in the same session reads other pictures. With
    more pictures than one strip holds, the second strip starts from the ones
    not shown yet."""
    for n in range(8):
        _walkable(tmp_path, f"e{n}", seed=n + 1)
    with DemoScanner(root=tmp_path, speed=100000.0, seed=3) as s:
        s.LAST_POSITION = 3                    # a four-frame strip of eight
        first = _shown(s.scan_roll(frames=4, dry_run=True))
        for _ in range(4):
            s.retreat()
        second = _shown(s.scan_roll(frames=4, dry_run=True))
    assert len(set(first)) == len(set(second)) == 4
    assert not set(first) & set(second), "the unseen four come first"


def test_a_new_strip_repeats_pictures_only_in_other_places(tmp_path):
    for n in range(5):
        _walkable(tmp_path, f"e{n}", seed=n + 1)
    with DemoScanner(root=tmp_path, speed=100000.0, seed=4) as s:
        first = _shown(s.scan_roll(frames=5, dry_run=True))
        for _ in range(5):
            s.retreat()
        second = _shown(s.scan_roll(frames=5, dry_run=True))
    assert sorted(first) == sorted(second), "the same five pictures"
    assert first != second, "laid out differently"


def test_a_walk_further_along_the_strip_reads_the_same_strip(tmp_path):
    """A walk of 1 to 4, then of 5 and 6 added to the same sheet: the second
    starts past frame 1, which is more of the strip in the transport, not a
    new one -- or the sheet would put another strip's pictures on 5 and 6."""
    for n in range(8):
        _walkable(tmp_path, f"e{n}", seed=n + 1)
    with DemoScanner(root=tmp_path, speed=100000.0, seed=6) as s:
        s.LAST_POSITION = 5                    # a six-frame strip of eight
        first = _shown(s.scan_roll(frames=4, dry_run=True))
        s.advance()
        further = _shown(s.scan_roll(frames=2, first_index=4, dry_run=True))
        for _ in range(5):
            s.retreat()
        # Chosen frames always read the strip that was walked.
        whole = _shown(s.scan_roll(frames=6, only=tuple(range(6)),
                                   dry_run=True))
    assert whole == first + further


def test_scanning_chosen_frames_scans_the_strip_that_was_walked(tmp_path):
    """The contact sheet's positions belong to the pictures it showed. A roll
    commissioned from it must not find other ones in the transport."""
    for n in range(5):
        _walkable(tmp_path, f"e{n}", seed=n + 1)
    with DemoScanner(root=tmp_path, speed=100000.0, seed=5) as s:
        s.scan_roll(frames=1, dry_run=True).__next__()     # an earlier roll
        for _ in range(1):
            s.retreat()
        walked = _shown(s.scan_roll(frames=5, dry_run=True))
        for _ in range(5):
            s.retreat()
        chosen = list(s.scan_roll(frames=5, only=(1, 3), dry_run=True))
    assert [f.prescan.tobytes() for f in chosen] == [walked[1], walked[3]]


# -- the pictures a later roll can draw on: every library, one per photograph -


def _photograph(seed, h=60, w=90):
    """A picture with structure across and down, so a shift is still it."""
    rng = np.random.default_rng(seed)
    base = rng.random((h // 6, w // 6, 3))
    big = np.kron(base, np.ones((6, 6, 1)))
    return (big * 50000 + 2000).astype(np.uint16)


def _scan_only(root, seed, shift=0, raw=True):
    """An entry with no stored prescan: only a 300 dpi scan of ``seed``'s picture."""
    from rps7200.direction import encode_index

    image = np.roll(_photograph(seed), shift, axis=1)
    h, w = image.shape[:2]
    meta = {"resolution_dpi": 300, "channels": 3, "film": "negative",
            "channel_order": ["R", "G", "B"], "width": w, "height": h,
            "bytes_per_line": w * 2, "depth": 16}
    extra = ({"raw": encode_index(image),
              "raw_layout": {"format": "index", "bytes_per_line": w * 2,
                             "line_stride": w * 2 + 2, "index_header": 2,
                             "width": w, "lines": h, "channels": 3}}
             if raw else {})
    return library.save(image, meta, root=root, film=FilmNotes(frame=f"p{seed}"),
                        **extra), image


def test_the_libraries_beside_one_are_found_with_their_nested_ones(tmp_path):
    from rps7200.demo import libraries_beside

    first, second = tmp_path / "library", tmp_path / "library 2"
    _scan_only(first, 1)
    _scan_only(second, 2)
    _scan_only(second / "600dpi", 3)
    (tmp_path / "library-old").mkdir()
    assert libraries_beside(first) == [first, second, second / "600dpi"]


def test_one_photograph_scanned_twice_is_one_picture(tmp_path):
    """Two scans of one frame differ by where the frame sat in the aperture."""
    from rps7200.demo import group_pictures, picture_signature

    a, _ = _scan_only(tmp_path, 7)
    b, _ = _scan_only(tmp_path, 7, shift=12)
    c, _ = _scan_only(tmp_path, 8)
    labels = group_pictures([picture_signature(p) for p in (a, b, c)])
    assert labels[0] == labels[1] != labels[2]


def test_a_second_roll_draws_its_photographs_from_every_library(tmp_path):
    first, second = tmp_path / "library", tmp_path / "library 2"
    for n in range(2):
        _walkable(first, f"e{n}", seed=n + 1)
    twenty = _scan_only(second, 20)[0]
    elsewhere = {twenty, _scan_only(second, 21)[0], _scan_only(second, 22)[0]}
    again, _ = _scan_only(second, 20, shift=10)          # the same photograph again
    with DemoScanner(root=first, speed=100000.0, seed=6,
                     libraries=[first, second]) as s:
        s.LAST_POSITION = 2
        list(s.scan_roll(frames=2, dry_run=True))
        for _ in range(2):
            s.retreat()
        list(s.scan_roll(frames=3, dry_run=True))
        strip = s._strips["negative"]
    assert len(strip) == 3
    assert set(strip) <= elsewhere | {again}, "the three unseen photographs"
    assert len([p for p in strip if p in (twenty, again)]) == 1, (
        "one photograph appears once, whichever of its entries shows it")


def test_an_entry_without_bytes_shows_its_own_picture(tmp_path):
    """Nothing to decode, so its stored scan -- not another entry's picture."""
    path, image = _scan_only(tmp_path, 9, raw=False)
    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    got = s._decode(path)
    s.close()
    assert got is not None and np.array_equal(got["pixels"], image)
    assert got["file"] == "scan.tif", "and says that is what it read"


def test_the_likenesses_are_kept_between_sessions(tmp_path, monkeypatch):
    from rps7200 import demo

    for n in range(3):
        _scan_only(tmp_path / "lib", 30 + n)
    cache = tmp_path / "demo" / "pictures.npz"
    with DemoScanner(tmp_path / "lib", speed=1e9, cache=cache) as s:
        s._signing.join()
    assert cache.exists()
    monkeypatch.setattr(demo, "picture_signature",
                        lambda entry: pytest.fail("read again despite the cache"))
    with DemoScanner(tmp_path / "lib", speed=1e9, cache=cache) as s:
        s._signing.join()
        assert len(s._signatures) == 3


# -- what the demo files: raw pixels, corrected last, as the scanner does -----
#
# The demo corrected every picture as it decoded it and kept nothing else, so
# the session filed corrected pixels as raw -- beside the stored entry's
# reference, which then corrected them a second time on every view and export.
# A branch the scanner never takes, and the one that hid the window's own bug
# of that shape. These hold the stand-in to `DirectScanner.scan`'s contract.


def calibrated_entry(root, lines=8, width=12, channels=3, dpi=900,
                     prescan=None, seed=5):
    """An entry as the scanner files one: raw pixels, their bytes, and the
    reference and mask that correct them.

    The reference varies column to column and the mask reads every other CCD
    column, as a pass below the native resolution does -- so a correction
    applied at the wrong columns shows, where a flat one would hide it.
    """
    from rps7200.direction import encode_index
    from rps7200.shading import MASK_USED, ShadingReference

    rng = np.random.default_rng(seed)
    image = rng.integers(2000, 60000, (lines, width, channels), dtype=np.uint16)
    ccd = 2 * width + 4
    mask = bytearray([0x70]) * ccd
    for j in range(width):
        mask[1 + 2 * j] = MASK_USED
    reference = ShadingReference(
        ref={c: rng.uniform(30000, 45000, ccd) for c in range(4)},
        mean={c: 40000.0 for c in range(4)},
        pixels_per_line=ccd,
        dark={c: rng.uniform(100, 300, ccd) for c in range(4)},
        dark_mean={c: 170.0 for c in range(4)},
    )
    meta = {"resolution_dpi": dpi, "channels": channels, "film": "negative",
            "channel_order": list("RGBI"[:channels]), "width": width,
            "height": lines, "bytes_per_line": width * 2, "depth": 16}
    path = library.save(
        image, meta, root=root, film=FilmNotes(frame="demo"),
        reference=reference, ccd_mask=bytes(mask), prescan=prescan,
        raw=encode_index(image),
        raw_layout={"format": "index", "bytes_per_line": width * 2,
                    "line_stride": width * 2 + 2, "index_header": 2,
                    "width": width, "lines": lines, "channels": channels})
    return path, image, reference, bytes(mask)


def test_a_pass_is_corrected_last_and_its_raw_pixels_kept(tmp_path):
    from rps7200.shading import apply_shading

    _, truth, reference, mask = calibrated_entry(tmp_path)
    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    image, meta = s.scan(resolution=900, infrared=False, keep_raw=True)
    capture = s.capture_record()
    s.close()
    assert np.array_equal(s.last_pixels_raw, truth), "the decode, before correction"
    expected, _ = apply_shading(truth, reference, mask)
    assert np.array_equal(image, expected)
    assert not np.array_equal(image, truth), "and a correction did run"
    assert meta["shading"] is not None and meta["shading_skipped"] is None
    # the stored pass's own mask, since every column is where it measured it
    assert capture["ccd_mask"] == mask


def test_a_resized_pass_is_corrected_where_its_columns_came_from(tmp_path):
    """Half the resolution shows every other stored column. Each is corrected
    by the reference column that measured it, and what is filed gives the
    same picture back."""
    from rps7200.shading import apply_shading

    _, truth, reference, mask = calibrated_entry(tmp_path)
    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    image, meta = s.scan(resolution=450, infrared=False, keep_raw=True)
    capture = s.capture_record()
    s.close()
    whole, _ = apply_shading(truth, reference, mask)
    rows, columns = np.arange(4) * 2, np.arange(6) * 2
    assert np.array_equal(image, whole[np.ix_(rows, columns)])
    assert np.array_equal(s.last_pixels_raw, truth[np.ix_(rows, columns)])
    out = library.save(s.last_pixels_raw, meta, root=tmp_path / "out",
                       film=FilmNotes(), **capture)
    assert np.array_equal(library.corrected(out)[0], image)
    assert library.reconstruct(out)[1].startswith("identical")


def test_a_scan_shows_the_film_where_it_was_moved(tmp_path):
    """Only prescans used to move, so a frame held to its approved position
    was scanned where it had been before the hold."""
    from rps7200.shading import apply_shading

    _, truth, reference, mask = calibrated_entry(tmp_path)
    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    s.nudge(9.0)
    shift = s._shift(truth.shape[1])
    image, meta = s.scan(resolution=900, infrared=False, keep_raw=True)
    capture = s.capture_record()
    s.close()
    assert shift != 0
    whole, _ = apply_shading(truth, reference, mask)
    assert np.array_equal(image, np.roll(whole, shift, axis=1))
    out = library.save(s.last_pixels_raw, meta, root=tmp_path / "out",
                       film=FilmNotes(), **capture)
    assert np.array_equal(library.corrected(out)[0], image)


def test_a_prescan_from_a_stored_tiff_carries_nothing_of_the_pass_before(tmp_path):
    """A stored prescan has no bytes and no calibration of its own. It used
    to hand over whatever the previous decode had left: that pass's bytes,
    reference and mask, filed beside pixels they do not describe."""
    rng = np.random.default_rng(8)
    stored = rng.integers(0, 255, (6, 9, 3), dtype=np.uint8)
    calibrated_entry(tmp_path, prescan=stored)
    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    s.scan(resolution=900, infrared=False, keep_raw=True)
    image, _ = s.prescan(keep_raw=True)
    capture = s.capture_record()
    s.close()
    assert np.array_equal(image, stored)
    assert np.array_equal(s.last_pixels_raw, image), "nothing to take off"
    assert capture["reference"] is None and capture["ccd_mask"] is None
    layout = capture["raw_layout"]
    assert (layout["lines"], layout["width"], layout["channels"]) == (6, 9, 3)
    assert s.last_scan_meta["shading"] is None, "no correction ran"


def test_what_a_demo_session_files_is_raw_and_reconstructs(tmp_path):
    """The whole of the real software, with the demo where the scanner is: a
    prescan, a scan at the stored resolution, a resized one and a metered
    RGBI one. Every entry holds raw pixels labelled raw, re-decodes to
    exactly them, corrects to exactly what the window was shown, and says it
    came from the demo and from which stored picture."""
    import time
    from pathlib import Path

    from rps7200.session import Prescan, Scan, ScanSession

    source, *_ = calibrated_entry(tmp_path / "lib")
    demo = DemoScanner(tmp_path / "lib", speed=1e9)
    session = ScanSession(root=str(tmp_path / "demo-library"),
                          rolls=str(tmp_path / "rolls"),
                          reference=str(tmp_path / "shading.npz"),
                          open_scanner=lambda: demo, verbose=False)
    session.start()
    for job in (Prescan(resolution=300),
                Scan(resolution=900, infrared=False, auto_exposure=False),
                Scan(resolution=450, infrared=False, auto_exposure=False),
                Scan(resolution=900, infrared=True)):
        session.submit(job)
    session.shutdown()
    events = []
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        batch = session.poll()
        events += batch
        if any(e.kind == "closed" for e in batch):
            break
        time.sleep(0.01)
    session.join(timeout=5)

    assert not [e.text for e in events if e.kind == "failed"]
    shown = {e.result.seq: e.result.image for e in events if e.kind == "result"}
    filed = {e.done: e.text for e in events if e.kind == "filed"}
    assert len(filed) == 4
    records = []
    for seq, path in filed.items():
        record = json.loads((Path(path) / "scan.json").read_text(encoding="utf-8"))
        records.append(record)
        assert record["image"]["corrections_applied"] == [], path
        assert library.reconstruct(path)[1].startswith("identical"), path
        assert np.array_equal(library.corrected(path)[0], shown[seq]), path
        assert record["extra"]["demo"] is True
        assert record["extra"]["demo_source"]["entry"] == source.name
    assert any(r["metering"] for r in records), "the RGBI scan metered itself"


# -- what the demo refuses, and how it answers ---------------------------------


def test_shading_false_hands_back_the_raw_pass(tmp_path, monkeypatch):
    """As on the scanner: raw on purpose, recorded as such -- and asked of a
    session that never calibrated, taken rather than refused."""
    from rps7200.direct import SHADING_SKIPPED_EXPLICIT
    from rps7200.protocol import ShadingUnavailable

    monkeypatch.setattr(DemoScanner, "_calibrated", False, raising=False)
    _, truth, *_ = calibrated_entry(tmp_path)
    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    image, meta = s.scan(resolution=900, infrared=False, shading=False)
    assert np.array_equal(image, truth)
    assert meta["shading"] is None
    assert meta["shading_skipped"] == SHADING_SKIPPED_EXPLICIT
    s.prescan(shading=False)
    assert s.last_scan_meta["shading_skipped"] == SHADING_SKIPPED_EXPLICIT
    with pytest.raises(ShadingUnavailable):
        s.scan(resolution=900, infrared=False)
    s.close()


def test_a_pass_no_calibration_can_cover_is_refused_in_the_drivers_words(
        tmp_path, monkeypatch):
    """7200 dpi is twice the widest reference the device will produce. The
    scanner refuses it before sending anything; the demo used to resize a
    stored picture to it, and a roll the scanner refuses frame by frame ran
    to the end with sane-looking pictures."""
    from conftest import FakeTransport

    from rps7200.direct import DirectScanner
    from rps7200.protocol import ShadingUnavailable

    entry(tmp_path)
    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    with pytest.raises(ShadingUnavailable) as refused:
        s.scan(resolution=7200, infrared=False)
    words = str(DirectScanner.uncorrectable(7200))
    assert str(refused.value) == words
    real = DirectScanner(transport=FakeTransport())
    real.verbose = False
    with pytest.raises(ShadingUnavailable) as also:
        real.scan(resolution=7200, infrared=False, require_media=False)
    assert str(also.value) == words, "one refusal, in one set of words"
    with pytest.raises(ShadingUnavailable):
        s.prescan(resolution=7200)
    # asked for raw pixels, it is a pass like any other
    image, meta = s.scan(resolution=7200, infrared=False, shading=False)
    assert meta["resolution_dpi"] == 7200 and image.size
    # and the width comes before the calibration, as on the scanner
    monkeypatch.setattr(DemoScanner, "_calibrated", False, raising=False)
    with pytest.raises(ShadingUnavailable, match="cannot be corrected at all"):
        s.scan(resolution=7200, infrared=False)
    s.close()


def test_a_roll_at_7200_dpi_fails_frame_by_frame_as_the_scanners_does(tmp_path):
    """Through the driver's own loop, which catches the refusal per frame:
    each frame is yielded with its error and its prescan, and nothing is
    scanned. (A picture with something in it: `entry`'s rows are all alike,
    and the driver's loop rightly ends a roll on clear film.)"""
    calibrated_entry(tmp_path)
    with DemoScanner(tmp_path, speed=1e9) as s:
        frames = list(s.scan_roll(frames=2, resolution=7200, infrared=False,
                                  meter="none"))
    assert len(frames) == 2
    for f in frames:
        assert f.image is None and "cannot be corrected at all" in f.error
        assert f.prescan is not None


def test_the_position_is_unknown_once_the_transport_is_closed(tmp_path):
    """The real one answers None when READ STATE fails, and its callers have
    a branch for that; a force abort is where the stand-in's read fails."""
    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    assert s.position() == 0
    s.t.close()
    assert s.position() is None


def test_an_rgbi_pass_is_four_planes_whatever_was_stored(tmp_path):
    """The device always sends four; an RGB entry used to answer three. The
    plane it has no record of is clear film, and nothing corrects it: no
    calibration measured it."""
    from rps7200.shading import apply_shading

    _, truth, reference, mask = calibrated_entry(tmp_path)
    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    image, meta = s.scan(resolution=900, infrared=True, keep_raw=True)
    capture = s.capture_record()
    s.close()
    assert image.shape[2] == 4 and meta["channels"] == 4
    assert meta["channel_order"] == list("RGBI")
    assert len(np.unique(image[..., 3])) == 1
    expected, _ = apply_shading(truth, reference, mask)
    assert np.array_equal(image[..., :3], expected)
    out = library.save(s.last_pixels_raw, meta, root=tmp_path / "out",
                       film=FilmNotes(), **capture)
    assert np.array_equal(library.corrected(out)[0], image)


def test_a_prescan_is_eight_bit_as_the_devices_is(tmp_path):
    """Stored prescans were handed on as they were, 16-bit among them, while
    the record said depth 8 -- so a walk could mix dtypes the device never
    sends. A prescan decoded from a scan was 16-bit too."""
    stored = (np.random.default_rng(4).random((6, 9, 3)) * 60000).astype(np.uint16)
    entry(tmp_path, prescan=stored)
    s = DemoScanner(tmp_path, speed=1e9)
    s.open()
    image, _ = s.prescan()
    assert image.dtype == np.uint8 and s.last_scan_meta["depth"] == 8
    assert np.array_equal(image, to_8bit(stored))
    s.close()

    other = tmp_path / "other"
    entry(other)                                   # no stored prescan
    s = DemoScanner(other, speed=1e9)
    s.open()
    image, _ = s.prescan()
    s.close()
    assert image.dtype == np.uint8


def test_a_demo_roll_prescans_at_the_resolution_it_is_given(tmp_path):
    """It prescanned at 300 dpi whatever `prescan_resolution` said, so the
    detectors read the demo's walks at settings the hardware never used."""
    calibrated_entry(tmp_path)                     # 900 dpi, 8 x 12
    with DemoScanner(tmp_path, speed=1e9) as s:
        at_300 = list(s.scan_roll(frames=1, dry_run=True))[0]
        at_600 = list(s.scan_roll(frames=1, dry_run=True,
                                  prescan_resolution=600))[0]
    assert at_300.prescan_meta["resolution_dpi"] == 300
    assert at_600.prescan_meta["resolution_dpi"] == 600
    assert at_300.prescan.shape[:2] == (3, 4)
    assert at_600.prescan.shape[:2] == (5, 8)


def test_an_empty_transport_refuses_a_framing_pass(tmp_path):
    """`--look-only` promises that anything reaching for film says there is
    none. A prescan used to return a stored photograph of film that was not
    there."""
    from rps7200.usb_transport import UsbError

    entry(tmp_path)
    empty = DemoScanner(tmp_path, no_film=True, speed=1e9)
    empty.open()
    for call in (empty.prescan,
                 lambda: empty.scan(resolution=900, infrared=False)):
        with pytest.raises(UsbError, match="no film in the transport"):
            call()
    empty.close()


# -- the roll is the driver's own loop ------------------------------------------
#
# The demo's roll was a loop of its own that borrowed the driver's decisions
# one at a time, and drifted in everything it had not borrowed: a failed frame
# ended the roll, a blank one did not, the prescan resolution was swallowed,
# the stop never reached the hold loop. It runs `DirectScanner.scan_roll` now.
# These walk the same strip under both -- the driver on a transport-level
# double, the demo with only its film replaced -- and compare what comes out.


def _strip(blank=(), failing=()):
    """The strip both walk: `strip_picture` per place, with clear film at the
    places in ``blank`` and a transport that refuses at those in ``failing``."""
    from conftest import strip_picture

    from rps7200.usb_transport import UsbError

    def picture(place):
        if place in failing:
            raise UsbError(f"the transport refused frame {place + 1}")
        if place in blank:
            return np.full((40, 60, 3), 200, np.uint8)
        return strip_picture(place)
    return picture


def _driver_on(picture):
    from conftest import ScannerOnStrip

    class Driver(ScannerOnStrip):
        def prescan(self, resolution=300, frame=None, keep_raw=False, **kw):
            self.last_scan_meta = {"resolution_dpi": resolution}
            return picture(self.t.at), None

    return Driver(at=0, last=16)


class _DemoOnStrip(DemoScanner):
    """The demo, with nothing replaced but the film in its transport."""

    def __init__(self, picture):
        super().__init__("no-library-here", speed=1e9)
        self.picture = picture
        self.LAST_POSITION = 16
        self.logged = []
        self.log_hook = self.logged.append

    def _stored(self, kind, film, channels):
        return {"pixels": self.picture(self._position), "dpi": None,
                "reference": None, "ccd_mask": None, "entry": None,
                "file": "strip"}


def _walk(scanner, **kw):
    kw.setdefault("dry_run", True)
    kw.setdefault("meter", "none")
    return [(f.index, f.position, f.error, f.registration,
             None if f.prescan is None else f.prescan.tobytes())
            for f in scanner.scan_roll(infrared=False, **kw)]


def _said(lines):
    """The loop's own account of the roll, less its timings."""
    import re
    return [line for line in lines
            if re.match(r"frame \d+", line) and " took " not in line]


@pytest.mark.parametrize("case", [
    {"kw": {"frames": 5}},
    {"kw": {"only": (1, 3)}},
    {"kw": {"frames": 8}, "blank": (3,)},
    {"kw": {"frames": 6}, "failing": (1, 2)},
    {"kw": {"frames": 8}, "failing": (1, 2, 3)},
    {"kw": {"frames": 5, "skip": 2}},
], ids=["plain", "chosen", "blank-ends-it", "failures-cost-a-frame",
        "three-failures-end-it", "skip"])
def test_the_demo_roll_is_the_drivers_loop(monkeypatch, case):
    from conftest import NoWaiting

    from rps7200 import direct

    monkeypatch.setattr(direct, "time", NoWaiting())
    picture = _strip(case.get("blank", ()), case.get("failing", ()))
    driver, demo = _driver_on(picture), _DemoOnStrip(picture)
    walked = _walk(demo, **case["kw"])
    assert walked == _walk(driver, **case["kw"])
    assert walked, "something was walked"
    assert _said(driver.logged), "and the loop said something about it"
    assert _said(demo.logged) == _said(driver.logged)
    assert demo.position() == driver.t.at, "and the film ends in one place"


def test_the_demo_roll_stops_where_the_drivers_does(monkeypatch):
    from conftest import NoWaiting

    from rps7200 import direct

    monkeypatch.setattr(direct, "time", NoWaiting())
    ended = []
    for scanner in (_driver_on(_strip()), _DemoOnStrip(_strip())):
        got = []
        for f in scanner.scan_roll(frames=6, dry_run=True, meter="none",
                                   infrared=False,
                                   should_stop=lambda: len(got) >= 2):
            got.append(f.index)
        where = (scanner.position() if isinstance(scanner, DemoScanner)
                 else scanner.t.at)
        ended.append((got, where))
    assert ended[0] == ended[1] == ([0, 1], 1)


def test_the_demo_runs_the_drivers_roll_and_metering_not_copies():
    """Taken, as `_hold_to_approved` is: the same function objects, so the
    next change to either reaches the demo without anyone remembering to."""
    from rps7200.direct import DirectScanner

    assert DemoScanner._drivers_roll is DirectScanner.scan_roll
    assert DemoScanner._drivers_metering is DirectScanner.auto_exposure


def test_the_demo_has_everything_the_drivers_roll_reaches_for():
    """Every `self.` the borrowed loops read, found by reading them rather
    than listed by hand -- a hand list is how `_aim_frame`'s dry run died on
    a `param_for_mm` nobody had listed."""
    import inspect
    import re

    from rps7200.direct import DirectScanner

    demo = DemoScanner("library")
    for method in (DirectScanner.scan_roll, DirectScanner._hold_to_approved,
                   DirectScanner._aim_frame, DirectScanner._rejudge_for,
                   DirectScanner.auto_exposure):
        wanted = set(re.findall(r"self\.(\w+)", inspect.getsource(method)))
        missing = [name for name in sorted(wanted) if not hasattr(demo, name)]
        assert not missing, (method.__name__, missing)
