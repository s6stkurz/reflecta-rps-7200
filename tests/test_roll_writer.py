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
from rps7200.session import NOT_FILED, WRITTEN

scan_roll = load_tool("scan_roll")


def job(number, path, library=None, image=None, raw=b"raw bytes"):
    return dict(
        number=number,
        # A frame can want several destinations now -- its place in the roll
        # and a copy wherever the operator asked for one.
        paths=[path] if path is not None else [],
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
    # A missing directory is no longer a failure -- the writer creates the
    # output folder it was pointed at. A *file* where a directory has to be is,
    # and it is the same kind of accident.
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"not a directory")

    writer = scan_roll.FrameWriter()
    writer.submit(**job(1, tmp_path / "frame01.tif"))
    writer.submit(**job(2, blocker / "frame02.tif"))
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


def test_the_queue_is_bounded_by_frames(tmp_path):
    """Unbounded would hold whole frames in memory -- 142 MB each at 3600 dpi,
    about 1.7 GB at 7200 dpi RGBI. Bounded by jobs, the room made for a
    roll's prescans was frames wherever no prescan was queued
    (`--no-library`): four of them waiting behind a stalled writer, where
    two is the bound, and the prescans, a few hundred kilobytes each, do
    not take a frame's place."""
    import threading
    import time

    go = threading.Event()
    writer = scan_roll.FrameWriter()
    write = writer._write
    writer._write = lambda j: (go.wait(10), write(j))[1]
    submitting = []

    def submitted(*jobs):
        t = threading.Thread(target=lambda: [writer.submit(**j) for j in jobs],
                             daemon=True)
        t.start()
        t.join(0.5)
        submitting.append(t)
        return not t.is_alive()

    try:
        writer.submit(**job(1, tmp_path / "frame01.tif"))
        deadline = time.monotonic() + 5
        while not writer.queue.empty() and time.monotonic() < deadline:
            time.sleep(0.01)                     # taken, and held in the write
        assert submitted(*(job(n, tmp_path / f"frame{n:02d}.tif")
                           for n in (2, 3)))
        assert not submitted(job(4, tmp_path / "frame04.tif")), \
            "a third frame waited behind a stalled writer"
        assert submitted(*(dict(job(n, tmp_path / f"prescan{n:02d}.tif"),
                                kind="prescan") for n in (5, 6, 7, 8))), \
            "a prescan waited for room a frame needs"
    finally:
        go.set()
        for t in submitting:                     # the frame that waited
            t.join(10)
        writer.finish()
    assert sorted(n for n, _ in writer.done) == list(range(1, 9))


@pytest.mark.parametrize("depth", [1, 4])
def test_any_depth_still_writes_everything(tmp_path, depth):
    writer = scan_roll.FrameWriter(depth=depth)
    for n in range(1, 5):
        writer.submit(**job(n, tmp_path / f"frame{n:02d}.tif"))
    writer.finish()
    assert [n for n, _ in writer.done] == [1, 2, 3, 4]


def test_a_jpeg_frame_leaves_its_infrared_beside_it_and_the_roll_keeps_tiff(tmp_path):
    """The two destinations of one frame, written in the same call.

    `_write` picks the format from each filename, so a roll's own `rolls/...tif`
    and an output folder set to JPEG are written side by side without the writer
    knowing the setting. The infrared plane follows the JPEG into a DNG and does
    not follow the TIFF anywhere -- a four-sample TIFF already carries it.
    """
    rolls = tmp_path / "rolls"
    out = tmp_path / "out"
    # Both there, as a roll's folder and the output folder are by the time
    # a frame is written: the writer makes neither (`FrameWriter._folder_for`).
    rolls.mkdir()
    out.mkdir()
    rgbi = np.zeros((8, 8, 4), np.uint16)
    rgbi[..., 3] = 4242                                  # a plane worth finding again

    writer = scan_roll.FrameWriter()
    writer.submit(**dict(job(1, None, image=rgbi),
                         paths=[rolls / "frame01.tif", out / "frame01_ir.jpg"]))
    writer.finish()

    assert not writer.errors
    assert sorted(p.name for p in rolls.iterdir()) == ["frame01.tif"]
    assert sorted(p.name for p in out.iterdir()) == ["frame01_ir.dng", "frame01_ir.jpg"]

    from rps7200 import tiff
    kept = tiff.read(str(out / "frame01_ir.dng"))
    assert kept.shape[2] == 4
    assert (kept[..., 3] == 4242).all(), "the plane, at full depth, not the picture"
    assert any("frame01_ir.dng" in n for n in writer.notes), "the operator is told"


