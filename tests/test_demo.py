"""The stand-in scanner, checked where a wrong answer would mislead the eye.

The demo is how the window gets verified without hardware, so the ways it can
lie matter. The one that mattered here: a roll used to hand back the same
picture for every frame with the same made-up registration numbers, which made
a contact sheet six identical thumbnails -- a wrong pick and a right one looked
exactly alike.
"""
import numpy as np

from rps7200 import tiff
from rps7200.demo import DemoScanner


def _entry(root, name, seed, shape=(48, 72, 3)):
    """A library entry with a prescan in it, which is what a roll walks."""
    entry = root / name
    entry.mkdir(parents=True)
    (entry / "scan.json").write_text("{}")
    rng = np.random.default_rng(seed)
    picture = (rng.random(shape) * 40000 + seed * 900).astype(np.uint16)
    tiff.write(str(entry / "prescan.tif"), picture)
    return entry


def test_a_roll_walks_a_different_picture_every_frame(tmp_path):
    for n in range(4):
        _entry(tmp_path, f"e{n}", seed=n + 1)
    with DemoScanner(root=tmp_path, speed=100000.0) as s:
        frames = list(s.scan_roll(frames=4, dry_run=True))
    digests = {f.prescan.tobytes() for f in frames}
    assert len(digests) == 4, "a contact sheet of one picture repeated"


def test_the_frames_are_measured_not_invented(tmp_path):
    """The captions under a contact sheet have to come from the pictures, or
    they say the same thing about every frame and mean nothing."""
    for n in range(4):
        _entry(tmp_path, f"e{n}", seed=n + 1)
    with DemoScanner(root=tmp_path, speed=100000.0) as s:
        frames = list(s.scan_roll(frames=4, dry_run=True))
    contrasts = {f.registration["contrast"] for f in frames}
    assert len(contrasts) > 1
    for f in frames:
        assert "offset_mm" in f.registration
        assert "shortfall_mm" in f.registration


def test_a_frame_nobody_chose_is_not_walked(tmp_path):
    """`only` is what the contact sheet's ticks become."""
    for n in range(5):
        _entry(tmp_path, f"e{n}", seed=n + 1)
    with DemoScanner(root=tmp_path, speed=100000.0) as s:
        frames = list(s.scan_roll(frames=5, only=(0, 3), dry_run=True))
    assert [f.index for f in frames] == [0, 3]


def test_a_library_with_no_prescans_still_walks_a_strip(tmp_path):
    """Test cards, seeded per frame -- still different from each other, so the
    sheet is readable on a machine with an empty library."""
    with DemoScanner(root=tmp_path, speed=100000.0) as s:
        frames = list(s.scan_roll(frames=3, dry_run=True))
    assert len({f.prescan.tobytes() for f in frames}) == 3


def test_an_unreadable_prescan_does_not_end_the_demo(tmp_path):
    """A stored file of the wrong shape costs its frame's numbers, not the run."""
    entry = tmp_path / "broken"
    entry.mkdir(parents=True)
    (entry / "scan.json").write_text("{}")
    (entry / "prescan.tif").write_bytes(b"not a tiff")
    with DemoScanner(root=tmp_path, speed=100000.0) as s:
        frames = list(s.scan_roll(frames=2, dry_run=True))
    assert len(frames) == 2
