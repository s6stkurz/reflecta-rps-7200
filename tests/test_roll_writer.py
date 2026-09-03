"""Frames must reach disk without the scanner sitting open and idle.

Filing a frame gzips its raw bytes, which takes seconds -- and "the device open
and idle through heavy local work" is the state that preceded a wedge. The
writer exists so that work overlaps the next frame's scan instead. These tests
hold it to the two things that makes it worth having: it must actually write
everything, and one frame it cannot write must not take the rest of the roll
with it.
"""

import numpy as np
import pytest

from conftest import load_tool
from rps7200.library import FilmNotes

scan_roll = load_tool("scan_roll")


def job(number, path, library=None, image=None, raw=b"raw bytes"):
    return dict(
        number=number,
        path=path,
        dpi=600,
        image=np.zeros((8, 8, 3), np.uint16) if image is None else image,
        meta={"resolution_dpi": 600, "channels": 3},
        prescan=None,
        library=library,
        inquiry=None,
        capture={
            "reference": None,
            "ccd_mask": None,
            "raw": raw,
            "raw_layout": {"format": "index"},
        },
        tags=["roll"],
        film=FilmNotes(frame=f"roll/{number:02d}"),
    )


def test_every_submitted_frame_reaches_disk(tmp_path):
    writer = scan_roll.FrameWriter()
    for n in (1, 2, 3):
        writer.submit(**job(n, tmp_path / f"frame{n:02d}.tif"))
    writer.finish()

    assert not writer.errors
    assert [n for n, _ in writer.done] == [1, 2, 3]
    assert sorted(p.name for p in tmp_path.glob("*.tif")) == [
        "frame01.tif", "frame02.tif", "frame03.tif"
    ]


def test_a_frame_is_filed_in_the_library_with_its_raw_bytes(tmp_path):
    writer = scan_roll.FrameWriter()
    writer.submit(**job(1, tmp_path / "frame01.tif", library=str(tmp_path / "lib")))
    writer.finish()

    (_, entry), = writer.done
    assert entry is not None
    assert (entry / "scan.tif").exists()
    assert (entry / "raw.bin.gz").exists()


def test_one_unwritable_frame_does_not_end_the_roll(tmp_path):
    """A roll runs for hours; a frame that cannot be filed costs that frame."""
    writer = scan_roll.FrameWriter()
    writer.submit(**job(1, tmp_path / "frame01.tif"))
    writer.submit(**job(2, tmp_path / "no-such-dir" / "frame02.tif"))
    writer.submit(**job(3, tmp_path / "frame03.tif"))
    writer.finish()

    assert [n for n, _ in writer.done] == [1, 3]
    assert len(writer.errors) == 1
    assert "picture 2" in writer.errors[0]


def test_finish_waits_rather_than_dropping_what_is_queued(tmp_path):
    """finish() runs after the session closes, so nothing may still be pending."""
    writer = scan_roll.FrameWriter()
    for n in range(1, 7):
        writer.submit(**job(n, tmp_path / f"frame{n:02d}.tif"))
    writer.finish()

    assert len(list(tmp_path.glob("*.tif"))) == 6
    assert writer.queue.empty()


def test_the_queue_is_bounded(tmp_path):
    """Unbounded would hold whole frames in memory -- 142 MB each at 3600 dpi."""
    writer = scan_roll.FrameWriter(depth=2)
    assert writer.queue.maxsize == 2
    writer.finish()


@pytest.mark.parametrize("depth", [1, 4])
def test_any_depth_still_writes_everything(tmp_path, depth):
    writer = scan_roll.FrameWriter(depth=depth)
    for n in range(1, 5):
        writer.submit(**job(n, tmp_path / f"frame{n:02d}.tif"))
    writer.finish()
    assert [n for n, _ in writer.done] == [1, 2, 3, 4]