def test_a_channel_it_will_not_deliver_costs_the_copy_and_not_the_entry(tmp_path):
    """`to_monochrome` refuses a channel outside `MONO_CHOICES` -- "I", or
    whatever a roll manifest or a hand-edited preset put back -- and it was
    asked before `library.save`, so the refusal took the pass's raw bytes
    with it. The entry is filed; the copy it could not make is the problem."""
    writer = scan_roll.FrameWriter()
    writer.submit(**dict(job(1, tmp_path / "frame01.tif",
                             library=str(tmp_path / "lib")),
                         mono=True, mono_channel="I"))
    writer.finish()

    (_, entry), = writer.done
    assert entry is not None and (entry / "raw.bin.gz").exists()
    assert not (tmp_path / "frame01.tif").exists()
    assert len(writer.errors) == 1 and "one-channel" in writer.errors[0]
    assert f"library entry {entry.name} is safe" in writer.errors[0]
    (filing,) = writer.filings
    assert filing.outcome == scan_roll.COPY_FAILED
    assert "one-channel" in filing.why_unwritten(tmp_path / "frame01.tif")


def test_a_roll_folder_deleted_between_two_frames_is_not_made_again(tmp_path):
    """Renamed or deleted by hand while its frames were filed -- the roll's
    lock keeps every program off it, not a hand -- it was made again, empty
    but for the frames after: the roll in two folders. The next frame's copy
    there is not written, and says why; its entry is filed and its copy in
    the output folder written."""
    roll = tmp_path / "rolls" / "strip"
    out = tmp_path / "out"
    roll.mkdir(parents=True)
    out.mkdir()
    writer = scan_roll.FrameWriter()
    for n in (1, 2):
        if n == 2:
            writer.queue.join()
            for f in roll.iterdir():
                f.unlink()
            roll.rmdir()
        writer.submit(**dict(job(n, None, library=str(tmp_path / "lib")),
                             paths=[roll / f"frame{n:02d}.tif",
                                    out / f"strip_frame{n:02d}.tif"],
                             roll_folder=roll))
    writer.finish()

    assert not roll.exists(), "the roll's folder was made again"
    assert sorted(p.name for p in out.iterdir()) == ["strip_frame01.tif",
                                                     "strip_frame02.tif"]
    second = writer.filings[1]
    assert second.outcome == scan_roll.COPY_FAILED and second.entry is not None
    (said,) = writer.errors
    assert (f"{roll} is gone -- moved or deleted while its frames were "
            "filed -- and is not made again") in said
    assert f"library entry {second.entry.name} is safe" in said


# -- a writer that dies -------------------------------------------------------


def test_a_job_that_breaks_the_fallback_is_still_reported_once(tmp_path):
    """A job `_write` could not handle is reported as not filed by a record
    made from the job's own fields -- and one whose fields would not make
    one raised from inside that handler, which ended the thread with the job
    never reported and every frame behind it waiting for room for good."""
    done, filed = [], []
    writer = scan_roll.FrameWriter(on_done=done.append)
    broken = dict(job(1, tmp_path / "frame01.tif"), number="one",
                  on_filed=filed.append)
    del broken["image"]                          # `_write` raises at once
    writer.submit(**broken)
    writer.submit(**job(2, tmp_path / "frame02.tif"))
    writer.finish()

    assert writer.died is None and writer.restarts == 0
    assert [f.outcome for f in done] == [NOT_FILED, WRITTEN]
    assert len(filed) == 1 and filed[0].outcome == NOT_FILED
    assert filed[0].number == 0 and "its record could not be made" \
        in filed[0].error
    assert (tmp_path / "frame02.tif").exists(), "the job after it was lost"
    assert writer.queue.unfinished_tasks == 0


@pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_dead_writer_is_restarted_and_nothing_queued_is_lost(tmp_path):
    """Anything that is no `Exception` still ends the thread -- here a report
    that raises SystemExit. `submit` then waited for room nothing would make,
    the scanner held between passes for good, and `finish` returned at once
    with every queued frame unwritten. It is started again on the same queue:
    every job is written, in order, each with its own compression."""
    import threading

    reports = []
    killed = threading.Event()

    def on_done(filing):
        reports.append(filing)
        if not killed.is_set():
            killed.set()
            raise SystemExit("the report ended the thread")

    go = threading.Event()
    writer = scan_roll.FrameWriter(on_done=on_done)
    write = writer._write
    writer._write = lambda j: (go.wait(10), write(j))[1]
    jobs = [dict(job(n, tmp_path / f"frame{n:02d}.tif"),
                 compress=bool(n % 2)) for n in range(1, 6)]

    def submit_all():
        for j in jobs:
            writer.submit(**j)

    submitting = threading.Thread(target=submit_all, daemon=True)
    submitting.start()
    go.set()
    submitting.join(5)
    assert not submitting.is_alive(), "submit waited for a writer that had died"
    writer.finish()

    assert killed.is_set() and writer.restarts == 1
    assert "SystemExit: the report ended the thread" in (writer.died or "")
    assert [f.number for f in writer.filings] == [1, 2, 3, 4, 5]
    assert sorted(p.name for p in tmp_path.glob("frame*.tif")) == [
        f"frame{n:02d}.tif" for n in range(1, 6)]
    restarted = [e for e in writer.errors if "started again" in e]
    assert len(restarted) == 1 and "nothing queued was lost" in restarted[0]
    assert any("started again" in n for n in writer.notes)
    assert writer.queue.unfinished_tasks == 0


@pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_finish_with_a_dead_writer_files_what_was_queued(tmp_path):
    """The thread dead before `finish`: it waited on the dead thread, which
    returned at once, and the queued frames were never written."""
    import threading

    go = threading.Event()
    writer = scan_roll.FrameWriter()
    write = writer._write

    def write_or_die(j):
        go.wait(10)
        if j["number"] == 1:
            raise KeyboardInterrupt("stand-in for a BaseException")
        return write(j)

    writer._write = write_or_die
    for n in (1, 2, 3):
        writer.submit(**job(n, tmp_path / f"frame{n:02d}.tif"))
    go.set()
    writer._thread.join(5)
    assert not writer._thread.is_alive()
    writer.finish()

    assert [(f.number, f.outcome) for f in writer.filings][0] == (
        1, NOT_FILED)
    assert [f.number for f in writer.filings] == [1, 2, 3]
    assert (tmp_path / "frame02.tif").exists() and \
        (tmp_path / "frame03.tif").exists()
    assert writer.restarts == 1


@pytest.mark.filterwarnings("ignore:.*INCOMPLETE marker:RuntimeWarning")
def test_a_marker_the_library_left_is_said_where_it_is_read(tmp_path,
                                                            monkeypatch):
    """`library.save` warns, on stderr alone, when an entry is filed but its
    INCOMPLETE marker could not be taken away. Said in `notes`, which the
    session relays to the window as the roll runs."""
    from rps7200 import library

    monkeypatch.setattr(library, "_unmark", lambda path: (_ for _ in ()).throw(
        PermissionError("held by a virus scanner")))
    writer = scan_roll.FrameWriter()
    writer.submit(**job(1, tmp_path / "frame01.tif",
                        library=str(tmp_path / "lib")))
    writer.finish()
    (_, entry), = writer.done
    assert any(f"{entry.name} is filed, but its INCOMPLETE marker" in n
               for n in writer.notes)
