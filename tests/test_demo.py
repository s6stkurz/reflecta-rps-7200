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
    image, meta = s.scan(resolution=900, infrared=True)
    capture = s.capture_record()
    s.close()
    assert _read(meta) == "reversed" and capture["raw"] is not None
    out = library.save(image, meta, root=tmp_path / "out", film=FilmNotes(),
                       **capture)
    _, verdict = library.reconstruct(out)
    assert verdict.startswith("identical"), verdict
