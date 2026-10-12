"""The job engine behind the GUI: one thread owns the scanner, events come back.

The point of these tests is the two things that are easy to get wrong and
expensive to get wrong on the hardware: that every pass reaches the library
exactly once, and that a stop lands *between* frames rather than inside one.
Abandoning a read mid-scan is what costs a power cycle.

`FakeScanner` stands in for `DirectScanner` at the level the session actually
uses it. It stays here rather than in conftest because it is tuned to this loop,
the same reason `FakeRoll` stays beside test_roll.
"""
import inspect
import json
import os
import queue
import re
import shutil
import sys
import threading
import time
import types

from pathlib import Path

import numpy as np
import pytest

from conftest import walk_with_entries
from rps7200 import library, session, storage, tiff
from rps7200.direct import DirectScanner, RollFrame
from rps7200.library import FilmNotes
from rps7200.session import (
    Approved,
    Calibrate,
    Move,
    Prescan,
    Roll,
    Scan,
    ScanSession,
    estimate_seconds,
)


def picture(h=24, w=36, channels=4, seed=0):
    rng = np.random.default_rng(seed)
    return (rng.random((h, w, channels)) * 65535).astype(np.uint16)


class FakeTransport:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeInquiry:
    def describe(self):
        return "FAKE MF Scanner 1.70"


class FakeScanner:
    """Answers the handful of calls `ScanSession` makes, and records them."""

    def __init__(self, frames=3, on_yield=None, fail_scan=False):
        self.t = FakeTransport()
        self.log_hook = None
        self.progress_hook = None
        self._inquiry = FakeInquiry()
        self.calls = []
        self.produced = 0
        self.opened = False
        self.closed = False
        self._frames = frames
        self._on_yield = on_yield
        self._fail_scan = fail_scan
        self.only = None
        #: Where the film is, as the transport's counter says it. A roll now
        #: asks before it moves anything, so a stand-in with no transport at
        #: all refuses every roll -- which is right for a stand-in that cannot
        #: say, and wrong for one meant to be a strip on frame 1.
        self.pos = 0
        #: Every whole-frame move, in order, so a test can say which way the
        #: film went and whether it went before the roll started.
        self.moves = []

    def wait_warm(self, timeout=300.0, poll=5.0):
        # Asked by every roll before the counter is; the lamp here is warm.
        self.warmed = getattr(self, "warmed", 0) + 1

    def position(self):
        return self.pos

    def advance(self, steps=1, **kw):
        self.moves.append(("advance", steps))
        self.pos += 1
        return self.pos

    def retreat(self, steps=1, **kw):
        self.moves.append(("retreat", steps))
        if self.pos <= 0:
            return None
        self.pos -= 1
        return self.pos

    def open(self):
        self.opened = True
        return self

    def close(self):
        self.closed = True

    def inquiry(self, refresh=False):
        return self._inquiry

    def capture_record(self):
        # Real raw bytes, so the entry written has a raw.bin.gz to check.
        return {
            "reference": None,
            "ccd_mask": None,
            "raw": b"\x00\x01" * 32,
            "raw_layout": {"format": "index", "width": 36, "lines": 24,
                           "channels": 4},
        }

    def ensure_shading(self, path, reuse=False, skip=False):
        self.calls.append(("shading", reuse, skip))
        return {"action": "calibrated", "summary": "shading calibrated (fake)"}

    def prescan(self, resolution=300, frame=None, keep_raw=False,
                film="negative"):
        self.calls.append(("prescan", resolution, keep_raw))
        if self.progress_hook:
            self.progress_hook(287, 287)
        # Published per pass as the driver publishes it: a prescan with none
        # is shown and written, and not filed (`ScanSession._unpublished`).
        # `prescan_meta` is what a test sets to change it.
        published = getattr(self, "prescan_meta", {
            "resolution_dpi": resolution, "film": film, "channels": 3,
            "channel_order": ["R", "G", "B"]})
        self.last_scan_meta = dict(published) if published is not None else None
        return picture(channels=3, seed=99), None

    def scan(self, resolution=1800, infrared=True, **kw):
        self.calls.append(("scan", resolution, infrared, kw.get("auto_exposure")))
        if self._fail_scan:
            raise RuntimeError("the scanner said no")
        if self.log_hook:
            self.log_hook(f"{resolution} lines")
        return picture(channels=4 if infrared else 3, seed=resolution), {
            "resolution_dpi": resolution,
            "channels": 4 if infrared else 3,
            "channel_order": list("RGBI"[: 4 if infrared else 3]),
            "width": 36,
            "height": 24,
            "depth": 16,
            # What the scanner was asked for, as `DirectScanner.scan` returns
            # it. Here because a roll's manifest records these so a roll that
            # died can be resumed with the same request -- and without them the
            # fake could not exercise the one part of that anybody asked for.
            "exposure": [30766, 45619, 15017, 7745],
            "gain": [39, 33, 21, 25],
            "offset": [14, 12, 32, 8],
        }

    def scan_roll(self, frames=None, resolution=1800, infrared=True,
                  dry_run=False, skip=0, only=None, first_index=0, **kw):
        """Frames from wherever the film is, as the driver's loop gives them.

        It used to hand back frame ``skip + i`` at position ``skip + i``
        whatever the transport said, which is the demo's old teleport in
        miniature: a session that never put the film anywhere passed every
        test, because the film was always where the numbers said. Now the
        first frame is where `pos` is, the film advances between frames, and
        the index counts from ``first_index`` -- so a roll numbers the frame it
        is on only if the session put the film there first.
        """
        self.calls.append(("roll", frames, resolution, dry_run, skip))
        self.only = only
        self.pos += skip
        for i in range(frames or self._frames):
            if i:
                self.pos += 1             # the advance between frames
            index = first_index + skip + i
            # The real one advances past an unchosen frame without prescanning
            # it, which from here looks like a frame that never arrives.
            if only is not None and index not in only:
                continue
            if self._on_yield:
                # Stands in for the operator pressing stop while this frame is
                # still being scanned.
                self._on_yield(i)
            self.produced += 1
            image, meta = (None, {}) if dry_run else self.scan(
                resolution=resolution, infrared=infrared
            )
            yield RollFrame(
                index=index, position=self.pos, image=image, meta=meta,
                prescan=picture(channels=3, seed=i),
                registration={"offset_mm": 0.04, "shortfall_mm": 0.02},
                # As the driver publishes it: a prescan with none is
                # written and not filed (`ScanSession._unpublished`).
                prescan_meta={"resolution_dpi": 300,
                              "channel_order": list("RGB")},
            )


def run(job, tmp_path, scanner=None, extra=None, timeout=60.0):
    """Submit `job` (and any `extra`), wait for the session to close, return events.

    The wait ends as soon as the session closes; the limit only decides how
    long a real hang takes to fail. Ten seconds failed two roll tests on a
    loaded Windows runner whose whole suite ran half again as long as usual.
    """
    scanner = scanner or FakeScanner()
    s = ScanSession(root=str(tmp_path), rolls=str(tmp_path / "rolls"),
                    open_scanner=lambda: scanner, verbose=False)
    if extra:
        extra(s, scanner)
    s.start()
    s.submit(job)
    s.shutdown()
    events = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events.extend(s.poll())
        if any(e.kind == "closed" for e in events):
            break
        time.sleep(0.01)
    s.join(timeout=2.0)
    events.extend(s.poll())
    assert any(e.kind == "closed" for e in events), "session never closed"
    return s, scanner, events


def kinds(events, kind):
    return [e for e in events if e.kind == kind]


def frame_entries(root):
    """The library's entries less the prescans a roll files beside each frame."""
    return [e for e in library.entries(root) if "prescan" not in e["tags"]]


def test_a_failed_job_carries_its_traceback_for_the_kept_log(tmp_path,
                                                            monkeypatch):
    """A job that died of a bug reached the window as "KeyError: 'x'", and
    its traceback was printed nowhere -- an overnight roll that broke could
    not be diagnosed from the log kept for exactly that. Every failure
    carries it, refusal or bug: no type tells the two apart."""
    def the_step_that_broke(job):
        raise KeyError("x")

    monkeypatch.setattr(ScanSession, "_dispatch",
                        lambda self, job: the_step_that_broke(job))
    _s, _scanner, events = run(Scan(resolution=600), tmp_path)
    (failed,) = kinds(events, "failed")
    assert failed.text == "KeyError: 'x'"
    assert failed.detail.startswith("Traceback (most recent call last)")
    assert "the_step_that_broke" in failed.detail

    class Unopenable(FakeScanner):
        def open(self):
            raise RuntimeError("no scanner on the bus")

    _s, _scanner, events = run(Scan(resolution=600), tmp_path,
                               scanner=Unopenable())
    (failed,) = kinds(events, "failed")
    assert failed.text == "could not open the scanner: no scanner on the bus"
    assert "Traceback" in failed.detail and "in open" in failed.detail


# -- filing -----------------------------------------------------------------


def test_a_scan_is_filed_exactly_once(tmp_path):
    """Twice is the failure mode that matters: the driver can file a scan
    itself, and letting it do that as well as the session writes every frame
    twice -- 43 GB of duplicate on a 7200 dpi roll."""
    run(Scan(resolution=600, infrared=True), tmp_path)
    entries = library.entries(tmp_path)
    assert len(entries) == 1
    assert entries[0]["scan"]["resolution_dpi"] == 600


def test_a_filed_scan_keeps_the_raw_bytes(tmp_path):
    """Raw bytes, reference and mask are the only things that make a scan
    re-decodable later, and none of them survive in a TIFF."""
    run(Scan(resolution=600), tmp_path)
    entry = tmp_path / library.entries(tmp_path)[0]["id"]
    assert (entry / "raw.bin.gz").exists()
    assert library.read_raw(entry) == b"\x00\x01" * 32


class CorrectingScanner(FakeScanner):
    """Returns corrected pixels and keeps the raw ones aside, as the driver does.

    `DirectScanner.scan` hands back the flat-fielded image -- what a caller
    wants to look at -- and keeps what the scanner sent in `last_pixels_raw`.
    The plain fake returns one array and no `last_pixels_raw`, which is why no
    test here could see the Scan job filing the corrected one.
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        self.claimed = []
        self.answered = []

    def scan(self, resolution=1800, infrared=True, **kw):
        image, meta = super().scan(resolution=resolution, infrared=infrared, **kw)
        self.last_pixels_raw = image
        corrected = (image // 2).astype(image.dtype)
        return corrected, dict(meta, shading={"columns": 36, "clipped": 0})

    def debug_claim(self, pixels):
        self.claimed.append(pixels)
        # The receipt, as `DirectScanner.debug_claim` hands one back: told
        # the entry once the writer has filed the pass, or None.
        return self.answered.append


def test_a_scan_files_the_raw_pixels_not_the_corrected_ones(tmp_path):
    """Filing the corrected image as `scan.tif` made every view and export of
    it correct a second time, and `reconstruct` call it a changed decode."""
    scanner = CorrectingScanner()
    run(Scan(resolution=600), tmp_path, scanner=scanner)
    record = library.entries(tmp_path)[0]
    stored = tiff.read(tmp_path / record["id"] / "scan.tif")
    assert np.array_equal(stored, scanner.last_pixels_raw)
    assert record["image"]["corrections_applied"] == []


def test_corrected_pixels_with_no_raw_beside_them_are_labelled(tmp_path):
    """A stand-in that keeps no raw pixels still must not file a lie."""
    scanner = CorrectingScanner()
    real = scanner.scan

    def scan(**kw):
        image, meta = real(**kw)
        scanner.last_pixels_raw = None
        return image, meta

    scanner.scan = scan
    run(Scan(resolution=600), tmp_path, scanner=scanner)
    record = library.entries(tmp_path)[0]
    assert record["image"]["corrections_applied"] == ["shading"]


def test_what_the_session_files_it_claims_from_debug_filing(tmp_path):
    """So RPS7200_DEBUG=1 files the passes the session does not keep --
    probes, holds -- without writing the ones it does keep a second time."""
    scanner = CorrectingScanner()
    run(Scan(resolution=600), tmp_path, scanner=scanner)
    assert len(scanner.claimed) == 1
    assert scanner.claimed[0] is scanner.last_pixels_raw


class SpoolingScanner(FakeScanner, DirectScanner):
    """The fake's answers, with the driver's own debug spool, claim and flush.

    Every scan spools a metering probe first -- a pass nothing in the session
    files, which debug filing exists for -- and then the pass itself, as
    `DirectScanner.scan` does.
    """

    def __init__(self, **kw):
        FakeScanner.__init__(self, **kw)
        self.verbose = False
        self.debug = True
        self._debug_pending = []
        self._debug_spool = None
        self._debug_lock = threading.Lock()
        self._shading = None
        self._ccd_mask = None

    def scan(self, resolution=1800, infrared=True, **kw):
        self._debug_capture(picture(channels=3, seed=5),
                            {"resolution_dpi": 300, "channels": 3,
                             "channel_order": list("RGB")})
        image, meta = FakeScanner.scan(self, resolution=resolution,
                                       infrared=infrared, **kw)
        self._debug_capture(image, meta)
        self.last_pixels_raw = image
        return image, meta

    def close(self):
        FakeScanner.close(self)
        self._debug_flush()


def test_a_pass_the_writer_could_not_file_is_filed_by_debug_filing(
        tmp_path, monkeypatch):
    """The session claimed each pass as it queued it, and the scanner closed
    -- deleting every claimed spool -- before the writer had filed anything.
    A library.save that failed then lost the pass from both places."""
    real = library.save

    def save(image, meta, **kw):
        if "debug" not in (kw.get("tags") or ()):
            raise OSError(28, "No space left on device")
        return real(image, meta, **kw)

    monkeypatch.setattr(library, "save", save)
    run(Scan(resolution=600), tmp_path, scanner=SpoolingScanner())
    shapes = sorted(tuple(e["image"]["shape"]) for e in library.entries(tmp_path))
    assert shapes == [(24, 36, 3), (24, 36, 4)], \
        "the scan the writer could not file was lost"


def test_a_pass_the_writer_filed_is_not_filed_again(tmp_path):
    scanner = SpoolingScanner()
    run(Scan(resolution=600), tmp_path, scanner=scanner)
    entries = library.entries(tmp_path)
    assert sorted(tuple(e["image"]["shape"]) for e in entries) == \
        [(24, 36, 3), (24, 36, 4)]
    assert [e for e in entries if "debug" in e["tags"]][0]["image"]["shape"] \
        == [24, 36, 3], "the scan was filed twice, or the probe not at all"
    assert not (tmp_path / DirectScanner.DEBUG_SPOOL_DIR).exists()


def test_a_force_abort_still_files_what_debug_filing_holds(tmp_path):
    """force_abort skipped close(), then the only caller of the flush, so
    every probe and hold of the session was left in the spool, unfiled. The
    session now closes after an abort too, and settles what close() left."""
    scanner = SpoolingScanner()
    s = ScanSession(root=str(tmp_path), rolls=str(tmp_path / "rolls"),
                    open_scanner=lambda: scanner, verbose=False)
    s.start()
    s.submit(Scan(resolution=600))
    events = []
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not kinds(events, "finished"):
        events.extend(s.poll())
        time.sleep(0.01)
    s.force_abort()
    s.shutdown()
    while time.monotonic() < deadline and not kinds(events, "closed"):
        events.extend(s.poll())
        time.sleep(0.01)
    s.join(timeout=2.0)
    shapes = sorted(tuple(e["image"]["shape"]) for e in library.entries(tmp_path))
    assert (24, 36, 3) in shapes, "the probe was left unfiled in the spool"


def test_a_pass_filed_without_its_bytes_is_not_claimed(tmp_path):
    """The spooled copy has the bytes the session dropped as another pass's,
    so it is the only copy that does and must be filed too."""
    scanner = CorrectingScanner()
    scanner.capture_record = lambda: {
        "reference": None, "ccd_mask": None, "raw": b"\x00" * 64,
        "raw_layout": {"format": "index", "width": 860, "lines": 573,
                       "channels": 4},
    }
    run(Scan(resolution=600), tmp_path, scanner=scanner)
    assert library.entries(tmp_path), "the pass was not filed at all"
    assert scanner.claimed == []


def test_the_session_files_debug_passes_into_its_own_library(tmp_path):
    scanner = SpoolingScanner()
    run(Scan(resolution=600), tmp_path, scanner=scanner)
    assert scanner.debug_root == str(tmp_path)


def test_a_pass_whose_filing_failed_is_answered_as_not_filed(tmp_path,
                                                             monkeypatch):
    """Claimed as it was queued and let go on the claim alone, a pass whose
    filing then failed was deleted from the debug spool at close as well --
    the one copy RPS7200_DEBUG=1 exists to keep. The claim is answered once
    the writer is done with it, and here the answer is that it was not."""
    scanner = CorrectingScanner()
    refusing(monkeypatch, tmp_path / "library")
    events = []
    s = ScanSession(root=str(tmp_path / "library"),
                    open_scanner=lambda: scanner, verbose=False)
    s.start()
    s.submit(Scan(resolution=600))
    s.shutdown()
    s.join(timeout=10.0)
    events.extend(s.poll())
    assert any("could not be filed" in (e.text or "") for e in events)
    assert scanner.answered == [None], "a pass never filed was answered as filed"


def test_the_default_scanner_leaves_debug_to_the_environment(monkeypatch):
    # No libusb needed to build one: this is about the flag, not the bus.
    from rps7200 import direct

    monkeypatch.setattr(direct, "Transport", lambda **kw: FakeTransport())
    monkeypatch.setenv("RPS7200_DEBUG", "1")
    s = ScanSession(root=None, open_scanner=None, verbose=False)
    assert s._default_scanner().debug is True
    monkeypatch.setenv("RPS7200_DEBUG", "0")
    assert s._default_scanner().debug is False


def test_a_single_scan_is_compressed_only_after_the_scanner_closes(
        tmp_path, monkeypatch):
    """Compressing with the scanner open and idle preceded a wedge. A single
    scan is filed plain while the session is open, and compacted after."""
    scanner = FakeScanner()
    compacted = []
    real = library.compact

    def compact(entry):
        compacted.append((entry, scanner.closed,
                          (Path(entry) / library.RAW_PLAIN).exists()))
        return real(entry)

    monkeypatch.setattr(library, "compact", compact)
    run(Scan(resolution=600), tmp_path, scanner=scanner)
    assert len(compacted) == 1
    _entry, closed, plain = compacted[0]
    assert closed, "compressed with the scanner still open"
    assert plain, "the entry was not filed plain"
    entry = tmp_path / library.entries(tmp_path)[0]["id"]
    assert (entry / library.RAW_FILE).exists()
    assert not (entry / library.RAW_PLAIN).exists()
    # The fake has no shading reference; that is all verify may say.
    assert [p for p in library.verify(tmp_path) if "never be corrected" not in p] == []


def _compressions(monkeypatch):
    """What each writer job was queued as: frame number -> compress."""
    queued = {}
    real = session.FrameWriter.submit

    def submit(self, **job):
        if job.get("library"):
            queued[job["number"]] = job.get("compress", True)
        return real(self, **job)

    monkeypatch.setattr(session.FrameWriter, "submit", submit)
    return queued


def test_a_rolls_last_frame_is_filed_plain(tmp_path, monkeypatch):
    """A roll's frames gzip on the writer while the next one scans. The last
    has no next one: gzipped, it ran with the device open and idle -- at 3600
    dpi RGBI some 280 MB, the state that preceded a wedge."""
    queued = _compressions(monkeypatch)
    run(Roll(frames=3, resolution=600, name="tail"), tmp_path)
    assert queued == {1: True, 2: True, 3: False}


def test_the_last_chosen_frame_is_filed_plain(tmp_path, monkeypatch):
    queued = _compressions(monkeypatch)
    run(Roll(frames=5, only=(2, 4), resolution=600, name="chosen"), tmp_path)
    assert queued == {2: True, 4: False}


def test_the_frame_a_stop_ends_on_is_filed_plain(tmp_path, monkeypatch):
    queued = _compressions(monkeypatch)
    holder = {}

    def press_stop_during(index):
        if index == 1:
            holder["s"].request_stop()

    scanner = FakeScanner(frames=4, on_yield=press_stop_during)
    run(Roll(frames=4, resolution=600, name="stopped"), tmp_path,
        scanner=scanner, extra=lambda s, _scanner: holder.update(s=s))
    assert queued == {1: True, 2: False}


def test_a_roll_frame_the_writer_starts_after_the_roll_is_filed_plain(tmp_path):
    """The end nobody saw coming -- a blank frame, the end of the strip --
    leaves frames queued to compress. With nothing scanning they are written
    plain, and compacted once the device has closed."""
    writer = session.FrameWriter(idle=lambda: True)
    writer.submit(number=1, paths=[], image=picture(), meta={"resolution_dpi": 600},
                  dpi=600, library=str(tmp_path / "lib"), film=FilmNotes(),
                  tags=[], prescan=None, inquiry=None,
                  capture={"raw": RAW, "raw_layout": LAYOUT}, compress=True)
    writer.finish()
    (entry,) = writer.uncompressed
    assert (entry / library.RAW_PLAIN).exists()
    assert not (entry / library.RAW_FILE).exists()


def test_a_roll_frame_filed_while_the_next_one_scans_is_compressed(tmp_path):
    writer = session.FrameWriter(idle=lambda: False)
    writer.submit(number=1, paths=[], image=picture(), meta={"resolution_dpi": 600},
                  dpi=600, library=str(tmp_path / "lib"), film=FilmNotes(),
                  tags=[], prescan=None, inquiry=None,
                  capture={"raw": RAW, "raw_layout": LAYOUT}, compress=True)
    writer.finish()
    assert writer.uncompressed == []
    (entry,) = [tmp_path / "lib" / e["id"] for e in library.entries(tmp_path / "lib")]
    assert (entry / library.RAW_FILE).exists()


def test_every_entry_of_a_roll_ends_up_compressed(tmp_path):
    """Plain only until the device closes."""
    run(Roll(frames=3, resolution=600, name="whole"), tmp_path)
    entries = library.entries(tmp_path)
    folders = [tmp_path / e["id"] for e in entries]
    # Three frames, and the prescan each was framed on: an entry of its own,
    # which this fake gives no raw bytes of its own to carry.
    frames = [tmp_path / e["id"] for e in entries if "prescan" not in e["tags"]]
    assert len(folders) == 6 and len(frames) == 3
    assert all((f / library.RAW_FILE).exists() for f in frames)
    assert not any((f / library.RAW_PLAIN).exists() for f in folders)


def test_the_delivered_copy_of_a_single_scan_is_written_plain_too(
        tmp_path, monkeypatch):
    """The entry was kept plain until close and the output-folder copy of the
    very same pass was deflated straight away, scanner open and idle -- the
    comment choosing plain named TIFF deflate as the hazard. A roll's copies
    still compress: the device is busy with the next frame then."""
    from rps7200 import tiff

    written = []
    real = tiff.write

    def spy(path, image, *a, compress=True, **kw):
        written.append((Path(path), compress))
        return real(path, image, *a, compress=compress, **kw)

    monkeypatch.setattr(tiff, "write", spy)
    out = tmp_path / "out"
    out.mkdir()
    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(tmp_path / "r"),
                    out_dir=str(out), open_scanner=FakeScanner, verbose=False)
    s.start()
    s.submit(Scan(resolution=600))
    s.shutdown()
    s.join(timeout=15)
    delivered = [c for p, c in written if out in p.parents]
    assert delivered == [False], written

    # A roll's copy compresses while the next frame scans, and is plain when
    # its entry is: once nothing is scanning (`FrameWriter.idle`) -- which,
    # with a fake that yields every frame at once, is every frame of a
    # session's roll, so the writer is asked directly.
    for scanning in (True, False):
        written.clear()
        out = tmp_path / f"out-roll-{scanning}"
        out.mkdir()
        writer = session.FrameWriter(idle=lambda s=scanning: not s)
        writer.submit(number=1, paths=[out / "frame01.tif"], image=picture(),
                      meta={"resolution_dpi": 600}, dpi=600,
                      library=str(tmp_path / f"lib-{scanning}"),
                      film=FilmNotes(), tags=[], prescan=None, inquiry=None,
                      capture={"raw": RAW, "raw_layout": LAYOUT}, compress=True)
        writer.finish()
        delivered = [c for p, c in written if out in p.parents]
        assert delivered == [scanning], written


def test_an_aimed_walks_output_prescan_is_the_aimed_one(tmp_path):
    """The prescan a correction replaced was given the output folder's name
    for the frame, the same one the aimed prescan had just been given -- the
    name is chosen before either is written -- and, written second, it
    replaced the aimed one there."""
    from rps7200 import tiff

    aimed = picture(channels=3, seed=7)
    before = picture(channels=3, seed=8)

    class Aiming(FakeScanner):
        def scan_roll(self, frames=None, first_index=0, **kw):
            yield RollFrame(index=first_index, position=self.pos, image=None,
                            meta={"resolution_dpi": 300,
                                  "channel_order": ["R", "G", "B"]},
                            prescan=aimed, prescan_before=before,
                            registration={"offset_mm": 0.0})

    out = tmp_path / "out"
    out.mkdir()
    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(tmp_path / "r"),
                    out_dir=str(out), open_scanner=Aiming, verbose=False)
    s.start()
    s.submit(Roll(frames=1, resolution=300, dry_run=True, name="aimed"))
    s.shutdown()
    s.join(timeout=15)
    delivered = {p.name: tiff.read(str(p)) for p in out.rglob("*.tif")}
    assert set(delivered) == {"aimed_frame01_300dpi.tif",
                              "aimed_frame01_300dpi-before.tif"}, set(delivered)
    assert np.array_equal(delivered["aimed_frame01_300dpi.tif"], aimed)
    assert np.array_equal(delivered["aimed_frame01_300dpi-before.tif"], before)


def test_a_prescan_is_filed_too(tmp_path):
    """CLAUDE.md says file every scan, without an exception for the cheap ones.
    A prescan is ~370 KB and it is the evidence about framing."""
    run(Prescan(), tmp_path)
    entries = library.entries(tmp_path)
    assert len(entries) == 1
    assert "prescan" in entries[0]["tags"]
    assert "gui" in entries[0]["tags"]


def test_a_prescan_is_filed_with_the_pass_its_own_meta(tmp_path):
    """It used to be filed with a meta built by hand here -- `resolution_dpi`
    and `channel_order`, nothing else. That dropped `shading`, and with it
    `protocol_revision` and the exposure, so 26 real entries described
    themselves as uncorrected raw when their pixels were corrected. Every one
    of them made `reconstruct` report a changed decode, which is the check that
    is supposed to catch a real decode regression.

    The fake scanner publishes `last_scan_meta` the way the real one does; the
    point of the test is that the session reads it instead of inventing one."""
    scanner_meta = {"resolution_dpi": 300, "channel_order": ["R", "G", "B"],
                    "protocol_revision": 99, "depth": 8,
                    "shading": {"columns": 16, "width": 16}}

    def with_meta(_session, scanner):
        scanner.prescan_meta = scanner_meta
        scanner.last_pixels_raw = None

    _, scanner, _ = run(Prescan(), tmp_path, extra=with_meta)
    record = library.entries(tmp_path)[0]
    assert record["scan"]["protocol_revision"] == 99, \
        "the pass's own meta must reach the entry, not a hand-built stand-in"
    assert record["calibration"]["report"] == {"columns": 16, "width": 16}


def test_a_prescan_that_published_no_meta_is_shown_and_written_not_filed(
        tmp_path):
    """The window's prescan still had the hand-built meta as a fallback, for
    a scanner that published none -- the very substitute the test above is
    about, kept for anything but the real driver. Such a pass is now what a
    roll's is (`_unpublished`): shown, written to the output folder, kept
    out of the library, and said."""
    out = tmp_path / "out"
    out.mkdir()

    def unpublished(s, scanner):
        scanner.prescan_meta = None
        s.out_dir = out

    _, _, events = run(Prescan(), tmp_path / "lib", extra=unpublished)
    assert library.entries(tmp_path / "lib") == [], \
        "filed on a meta made up from the job"
    assert [e.result.kind for e in kinds(events, "result")] == ["prescan"], \
        "the picture was not shown"
    assert [p.name for p in out.rglob("*.tif")], "the copy was not written"
    said = " ".join(e.text for e in kinds(events, "log"))
    assert "published no record of this pass" in said, said


def test_a_prescan_keeps_its_raw_bytes(tmp_path):
    _, scanner, _ = run(Prescan(), tmp_path)
    assert ("prescan", 300, True) in scanner.calls


def test_film_notes_reach_the_entry(tmp_path):
    run(Scan(resolution=600, notes=FilmNotes(stock="Kodak Gold 200", frame="7"),
             tags=("test",)), tmp_path)
    record = library.entries(tmp_path)[0]
    assert record["film"]["stock"] == "Kodak Gold 200"
    assert record["film"]["frame"] == "7"
    assert "test" in record["tags"]


def test_every_frame_of_a_roll_is_filed(tmp_path):
    run(Roll(frames=3, resolution=600, name="strip1"), tmp_path)
    entries = frame_entries(tmp_path)
    # Three pictures, and nothing filed twice.
    assert len(entries) == 3
    assert all("roll" in e["tags"] and "strip1" in e["tags"] for e in entries)


def test_a_roll_writes_its_manifest_after_every_frame(tmp_path):
    """A roll takes hours. A crash should cost the frame it was on, not the roll."""
    run(Roll(frames=3, resolution=600, name="strip2"), tmp_path)
    manifest = tmp_path / "rolls" / "strip2" / "roll.json"
    assert manifest.exists()
    import json
    recorded = json.loads(manifest.read_text(encoding="utf-8"))
    assert len(recorded["frames"]) == 3
    assert recorded["frames"][0]["registration"]["offset_mm"] == 0.04


def _manifest_of(tmp_path, name, survey=False):
    return json.loads((tmp_path / "rolls" / name /
                       ("survey.json" if survey else "roll.json"))
                      .read_text(encoding="utf-8"))


def test_a_roll_says_when_it_ran_and_that_it_finished(tmp_path):
    """Frames 1-9 done and 10-38 missing said nothing about whether frame 10
    was the end of the film, a Stop, or a device gone suspect -- which is what
    decides whether a resume is safe."""
    run(Roll(frames=2, resolution=600, name="ran"), tmp_path)
    manifest = _manifest_of(tmp_path, "ran")
    assert manifest["started"] and manifest["finished"]
    assert "stopped" not in manifest


def test_a_roll_says_it_was_stopped(tmp_path):
    holder = {}

    def press_stop(index):
        if index == 0:
            holder["s"].request_stop()

    run(Roll(frames=3, resolution=600, name="halted"), tmp_path,
        scanner=FakeScanner(frames=3, on_yield=press_stop),
        extra=lambda s, _scanner: holder.update(s=s))
    assert "as asked" in _manifest_of(tmp_path, "halted")["stopped"]


def test_a_roll_says_what_ended_it(tmp_path):
    from rps7200.protocol import DeviceSuspect

    class Suspect(FakeScanner):
        def scan_roll(self, **kw):
            yield from super().scan_roll(**kw)
            raise DeviceSuspect("a read was abandoned")

    _s, _scanner, events = run(Roll(frames=1, resolution=600, name="suspect"),
                               tmp_path, scanner=Suspect())
    assert "DeviceSuspect" in _manifest_of(tmp_path, "suspect")["stopped"]
    assert kinds(events, "failed")


def test_a_roll_that_ends_short_of_what_was_asked_says_so(tmp_path):
    """A frame with no picture in it reads as the end of the film, and the
    roll finished like any other."""
    class EndsEarly(FakeScanner):
        def scan_roll(self, frames=None, **kw):
            for rf in super().scan_roll(frames=frames, **kw):
                if rf.index == 2:
                    return
                yield rf

    _s, _scanner, events = run(Roll(frames=5, resolution=600, name="short"),
                               tmp_path, scanner=EndsEarly(frames=5))
    said = _manifest_of(tmp_path, "short")["stopped"]
    assert "ended after 2 of the 5" in said
    assert any("ended after 2 of the 5" in e.text
               for e in kinds(events, "finished"))


def test_a_stop_while_passing_unchosen_frames_is_a_stop(tmp_path):
    """The driver looks at the stop before every advance, the ones past
    frames nobody chose included, and returns without yielding. The roll
    took that for the driver's own end and recorded a Stop as "ended after 1
    of the 2 frames asked for" -- the end of the film."""
    holder = {}

    class StopWhilePassing(FakeScanner):
        def scan_roll(self, should_stop=None, **kw):
            for rf in super().scan_roll(**kw):
                yield rf
                # Advancing past frames 2 and 3, nobody's choice, the
                # operator presses Stop; the driver sees it and returns.
                holder["s"].request_stop()
                if should_stop():
                    return

    run(Roll(frames=4, only=(1, 4), resolution=600, name="passing"), tmp_path,
        scanner=StopWhilePassing(frames=4),
        extra=lambda s, _scanner: holder.update(s=s))
    said = _manifest_of(tmp_path, "passing")["stopped"]
    assert "ended after" not in said
    assert said == "stopped after frame 1, as asked"


def test_a_roll_is_short_only_of_the_chosen_frames_it_can_reach(tmp_path):
    """Frame 5 is past the four places the roll was given, so it is never
    reached, and a roll that took frame 2 took everything it could."""
    run(Roll(frames=4, only=(2, 5), resolution=600, name="reach"), tmp_path,
        scanner=FakeScanner(frames=4))
    assert "stopped" not in _manifest_of(tmp_path, "reach")


def test_a_walk_names_its_picture_from_before_the_aim(tmp_path):
    """Named in its record, as the roll tool names it; unnamed, carrying the
    walk to another folder left it behind."""
    class Aimed(FakeScanner):
        def scan_roll(self, first_index=0, **kw):
            yield RollFrame(
                index=first_index, position=self.pos, image=None, meta={},
                prescan=np.full((24, 36, 3), 50, np.uint8), registration={},
                prescan_before=np.full((24, 36, 3), 20, np.uint8),
                prescan_meta={"resolution_dpi": 300,
                              "channel_order": list("RGB")})

    run(Roll(frames=1, dry_run=True, name="aimed"), tmp_path, scanner=Aimed())
    (record,) = _manifest_of(tmp_path, "aimed", survey=True)["frames"]
    assert record["prescan_before"] == "prescan01-before.tif"
    assert (tmp_path / "rolls" / "aimed" / record["prescan_before"]).exists()


def test_a_roll_records_what_it_would_take_to_finish_it(tmp_path):
    """A roll that dies at frame 11 of 24 has to be finishable -- a year later,
    by which time the window's own settings have moved on to other film. So the
    manifest carries the job rather than relying on anything outside itself.

    The shading reference is deliberately *not* in it, and not because it could
    not be -- `load_shading` exists. It is that a reference describes the sensor
    at the exposure and gain that measured it, so the one a roll started with is
    the wrong thing to hand a resume months later.
    """
    run(Roll(frames=2, resolution=600, infrared=False, fast_infrared=False,
             meter="once", name="strip3"), tmp_path)
    recorded = json.loads(
        (tmp_path / "rolls" / "strip3" / "roll.json").read_text(encoding="utf-8"))

    settings = recorded["settings"]
    assert settings["resolution"] == 600
    assert settings["infrared"] is False
    assert settings["fast_infrared"] is False, "younger than the rolls on disk"
    assert settings["meter"] == "once"
    assert "shading" not in settings and "reference" not in settings

    # Which frames are finished, so a resume offers the rest and not all of it.
    assert [f["done"] for f in recorded["frames"]] == [True, True]

    # And what the scanner was asked for, per frame -- metering each frame is
    # the default, and then no single exposure describes the roll.
    first = recorded["frames"][0]
    assert first["exposure"] == [30766, 45619, 15017, 7745]
    assert first["gain"] == [39, 33, 21, 25]
    assert first["offset"] == [14, 12, 32, 8]


def test_finishing_a_roll_keeps_what_the_first_attempt_did(tmp_path):
    """A resumed run writes into the manifest its earlier attempt left.

    Without carrying the earlier frames forward the second run *replaced* the
    first: four frames scanned across two sessions came back as a two-frame
    roll, with no record that the first two were ever taken -- in the one file
    whose job is to still describe this roll a year from now. And `wanted` is
    the union, because a resumed run is told only what is *left*, so its `only`
    is not the roll's set.
    """
    run(Roll(frames=4, only=(1, 2), resolution=600, name="strip"), tmp_path)
    run(Roll(frames=4, only=(3, 4), resolution=600, name="strip"), tmp_path)

    recorded = json.loads(
        (tmp_path / "rolls" / "strip" / "roll.json").read_text(encoding="utf-8"))
    assert sorted(f["number"] for f in recorded["frames"]) == [1, 2, 3, 4]
    assert recorded["wanted"] == [1, 2, 3, 4]
    assert all(f["done"] for f in recorded["frames"])


def test_a_resume_told_what_is_left_keeps_the_frames_before_it(tmp_path):
    """A roll run to the end of the strip names no frames and writes no
    `wanted`; a reader takes every frame it recorded as the roll's. Resumed
    with only what was left -- the window's reopened roll, 1, 2, 4 and 5
    done -- the union started from nothing and `wanted` became the resumed
    few: the roll read as finished with a frame still to do, and a frame that
    failed outside the range left the list of what remained."""
    path = tmp_path / "rolls" / "open" / "roll.json"
    strip = FakeScanner(frames=2)
    run(Roll(frames=None, resolution=600, name="open"), tmp_path,
        scanner=strip)
    assert json.loads(path.read_text(encoding="utf-8"))["wanted"] is None
    run(Roll(frames=4, only=(3, 4), resolution=600, name="open"), tmp_path)
    assert json.loads(path.read_text(encoding="utf-8"))["wanted"] == [
        1, 2, 3, 4]
    # A run to the end of the strip still names none: the reader's own
    # fallback, every frame recorded, then counts this run's frames as well.
    run(Roll(frames=None, resolution=600, name="open2"), tmp_path,
        scanner=FakeScanner(frames=2))
    run(Roll(frames=None, start_at=3, resolution=600, name="open2"), tmp_path,
        scanner=FakeScanner(frames=2))
    recorded = json.loads((tmp_path / "rolls" / "open2" / "roll.json")
                          .read_text(encoding="utf-8"))
    assert recorded["wanted"] is None


def test_a_range_is_frames_asked_for_even_where_nothing_came_before(tmp_path):
    """A roll is recorded frame by frame as it goes, so a range left to the
    reader's fallback read as only what had been scanned: "1 to 4", stopped
    after frame 1, reopened as "nothing left to scan". The frames earlier
    runs recorded stay in, as they do beside `only`."""
    path = tmp_path / "rolls" / "range" / "roll.json"
    run(Roll(frames=2, resolution=600, name="range"), tmp_path)
    assert json.loads(path.read_text(encoding="utf-8"))["wanted"] == [1, 2]
    assert session.wanted_after(
        {}, Roll(frames=4, start_at=1, name="x")) == [1, 2, 3, 4]
    assert session.wanted_after(
        {"frames": [{"number": 1, "done": True}]},
        Roll(frames=2, start_at=3, name="x")) == [1, 3, 4]


def test_a_range_scanned_into_a_roll_with_a_list_joins_the_list(tmp_path):
    """A sheet asked for 2 and 4; the Roll button then scanned "1 to 4" into
    the same roll and was stopped after frame 1. The list stayed 2 and 4, so
    the roll reopened as finished -- "all of them already scanned" -- with
    frame 3 asked for and never taken."""
    path = tmp_path / "rolls" / "chosen" / "roll.json"
    run(Roll(frames=4, only=(2, 4), resolution=600, name="chosen"), tmp_path)
    run(Roll(frames=4, start_at=1, resolution=600, name="chosen"), tmp_path)
    assert json.loads(path.read_text(encoding="utf-8"))["wanted"] == [
        1, 2, 3, 4]
    # To the end of the strip is no list, and a walk asks for nothing.
    earlier = {"wanted": [2, 4]}
    assert session.wanted_after(
        earlier, Roll(frames=None, start_at=5, name="x")) == [2, 4]
    assert session.wanted_after(
        earlier, Roll(frames=6, dry_run=True, name="x")) == [2, 4]


def test_a_frame_scanned_twice_appears_once(tmp_path):
    """A frame rescanned after a failure must not be in the file twice saying
    two different things about itself."""
    run(Roll(frames=2, only=(1,), resolution=600, name="strip2b"), tmp_path)
    run(Roll(frames=2, only=(1,), resolution=600, name="strip2b"), tmp_path)
    recorded = json.loads(
        (tmp_path / "rolls" / "strip2b" / "roll.json").read_text(encoding="utf-8"))
    assert [f["number"] for f in recorded["frames"]] == [1]


def test_a_frame_the_writer_could_not_file_is_not_done(tmp_path, monkeypatch):
    """A frame used to be marked done the moment it was queued. The disk
    filled at frame 20, the writer failed 20 to 24, and the reopened roll said
    it was finished -- five frames that existed nowhere, and nothing left to
    resume."""
    real_save = library.save

    def full_disk(image, meta, **kw):
        if "roll-02" in str((kw.get("film") or FilmNotes()).frame):
            raise OSError(28, "No space left on device")
        return real_save(image, meta, **kw)

    monkeypatch.setattr(library, "save", full_disk)
    run(Roll(frames=3, resolution=600, name="roll"), tmp_path)
    recorded = json.loads(
        (tmp_path / "rolls" / "roll" / "roll.json").read_text(encoding="utf-8"))
    by_number = {f["number"]: f for f in recorded["frames"]}
    assert [by_number[n]["done"] for n in (1, 2, 3)] == [True, False, True]
    assert "No space left" in by_number[2]["filing_error"]
    assert by_number[1]["entry"] and by_number[3]["entry"]
    assert by_number[2].get("entry") is None


def test_a_roll_stopped_by_a_frame_it_could_not_file_says_so(tmp_path,
                                                             monkeypatch):
    """`_filed` stops the roll after a frame the library refused, and the roll
    then ended "stopped after frame N, as asked" -- in roll.json and the log,
    the operator's Stop word for word. And the failure was a log line only."""
    real_save = library.save

    def full_disk(image, meta, **kw):
        if "refused-01" in str((kw.get("film") or FilmNotes()).frame):
            raise OSError(28, "No space left on device")
        return real_save(image, meta, **kw)

    monkeypatch.setattr(library, "save", full_disk)
    holder = {}

    def after_the_failure(index):
        # The next frame waits for the writer's answer, as a real frame's
        # minutes of scanning would.
        deadline = time.monotonic() + 5
        while index and not holder["s"]._stop.is_set():
            assert time.monotonic() < deadline, "the roll was never stopped"
            time.sleep(0.01)

    _, _, events = run(
        Roll(frames=3, resolution=600, name="refused"), tmp_path,
        scanner=FakeScanner(frames=3, on_yield=after_the_failure),
        extra=lambda s, _scanner: holder.update(s=s))
    stopped = _manifest_of(tmp_path, "refused")["stopped"]
    # After frame 1 or 2, whichever the writer's answer beat.
    # Named as its result is (B3-T1): its frame, or that frame's prescan.
    assert re.match(r"stopped after frame [12]: frame 1( prescan| · 600 dpi "
                    r"RGB) could not be filed", stopped), stopped
    assert "No space left" in stopped and "as asked" not in stopped
    unfiled = [e for e in kinds(events, "filing")
               if e.filing.outcome == session.NOT_FILED]
    # Frame 1 and its prescan, both refused; nothing else.
    assert unfiled and {e.total for e in unfiled} == {1}
    assert {e.filing.kind for e in unfiled} == {"scan", "prescan"}
    assert all("No space left" in e.filing.error for e in unfiled)
    # Each shown under the result it belongs to.
    results = {e.result.seq: e.result for e in kinds(events, "result")}
    assert {(results[e.done].kind, results[e.done].number)
            for e in unfiled} == {("frame", 1), ("prescan", 1)}
    # No longer an event of its own: the window never read it.
    assert not kinds(events, "unfiled") and "unfiled" not in session.KINDS


def made(folder):
    """``folder``, made: an output folder the operator chose is there."""
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def refusing(monkeypatch, root):
    """Make the library at ``root`` refuse every pass as it is filed, as a
    disk that filled after the job began does. A file in the way of the
    library's folder is refused before a job moves anything now, and so no
    longer stands for one."""
    real = library.save

    def save(image, meta, *, root=library.DEFAULT_ROOT, **kw):
        if Path(root) == Path(refused):
            raise OSError(28, "No space left on device")
        return real(image, meta, root=root, **kw)

    refused = root
    monkeypatch.setattr(library, "save", save)


def unwritable(monkeypatch, folder):
    """An output folder that is there and takes no file: a full disk, for a
    job that has passed the pre-flight, which one not there no longer does."""
    made(folder)
    real = session.export.write

    def write(path, *a, **kw):
        if folder in Path(path).parents:
            raise OSError(28, "No space left on device")
        return real(path, *a, **kw)

    monkeypatch.setattr(session.export, "write", write)
    return folder


def _free(monkeypatch, free):
    """Every disk reports ``free()`` bytes free."""
    import collections
    import shutil

    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage",
                        lambda path: usage(10 ** 13, 0, free()))


def _roll_need(tmp_path, frames, resolution=600, infrared=True, **kw):
    """All a roll can put on the one disk a test's folders share."""
    return sum(n for _, n in session.roll_space(
        frames, resolution, infrared, library_root=tmp_path,
        roll_folder=tmp_path / "rolls", film="negative", colour_pass=True,
        **kw))


def test_a_roll_the_disk_cannot_hold_is_refused_before_anything_moves(
        tmp_path, monkeypatch):
    """Nothing asked how much room there was: a full disk showed itself as a
    failed filing, after the seek, the calibration and the frame's scan. And
    2 GB are kept free beyond the roll's own need (Stefan, 2026-10-09)."""
    monkeypatch.setattr(storage, "SPACE_RESERVE", 2 * 10**9)
    need = _roll_need(tmp_path, 3)
    _free(monkeypatch, lambda: need + 2 * 10**9 - 1)
    _, scanner, events = run(Roll(frames=3, resolution=600, name="full"),
                             tmp_path)
    failed = kinds(events, "failed")
    assert failed and "not starting the roll" in failed[0].text, events
    assert "GB free" in failed[0].text and "2 GB is kept free" in failed[0].text
    assert not [c for c in scanner.calls if c[0] == "roll"]
    assert not scanner.moves
    assert not (tmp_path / "rolls" / "full").exists()
    assert library.entries(tmp_path) == []


def test_a_roll_that_may_fill_the_disk_is_warned_about(tmp_path, monkeypatch):
    # Room for the three frames, not for twice that.
    _free(monkeypatch, lambda: 1.5 * _roll_need(tmp_path, 3))
    _, _, events = run(Roll(frames=3, resolution=600, name="tight"),
                       tmp_path)
    assert any("the roll may fill the disk" in e.text
               for e in kinds(events, "log"))
    assert len(frame_entries(tmp_path)) == 3


def test_a_roll_stops_before_a_frame_there_is_no_room_for(tmp_path,
                                                          monkeypatch):
    """The disk can fill while the roll runs, and an open-ended roll is
    let start with room for its first frame only."""
    one = session.frame_bytes(600, True)
    room = {"free": 100 * _roll_need(tmp_path, 1)}
    _free(monkeypatch, lambda: room["free"])

    def fills(index):
        if index == 0:
            room["free"] = one                 # not three copies' worth

    run(Roll(frames=3, resolution=600, name="filling"), tmp_path,
        scanner=FakeScanner(frames=3, on_yield=fills))
    stopped = _manifest_of(tmp_path, "filling")["stopped"]
    assert stopped.startswith("stopped after frame 1: no room for the next "
                              "frame"), stopped
    assert len(frame_entries(tmp_path)) == 1


def test_a_disk_whose_room_cannot_be_read_is_said_not_skipped(tmp_path,
                                                             monkeypatch):
    """`short_of_space` passed over a disk it could not stat: a roll whose
    library was on an unplugged drive passed as having room."""
    real = storage._device

    def device(where):
        if Path(where) == tmp_path:
            raise OSError(21, "The device is not ready")
        return real(where)

    monkeypatch.setattr(storage, "_device", device)
    short, unknown = session.check_space([(tmp_path, 10**15)])
    assert short == []
    assert unknown == [f"cannot tell how much room {tmp_path} has "
                       "([Errno 21] The device is not ready)"]
    # The thin wrapper's callers only refuse, as they did.
    assert session.short_of_space([(tmp_path, 10**15)]) == []


@pytest.mark.skipif(sys.platform != "win32", reason="drive letters")
def test_a_drive_letter_nothing_holds_is_a_disk_whose_room_is_unknown():
    free = [f"{c}:" for c in "QRSTUVWXYZ" if not Path(f"{c}:/").exists()]
    if not free:
        pytest.skip("every drive letter here is in use")
    short, unknown = session.check_space(
        [(Path(f"{free[0]}/scans/library"), 10**15)])
    assert short == [] and len(unknown) == 1
    assert f"{free[0]}" in unknown[0]


def test_a_slides_colour_pass_is_counted_where_one_is_taken():
    """The window's roll takes a slide's RGB pass after its RGBI one and
    files both (`_file_pair`); counted as one, a 38-frame 7200 dpi slide
    roll was some 32 GB short. `tools/scan_roll.py` takes one."""
    rgbi, rgb = session.frame_bytes(1800, True), session.frame_bytes(1800, False)
    both = dict(session.roll_space(2, 1800, True, library_root="lib",
                                   film="positive", colour_pass=True))
    one = dict(session.roll_space(2, 1800, True, library_root="lib",
                                  film="positive", colour_pass=False))
    assert both[Path("lib")] - one[Path("lib")] == 2 * (
        2 * rgb + session.ENTRY_EXTRA)
    assert one[Path("lib")] == 2 * (2 * rgbi + session.ENTRY_EXTRA
                                    + 4 * session.frame_bytes(300, False))
    # A negative takes no colour pass, whatever the caller offers.
    assert session.pass_list(1800, True, "negative", True) == [rgbi]
    assert session.pass_list(1800, True, "positive", True) == [rgbi, rgb]


def test_a_rolls_spool_holds_the_frames_in_flight_not_every_frame(
        tmp_path, monkeypatch):
    """A claimed frame's spooled copy goes as the writer answers for it, so
    the spool holds the frame being scanned and those waiting to be filed --
    not 38 frames, which would refuse a roll the disks hold."""
    fb = session.frame_bytes(3600, True)
    needs = dict(session.roll_space(38, 3600, True, spool="spool"))
    waiting = 1 + session.FrameWriter.DEPTH + 1
    assert needs[Path("spool")] == (
        2 * fb * waiting
        + 2 * session.frame_bytes(300, False) * session.SPOOLED_A_FRAME * 38)
    # With room for the library, the roll's folder, the output folder and
    # three frames spooled, a 38-frame roll with debug on is not refused.
    room = sum(n for _, n in session.roll_space(
        38, 3600, True, library_root="lib", roll_folder="rolls",
        out_dir="out")) + 2 * fb * 3 + 10**9
    _free(monkeypatch, lambda: room)
    assert session.check_space(session.roll_space(
        38, 3600, True, library_root=tmp_path, roll_folder=tmp_path,
        out_dir=tmp_path, spool=tmp_path)) == ([], [])


def _scan_need(tmp_path, resolution=600, infrared=True):
    return sum(n for _, n in session.scan_space(
        [session.frame_bytes(resolution, infrared)], library_root=tmp_path))


def test_a_scan_the_disk_cannot_hold_is_refused_before_anything_is_sent(
        tmp_path, monkeypatch):
    """Only a roll asked: a Scan whose disk was full showed itself after the
    pass, as an entry kept in a temporary folder."""
    monkeypatch.setattr(storage, "SPACE_RESERVE", 2 * 10**9)
    _free(monkeypatch, lambda: _scan_need(tmp_path) + 2 * 10**9 - 1)
    _, scanner, events = run(Scan(resolution=600, infrared=True), tmp_path)
    (failed,) = kinds(events, "failed")
    assert "not scanning" in failed.text and "GB free" in failed.text
    assert not [c for c in scanner.calls if c[0] == "scan"]
    assert library.entries(tmp_path) == []


def test_a_scan_that_may_fill_the_disk_is_warned_about(tmp_path, monkeypatch):
    _free(monkeypatch, lambda: 1.5 * _scan_need(tmp_path))
    _, _, events = run(Scan(resolution=600, infrared=True), tmp_path)
    assert any("this scan may fill the disk" in e.text
               for e in kinds(events, "log"))
    assert not kinds(events, "failed")
    assert len(library.entries(tmp_path)) == 1


def test_a_prescan_is_checked_for_room_too(tmp_path, monkeypatch):
    _free(monkeypatch, lambda: 1000)
    _, scanner, events = run(Prescan(), tmp_path)
    (failed,) = kinds(events, "failed")
    assert "not prescanning" in failed.text and "GB free" in failed.text
    assert not [c for c in scanner.calls if c[0] == "prescan"]


@pytest.mark.parametrize("missing", ["library", "reference"])
def test_a_calibration_whose_folder_cannot_be_made_is_refused_first(
        tmp_path, missing):
    """Its lines are kept in the library and its reference in a cache, and a
    folder on a drive that has gone was made again on C: -- or failed after
    the calibration had run, its lines lost."""
    gone = tmp_path / "gone"
    root = gone / "library" if missing == "library" else tmp_path / "lib"
    reference = (gone / "calibration" / "shading.npz"
                 if missing == "reference" else tmp_path / "c" / "shading.npz")
    scanner = FakeScanner()
    s = ScanSession(root=str(root), reference=str(reference),
                    rolls=str(tmp_path / "rolls"),
                    open_scanner=lambda: scanner, verbose=False)
    s.start()
    s.submit(Calibrate(mode="measure", reference=str(reference)))
    s.shutdown()
    s.join(timeout=10)
    events = s.poll()
    (failed,) = kinds(events, "failed")
    assert "not calibrating" in failed.text
    assert "is the drive mounted?" in failed.text and str(gone) in failed.text
    assert not [c for c in scanner.calls if c[0] == "shading"]
    assert not gone.exists()


def test_a_scan_queued_behind_one_not_yet_written_counts_its_bytes(
        tmp_path, monkeypatch):
    """A Scan returns once its pass is queued, so the next job's check read
    a disk that did not have it on yet: two passes where one fitted."""
    go = threading.Event()
    real = session.FrameWriter._write

    def held(self, job):
        assert go.wait(10)
        return real(self, job)

    monkeypatch.setattr(session.FrameWriter, "_write", held)
    need = _scan_need(tmp_path)
    _free(monkeypatch, lambda: need + 100)       # one scan's need, no more

    def second(s, _scanner):
        s.submit(Scan(resolution=600, infrared=True))
        threading.Timer(1.0, go.set).start()

    _, scanner, events = run(Scan(resolution=600, infrared=True), tmp_path,
                             extra=second)
    go.set()
    failed = kinds(events, "failed")
    assert len(failed) == 1 and "not scanning" in failed[0].text, events
    assert len([c for c in scanner.calls if c[0] == "scan"]) == 1
    assert len(library.entries(tmp_path)) == 1


def test_a_scans_need_counts_debug_filings_spool(tmp_path, monkeypatch):
    """Debug filing spools each pass twice over until it is filed, and the
    metering probes until close, on the library's disk -- or the system's
    temporary one, where it fell back: counted where it really is."""
    asked: list = []
    real = storage.check_space

    def recorded(needs, *a, **kw):
        needs = list(needs)
        asked.append([Path(p) for p, _ in needs])
        return real(needs, *a, **kw)

    monkeypatch.setattr(storage, "check_space", recorded)
    scanner = SpoolingScanner()
    run(Scan(resolution=600), tmp_path, scanner=scanner,
        extra=lambda s, _: s.submit(Scan(resolution=600)))
    # Each job asks twice, to refuse and to warn; the second job's first.
    first, second = asked[0], asked[2]
    assert tmp_path / ".spool" in first, "the spool's disk was not counted"
    spooled = [p for p in second if p.parent == tmp_path / ".spool"]
    assert spooled and spooled[0].name.startswith("rps7200-debug-"), second

    asked.clear()
    quiet = SpoolingScanner()
    quiet.debug = False
    run(Scan(resolution=600), tmp_path / "quiet", scanner=quiet)
    assert asked and not any(".spool" in str(p) for p in asked[0])


def test_a_spools_predicted_path_does_not_grow_once_it_is_made(tmp_path,
                                                              monkeypatch):
    """Once a pass has been spooled, `spool_place` names the session's own
    folder, and the prediction put a second one below it: 23 characters
    longer than anything written there, which on Windows could refuse a job
    under a long library from its second pass on."""
    parent = tmp_path / ".spool"
    own = parent / (DirectScanner.DEBUG_SPOOL_PREFIX + "abcd1234")
    assert session.spool_bound(parent).parent.parent == parent
    assert session.spool_bound(own).parent == own

    asked: list = []
    real = storage.longest_too_long

    def recorded(paths, *a, **kw):
        paths = [Path(p) for p in paths]
        asked.append([p for p in paths if ".spool" in p.parts])
        return real(paths, *a, **kw)

    monkeypatch.setattr(storage, "longest_too_long", recorded)
    run(Scan(resolution=600), tmp_path, scanner=SpoolingScanner(),
        extra=lambda s, _: s.submit(Scan(resolution=600)))
    (before,), (after,) = asked
    assert after.parent.parent == parent, "not the spool the scan made"
    assert len(str(after)) == len(str(before))


@pytest.mark.parametrize("what", ["rolls", "output", "long"])
@pytest.mark.parametrize("walk", [False, True])
def test_a_roll_that_cannot_be_kept_is_refused_before_the_seek(
        tmp_path, monkeypatch, what, walk):
    """A rolls folder on a drive that has gone, an output folder that has
    gone, or a path too long for Windows: each failed only after the seek
    had wound the film, a walk's as well as a roll's."""
    rolls = tmp_path / "rolls"
    out_dir = None
    if what == "rolls":
        rolls = tmp_path / "gone" / "rolls"
    elif what == "output":
        out_dir = tmp_path / "iCloudDrive"
    else:
        monkeypatch.setattr(storage, "sys",
                            types.SimpleNamespace(platform="win32"))
        rolls = tmp_path / ("r" * 200)
    scanner = FakeScanner(frames=2)
    scanner.pos = 3                              # a seek would move it
    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(rolls),
                    out_dir=out_dir, open_scanner=lambda: scanner,
                    verbose=False)
    s.start()
    s.submit(Roll(frames=2, resolution=600, dry_run=walk, name="strip"))
    s.shutdown()
    s.join(timeout=10)
    events = s.poll()
    (failed,) = kinds(events, "failed")
    assert f"not starting the {'walk' if walk else 'roll'}" in failed.text
    said = {"rolls": "is the drive mounted?",
            "output": "Choose the output folder again, or clear it",
            "long": "past the 259"}[what]
    assert said in failed.text, failed.text
    assert not scanner.moves and not [c for c in scanner.calls
                                      if c[0] == "roll"]
    assert not (rolls / "strip").exists() and not (tmp_path / "gone").exists()
    if out_dir is not None:
        assert not out_dir.exists(), "the output folder was made again"


def test_a_disk_that_cannot_be_read_mid_roll_costs_its_copies_not_the_roll(
        tmp_path, monkeypatch):
    """An output stick pulled out part way: its room cannot be read, which
    is said once and does not stop the roll -- its copies are said as they
    are not written, and every frame is filed."""
    out = made(tmp_path / "out")
    real = storage._device
    pulled = threading.Event()

    def device(where):
        if pulled.is_set() and (Path(where) == out or out in Path(where).parents):
            raise OSError(21, "The device is not ready")
        return real(where)

    monkeypatch.setattr(storage, "_device", device)

    def pull(index):
        if index == 0:
            pulled.set()

    _, _, events = run(Roll(frames=3, resolution=600, name="pulled"),
                       tmp_path, scanner=FakeScanner(frames=3, on_yield=pull),
                       extra=lambda s, _: setattr(s, "out_dir", out))
    said = [e.text for e in kinds(events, "log")
            if "cannot tell how much room" in e.text]
    assert len(said) == 1 and "the roll goes on" in said[0], said
    assert "stopped" not in _manifest_of(tmp_path, "pulled")
    assert len(frame_entries(tmp_path)) == 3


def test_the_writer_never_makes_a_vanished_output_folder_again(tmp_path):
    """A plain `mkdir(parents=True)` made an output folder iCloud had taken
    away again on C:, and filled it. The copy is not written now, and says
    why; the entry is filed. The prescans/ folder below an output folder
    that is there is still made."""
    gone = tmp_path / "iCloudDrive"
    there = made(tmp_path / "out")
    writer = session.FrameWriter()
    writer.submit(number=1, paths=[gone / "frame01.tif"], image=picture(),
                  meta={"resolution_dpi": 600}, dpi=600,
                  library=str(tmp_path / "lib"), film=FilmNotes(), tags=[],
                  prescan=None, inquiry=None, capture={}, seq=1)
    writer.submit(number=2, kind="prescan",
                  paths=[there / session.PRESCAN_SUBDIR / "p.tif"],
                  image=picture(channels=3), meta={"resolution_dpi": 300},
                  dpi=300, library=None, film=FilmNotes(), tags=[],
                  prescan=None, inquiry=None, capture={}, seq=2)
    writer.finish()
    lost, kept = writer.filings
    assert lost.outcome == session.COPY_FAILED and lost.entry is not None
    ((_path, why),) = lost.unwritten
    assert "is the drive mounted?" in why
    assert not gone.exists(), "the vanished folder was made again"
    assert kept.outcome == session.WRITTEN
    assert (there / session.PRESCAN_SUBDIR / "p.tif").exists()


def test_a_long_roll_name_is_cut_with_its_hash_and_kept_as_typed(tmp_path):
    """Uncapped, a long name failed as a WinError after the seek; cut
    plainly, two long names sharing their start were one roll. The folder
    is the first 56 characters and a hash, said; the record keeps the name."""
    typed = "Holiday in the mountains with the whole family " * 3
    s, _, events = run(Roll(frames=1, resolution=600, name=typed), tmp_path)
    (folder,) = [p for p in (tmp_path / "rolls").iterdir() if p.is_dir()]
    assert len(folder.name) == session.NAME_CAP
    assert folder.name == session._safe(typed)
    manifest = json.loads((folder / "roll.json").read_text(encoding="utf-8"))
    assert manifest["typed_name"] == typed.strip()
    assert manifest["roll"] == folder.name
    assert any(f"its folder and files are called {folder.name}" in e.text
               for e in kinds(events, "log"))
    # Run again under the same name: the same folder, a resume.
    run(Roll(frames=1, resolution=600, name=typed), tmp_path)
    assert [p for p in (tmp_path / "rolls").iterdir() if p.is_dir()] == [folder]


def test_the_windows_long_roll_name_is_said_and_kept_through_its_sheet(
        tmp_path):
    """The window works a roll's folder out itself and hands over `out`, so
    a name cut to its cap reached the session as a folder alone: no line in
    the log and no `typed_name`. And a run that names no roll -- the sheet's,
    a resume -- wrote its manifest afresh and dropped what an earlier one
    had recorded. The walk records it now, and every run after keeps it."""
    typed = "Holiday in the mountains with the whole family " * 3
    folder = session.roll_dir(tmp_path / "rolls", typed)
    _, _, events = run(Roll(frames=1, dry_run=True, out=str(folder),
                            typed_name=typed), tmp_path)
    survey = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    assert survey["roll"] == folder.name == session._safe(typed)
    assert survey["typed_name"] == typed.strip()
    assert any(f"its folder and files are called {folder.name}" in e.text
               for e in kinds(events, "log"))
    # The sheet's roll: into the walk's folder, with nothing typed.
    run(Roll(frames=1, resolution=600, only=(1,), out=str(folder)), tmp_path)
    scanned = json.loads((folder / "roll.json").read_text(encoding="utf-8"))
    assert scanned["roll"] == folder.name
    assert scanned["typed_name"] == typed.strip()
    # Resumed with the walk gone: from roll.json's own record.
    (folder / "survey.json").unlink()
    run(Roll(frames=1, resolution=600, out=str(folder)), tmp_path)
    resumed = json.loads((folder / "roll.json").read_text(encoding="utf-8"))
    assert resumed["typed_name"] == typed.strip()


def test_a_roll_resumed_by_its_folders_name_keeps_the_name_it_was_typed_as(
        tmp_path):
    """A reopened roll's folder is put in the roll box, and the Roll button
    sent it back as typed; `tools/scan_roll.py` advises `--roll <folder>`.
    Typed, but no other than the roll's label or folder, it replaced the
    long name in roll.json -- the `.bak` kept it for one more run. Twice
    here, so the `.bak` cannot be what holds it; and a Duplicate's copy,
    whose folder is neither the label nor the name typed, the same."""
    typed = "Holiday in the mountains with the whole family " * 3
    folder = session.roll_dir(tmp_path / "rolls", typed)
    run(Roll(frames=1, resolution=600, out=str(folder), typed_name=typed),
        tmp_path)
    for _ in range(2):
        run(Roll(frames=1, resolution=600, out=str(folder),
                 typed_name=folder.name), tmp_path)
        resumed = json.loads((folder / "roll.json").read_text(
            encoding="utf-8"))
        assert resumed["roll"] == folder.name
        assert resumed["typed_name"] == typed.strip()
    copy = folder.with_name(f"{folder.name}-2")
    shutil.copytree(folder, copy)
    for _ in range(2):
        run(Roll(frames=1, resolution=600, out=str(copy),
                 typed_name=copy.name), tmp_path)
        resumed = json.loads((copy / "roll.json").read_text(encoding="utf-8"))
        assert resumed["roll"] == folder.name, "it keeps calling itself so"
        assert resumed["typed_name"] == typed.strip()


def test_a_folder_reused_under_another_name_lends_it_no_typed_name(
        tmp_path):
    folder = made(tmp_path / "roll")
    (folder / "survey.json").write_text(json.dumps(
        {"roll": "Gold", "typed_name": "Gold / summer", "frames": []}),
        encoding="utf-8")
    assert session.recorded_typed_name(folder, "Gold") == "Gold / summer"
    assert session.recorded_typed_name(folder, "Portra") is None
    assert session.recorded_typed_name(tmp_path / "none", "Gold") is None


def test_a_jobs_predicted_paths_are_as_long_as_any_it_writes(tmp_path,
                                                             monkeypatch):
    """The refusal of a path too long stands on the prediction: each place a
    roll, a walk or a scan really writes in -- the library entry, the roll's
    own folder, the copies and the prescan copies -- is no longer than the
    longest predicted for that place, temporary names included. Measured
    against the longest prediction of all, the entry's always passed, and a
    prediction that left out the copies or a roll's records went unseen."""
    # Every temporary name, as it is renamed into place: the longest paths
    # a job makes, and gone by the time the folders are walked.
    temporary: list = []
    real_replace = os.replace

    def replace(source, target, *a, **kw):
        temporary.append(Path(source))
        return real_replace(source, target, *a, **kw)

    monkeypatch.setattr(os, "replace", replace)
    for job in (Roll(frames=2, resolution=600, name="predicted"),
                Roll(frames=2, dry_run=True, name="walked"),
                Scan(resolution=600, infrared=True),
                Prescan()):
        here = made(tmp_path / type(job).__name__ / str(getattr(job, "dry_run",
                                                                 "")))
        out = made(here / "out")
        predicted: list = []

        def place(path, here=here, out=out):
            """Which folder a path is written in, as the prediction groups
            them: the copies, the prescan copies, a roll's own folder, or
            the library."""
            path = Path(os.path.abspath(path))
            for folder in (out / session.PRESCAN_SUBDIR, out):
                if folder in path.parents:
                    return folder
            rolls = here / "rolls"
            if rolls in path.parents:
                return rolls / path.relative_to(rolls).parts[0]
            return here

        def predict(s, _scanner, job=job, out=out):
            s.out_dir = out
            where = s._roll_place(job) if isinstance(job, Roll) else None
            predicted.extend(s._longest_paths(job, where))

        temporary.clear()
        run(job, here, extra=predict)
        longest: dict = {}
        for path in predicted:
            longest[place(path)] = max(longest.get(place(path), 0),
                                       len(os.path.abspath(path)))
        written = [p for p in here.rglob("*") if p.is_file()]
        written += [p for p in temporary if here in p.parents]
        assert written, job
        assert any(".part" in p.name for p in written), "no temporary seen"
        for path in written:
            assert place(path) in longest, (job, path, "not predicted")
            assert len(os.path.abspath(path)) <= longest[place(path)], (
                job, path, longest[place(path)])


def test_frame_bytes_is_claude_mds_figure_for_a_7200_dpi_rgbi_pass():
    """570 MB of pixels, CLAUDE.md's figure, from the transport's window."""
    assert session.frame_bytes(7200, True) == pytest.approx(570e6, rel=0.01)


def test_a_frame_waiting_to_be_filed_is_not_yet_done(tmp_path):
    """The two threads, in either order: the record first and the filing
    after, or a small frame filed before its record was written."""
    path = tmp_path / "roll.json"
    manifest = session.RollManifest(path, {"frames": []})
    manifest.record({"number": 1}, awaiting=True)
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["frames"] == [{"number": 1, "done": False}]
    manifest.filed(1, tmp_path / "lib" / "entry-1", None)
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["frames"][0]["done"] is True
    assert on_disk["frames"][0]["entry"].endswith("entry-1")

    manifest.filed(2, tmp_path / "lib" / "entry-2", None, file="frame02.tif")
    manifest.record({"number": 2}, awaiting=True)
    second = json.loads(path.read_text(encoding="utf-8"))["frames"][1]
    assert second["done"] is True and second["file"] == "frame02.tif"


def test_a_roll_straight_after_another_keeps_its_frames_done(tmp_path,
                                                             monkeypatch):
    """The second roll into a folder reads the first one's manifest to carry
    it forward. Read while the writer was still filing the first roll's last
    frame, it would carry that frame forward as not done and write over the
    writer's answer."""
    real_save = library.save

    def slow(*a, **kw):
        time.sleep(0.2)
        return real_save(*a, **kw)

    monkeypatch.setattr(library, "save", slow)

    def first(s, _scanner):                      # queued ahead of the job
        s.submit(Roll(frames=4, only=(3,), start_at=3, resolution=600,
                      name="pair"))

    run(Roll(frames=2, resolution=600, name="pair"), tmp_path, extra=first)
    recorded = json.loads(
        (tmp_path / "rolls" / "pair" / "roll.json").read_text(encoding="utf-8"))
    assert {f["number"]: f["done"] for f in recorded["frames"]} == {
        1: True, 2: True, 3: True}


def test_a_roll_straight_after_another_does_not_wait_for_its_filing(
        tmp_path, monkeypatch):
    """It waited for the writer before reading the manifest -- with the film
    moved and the device open and idle while the last roll's frames gzipped,
    the state FrameWriter exists to avoid. Here the first roll's filing is
    held until the second roll starts scanning: waited for, it never would."""
    real_save = library.save
    scanning = threading.Event()
    held = []

    def held_until_the_next_roll_scans(*a, **kw):
        if not scanning.is_set():
            held.append(scanning.wait(timeout=2.0))
        return real_save(*a, **kw)

    monkeypatch.setattr(library, "save", held_until_the_next_roll_scans)
    scanner = FakeScanner(frames=4)
    real_roll = scanner.scan_roll
    rolls = []

    def scan_roll(*a, **kw):
        rolls.append(kw.get("first_index"))
        if len(rolls) == 2:
            scanning.set()
        return real_roll(*a, **kw)

    monkeypatch.setattr(scanner, "scan_roll", scan_roll)

    def first(s, _scanner):                      # queued ahead of the job
        s.submit(Roll(frames=2, resolution=600, name="pair"))

    _s, _scanner, events = run(
        Roll(frames=4, only=(3,), start_at=3, resolution=600, name="pair"),
        tmp_path, scanner=scanner, extra=first)
    assert held and all(held), "the second roll waited for the first's filing"
    assert not kinds(events, "failed")
    recorded = json.loads(
        (tmp_path / "rolls" / "pair" / "roll.json").read_text(encoding="utf-8"))
    assert {f["number"]: f["done"] for f in recorded["frames"]} == {
        1: True, 2: True, 3: True}
    assert all(f["entry"] for f in recorded["frames"])


def test_a_frame_taken_again_before_its_first_filing_lands_waits_for_its_own(
        tmp_path):
    """A roll straight after another carries the same manifest on, and can
    take again a frame the writer has not filed yet. The first take's answer
    is not the second's: applied to it, a frame whose own filing then failed
    read done, with the other take's entry."""
    path = tmp_path / "roll.json"
    manifest = session.RollManifest(path, {"frames": []})
    manifest.record({"number": 2, "take": 1}, awaiting=True)
    manifest.record({"number": 2, "take": 2}, awaiting=True)
    manifest.filed(2, tmp_path / "lib" / "first-take", None)
    (record,) = json.loads(path.read_text(encoding="utf-8"))["frames"]
    assert record["take"] == 2 and record["done"] is False
    manifest.filed(2, None, "No space left on device")
    (record,) = json.loads(path.read_text(encoding="utf-8"))["frames"]
    assert record["done"] is False and record.get("entry") is None
    assert "No space" in record["filing_error"]
    assert not manifest.pending()


def _refusing_replace(monkeypatch, refuse):
    """`os.replace` refusing a manifest as Windows does while it is held open.

    Only the manifests: the library's own atomic writes go through untouched.
    """
    real = os.replace
    refused = []

    def replace(src, dst):
        if Path(dst).name in ("roll.json", "survey.json") and refuse():
            refused.append(Path(dst).name)
            raise PermissionError(13, "The process cannot access the file "
                                      "because it is being used by another "
                                      "process")
        return real(src, dst)

    monkeypatch.setattr(session.os, "replace", replace)
    return refused


def test_a_manifest_held_open_for_a_moment_is_still_replaced(tmp_path,
                                                             monkeypatch):
    """A rename once a frame, for hours, meets Defender, the indexer and the
    window's own reader, each holding the file for a moment."""
    monkeypatch.setattr(session, "REPLACE_RETRY_S", (0.0, 0.0, 0.0))
    attempts = iter(range(100))
    refused = _refusing_replace(monkeypatch, lambda: next(attempts) < 2)
    path = tmp_path / "roll.json"
    session.write_manifest(path, {"frames": [1]})
    assert refused == ["roll.json"] * 2
    assert json.loads(path.read_text(encoding="utf-8")) == {"frames": [1]}

    _refusing_replace(monkeypatch, lambda: True)
    with pytest.raises(PermissionError):
        session.write_manifest(path, {"frames": [1, 2]})
    assert json.loads(path.read_text(encoding="utf-8")) == {"frames": [1]}
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".part")] \
        == [], "the refused copy is not left beside it"


def test_a_roll_goes_on_when_its_manifest_cannot_be_written(tmp_path,
                                                            monkeypatch):
    """It raised out of the scanning loop, so a rename refused for half a
    second ended an hours-long roll part-way. The next frame writes it whole,
    so this one's record is kept and said, and the roll carries on."""
    monkeypatch.setattr(session, "REPLACE_RETRY_S", (0.0, 0.0))
    attempts = iter(range(100))
    refused = _refusing_replace(monkeypatch, lambda: next(attempts) < 3)
    _s, _scanner, events = run(Roll(frames=3, resolution=600, name="held"),
                               tmp_path)
    assert not kinds(events, "failed")
    assert refused == ["roll.json"] * 3, "the first write was refused outright"
    assert any("could not write" in e.text and "roll.json" in e.text
               for e in kinds(events, "log"))
    recorded = json.loads((tmp_path / "rolls" / "held" / "roll.json")
                          .read_text(encoding="utf-8"))
    assert [(f["number"], f["done"]) for f in recorded["frames"]] == [
        (1, True), (2, True), (3, True)]


def test_a_manifest_refused_to_the_end_is_written_once_the_scanner_closes(
        tmp_path, monkeypatch):
    """The last frame's filing has no frame after it to carry what it could
    not write, so the session writes it again once everything is filed."""
    monkeypatch.setattr(session, "REPLACE_RETRY_S", ())
    filed = threading.Event()
    real_finish = session.FrameWriter.finish

    def finish(self):
        real_finish(self)
        filed.set()

    monkeypatch.setattr(session.FrameWriter, "finish", finish)
    _refusing_replace(monkeypatch, lambda: not filed.is_set())
    _s, _scanner, events = run(Roll(frames=2, resolution=600, name="late"),
                               tmp_path)
    assert not kinds(events, "failed")
    recorded = json.loads((tmp_path / "rolls" / "late" / "roll.json")
                          .read_text(encoding="utf-8"))
    assert [(f["number"], f["done"]) for f in recorded["frames"]] == [
        (1, True), (2, True)]


def test_a_roll_after_one_whose_last_write_was_refused_carries_it_on(
        tmp_path, monkeypatch):
    """The first roll's last filing reaches the manifest in hand but not the
    disk. A roll into the same folder read the stale file instead -- that
    frame not done -- replaced the manifest that knew better, and wrote the
    stale record for good."""
    monkeypatch.setattr(session, "REPLACE_RETRY_S", ())
    refusing = threading.Event()
    refused = threading.Event()
    real_filed = session.RollManifest.filed

    def filed(self, number, *a, **kw):
        if int(number) != 2 or refused.is_set():
            return real_filed(self, number, *a, **kw)
        refusing.set()
        try:
            return real_filed(self, number, *a, **kw)
        finally:
            refusing.clear()
            refused.set()

    monkeypatch.setattr(session.RollManifest, "filed", filed)
    _refusing_replace(monkeypatch, refusing.is_set)
    real_roll = session.ScanSession._roll
    started = []

    def roll(self, job, *a, **kw):
        started.append(job)
        if len(started) == 2:                    # after the refused filing
            assert refused.wait(timeout=30.0)
        return real_roll(self, job, *a, **kw)

    monkeypatch.setattr(session.ScanSession, "_roll", roll)

    def first(s, _scanner):                      # queued ahead of the job
        s.submit(Roll(frames=2, resolution=600, name="pair"))

    _s, _scanner, events = run(
        Roll(frames=4, only=(3,), start_at=3, resolution=600, name="pair"),
        tmp_path, extra=first)
    assert refused.is_set() and not kinds(events, "failed")
    recorded = json.loads(
        (tmp_path / "rolls" / "pair" / "roll.json").read_text(encoding="utf-8"))
    assert {f["number"]: f["done"] for f in recorded["frames"]} == {
        1: True, 2: True, 3: True}
    assert all(f.get("entry") for f in recorded["frames"])


def test_a_manifest_is_replaced_whole_and_the_last_run_kept(tmp_path):
    """Truncated and rewritten in place, a kill between the two left an empty
    roll.json: the roll vanished from the list and the next resume wrote a
    fresh one over it."""
    path = tmp_path / "roll.json"
    session.write_manifest(path, {"frames": [1]})
    session.write_manifest(path, {"frames": [1, 2]}, keep_previous=True)
    assert json.loads(path.read_text(encoding="utf-8")) == {"frames": [1, 2]}
    kept = tmp_path / "roll.json.bak"
    assert json.loads(kept.read_text(encoding="utf-8")) == {"frames": [1]}
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".part")] == []

    # Every frame of a run after the first leaves the kept copy alone, so it
    # is the manifest as it stood before this run, not one frame back.
    manifest = session.RollManifest(path, {"frames": [1, 2, 3]})
    manifest.write()
    manifest.write()
    assert json.loads(kept.read_text(encoding="utf-8")) == {"frames": [1, 2]}


def test_a_kept_copy_cut_short_does_not_replace_the_last_one(tmp_path,
                                                             monkeypatch):
    """The `.bak` was copied in place: a full disk part-way left a truncated
    file as the version `read_manifest` falls back on."""
    import shutil

    path = tmp_path / "roll.json"
    kept = tmp_path / "roll.json.bak"
    session.write_manifest(path, {"frames": [1]})
    session.write_manifest(path, {"frames": [1, 2]}, keep_previous=True)

    def cut_short(src, dst):
        Path(dst).write_text('{"fra', encoding="utf-8")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(shutil, "copyfile", cut_short)
    session.write_manifest(path, {"frames": [1, 2, 3]}, keep_previous=True)
    assert json.loads(path.read_text(encoding="utf-8")) == {"frames": [1, 2, 3]}
    assert json.loads(kept.read_text(encoding="utf-8")) == {"frames": [1]}
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".part")] == []


def test_a_first_numbering_copy_cut_short_leaves_nothing_behind(tmp_path,
                                                                monkeypatch):
    import shutil

    path = tmp_path / "roll.json"
    path.write_text(json.dumps({"frames": [{"number": 1}]}), encoding="utf-8")

    def cut_short(src, dst):
        Path(dst).write_text('{"fra', encoding="utf-8")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(shutil, "copyfile", cut_short)
    with pytest.raises(OSError):
        session.keep_first_numbering(path, {"frames": [{"number": 1}]})
    assert sorted(p.name for p in tmp_path.iterdir()) == ["roll.json"]


def test_a_damaged_manifest_is_read_from_the_version_kept_beside_it(tmp_path):
    path = tmp_path / "survey.json"
    (tmp_path / "survey.json.bak").write_text('{"frames": [7]}',
                                              encoding="utf-8")
    path.write_text('{"frames": [', encoding="utf-8")
    said = []
    assert session.read_manifest(path, say=said.append) == {"frames": [7]}
    assert "survey.json.bak" in said[0]
    (tmp_path / "survey.json.bak").unlink()
    with pytest.raises(ValueError, match="survey.json"):
        session.read_manifest(path)


def test_an_unreadable_roll_json_is_kept_aside_not_written_over(tmp_path):
    """It was read as empty and replaced by the resume that could not read
    it, and with it the record of every frame already scanned."""
    folder = tmp_path / "rolls" / "roll"
    folder.mkdir(parents=True)
    (folder / "roll.json").write_text('{"frames": [{"number": 1, "do',
                                      encoding="utf-8")
    _s, _scanner, events = run(Roll(frames=1, only=(2,), start_at=2,
                                    resolution=600, name="roll"), tmp_path)
    aside = folder / "roll.json.unreadable"
    assert aside.read_text(encoding="utf-8").startswith('{"frames": [{"number"')
    assert any("roll.json.unreadable" in e.text for e in kinds(events, "log"))
    recorded = json.loads((folder / "roll.json").read_text(encoding="utf-8"))
    assert [f["number"] for f in recorded["frames"]] == [2]


def test_resuming_an_old_roll_keeps_the_numbering_it_was_written_with(tmp_path):
    """A resume moves an old file's numbers onto the strip's and writes them
    back for good. What it moved them from is kept, once."""
    folder = tmp_path / "rolls" / "old"
    folder.mkdir(parents=True)
    old = {"roll": "old", "frames": [
        {"number": 1, "transport_position": 4, "done": True}]}
    (folder / "roll.json").write_text(json.dumps(old), encoding="utf-8")
    run(Roll(frames=1, only=(7,), start_at=7, resolution=600, name="old"),
        tmp_path)
    assert json.loads((folder / "roll.json.legacy").read_text(
        encoding="utf-8")) == old
    recorded = json.loads((folder / "roll.json").read_text(encoding="utf-8"))
    assert sorted(f["number"] for f in recorded["frames"]) == [5, 7]


def test_a_walk_records_no_exposure_because_it_took_none(tmp_path):
    """A dry run prescans and advances; there is no scan and so nothing to
    record. The key is absent rather than zero, which is the difference between
    "this roll did not scan that frame" and "it scanned it at nothing"."""
    run(Roll(frames=2, dry_run=True, name="walk2"), tmp_path)
    recorded = json.loads(
        (tmp_path / "rolls" / "walk2" / "survey.json").read_text(encoding="utf-8"))
    assert recorded["frames"], "the walk recorded nothing at all"
    assert all("exposure" not in f for f in recorded["frames"])
    assert all(f["done"] is False for f in recorded["frames"])


def test_a_dry_run_files_its_prescans(tmp_path):
    """They are the entire product of a dry run -- there is no frame to hang
    them off, and a walk that leaves nothing behind cannot be looked at later."""
    run(Roll(frames=3, dry_run=True, name="walk"), tmp_path)
    entries = library.entries(tmp_path)
    assert len(entries) == 3
    assert all("prescan" in e["tags"] for e in entries)


def test_a_walk_records_the_folder_it_wrote_into(tmp_path):
    """The contact sheet's decisions are made *after* the walk finishes, and
    until the roll is commissioned its manifest holds no positions -- so this
    folder is the only thing those decisions can be filed against. Asked for
    rather than rebuilt from the roll's name by the caller, which would drift
    the moment the naming here changed."""
    session, _scanner, _events = run(
        Roll(frames=2, dry_run=True, name="walk3"), tmp_path)
    assert session.last_roll_dir == tmp_path / "rolls" / "walk3"
    assert (session.last_roll_dir / "survey.json").exists()


class PrescanningScanner(FakeScanner):
    """Yields a real roll the way the driver does: each frame's prescan with
    its raw pixels, its meta and the record taken as it was taken -- bytes
    laid out for the prescan, not for the frame scanned after it."""

    def scan_roll(self, frames=None, dry_run=False, **kw):
        for rf in FakeScanner.scan_roll(self, frames=frames, dry_run=dry_run,
                                        **kw):
            n = rf.index
            raw = picture(h=4, w=6, channels=3, seed=40 + n).astype(np.uint8)
            layout = {"format": "index", "bytes_per_line": 6,
                      "line_stride": 8, "index_header": 2, "width": 6,
                      "lines": 4, "channels": 3, "byte_order": "little",
                      "lines_received": 12}
            blob = b"".join(bytes([ch, 0]) + raw[y, :, c].tobytes()
                            for y in range(4)
                            for c, ch in enumerate(b"RGB"))
            yield RollFrame(
                index=rf.index, position=rf.position, image=rf.image,
                meta=rf.meta, prescan=(raw // 2).astype(np.uint8),
                registration=rf.registration, raw_image=rf.image,
                raw_prescan=raw,
                prescan_meta={"resolution_dpi": 300, "channels": 3,
                              "channel_order": ["R", "G", "B"], "depth": 8,
                              "shading": {"columns": 6, "width": 6}},
                prescan_capture={"reference": None, "ccd_mask": b"\x00" * 6,
                                 "raw": blob, "raw_layout": layout})


def test_a_real_roll_files_each_prescan_raw_in_its_own_entry(tmp_path):
    """The framing pass every hold, aim and reversal was judged from. It rode
    along inside the frame's entry as the corrected 8-bit `prescan.tif` --
    no bytes, no mask, nothing to say it was corrected -- so it could never
    be re-decoded or corrected again."""
    run(Roll(frames=3, dry_run=False, name="real"), tmp_path,
        scanner=PrescanningScanner())
    prescans = [e for e in library.entries(tmp_path) if "prescan" in e["tags"]]
    assert len(prescans) == 3
    assert len(frame_entries(tmp_path)) == 3
    for record in prescans:
        member = record["extra"]["roll_membership"]
        assert member["kind"] == "prescan" and member["roll"] == "real"
        entry = tmp_path / record["id"]
        assert record["image"]["corrections_applied"] == []
        image, verdict = library.reconstruct(entry)
        assert image is not None, verdict
        assert np.array_equal(image, tiff.read(entry / "scan.tif")), \
            "the bytes filed are not this prescan's"
        assert (entry / "ccd_mask.bin").exists()


def test_a_rolls_prescans_wait_against_their_own_bound(tmp_path, monkeypatch):
    """`FrameWriter` bounds frames and prescans apart, by the job's kind. A
    prescan not said to be one takes a frame's room, and the scanning thread
    stops a frame early behind a writer held up for a moment."""
    submitted = []
    real = session.FrameWriter.submit

    def noted(self, **job):
        submitted.append((job.get("kind"), "prescan" in job["tags"]))
        real(self, **job)

    monkeypatch.setattr(session.FrameWriter, "submit", noted)
    run(Roll(frames=2, dry_run=False, name="real"), tmp_path,
        scanner=PrescanningScanner())
    assert sorted(submitted) == [("prescan", True)] * 2 + [("scan", False)] * 2


def _raw_prescan(seed):
    """A prescan's raw pixels, its bytes laid out as the device sends them,
    and the record the driver takes as the pass is taken."""
    raw = picture(h=4, w=6, channels=3, seed=seed).astype(np.uint8)
    layout = {"format": "index", "bytes_per_line": 6, "line_stride": 8,
              "index_header": 2, "width": 6, "lines": 4, "channels": 3,
              "byte_order": "little", "lines_received": 12}
    blob = b"".join(bytes([ch, 0]) + raw[y, :, c].tobytes()
                    for y in range(4) for c, ch in enumerate(b"RGB"))
    return raw, {"reference": None, "ccd_mask": b"\x00" * 6, "raw": blob,
                 "raw_layout": layout}


_PRESCAN_META = {"resolution_dpi": 300, "channels": 3,
                 "channel_order": ["R", "G", "B"], "depth": 8,
                 "shading": {"columns": 6, "width": 6}}


class CorrectedAndFailingScanner(FakeScanner):
    """Frame 1 is held, which replaces its prescan; frame 2 fails after its
    prescan. The scanner's own last record is a third pass of the prescan's
    very shape, which no guard can tell from it."""

    def capture_record(self):
        return _raw_prescan(99)[1]

    def scan_roll(self, frames=None, dry_run=False, first_index=0, **kw):
        self.calls.append(("roll", frames, dry_run))
        before, before_capture = _raw_prescan(10)
        after, after_capture = _raw_prescan(11)
        image = None if dry_run else picture(seed=1)
        yield RollFrame(
            index=first_index, position=self.pos, image=image,
            meta={} if dry_run else {"resolution_dpi": 600,
                                     "channel_order": list("RGBI")},
            prescan=after // 2, registration={}, raw_image=image,
            raw_prescan=after, prescan_meta=dict(_PRESCAN_META),
            prescan_capture=after_capture,
            prescan_before=before // 2, raw_prescan_before=before,
            prescan_before_meta=dict(_PRESCAN_META),
            prescan_before_capture=before_capture)
        self.pos += 1
        failed, failed_capture = _raw_prescan(12)
        yield RollFrame(
            index=first_index + 1, position=self.pos, image=None, meta={},
            prescan=failed // 2, registration={}, error="the scanner said no",
            raw_prescan=failed, prescan_meta=dict(_PRESCAN_META),
            prescan_capture=failed_capture)


def _prescan_entries(root):
    out = {}
    for record in library.entries(root):
        if "prescan" in record["tags"]:
            out[(record["extra"]["roll_membership"]["number"],
                 "before" in record["tags"])] = root / record["id"]
    return out


@pytest.mark.parametrize("dry_run", [False, True])
def test_every_prescan_a_roll_took_is_filed_raw_with_its_own_bytes(
        tmp_path, dry_run):
    """The one a hold replaced, the one it replaced it with, and a failed
    frame's -- on a walk and on a real roll -- each with the bytes it was
    read from. A real roll filed none of them raw, a failed frame's prescan
    nowhere, and the replaced one nowhere at all; a walk filed its prescans
    with the scanner's *last* bytes, which were not theirs."""
    run(Roll(frames=2, dry_run=dry_run, name="held"), tmp_path,
        scanner=CorrectedAndFailingScanner())
    filed = _prescan_entries(tmp_path)
    assert sorted(filed) == [(1, False), (1, True), (2, False)]
    for key, seed in (((1, True), 10), ((1, False), 11), ((2, False), 12)):
        entry = filed[key]
        assert np.array_equal(tiff.read(entry / "scan.tif"),
                              _raw_prescan(seed)[0])
        image, verdict = library.reconstruct(entry)
        assert image is not None and np.array_equal(
            image, _raw_prescan(seed)[0]), (key, verdict)


def test_a_walk_names_the_prescan_a_correction_replaced(tmp_path):
    """So what copies a walk by its records takes it along, as the roll
    tool's walks already said it."""
    run(Roll(frames=2, dry_run=True, name="named"), tmp_path,
        scanner=CorrectedAndFailingScanner())
    out = tmp_path / "rolls" / "named"
    survey = json.loads((out / "survey.json").read_text(encoding="utf-8"))
    first = [f for f in survey["frames"] if f["number"] == 1][0]
    assert first["prescan_before"] == "prescan01-before.tif"
    assert (out / "prescan01-before.tif").exists()


def test_a_walk_records_the_entry_each_prescan_was_filed_as(tmp_path):
    """The record named only the file, so a walk reopened in a later session
    had no way back to its references' entries, and the approvals made from
    it named none."""
    run(Roll(frames=2, dry_run=True, name="linked"), tmp_path,
        scanner=CorrectedAndFailingScanner())
    survey = json.loads((tmp_path / "rolls" / "linked" / "survey.json")
                        .read_text(encoding="utf-8"))
    by_number = {f["number"]: f for f in survey["frames"]}
    filed = _prescan_entries(tmp_path)
    assert by_number[1]["prescan_entry"] == filed[(1, False)].name
    assert by_number[1]["prescan_before_entry"] == filed[(1, True)].name
    assert by_number[2]["prescan_entry"] == filed[(2, False)].name
    # And read back as that entry, by the id against the library -- and, for
    # a walk from before records named it, by the entries' own membership.
    folder = tmp_path / "rolls" / "linked"
    pairs = [(n, r) for n, r in by_number.items()]
    assert session.walked_prescan_entries(folder, pairs, tmp_path) == {
        1: filed[(1, False)], 2: filed[(2, False)]}
    older = [(n, {k: v for k, v in r.items() if k != "prescan_entry"})
             for n, r in pairs]
    assert session.walked_prescan_entries(folder, older, tmp_path) == {
        1: filed[(1, False)], 2: filed[(2, False)]}


class _LastPassKeptScanner(CorrectedAndFailingScanner):
    """As the driver is: `last_pixels_raw` is whatever pass came last -- a
    third one, by the time any of the frame's prescans is filed."""

    last_pixels_raw = _raw_prescan(99)[0]


@pytest.mark.parametrize("dry_run", [False, True])
def test_a_prescan_filed_with_its_own_record_keeps_its_bytes_after_later_passes(
        tmp_path, dry_run):
    """The identity guard is for a record read off the scanner. Asked of one
    the driver took as the pass landed, it compared those pixels with the
    scanner's last pass -- the frame's scan, a verification prescan -- and
    dropped the bytes of every roll prescan and every picture from before an
    aim. The fake above keeps no `last_pixels_raw`, which is why nothing saw."""
    _s, _scanner, events = run(Roll(frames=2, dry_run=dry_run, name="kept"),
                               tmp_path, scanner=_LastPassKeptScanner())
    filed = _prescan_entries(tmp_path)
    for key, seed in (((1, True), 10), ((1, False), 11), ((2, False), 12)):
        image, verdict = library.reconstruct(filed[key])
        assert image is not None and np.array_equal(
            image, _raw_prescan(seed)[0]), (key, verdict)
    assert not any("later pass" in (e.text or "")
                   for e in kinds(events, "log"))


def test_the_picture_from_before_an_aim_never_names_the_prescans_result(
        tmp_path):
    """Filed under the prescan's own number, its "filed" came second and
    replaced the prescan's entry on the window's result: every aimed frame
    zoomed in to the picture from before its aim, shifted back by the aim,
    and Save As and Delete took that entry too."""
    _s, _scanner, events = run(Roll(frames=2, dry_run=True, name="own"),
                               tmp_path, scanner=CorrectedAndFailingScanner())
    filed = _prescan_entries(tmp_path)
    results = {e.result.seq: e.result for e in kinds(events, "result")}
    entry_of = {}
    for e in kinds(events, "filed"):
        if e.done in results:
            entry_of[e.done] = Path(e.text)
    (seq,) = [s for s, r in results.items()
              if r.kind == "prescan" and r.number == 1]
    assert entry_of[seq] == filed[(1, False)]
    announced = [Path(e.text) for e in kinds(events, "filed")]
    assert filed[(1, True)] in announced, "and still filed, under its own"


def test_a_manifest_waiting_for_a_prescan_amendment_is_ahead_of_its_file(
        tmp_path):
    """Until the writer has named the prescan, the file lacks it, and a walk
    straight after into the same folder must carry the manifest on rather
    than read the file (`ScanSession._roll`) -- or the name never lands."""
    path = tmp_path / "survey.json"
    manifest = session.RollManifest(path, {"frames": []})
    record = {"number": 1}
    told = manifest.prescan_told(record, tmp_path / "prescan01.tif")
    session.queued(lambda **job: None, told)
    manifest.record(record)
    assert manifest.ahead_of_disk()
    tiff.write(str(tmp_path / "prescan01.tif"), np.zeros((3, 3, 3), np.uint8))
    told(_filing(session.FILED, entry=tmp_path / "entry-1",
                 written=(tmp_path / "prescan01.tif",)))
    assert not manifest.ahead_of_disk()
    on_disk = json.loads(path.read_text(encoding="utf-8"))["frames"][0]
    assert on_disk["prescan"] == "prescan01.tif"
    assert on_disk["prescan_entry"] == "entry-1"
    # With its checksum, which a reader of the walk can hold the file to.
    assert on_disk["prescan_sha256"] == library._sha256(
        tmp_path / "prescan01.tif")
    assert "prescan_error" not in on_disk


def _filing(outcome, number=1, kind="prescan", **fields):
    """A writer job's `Filing`, as `FrameWriter` hands one to `on_filed`."""
    return session.Filing(seq=1, number=number, kind=kind, outcome=outcome,
                          **fields)


def _prescan_entry(root, name, folder, number, created, before=False):
    """A library entry as a roll files a prescan: only what the join reads."""
    (root / name).mkdir(parents=True)
    (root / name / "scan.json").write_text(json.dumps({
        "id": name, "created": created,
        "tags": ["roll", "prescan"] + (["before"] if before else []),
        "extra": {"roll_membership": session.roll_membership(
            "walk", number, "prescan", folder)}}), encoding="utf-8")
    return root / name


def test_an_old_walk_is_joined_only_to_the_entries_it_filed(tmp_path):
    """Taken newest first and unbounded, the join gave a walk reopened
    beside a roll the roll's own verification prescans -- same folder, same
    kind, same numbers, filed later -- and the next approval wrote one into
    approved.json as the walk's reference. And a re-walk's frame the writer
    never filed got the earlier walk's entry of that place."""
    folder = tmp_path / "rolls" / "walk"
    folder.mkdir(parents=True)
    lib = tmp_path / "lib"
    _prescan_entry(lib, "a-earlier-walk", folder, 2,
                   "2026-09-20T09:00:00+00:00")
    own = _prescan_entry(lib, "b-walk", folder, 1,
                         "2026-09-20T10:00:05+00:00")
    _prescan_entry(lib, "c-roll-1", folder, 1, "2026-09-20T11:00:00+00:00")
    _prescan_entry(lib, "c-roll-2", folder, 2, "2026-09-20T11:00:30+00:00")
    walk = {"started": "2026-09-20T10:00:00+00:00",
            "finished": "2026-09-20T10:01:00+00:00",
            "frames": [{"number": 1, "prescan": "prescan01.tif"},
                       {"number": 2, "prescan": "prescan02.tif"}]}
    roll = {"started": "2026-09-20T10:59:00+00:00"}
    pairs = [(r["number"], r) for r in walk["frames"]]
    assert session.walked_prescan_entries(folder, pairs, lib, walk=walk,
                                          roll=roll) == {1: own}
    # Without a roll.json beside it, the walk's own end bounds it.
    assert session.walked_prescan_entries(folder, pairs, lib,
                                          walk=walk) == {1: own}


def test_a_walk_that_names_its_entries_is_never_joined_by_guess(tmp_path):
    """A record naming an entry since deleted, or saying it filed none, got
    another pass's of that frame; so did every frame of a walk made since
    records named them, where one that names none was not filed."""
    folder = tmp_path / "rolls" / "walk"
    folder.mkdir(parents=True)
    lib = tmp_path / "lib"
    for number in (1, 2, 3):
        _prescan_entry(lib, f"other-{number}", folder, number,
                       "2026-09-20T10:00:10+00:00")
    frames = [{"number": 1, "prescan_entry": "deleted"},
              {"number": 2, "prescan_error": "the disk is full"},
              {"number": 3, "prescan_entry": None}]
    walk = {"started": "2026-09-20T10:00:00+00:00", "frames": frames}
    pairs = [(r["number"], r) for r in frames]
    assert session.walked_prescan_entries(folder, pairs, lib,
                                          walk=walk) == {}
    # A frame whose answer never came, in a walk whose others did.
    walk = {"started": "2026-09-20T10:00:00+00:00",
            "frames": [{"number": 1, "prescan_entry": "other-1"},
                       {"number": 2}]}
    pairs = [(r["number"], r) for r in walk["frames"]]
    assert session.walked_prescan_entries(folder, pairs, lib, walk=walk) == {
        1: lib / "other-1"}


def test_a_prescan_answer_is_promised_only_once_queued(tmp_path):
    """Promised as it was built, an answer whose `_file` raised before
    queuing it kept the manifest ahead of its file for the session: the
    window refused to move or delete that roll as still being filed."""
    manifest = session.RollManifest(tmp_path / "survey.json", {"frames": []})
    told = manifest.prescan_told({"number": 1}, tmp_path / "prescan01.tif")
    assert not manifest.ahead_of_disk()

    def refuses(**job):
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        session.queued(refuses, told)
    assert not manifest.ahead_of_disk()


def test_a_walk_prescan_that_could_not_be_queued_leaves_nothing_owed(
        tmp_path):
    """The same through the session: `_file` raising before the writer had
    the job left the walk's manifest waiting for an answer never coming."""
    real = ScanSession._orientation_for

    def fails(self, number, kind):
        # The second frame's: a walk that recorded none writes nothing and
        # keeps no manifest (FOUND-2), so the first is recorded.
        if kind == "prescan" and number == 2:
            raise RuntimeError("could not arrange it")
        return real(self, number, kind)

    s, _, events = run(Roll(frames=2, dry_run=True, name="owed"), tmp_path,
                       extra=lambda s, _: setattr(
                           s, "_orientation_for",
                           fails.__get__(s, ScanSession)))
    assert kinds(events, "failed")
    assert s._manifests
    assert not any(m.ahead_of_disk() for m in s._manifests.values())


def test_a_walk_not_carried_on_is_not_rewritten_by_the_last_ones_answers(
        tmp_path):
    """A plain walk into a folder replaces its manifest, and the writer's
    late answers for the last walk's prescans still went to the old one,
    which rewrote survey.json whole with that walk over this one's."""
    folder = tmp_path / "rolls" / "again"
    path = folder / "survey.json"
    old_record = {"number": 1}
    old = session.RollManifest(path, {"roll": "the last walk",
                                      "frames": [old_record]})
    told = old.prescan_told(old_record, folder / "prescan01.tif")
    session.queued(lambda **job: None, told)

    def live(s, _):
        s._manifests[path.resolve()] = old
    folder.mkdir(parents=True)
    run(Roll(frames=1, dry_run=True, name="again"), tmp_path, extra=live)
    told(_filing(session.FILED, entry=tmp_path / "late-entry",
                 written=(folder / "prescan01.tif",)))
    survey = json.loads(path.read_text(encoding="utf-8"))
    assert survey["roll"] == "again"
    assert "late-entry" not in json.dumps(survey)


def test_a_walk_names_no_prescan_its_writer_did_not_write(tmp_path,
                                                          monkeypatch):
    """Named on the scanning thread as it was queued, the record pointed at a
    file nothing had written -- or, re-walked, at the last walk's picture of
    that place, which the sheet then showed against the new position."""
    folder = tmp_path / "rolls" / "unwritten"
    folder.mkdir(parents=True)
    tiff.write(str(folder / "prescan01.tif"), np.zeros((3, 3, 3), np.uint8))
    # The last walk's, filed: a folder a fresh walk may go into (P18-a).
    session.write_manifest(folder / "survey.json", {
        "roll": "unwritten", "frames": [
            {"number": 1, "prescan": "prescan01.tif",
             "prescan_entry": "the-last-walks"}]})
    real = session.export.write

    def refuses(path, image, **kw):
        if Path(path).parent == folder:
            raise OSError("the disk is full")
        return real(path, image, **kw)

    monkeypatch.setattr(session.export, "write", refuses)
    _s, _scanner, events = run(Roll(frames=1, dry_run=True, name="unwritten"),
                               tmp_path)
    survey = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    record = survey["frames"][0]
    assert "prescan" not in record, record
    # Filed all the same, and said so.
    assert (tmp_path / record["prescan_entry"] / "scan.json").exists()
    # And why the file is missing: the record said nothing, and the frame
    # dropped off a reopened sheet without a word.
    assert record["prescan_error"] == (
        f"{session.NOT_WRITTEN}the disk is full")
    said = []
    assert session.walked_prescans(folder, survey, say=said.append) == []
    assert said == [f"frame 1 is not on the sheet: its prescan was not "
                    f"written (the disk is full); its pass is in the library "
                    f"as {record['prescan_entry']}"]
    # The entry stands for the frame all the same.
    assert session.walked_prescan_entries(
        folder, [(1, record)], tmp_path, walk=survey) == {
            1: tmp_path / record["prescan_entry"]}
    # A copy not written, its pass filed: no failure, and said as one.
    (filing,) = [e.filing for e in kinds(events, "filing")
                 if e.filing.kind == "prescan"]
    assert filing.outcome == session.COPY_FAILED
    assert filing.why_unwritten(folder / "prescan01.tif") == "the disk is full"
    (line,) = filing.trouble()
    assert "reopened contact sheet will not show this frame" in line
    assert not any("could not be filed" in e.text for e in kinds(events, "log"))


def test_a_walk_whose_output_folder_is_gone_keeps_its_sheet(tmp_path):
    """A walk writes each prescan twice: prescanNN.tif beside the record, and
    a copy in the output folder's prescans/. Any copy not written was said
    as the frame missing from a reopened sheet, so an unplugged output drive
    told the operator to walk again every frame of a strip whose sheet was
    whole."""
    out = made(tmp_path / "out")

    def pulled_out(index):
        if index == 0:
            shutil.rmtree(out)

    _s, _scanner, events = run(
        Roll(frames=2, dry_run=True, name="kept"), tmp_path,
        scanner=FakeScanner(frames=2, on_yield=pulled_out),
        extra=lambda s, _: setattr(s, "out_dir", out))
    folder = tmp_path / "rolls" / "kept"
    filings = [e.filing for e in kinds(events, "filing")
               if e.filing.kind == "prescan"]
    assert [f.outcome for f in filings] == [session.COPY_FAILED] * 2
    for filing in filings:
        assert filing.sheet == folder / f"prescan{filing.number:02d}.tif"
        assert filing.sheet in filing.written
        (line,) = filing.trouble()
        assert "contact sheet" not in line and "walk it again" not in line
        assert "Save As or Export" in line
        assert "is the drive mounted?" in line
    assert not out.exists(), "the output folder was made again"
    survey = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    said: list = []
    shown = session.walked_prescans(folder, survey, say=said.append)
    assert len(shown) == 2 and said == [], "the sheet lost a frame"


def test_a_picture_from_before_an_aim_is_marked_on_the_prescan_shown(
        tmp_path, monkeypatch):
    """It has no result of its own, so it is shown under the prescan that
    replaced it -- or, forgetting `shown`, counted and never marked. And no
    sheet reads it, so its copy missing takes no frame off one."""
    real = session.export.write

    def refuses(path, image, **kw):
        if Path(path).name == "prescan01-before.tif":
            raise OSError("the disk is full")
        return real(path, image, **kw)

    monkeypatch.setattr(session.export, "write", refuses)
    _s, _scanner, events = run(Roll(frames=2, dry_run=True, name="aimed"),
                               tmp_path, scanner=CorrectedAndFailingScanner())
    (prescan,) = [e.result for e in kinds(events, "result")
                  if e.result.kind == "prescan" and e.result.number == 1]
    (before,) = [e for e in kinds(events, "filing")
                 if e.filing.part == "prescan before its aim"]
    assert before.filing.outcome == session.COPY_FAILED
    assert before.done == before.filing.shown == prescan.seq
    assert before.filing.seq != prescan.seq, "filed under the prescan's seq"
    assert before.filing.sheet is None
    (line,) = before.filing.trouble()
    assert "prescan01-before.tif" in line and "the disk is full" in line
    assert "contact sheet" not in line and "Save As" not in line
    # The frame's own prescan is on the sheet, and its filing is clean.
    (own,) = [e.filing for e in kinds(events, "filing")
              if e.filing.seq == prescan.seq]
    assert own.outcome == session.FILED and own.part == ""


def test_a_prescan_the_scanner_published_no_meta_for_is_not_filed(tmp_path):
    """A meta built from the job -- a resolution and a channel order -- is
    what filed 26 prescans describing themselves wrongly, and it stayed as
    the fallback for a pass that came with none."""

    class Unpublished(FakeScanner):
        def scan_roll(self, frames=None, dry_run=False, first_index=0, **kw):
            yield RollFrame(index=first_index, position=self.pos, image=None,
                            meta={}, prescan=picture(channels=3),
                            registration={})

    _, _, events = run(Roll(frames=1, dry_run=True, name="bare"), tmp_path,
                       scanner=Unpublished())
    assert library.entries(tmp_path) == []
    assert any("published no record" in e.text for e in kinds(events, "log"))
    # Still written where a walk's prescan goes, and named there.
    folder = tmp_path / "rolls" / "bare"
    assert (folder / "prescan01.tif").exists()
    survey = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    assert survey["frames"][0]["prescan"] == "prescan01.tif"
    # Said to have none, so no reader joins it to another pass's entry.
    assert survey["frames"][0]["prescan_entry"] is None


def test_the_prescan_before_an_aim_is_never_filed_with_the_later_ones_meta(
        tmp_path):
    """It is another pass, which can have read the other way; filed with
    the replacing prescan's meta, its entry would describe that pass."""

    class NoBeforeMeta(FakeScanner):
        def scan_roll(self, frames=None, dry_run=False, first_index=0, **kw):
            yield RollFrame(index=first_index, position=self.pos, image=None,
                            meta={}, prescan=picture(channels=3),
                            registration={},
                            prescan_meta=dict(_PRESCAN_META),
                            prescan_before=picture(channels=3, seed=5))

    run(Roll(frames=1, dry_run=True, name="unaimed"), tmp_path,
        scanner=NoBeforeMeta())
    assert [k for k in _prescan_entries(tmp_path)] == [(1, False)]
    folder = tmp_path / "rolls" / "unaimed"
    assert (folder / "prescan01-before.tif").exists()


def test_only_the_chosen_frames_are_scanned(tmp_path):
    """The whole point of surveying first: four good frames, not seventeen."""
    _, scanner, _ = run(
        Roll(frames=5, resolution=600, name="picked", only=(1, 4)), tmp_path
    )
    # The window counts pictures from 1; the driver counts them from 0.
    assert scanner.only == (0, 3)
    assert len(frame_entries(tmp_path)) == 2
    assert sorted(f.name for f in (tmp_path / "rolls" / "picked").glob("*.tif")) \
        == ["frame01.tif", "frame04.tif"]


def test_choosing_every_frame_is_not_the_same_as_choosing_none(tmp_path):
    """`only=()` has to reach the driver as an empty set, not as None."""
    _, scanner, _ = run(Roll(frames=3, resolution=600, name="none", only=()),
                        tmp_path)
    assert scanner.only == ()
    assert library.entries(tmp_path) == []


def test_a_roll_with_no_choice_scans_everything(tmp_path):
    _, scanner, _ = run(Roll(frames=3, resolution=600, name="all"), tmp_path)
    assert scanner.only is None
    assert len(frame_entries(tmp_path)) == 3


def test_a_scan_of_chosen_frames_does_not_overwrite_the_survey(tmp_path):
    """The walk found six; the scan took three. Both records have to survive --
    one file for both meant the six were replaced by the three."""
    run(Roll(frames=3, dry_run=True, name="both"), tmp_path)
    run(Roll(frames=3, resolution=600, only=(2,), name="both"), tmp_path)
    out = tmp_path / "rolls" / "both"
    surveyed = json.loads((out / "survey.json").read_text(encoding="utf-8"))
    scanned = json.loads((out / "roll.json").read_text(encoding="utf-8"))
    assert [f["number"] for f in surveyed["frames"]] == [1, 2, 3]
    assert [f["number"] for f in scanned["frames"]] == [2]


def test_a_survey_leaves_its_prescans_beside_the_manifest(tmp_path):
    """So a strip can be looked at again tomorrow instead of walked again."""
    run(Roll(frames=3, dry_run=True, name="walk2"), tmp_path)
    out = tmp_path / "rolls" / "walk2"
    assert sorted(f.name for f in out.glob("prescan*.tif")) == [
        "prescan01.tif", "prescan02.tif", "prescan03.tif"
    ]
    recorded = json.loads((out / "survey.json").read_text(encoding="utf-8"))
    assert [f["prescan"] for f in recorded["frames"]] == [
        "prescan01.tif", "prescan02.tif", "prescan03.tif"
    ]


def test_a_real_roll_leaves_no_loose_prescans(tmp_path):
    """They are filed in the library, each in an entry of its own; the roll's
    folder holds its frames, and a stray copy there is just clutter."""
    run(Roll(frames=2, resolution=600, name="real2"), tmp_path)
    assert list((tmp_path / "rolls" / "real2").glob("prescan*.tif")) == []


# -- stopping ---------------------------------------------------------------


def test_a_stop_ends_the_roll_after_the_frame_in_flight(tmp_path):
    """Not during it. The frame being scanned always finishes -- an abandoned
    read is what leaves the scanner needing a power cycle."""
    holder = {}

    def press_stop_during(index):
        if index == 1:
            holder["session"].request_stop()

    scanner = FakeScanner(on_yield=press_stop_during)

    def remember(s, _scanner):
        holder["session"] = s

    _, scanner, events = run(Roll(frames=5, resolution=600, name="s"), tmp_path,
                             scanner=scanner, extra=remember)
    # Frame 2 was produced and kept; frames 3-5 never started.
    assert scanner.produced == 2
    assert len(frame_entries(tmp_path)) == 2
    assert any("stopped after frame 2" in e.text for e in kinds(events, "finished"))


def test_a_stop_does_not_carry_into_the_next_job(tmp_path):
    """Otherwise a stop pressed during one scan silently kills the next one the
    operator deliberately started."""
    scanner = FakeScanner()
    s = ScanSession(root=str(tmp_path), rolls=str(tmp_path / "rolls"),
                    open_scanner=lambda: scanner, verbose=False)
    s.start()
    s.request_stop()
    s.submit(Scan(resolution=600))
    s.shutdown()
    deadline = time.monotonic() + 10
    events = []
    while time.monotonic() < deadline:
        events.extend(s.poll())
        if any(e.kind == "closed" for e in events):
            break
        time.sleep(0.01)
    s.join(timeout=2.0)
    assert len(library.entries(tmp_path)) == 1


def test_a_job_queued_behind_a_stopped_roll_does_not_cancel_the_stop(tmp_path):
    """`submit` cleared the stop flag, so an aim-click or a key pressed after
    Stop -- anything queued behind the running roll -- cancelled the Stop and
    the roll ran to its end."""
    holder = {}

    def stop_then_queue(index):
        if index == 1:
            holder["session"].request_stop()
            holder["session"].submit(Prescan())

    scanner = FakeScanner(on_yield=stop_then_queue)

    def remember(s, _scanner):
        holder["session"] = s

    _, scanner, events = run(Roll(frames=5, resolution=600, name="s"), tmp_path,
                             scanner=scanner, extra=remember)
    assert scanner.produced == 2, "the Stop was cancelled by the job behind it"


def test_stop_drops_what_is_queued_behind_the_running_job(tmp_path):
    """A double-pressed Scan, or a roll queued behind a walk, ran anyway once
    the first had stopped."""
    holder = {}

    def queue_then_stop(index):
        if index == 0:
            holder["session"].submit(Scan(resolution=600))
            holder["session"].submit(Scan(resolution=600))
            holder["session"].request_stop()

    scanner = FakeScanner(on_yield=queue_then_stop)

    def remember(s, _scanner):
        holder["session"] = s

    _, scanner, events = run(Roll(frames=3, resolution=600, name="s"), tmp_path,
                             scanner=scanner, extra=remember)
    scans = [c for c in scanner.calls if c[0] == "scan"]
    assert len(scans) == 1, "a job queued before the Stop still ran"
    assert any("2 queued jobs not started" in e.text for e in kinds(events, "log"))
    assert [type(e.job) for e in kinds(events, "dropped")] == [Scan, Scan]


def test_a_roll_stops_when_a_frame_cannot_be_filed(tmp_path, monkeypatch):
    """A full disk or a vanished output folder fails every frame after it the
    same way; scanning on spent the rest of the roll on pictures that were
    then thrown away, one log line each."""
    scanner = FakeScanner(frames=6)
    refusing(monkeypatch, tmp_path / "library")
    s = ScanSession(root=str(tmp_path / "library"),
                    rolls=str(tmp_path / "rolls"),
                    open_scanner=lambda: scanner, verbose=False)
    # Each frame after the first waits until the first one's failed filing
    # has been handled. This used to sleep 50 ms a frame and hope the writer
    # thread won the race, which on a loaded runner it need not.
    handled = threading.Event()
    real_filed = s._filed

    def filed(filing):
        real_filed(filing)
        if filing.failed:
            handled.set()

    s._filed = filed
    real = scanner.scan

    def scan(**kw):
        if scanner.produced > 1:
            assert handled.wait(10), "the first frame's filing never failed"
        return real(**kw)

    scanner.scan = scan
    s.start()
    s.submit(Roll(frames=6, resolution=600, name="full"))
    s.shutdown()
    events = []
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        events.extend(s.poll())
        if any(e.kind == "closed" for e in events):
            break
        time.sleep(0.01)
    s.join(timeout=2.0)
    assert scanner.produced <= 2, "the roll scanned on into a library it could not write"
    assert any("stopping the roll" in e.text for e in kinds(events, "log"))


def test_force_abort_closes_the_transport_and_marks_the_session_dead(tmp_path):
    scanner = FakeScanner()
    s = ScanSession(root=str(tmp_path), open_scanner=lambda: scanner, verbose=False)
    s.start()
    for _ in range(200):                     # wait for the worker to open it
        if scanner.opened:
            break
        time.sleep(0.01)
    s.force_abort()
    assert scanner.t.closed
    assert s.dead is True
    s.shutdown()
    s.join(timeout=5.0)


def test_a_force_abort_still_files_what_debug_filing_spooled(tmp_path):
    """`close()` is what files the debug spool, and a dead session skipped
    it: every probe and hold of the session was left in a temporary directory
    nothing named, at the one time they mattered most. It sends the device
    nothing, so it runs -- after the writer, like every close."""
    order = []
    scanner = FakeScanner()
    scanner.close = lambda: order.append("close")
    s = ScanSession(root=str(tmp_path), open_scanner=lambda: scanner, verbose=False)
    real_finish = session.FrameWriter.finish

    def watched_finish(self):
        order.append("writer finished")
        return real_finish(self)

    session.FrameWriter.finish = watched_finish
    try:
        s.start()
        for _ in range(200):
            if scanner.opened:
                break
            time.sleep(0.01)
        s.force_abort()
        s.shutdown()
        s.join(timeout=5.0)
    finally:
        session.FrameWriter.finish = real_finish
    assert order == ["writer finished", "close"]


# -- events -----------------------------------------------------------------


def test_the_driver_log_reaches_the_event_queue(tmp_path):
    """The GUI shows these lines without capturing stdout."""
    _, _, events = run(Calibrate(mode="measure"), tmp_path)
    assert any("shading calibrated" in e.text for e in kinds(events, "log"))


def test_a_calibration_says_whether_it_left_a_reference(tmp_path):
    """The window asks for a calibration before a scan until this says one
    exists, so it has to say so for every way a calibration can end."""
    s, _, events = run(Calibrate(mode="measure"), tmp_path)
    assert [e.done for e in kinds(events, "calibrated")] == [1]
    assert s.calibrated is True


@pytest.mark.parametrize(("answer", "why"), [
    ({"action": "calibrated", "reference": None, "summary": "no usable lines"},
     "a measurement with nothing usable in it"),
    ({"action": "skipped", "summary": "shading off"}, "raw pixels by request"),
])
def test_a_calibration_without_a_reference_is_not_one(tmp_path, answer, why):
    scanner = FakeScanner()
    scanner.ensure_shading = lambda *a, **k: answer
    s, _, events = run(Calibrate(mode="measure"), tmp_path, scanner=scanner)
    assert [e.done for e in kinds(events, "calibrated")] == [0], why
    assert s.calibrated is False


def test_a_calibration_that_fails_says_so_before_the_failure(tmp_path):
    scanner = FakeScanner()

    def broken(*a, **k):
        raise OSError("the pass came back short")

    scanner.ensure_shading = broken
    s, _, events = run(Calibrate(mode="measure"), tmp_path, scanner=scanner)
    order = [e.kind for e in events if e.kind in ("calibrated", "failed")]
    assert order == ["calibrated", "failed"]
    assert kinds(events, "calibrated")[0].done == 0
    assert s.calibrated is False


def test_progress_arrives_as_numbers_not_as_text(tmp_path):
    """A progress bar that parses the log line goes quietly dead the day the
    line is reworded."""
    _, _, events = run(Prescan(), tmp_path)
    progress = kinds(events, "progress")
    assert progress and progress[-1].done == 287 and progress[-1].total == 287


def test_a_result_carries_a_working_copy_the_ui_can_keep(tmp_path):
    """A pass wider than the window's working size comes back reduced to it.
    Asked of the fake's own 24 x 36 picture, as this used to be, any limit
    at all held."""
    from rps7200 import preview

    scanner = FakeScanner()
    real = scanner.scan

    def wide(**kw):
        image, meta = real(**kw)
        image = np.tile(image, (1, 81, 1))           # 24 x 2916
        return image, dict(meta, width=image.shape[1])

    scanner.scan = wide
    _, _, events = run(Scan(resolution=600), tmp_path, scanner=scanner)
    results = kinds(events, "result")
    assert len(results) == 1
    image = results[0].result.image
    assert image is not None
    assert max(image.shape[:2]) <= preview.PREVIEW_MAX_SIDE < 2916
    assert image.shape[2] == 4                       # every channel still there


def test_a_result_learns_where_its_entry_landed(tmp_path):
    """The 1:1 view reads real pixels back from the file; the working copy is
    decimated and cannot show grain."""
    _, _, events = run(Scan(resolution=600), tmp_path)
    filed = kinds(events, "filed")
    result = kinds(events, "result")[0].result
    assert filed and filed[0].done == result.seq
    assert (tmp_path / library.entries(tmp_path)[0]["id"]) == __import__(
        "pathlib").Path(filed[0].text)


def test_a_roll_shows_each_prescan_as_well_as_each_frame(tmp_path):
    """"All prescans should be able to be seen" -- so both land in the strip."""
    _, _, events = run(Roll(frames=2, resolution=600, name="r"), tmp_path)
    got = [e.result.kind for e in kinds(events, "result")]
    assert got == ["prescan", "frame", "prescan", "frame"]


def test_a_failed_scan_is_reported_and_does_not_kill_the_session(tmp_path):
    _, _, events = run(Scan(resolution=600), tmp_path,
                       scanner=FakeScanner(fail_scan=True))
    failures = kinds(events, "failed")
    assert failures and "the scanner said no" in failures[0].text
    assert any(e.kind == "closed" for e in events)


def test_a_scanner_that_will_not_open_is_reported_not_raised(tmp_path):
    class Broken:
        def open(self):
            raise RuntimeError("not on the bus -- is it powered on?")

    s = ScanSession(root=str(tmp_path), open_scanner=Broken, verbose=False)
    s.start()
    s.join(timeout=5.0)
    events = s.poll()
    assert any("not on the bus" in e.text for e in kinds(events, "failed"))


def test_a_scanner_that_opens_and_will_not_answer_is_closed_again(tmp_path):
    """Opened, then INQUIRY failed: the interface stayed claimed until the
    window quit, so nothing else could reach the scanner to see why."""
    class Mute(FakeScanner):
        def inquiry(self, refresh=False):
            raise RuntimeError("no answer to INQUIRY")

    scanner = Mute()
    s = ScanSession(root=str(tmp_path), open_scanner=lambda: scanner,
                    verbose=False)
    s.start()
    s.join(timeout=5.0)
    assert any("no answer" in e.text for e in kinds(s.poll(), "failed"))
    assert scanner.closed, "left open with its interface claimed"


def test_the_device_closes_before_the_writer_spends_time_gzipping(tmp_path):
    """Gzipping a library entry with the device open and idle preceded a wedge.

    And debug filing -- `close()` -- only once the writer has finished, so a
    pass is left to it exactly when nothing here filed it."""
    order = []
    scanner = FakeScanner()
    real_close = scanner.close
    real_transport_close = scanner.t.close

    def watched_transport_close():
        order.append("device closed")
        real_transport_close()

    def watched_close():
        order.append("debug filed")
        real_close()

    scanner.t.close = watched_transport_close
    scanner.close = watched_close
    s = ScanSession(root=str(tmp_path), open_scanner=lambda: scanner, verbose=False)
    real_writer = session.FrameWriter.finish

    def watched_finish(self):
        order.append("writer finished")
        return real_writer(self)

    session.FrameWriter.finish = watched_finish
    try:
        s.start()
        s.submit(Scan(resolution=600))
        s.shutdown()
        s.join(timeout=10.0)
    finally:
        session.FrameWriter.finish = real_writer
    assert order == ["device closed", "writer finished", "debug filed"]


# -- estimates --------------------------------------------------------------


def test_an_untied_infrared_pass_never_estimates_below_its_floor():
    """Untied, a pass with infrared on holds the device for ~212 s however few
    lines were asked for. That floor is why a short timeout once wedged it."""
    for dpi in (25, 300, 600, 1800):
        assert (estimate_seconds(dpi, infrared=True, fast_infrared=False)
                >= session.INFRARED_FLOOR_S)


def test_a_tied_infrared_pass_is_allowed_below_the_floor():
    """Because it measurably goes there. 24.6 s at 300 dpi, against 219.2 s
    untied -- the floor is not spent at all, so an estimate that still assumed
    it would be wrong by a factor of nine on the readout an operator watches."""
    assert estimate_seconds(300, infrared=True) < session.INFRARED_FLOOR_S


def test_the_estimates_track_what_was_measured():
    """RGB: 900 dpi 38.8 s, 1800 dpi 69.6 s.

    Infrared, measured 2026-09-16 on one slide: tied, 24.6 s at 300 dpi,
    110.5 s at 1800 and 213.6 s at 3600; untied, ~220 s at every one of them.
    """
    assert estimate_seconds(900, False) == pytest.approx(38.8, abs=3)
    assert estimate_seconds(1800, False) == pytest.approx(69.6, abs=3)

    assert estimate_seconds(300, True) == pytest.approx(24.6, abs=3)
    assert estimate_seconds(1800, True) == pytest.approx(110.5, abs=5)
    assert estimate_seconds(3600, True) == pytest.approx(213.6, abs=10)

    # Untied: flat at the floor until the line count overtakes it. The 3600 dpi
    # figure supersedes the older 334 s anchor in docs/dpi-tradeoff-plan.md,
    # which was a different film at a different exposure -- see
    # INFRARED_UNTIED_S.
    assert estimate_seconds(300, True, False) == pytest.approx(219.2, abs=3)
    assert estimate_seconds(1800, True, False) == pytest.approx(220.3, abs=3)
    assert estimate_seconds(3600, True, False) == pytest.approx(221.0, abs=3)


def test_an_untied_pass_stops_being_flat_once_the_lines_cost_more():
    """The floor is a floor, not a constant. Past the crossover an untied pass
    costs what the lines cost, same as a tied one -- which is exactly why there
    is nothing left to save up there."""
    assert (estimate_seconds(7200, True, False)
            == pytest.approx(estimate_seconds(7200, True), abs=1))


def test_tying_infrared_never_estimates_slower():
    """At every resolution, at worst a wash. The saving shrinks as the line
    count overtakes the floor -- 89% at 300 dpi, 50% at 1800, 3% at 3600 as
    measured -- but it never turns negative, and a readout that suggested
    otherwise would be talking an operator out of the right default."""
    for dpi in (300, 600, 900, 1200, 1800, 3600, 7200):
        assert estimate_seconds(dpi, True) <= estimate_seconds(dpi, True, False)


def test_more_resolution_never_estimates_less_time():
    previous = 0.0
    for dpi in (300, 600, 900, 1800, 3600, 7200):
        now = estimate_seconds(dpi, False)
        assert now >= previous
        previous = now


# -- one sum for a roll's time, the window's and the tool's (NEW-T1) ---------


def _slide_roll(**kw):
    """Six slides at 1800 dpi with infrared, prescanned at 300, by
    `session.roll_seconds`, with ``kw`` over that."""
    return session.roll_seconds(**dict(
        dict(count=6, places=6, prescan_dpi=300, dpi=1800, infrared=True,
             fast_ir=True, film="positive", meter="each"), **kw))


def test_a_slide_with_infrared_is_costed_its_colour_pass_where_one_is_taken():
    """The window's scans and rolls take an RGB pass a slide with infrared
    after its RGBI one; the tools take one pass, and so are costed."""
    rgb = estimate_seconds(1800, False)
    taken = _slide_roll(colour_pass=True)
    assert taken.passes - _slide_roll().passes == pytest.approx(6 * rgb)
    assert _slide_roll(film="negative", colour_pass=True) == _slide_roll(
        film="negative"), "a negative takes none"
    assert _slide_roll(infrared=False, colour_pass=True) == _slide_roll(
        infrared=False), "nor a slide without infrared"
    assert _slide_roll(dry_run=True, colour_pass=True).passes == (
        6 * estimate_seconds(300, False)), "a walk scans nothing"


def test_metering_is_costed_as_the_roll_meters():
    """The window charged 70 s of metering on every frame, metered once or
    not at all: about a minute a frame too much."""
    each, once, none = (_slide_roll(meter=m).other
                        for m in ("each", "once", "none"))
    assert each - none == pytest.approx(6 * session.METERING_S)
    assert once - none == pytest.approx(session.METERING_S)
    assert _slide_roll(meter="each", dry_run=True).other == (
        _slide_roll(meter="none", dry_run=True).other), "a walk meters nothing"


def test_holds_are_costed_as_they_ran_with_the_cap_beside():
    """493 held frames took 0.28 moves a frame; costed at the cap of three,
    as the tool's warning must be, a sheet's roll read about ten times the
    holding it would do."""
    from rps7200.framing import MAX_HOLD_MOVES

    look = estimate_seconds(300, False)
    bare = _slide_roll(meter="none")
    seen = _slide_roll(meter="none", placing=6, holds="seen")
    cap = _slide_roll(meter="none", placing=6, holds="cap")
    assert session.HOLD_MOVES_SEEN == 0.28
    assert seen.typical - bare.typical == pytest.approx(
        6 * session.HOLD_MOVES_SEEN * (look + session.HOLD_MOVE_S))
    assert cap.typical - bare.typical == pytest.approx(
        6 * MAX_HOLD_MOVES * (look + session.HOLD_MOVE_S))
    # The rest of the cap, said apart: together they are the cap.
    assert seen.typical + seen.holds == pytest.approx(cap.typical)
    assert cap.holds == 0.0 and bare.holds == 0.0


def test_a_single_scan_is_costed_with_its_metering_and_colour_pass():
    """A slide at 1800 dpi with infrared said "about 1m 51s", its RGBI pass
    alone, where metering and its RGB pass make it about four minutes."""
    pass_alone = estimate_seconds(1800, True, True)
    whole = session.scan_seconds(1800, True, True, "positive", metered=True,
                                 colour_pass=True)
    assert whole == pytest.approx(pass_alone + estimate_seconds(1800, False)
                                  + session.METERING_S)
    assert 230 < whole < 260
    assert session.scan_seconds(1800, True, True, "negative", metered=False,
                                colour_pass=True) == pass_alone


def test_one_fine_move_is_costed_from_the_stored_holds():
    """The aim said "about 1 s" from a 1.1 nothing measured. A hold's move
    less its settle is 2.2 s, from 85 stored hold looks: an upper bound."""
    assert session.MOVE_S == pytest.approx(
        session.HOLD_MOVE_S - DirectScanner.HOLD_SETTLE_S)
    assert session.MOVE_S == pytest.approx(2.2)


# -- a filing that went wrong, named as its picture is (B3-T1) --------------


def _refuse_every_save(monkeypatch):
    def refused(image, meta, **kw):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(library, "save", refused)


def _trouble(events):
    return [line for e in kinds(events, "filing")
            for line in e.filing.trouble()]


def test_a_single_pass_not_filed_is_named_by_its_label(tmp_path,
                                                       monkeypatch):
    """A single Scan's "Not filed" notice called it "picture 0" -- a number
    no single pass has -- beside a result the window labels "300 dpi RGB"
    (the batch 3 tour)."""
    _s, _sc, events = run(Scan(resolution=300, infrared=False), tmp_path)
    # Filed, the success line and the event the 1:1 view reads are as they
    # were: `Filing.said` is not the name.
    assert any(e.text.startswith("filed: ") for e in kinds(events, "log"))
    assert kinds(events, "filed")
    _refuse_every_save(monkeypatch)
    for each in ("scan", "prescan"):
        (tmp_path / each).mkdir()
    _s, _sc, events = run(Scan(resolution=300, infrared=False),
                          tmp_path / "scan")
    (line,) = _trouble(events)
    assert line.startswith("300 dpi RGB could not be filed: "), line
    _s, _sc, events = run(Prescan(resolution=300), tmp_path / "prescan")
    (line,) = _trouble(events)
    assert line.startswith("prescan 300 dpi could not be filed: "), line
    assert not any("picture 0" in e.text for e in events)


def test_a_slide_and_a_later_strips_frame_are_named_by_their_parts(
        tmp_path, monkeypatch):
    """A slide's three writes are told apart by their part, and a roll's
    frame by its number with its place beside it, as its result is."""
    from test_colour_pass import SingleSlideScanner, SlideScanner

    _refuse_every_save(monkeypatch)
    (tmp_path / "single").mkdir()
    _s, _sc, events = run(Scan(resolution=600, infrared=True,
                               film="positive"),
                          tmp_path / "single", scanner=SingleSlideScanner())
    lines = _trouble(events)
    assert any(line.startswith("600 dpi RGB + infrared · infrared pass "
                               "could not be filed: ") for line in lines), lines
    assert any(line.startswith("600 dpi RGB + infrared could not be filed: ")
               for line in lines), lines
    rolls = tmp_path / "rolls"
    roll = _strip_roll(rolls, "Slides", [(1, 1, 6, _walked(1)),
                                         (2, 7, None, _walked(1))])
    _s, _sc, events = run(
        Roll(frames=1, start_at=2, resolution=600, infrared=True,
             film="positive", out=str(roll / "strip-02")),
        tmp_path, scanner=SlideScanner())
    lines = _trouble(events)
    assert any(line.startswith("frame 8 (strip 2 · 2) · 600 dpi RGB + "
                               "infrared · infrared pass could not be filed")
               for line in lines), lines
    assert not any("picture" in line for line in lines), lines
    stopping = [e.text for e in kinds(events, "log")
                if e.text.startswith("stopping the roll")]
    assert stopping and "frame 8 (strip 2 · 2) · 600 dpi" in stopping[0]


# -- the writer -------------------------------------------------------------


def test_the_writer_collects_failures_rather_than_raising(tmp_path):
    """A roll runs for hours; a frame that cannot be filed should cost that
    frame, not the thirty after it."""
    done: queue.Queue = queue.Queue()
    writer = session.FrameWriter(on_done=done.put)
    writer.submit(number=1, paths=[], image=picture(), meta={},
                  dpi=600, library=None, film=FilmNotes(), tags=[],
                  prescan=None, inquiry=None, capture={}, seq=1)
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"not a directory")
    writer.submit(number=2, paths=[blocker / "no.tif"],
                  image=picture(), meta={}, dpi=600, library=None,
                  film=FilmNotes(), tags=[], prescan=None, inquiry=None,
                  capture={}, seq=2)
    writer.finish()
    assert len(writer.errors) == 1
    assert "picture 2" in writer.errors[0]
    assert writer.problems == []
    # One record per job, each reported once.
    first, second = done.get_nowait(), done.get_nowait()
    assert done.empty()
    assert (first.seq, first.outcome) == (1, session.WRITTEN)
    assert (second.seq, second.outcome) == (2, session.NOT_FILED)
    assert writer.filings == [first, second]


def test_a_copy_that_cannot_be_written_does_not_cost_the_entry(tmp_path):
    """The library entry holds the only raw bytes; a delivered copy can be
    exported again from it. Written copies-first, an unplugged output drive
    raised before the entry was filed, and the scan was gone."""
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"not a directory")
    (tmp_path / "out").mkdir()
    done: queue.Queue = queue.Queue()
    writer = session.FrameWriter(on_done=done.put)
    writer.submit(number=1, paths=[blocker / "no.tif", tmp_path / "out" / "yes.tif"],
                  image=picture(), meta={"resolution_dpi": 600}, dpi=600,
                  library=str(tmp_path / "lib"), film=FilmNotes(), tags=[],
                  prescan=None, inquiry=None, capture={}, seq=1)
    writer.finish()
    filing = done.get_nowait()
    assert done.empty(), "reported more than once"
    assert filing.entry is not None and (filing.entry / "scan.tif").exists()
    assert filing.outcome == session.COPY_FAILED and not filing.failed
    assert filing.written == (tmp_path / "out" / "yes.tif",)
    assert [p for p, _why in filing.unwritten] == [str(blocker / "no.tif")]
    assert (tmp_path / "out" / "yes.tif").exists(), "one bad copy stopped the rest"
    # Its words: the copy, the entry by name and what writes it again, and
    # never a success line among them.
    assert len(writer.errors) == 1 and "Save As" in writer.errors[0]
    assert filing.entry.name in writer.errors[0]
    assert not any(e.startswith("filed:") for e in writer.errors)
    assert writer.done == [(1, filing.entry)]
    assert writer.problems == []


RAW = b"\x00\x01" * 32
LAYOUT = {"format": "index", "width": 36, "lines": 24, "channels": 4}


def _refused_by_the_library(tmp_path, monkeypatch, everywhere=False):
    """A writer whose library cannot be written, and what came of one job.

    The library root is under a file, so `library.save` raises before it
    writes anything -- as a full disk, an unplugged drive or a folder that
    will not take a file does. ``everywhere`` makes every other place refuse
    too.
    """
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"not a directory")
    (tmp_path / "out").mkdir()
    monkeypatch.setattr("tempfile.gettempdir",
                        lambda: str(made(tmp_path / "temp")))
    if everywhere:
        def refuse(*a, **kw):
            raise OSError(28, "No space left on device")
        monkeypatch.setattr(library, "save", refuse)
    done: queue.Queue = queue.Queue()
    writer = session.FrameWriter(on_done=done.put)
    raw = picture(seed=5)
    writer.submit(number=4, paths=[tmp_path / "out" / "frame04.tif"],
                  image=(raw // 2).astype(raw.dtype), raw_image=raw,
                  meta={"resolution_dpi": 600, "channel_order": list("RGBI")},
                  dpi=600, library=str(blocker / "library"), film=FilmNotes(),
                  tags=["roll"], prescan=None, inquiry=None,
                  capture={"reference": None, "ccd_mask": None, "raw": RAW,
                           "raw_layout": LAYOUT},
                  seq=1)
    writer.finish()
    filing = done.get_nowait()
    assert done.empty() and writer.problems == []
    return writer, filing, raw


def test_a_library_that_refuses_a_picture_does_not_cost_its_copies(
        tmp_path, monkeypatch):
    """The library entry first, the copies after -- and a library that
    refused used to end the job there: no frameNN.tif, no output-folder copy,
    although the disk they go to had room. The picture was lost entirely."""
    writer, filing, _raw = _refused_by_the_library(tmp_path, monkeypatch)
    assert (tmp_path / "out" / "frame04.tif").exists(), (
        "the library refused, and the delivered copy was never attempted")
    assert filing.failed and filing.entry is None and filing.error, \
        "still a failure, said as one"
    assert filing.written == (tmp_path / "out" / "frame04.tif",)
    assert len(writer.errors) == 1
    assert writer.done == [], "a pass not filed is not done"


def test_a_picture_the_library_refused_keeps_its_raw_data(tmp_path, monkeypatch):
    """The raw bytes and pixels in the job are the only copy: nothing reads
    them from the scanner again. Kept as a whole entry beside the copy that
    was written, so moving the folder into the library files it."""
    writer, filing, raw = _refused_by_the_library(tmp_path, monkeypatch)
    kept = list((tmp_path / "out" / session.UNFILED).glob("*/scan.json"))
    assert len(kept) == 1, "the raw data went nowhere"
    folder = kept[0].parent
    assert filing.kept == folder
    assert library.read_raw(folder) == RAW
    assert np.array_equal(tiff.read(folder / "scan.tif"), raw), (
        "kept the corrected pixels, not the ones the scanner sent")
    assert str(folder) in filing.error, "the operator is not told where it is"
    assert "move that folder into the library" in filing.error
    assert session.UNFILED in library.entries(folder.parent)[0]["tags"]


def test_a_pass_kept_in_the_temporary_folder_is_said_to_be_at_risk(
        tmp_path, monkeypatch):
    """A slide's passes and a real roll's prescans have no copy to be kept
    beside, so a refused one always lands in %TEMP% -- which Disk Cleanup
    and Storage Sense empty. Said as the folder beside a copy was, nobody
    would hurry."""
    monkeypatch.setattr("tempfile.gettempdir",
                        lambda: str(made(tmp_path / "temp")))
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"not a directory")
    done: queue.Queue = queue.Queue()
    writer = session.FrameWriter(on_done=done.put)
    raw = picture(seed=5)
    writer.submit(number=4, paths=[], image=raw, raw_image=raw,
                  meta={"resolution_dpi": 600, "channel_order": list("RGBI")},
                  dpi=600, library=str(blocker / "library"), film=FilmNotes(),
                  tags=["roll"], prescan=None, inquiry=None,
                  capture={"reference": None, "ccd_mask": None, "raw": RAW,
                           "raw_layout": LAYOUT}, seq=1)
    writer.finish()
    filing = done.get_nowait()
    assert filing.failed and filing.kept is not None
    assert (tmp_path / "temp" / session.UNFILED_TEMP) in filing.kept.parents
    (said,) = filing.trouble()
    assert "temporary folder, which Windows may clear" in said
    assert "move it into the library now" in said
    assert session.kept_said(tmp_path / "out" / session.UNFILED / "x") == (
        "move that folder into the library to file it")


def test_bytes_filed_that_cannot_be_the_picture_are_said_in_the_log(tmp_path):
    """`library.save` files such bytes and warns -- on stderr, which the
    window's log pane never shows. The writer's notes are what reach it."""
    raw = picture(seed=5)                       # 24 x 36 x 4
    writer = session.FrameWriter()
    with pytest.warns(RuntimeWarning, match="cannot be this picture's"):
        writer.submit(number=4, paths=[], image=raw, raw_image=raw,
                      meta={"resolution_dpi": 600,
                            "channel_order": list("RGBI")},
                      dpi=600, library=str(tmp_path), film=FilmNotes(),
                      tags=["roll"], prescan=None, inquiry=None,
                      capture={"reference": None, "ccd_mask": None,
                               "raw": RAW, "raw_layout": dict(LAYOUT, width=40)},
                      compress=False, seq=1)
        writer.finish()
    assert library.read_raw(tmp_path / library.entries(tmp_path)[0]["id"]) == RAW
    assert any(n.startswith("picture 4: its raw bytes cannot be this "
                            "picture's (width 36 for the picture, 40 in the "
                            "bytes)") for n in writer.notes), writer.notes


def test_a_picture_the_library_refused_names_the_copy_it_wrote(tmp_path):
    """The job's own answer -- what a roll's record is written from -- said
    nothing was written: the frameNN.tif the writer had just finished was
    named only in the text of the error, where nothing reading records looks."""
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"not a directory")
    told = []
    writer = session.FrameWriter()
    raw = picture(seed=5)
    path = made(tmp_path / "out") / "frame04.tif"
    writer.submit(number=4, paths=[path], image=raw, raw_image=raw,
                  meta={"resolution_dpi": 600, "channel_order": list("RGBI")},
                  dpi=600, library=str(blocker / "library"), film=FilmNotes(),
                  tags=["roll"], prescan=None, inquiry=None,
                  capture={"reference": None, "ccd_mask": None, "raw": RAW,
                           "raw_layout": LAYOUT},
                  on_filed=told.append)
    writer.finish()
    (filing,) = told
    assert filing.entry is None and filing.failed and filing.error, \
        "still a failure, said as one"
    assert filing.written == (path,)


def test_a_picture_nowhere_will_take_says_so(tmp_path, monkeypatch):
    writer, filing, _raw = _refused_by_the_library(
        tmp_path, monkeypatch, everywhere=True)
    assert "could not be kept anywhere else" in filing.error
    assert filing.kept is None
    assert (tmp_path / "out" / "frame04.tif").exists()


def _job(tmp_path, number, paths, library_root=None, **extra):
    """A writer job of a small RGBI picture, as `ScanSession._file` makes one."""
    raw = picture(seed=number)
    return dict(number=number, paths=paths, image=raw, raw_image=raw,
                meta={"resolution_dpi": 600, "channel_order": list("RGBI")},
                dpi=600, library=library_root, film=FilmNotes(
                    frame=f"pair-{number:02d}"),
                tags=["roll"], prescan=None, inquiry=None,
                capture={"reference": None, "ccd_mask": None, "raw": RAW,
                         "raw_layout": LAYOUT}, **extra)


def test_a_picture_with_no_entry_is_written_or_not_filed(tmp_path):
    """No library, every copy written: written. One of two copies refused:
    not filed -- the copies were its only home -- and the one written still
    named, which a bare OSError lost."""
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"")
    writer = session.FrameWriter()
    writer.submit(**_job(tmp_path, 1, [tmp_path / "a.tif"], seq=1))
    writer.submit(**_job(tmp_path, 2, [tmp_path / "b.tif", blocker / "b.tif"],
                         seq=2))
    writer.finish()
    first, second = writer.filings
    assert first.outcome == session.WRITTEN and not first.trouble()
    assert first.said() is None, "no entry, no success line"
    assert second.outcome == session.NOT_FILED
    assert second.written == (tmp_path / "b.tif",)
    assert [p for p, _ in second.unwritten] == [str(blocker / "b.tif")]
    assert writer.done == [(1, None)]


@pytest.mark.parametrize("partner_filed", [True, False])
def test_a_slides_picture_stands_on_its_infrared_pass(tmp_path, monkeypatch,
                                                      partner_filed):
    """A slide's picture is filed in no entry: it is made again from the
    frame's two passes. Its copy refused was a failure whatever its passes
    did, and stopped a slide roll whose passes were all in the library. It
    is a copy not written while its infrared pass is filed, and still a
    failure when that pass is not."""
    monkeypatch.setattr("tempfile.gettempdir",
                        lambda: str(made(tmp_path / "temp")))
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"")
    lib = tmp_path / "lib" if partner_filed else blocker / "lib"
    writer = session.FrameWriter()
    writer.submit(**_job(tmp_path, 1, [], library_root=str(lib), seq=7,
                         shown=9, part="infrared pass"))
    writer.submit(**_job(tmp_path, 1, [blocker / "frame01.tif"], seq=8,
                         shown=9, part="picture", pair=7))
    writer.finish()
    infrared, picture_ = writer.filings
    assert (infrared.shown, infrared.part) == (9, "infrared pass")
    assert (picture_.shown, picture_.part) == (9, "picture")
    assert writer.problems == []
    if partner_filed:
        assert infrared.outcome == session.FILED
        assert picture_.outcome == session.COPY_FAILED
        assert picture_.partner == infrared.entry and picture_.entry is None
        (line,) = picture_.trouble()
        assert "made from the frame's two passes" in line
        assert infrared.entry.name in line
        # Nothing claimed of the RGB pass, which speaks for itself.
        assert "colour" not in line and "RGB" not in line
    else:
        assert infrared.outcome == session.NOT_FILED
        assert picture_.outcome == session.NOT_FILED


def test_a_roll_frame_the_library_refused_is_still_in_the_roll(tmp_path,
                                                              monkeypatch):
    """End to end: the frame's own TIFF is written, its raw data is kept in
    the roll's folder, its record says where, and the roll stops."""
    refusing(monkeypatch, tmp_path / "library")
    scanner = FakeScanner(frames=2)
    s = ScanSession(root=str(tmp_path / "library"),
                    rolls=str(tmp_path / "rolls"),
                    open_scanner=lambda: scanner, verbose=False)
    s.start()
    s.submit(Roll(frames=2, resolution=600, name="refused"))
    s.shutdown()
    s.join(timeout=10.0)
    folder = tmp_path / "rolls" / "refused"
    assert (folder / "frame01.tif").exists()
    kept = list((folder / session.UNFILED).glob("*/scan.json"))
    assert kept, "the frame's raw data went nowhere"
    recorded = json.loads((folder / "roll.json").read_text(encoding="utf-8"))
    first = next(f for f in recorded["frames"] if f["number"] == 1)
    assert first["done"] is False
    # Frame 2 may have been scanned, and kept the same way, before the stop.
    assert any(str(k.parent) in first["filing_error"] for k in kept)


def test_every_writer_job_ends_in_one_filing_of_the_job_that_queued_it(
        tmp_path):
    """One "filing" event a writer job, whatever came of it, naming the
    result it is shown under and the job that queued it -- the session's
    ordinal, which "state" says as each job starts. Its filings land after
    its own "finished", during the next job; counted by when they landed,
    a failure was the next job's."""
    _s, _scanner, events = run(
        Scan(resolution=600), tmp_path,
        extra=lambda s, _: s.submit(Prescan(resolution=300)))
    ordinals = [e.done for e in kinds(events, "state") if e.busy]
    assert ordinals == [1, 2]
    results = {e.result.seq: e.result for e in kinds(events, "result")}
    filings = [e.filing for e in kinds(events, "filing")]
    assert [(f.kind, f.owner, f.outcome) for f in filings] == [
        ("prescan", 1, session.FILED), ("scan", 2, session.FILED)]
    for event in kinds(events, "filing"):
        assert event.done == event.filing.shown == event.filing.seq
        assert results[event.done].kind == event.filing.kind
        assert event.text == event.filing.outcome
    # "filed" still names each entry, for the window's full view.
    assert {e.done for e in kinds(events, "filed")} == set(results)


def test_the_session_says_whether_a_pass_is_still_being_filed(
        tmp_path, monkeypatch):
    """The window learns of an entry from "filed", a tick or two after the
    writer has it, and could not tell a pass still being filed from one
    never to be: Delete then offered only to drop it from the session, and
    the entry was filed afterwards with nothing said."""
    go, saving = threading.Event(), threading.Event()
    real = library.save

    def slow(image, meta, **kw):
        saving.set()
        assert go.wait(10)
        return real(image, meta, **kw)

    monkeypatch.setattr(library, "save", slow)
    s = ScanSession(root=str(tmp_path), rolls=str(tmp_path / "rolls"),
                    open_scanner=FakeScanner, verbose=False)
    # What `filing` said as each "filed" was emitted: never behind it.
    said_then = []
    emit = s._emit

    def watching(kind, **kw):
        if kind == "filed":
            said_then.append(s.filing(kw["done"]))
        emit(kind, **kw)

    s._emit = watching
    s.start()
    s.submit(Scan(resolution=600))
    assert saving.wait(10)
    deadline = time.monotonic() + 10
    events: list = []
    while not kinds(events, "result") and time.monotonic() < deadline:
        events.extend(s.poll())
        time.sleep(0.01)
    (result,) = [e.result for e in kinds(events, "result")]
    assert s.filing(result.seq) == ("owed", None)
    go.set()
    s.shutdown()
    s.join(timeout=10.0)
    events.extend(s.poll())
    (filed,) = kinds(events, "filed")
    assert s.filing(result.seq) == ("filed", Path(filed.text))
    assert said_then == [("filed", Path(filed.text))]
    assert s.filing(999) == ("none", None)


def test_a_pass_the_library_refused_is_owed_no_more(tmp_path, monkeypatch):
    refusing(monkeypatch, tmp_path)
    s, _, events = run(Scan(resolution=600), tmp_path)
    (result,) = [e.result for e in kinds(events, "result")]
    assert s.filing(result.seq) == ("none", None)


def test_a_slide_frame_is_owed_from_the_moment_it_is_shown(tmp_path,
                                                           monkeypatch):
    """A slide's frame is shown first and queued third, behind its infrared
    pass and its picture, either of which can wait seconds on a writer still
    busy with the frame before. Owed only once queued, it said "none" all
    that time, and a Delete then took a pass about to be filed for one never
    to be."""
    from test_colour_pass import SlideScanner

    holder: dict = {}
    seen: list = []
    real_submit = session.FrameWriter.submit

    def submit(self, **job):
        if job.get("part") in ("infrared pass", "picture"):
            seen.append((job["part"], holder["s"].filing(job["shown"])))
        return real_submit(self, **job)

    monkeypatch.setattr(session.FrameWriter, "submit", submit)

    def watch(s, _scanner):
        holder["s"] = s
        emit = s._emit

        def emitting(kind, **kw):
            if kind == "result":
                seen.append(("result", s.filing(kw["result"].seq)))
            emit(kind, **kw)

        s._emit = emitting

    s, _, events = run(Roll(frames=2, resolution=600, infrared=True,
                            film="positive", name="owed"), tmp_path,
                       scanner=SlideScanner(), extra=watch)
    assert [part for part, _ in seen] == ["result", "infrared pass",
                                          "picture"] * 2
    assert {said for _, said in seen} == {("owed", None)}, seen
    for result in (e.result for e in kinds(events, "result")):
        assert s.filing(result.seq)[0] == "filed"


def test_a_pass_shown_and_never_queued_is_owed_no_more(tmp_path,
                                                       monkeypatch):
    """Owed from before its result is shown, a pass whose job then failed
    before queuing it was owed for good."""
    def fails(self, *a, **kw):
        raise RuntimeError("the output folder is not there")

    monkeypatch.setattr(ScanSession, "_file", fails)
    s, _, events = run(Scan(resolution=600), tmp_path)
    (result,) = [e.result for e in kinds(events, "result")]
    assert kinds(events, "failed")
    assert s.filing(result.seq) == ("none", None)
    assert s._unqueued == set()


def test_an_output_folder_that_cannot_be_looked_at_costs_only_the_copy(
        tmp_path, monkeypatch):
    """`Path.exists` raises, rather than saying no, for a folder it may not
    look in -- access denied, a share dropped or its logon expired. Only a
    name already taken was caught, so the claim raised past it: the job was
    never queued, and the pass left with it."""
    out = tmp_path / "out"
    out.mkdir()
    real = Path.exists

    def exists(self, *a, **kw):
        if out in self.parents:
            raise PermissionError(13, "Access is denied", str(self))
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "exists", exists)
    s, _, events = run(Scan(resolution=600), tmp_path,
                       extra=lambda s, _: setattr(s, "out_dir", out))
    assert not kinds(events, "failed")
    (filing,) = [e.filing for e in kinds(events, "filing")]
    assert filing.outcome == session.COPY_FAILED
    assert filing.entry is not None and real(filing.entry)
    ((path, why),) = filing.unwritten
    assert Path(path).parent == out and "Access is denied" in why
    assert len(library.entries(tmp_path)) == 1


def _slide_session(tmp_path, monkeypatch, refuse_infrared=False):
    """A slide roll whose output folder cannot be written, through the real
    session: the frame's picture is the only write that fails, unless the
    library also refuses its infrared pass."""
    from test_colour_pass import SlideScanner

    monkeypatch.setattr("tempfile.gettempdir",
                        lambda: str(made(tmp_path / "temp")))
    out = unwritable(monkeypatch, tmp_path / "out")
    if refuse_infrared:
        real = library.save

        def refuse(image, meta, **kw):
            if "infrared pass" in (kw.get("tags") or ()):
                raise OSError(28, "No space left on device")
            return real(image, meta, **kw)

        monkeypatch.setattr(library, "save", refuse)
    return run(Roll(frames=2, resolution=600, infrared=True, film="positive",
                    name="slides"), tmp_path, scanner=SlideScanner(),
               extra=lambda s, _: setattr(s, "out_dir", out))


def test_a_slide_whose_picture_was_not_written_does_not_stop_the_roll(
        tmp_path, monkeypatch):
    """Both its passes filed, its frameNN.tif written, and only the output
    folder's copy refused: the roll said "picture 1 could not be filed" and
    stopped, where a negative's frame carries on. A copy not written, now."""
    s, _, events = _slide_session(tmp_path, monkeypatch)
    results = {e.result.seq: e.result for e in kinds(events, "result")}
    pictures = [e.filing for e in kinds(events, "filing")
                if e.filing.part == "picture"]
    assert len(pictures) == 2, "the roll stopped after its first frame"
    for picture_ in pictures:
        assert picture_.outcome == session.COPY_FAILED
        assert results[picture_.shown].kind == "frame"
        assert results[picture_.shown].number == picture_.number
        (line,) = picture_.trouble()
        assert "infrared pass is in the library" in line
        # Named as its result is, its part beside it (B3-T1); what still
        # holds it says nothing of the RGB pass, which speaks for itself.
        assert line.startswith(f"frame {picture_.number} \u00b7 600 dpi RGB + "
                               "infrared \u00b7 picture: could not write ")
        safe = line.rsplit("); ", 1)[1]
        assert "colour" not in safe and "RGB" not in safe
    manifest = _manifest_of(tmp_path, "slides")
    assert "stopped" not in manifest
    assert [f["done"] for f in manifest["frames"]] == [True, True]
    assert not any("could not be filed" in e.text for e in kinds(events, "log"))
    # Each pass filed, and the frame's own "filed" is its RGB pass.
    filed = {e.done: Path(e.text) for e in kinds(events, "filed")}
    for seq in (s for s, r in results.items() if r.kind == "frame"):
        assert "colour pass" in library.load(filed[seq])[1]["tags"]
    assert len(library.entries(tmp_path)) == 4


def test_a_slides_picture_with_no_infrared_entry_is_not_filed(
        tmp_path, monkeypatch):
    s, _, events = _slide_session(tmp_path, monkeypatch, refuse_infrared=True)
    filings = [e.filing for e in kinds(events, "filing") if e.filing.number == 1]
    by_part = {f.part: f for f in filings}
    assert by_part["infrared pass"].outcome == session.NOT_FILED
    assert by_part["picture"].outcome == session.NOT_FILED, \
        "nothing in the library to make it again from"
    assert by_part["infrared pass"].shown == by_part["picture"].shown \
        == by_part[""].seq
    assert "could not be filed" in _manifest_of(tmp_path, "slides")["stopped"]


class ClaimingRoll(CorrectingScanner):
    """A negative roll whose frames carry their raw bytes, claimed from debug
    filing as the session files each."""

    def scan_roll(self, **kw):
        from dataclasses import replace

        for frame in FakeScanner.scan_roll(self, **kw):
            if frame.image is not None:
                frame = replace(frame, raw_image=self.last_pixels_raw,
                                capture=self.capture_record())
            yield frame


def test_a_frame_whose_copy_was_not_written_is_done_and_let_go(tmp_path,
                                                              monkeypatch):
    """Its pass is in the library: done in roll.json, waited on by nothing,
    and its spooled copy let go -- answered None, debug filing would file it
    a second time."""
    out = unwritable(monkeypatch, tmp_path / "out")
    scanner = ClaimingRoll(frames=2)
    s, _, events = run(Roll(frames=2, resolution=600, name="copies"),
                       tmp_path, scanner=scanner,
                       extra=lambda s, _: setattr(s, "out_dir", out))
    frames = [e.filing for e in kinds(events, "filing")
              if e.filing.kind == "scan"]
    assert [f.outcome for f in frames] == [session.COPY_FAILED] * 2
    assert len(scanner.claimed) == 2
    assert scanner.answered == [f.entry for f in frames]
    manifest = _manifest_of(tmp_path, "copies")
    assert [(f["done"], f["entry"]) for f in manifest["frames"]] == [
        (True, str(f.entry)) for f in frames]
    assert "stopped" not in manifest
    assert not any(m.pending() for m in s._manifests.values())


def test_the_output_folders_names_are_held_only_while_written(tmp_path,
                                                              monkeypatch):
    """Claimed as the pass is queued and released once its job is over,
    whatever came of it: a job that failed before its copies held its name
    for the session, and the retake took `-2` for nothing."""
    from rps7200 import export

    held = []
    real = session.FrameWriter._write

    def write(self, job):
        held.append((list(job.get("claims") or ()), set(export._claims)))
        return real(self, job)

    monkeypatch.setattr(session.FrameWriter, "_write", write)
    run(Scan(resolution=600, mono=True, mono_channel="I"), tmp_path,
        extra=lambda s, _: setattr(s, "out_dir", made(tmp_path / "out")))
    ((claims, during),) = held
    assert claims, "the copy's name was not claimed"
    assert {export._key(p) for p in claims} <= during
    assert export._claims == {}, "a claim outlived its job"
    assert not (tmp_path / "out").exists() or not any(
        (tmp_path / "out").iterdir())


def test_a_copy_with_no_free_name_costs_the_copy_and_not_the_entry(
        tmp_path, monkeypatch):
    """A folder already holding the name and every `-N` up to the last:
    the name asked for came back, and the copy replaced the file that held
    it. The copy goes, said; the entry is filed."""
    from rps7200 import export

    def full(wanted, **kw):
        raise FileExistsError(f"no free name beside {wanted}")

    monkeypatch.setattr(export, "claim", full)
    _s, _, events = run(Scan(resolution=600), tmp_path,
                        extra=lambda s, _: setattr(s, "out_dir",
                                                   made(tmp_path / "out")))
    (filing,) = [e.filing for e in kinds(events, "filing")]
    assert filing.outcome == session.COPY_FAILED
    assert filing.entry is not None and filing.entry.exists()
    ((_path, why),) = filing.unwritten
    assert why.startswith(f"{tmp_path / 'out'} has no free name left for ")


def test_a_manifest_with_no_free_name_beside_it_is_left_alone(tmp_path,
                                                              monkeypatch):
    from rps7200 import export

    path = tmp_path / "roll.json"
    path.write_text("{not json", encoding="utf-8")

    def full(wanted, **kw):
        raise FileExistsError(f"no free name beside {wanted}")

    monkeypatch.setattr(export, "unclaimed", full)
    with pytest.raises(OSError, match="no free name to set .*roll.json aside"):
        session.set_aside(path)
    assert path.read_text(encoding="utf-8") == "{not json"


def test_a_picture_left_unfiled_on_purpose_is_not_called_a_failure(tmp_path):
    """With no library, every pass logged 'could not be filed: None' -- the
    line that reports real failures, until nobody read it."""
    s = ScanSession(root=None, rolls=str(tmp_path / "rolls"),
                    open_scanner=FakeScanner, verbose=False)
    s.start()
    s.submit(Scan(resolution=600))
    s.shutdown()
    s.join(timeout=10.0)
    events = s.poll()
    said = [e.text for e in events if e.kind == "log"]
    assert not any("could not be filed" in t for t in said), said
    # Nor reported as a failure any other way: written, with no entry.
    filings = [e.filing for e in events if e.kind == "filing"]
    assert [f.outcome for f in filings] == [session.WRITTEN]


def test_a_late_failure_from_a_finished_roll_does_not_stop_the_next(tmp_path,
                                                                   monkeypatch):
    """A roll's last frames file after it has ended. One that failed then
    stopped whatever ran next, and dropped the jobs queued behind it."""
    started, said = threading.Event(), threading.Event()
    scanner = FakeScanner(frames=3)
    real_scan = scanner.scan

    def scan(**kw):
        scans = len([c for c in scanner.calls if c[0] == "scan"])
        if scans >= 1:
            started.set()                      # the second roll is scanning
        if scans >= 2:
            said.wait(timeout=5.0)             # and the failure has landed
        return real_scan(**kw)

    scanner.scan = scan
    real_filed = session.ScanSession._filed

    def filed(self, filing):
        real_filed(self, filing)
        if filing.failed:
            said.set()

    monkeypatch.setattr(session.ScanSession, "_filed", filed)
    real_save = library.save

    def slow_failure(image, meta, **kw):
        if str((kw.get("film") or FilmNotes()).frame).startswith("first-"):
            started.wait(timeout=5.0)
            raise OSError(28, "No space left on device")
        return real_save(image, meta, **kw)

    monkeypatch.setattr(library, "save", slow_failure)
    s = ScanSession(root=str(tmp_path), rolls=str(tmp_path / "rolls"),
                    open_scanner=lambda: scanner, verbose=False)
    s.start()
    s.submit(Roll(frames=1, resolution=600, name="first"))
    s.submit(Roll(frames=3, resolution=600, name="second"))
    s.shutdown()
    s.join(timeout=10.0)
    assert scanner.produced == 4, "the second roll was stopped for the first"


def test_a_frame_written_again_is_never_left_half_written(tmp_path,
                                                         monkeypatch):
    """A frame scanned again replaces its frameNN.tif, and that was written
    in place: a crash or a full disk part-way left a truncated file where a
    good one had been."""
    from rps7200 import export

    good = picture(seed=3)
    path = made(tmp_path / "roll") / "frame01.tif"

    def one(image):
        writer = session.FrameWriter()
        writer.submit(number=1, paths=[path], image=image, meta={}, dpi=600,
                      library=None, film=FilmNotes(), tags=[], prescan=None,
                      inquiry=None, capture={}, seq=1)
        writer.finish()
        return writer

    one(good)
    real = export.tiff.write

    # Below export.write, which is where the writing beside now lives.
    def cut_short(target, image, **kw):
        Path(target).write_bytes(b"II*\x00 half a tiff")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(export.tiff, "write", cut_short)
    writer = one(picture(seed=4))
    monkeypatch.setattr(export.tiff, "write", real)
    assert writer.errors, "the failure was not said"
    assert np.array_equal(tiff.read(path), good), "the good frame was lost"
    assert [p.name for p in path.parent.iterdir()] == ["frame01.tif"], (
        "the half-written file was left behind")


def test_a_failure_that_cannot_be_said_does_not_end_the_writer(tmp_path):
    """Raised from the failure branch, `on_done` ended the writer's thread,
    and every filing after it with it -- the next submit blocking for good."""
    def cannot_say(filing):
        if filing.failed:
            raise RuntimeError("the window has gone")

    writer = session.FrameWriter(on_done=cannot_say)
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"")
    writer.submit(number=1, paths=[blocker / "no.tif"], image=picture(),
                  meta={}, dpi=600, library=None, film=FilmNotes(), tags=[],
                  prescan=None, inquiry=None, capture={}, seq=1)
    writer.submit(number=2, paths=[tmp_path / "yes.tif"], image=picture(),
                  meta={}, dpi=600, library=None, film=FilmNotes(), tags=[],
                  prescan=None, inquiry=None, capture={}, seq=2)
    writer.finish()
    assert (tmp_path / "yes.tif").exists()
    assert writer.problems == [
        "picture 1: not filed, but it could not be reported "
        "(on_done: the window has gone)"]


def test_a_report_that_fails_never_turns_a_filed_frame_into_a_failed_one(
        tmp_path):
    """`on_done` raising on success -- `tools/scan_roll.py` printing to a
    closed stderr -- was taken for the frame's failure: told twice, its
    record not done, and the debug claim answered None, so debug filing
    filed the spooled copy a second time. The entry was on disk throughout."""
    def broken_pipe(filing):
        raise BrokenPipeError("Broken pipe")

    told, receipts = [], []
    writer = session.FrameWriter(on_done=broken_pipe)
    lib = tmp_path / "lib"
    writer.submit(**_job(tmp_path, 1, [tmp_path / "frame01.tif"],
                         library_root=str(lib), seq=1,
                         on_filed=session.answering(receipts.append,
                                                    told.append)))
    writer.submit(**_job(tmp_path, 2, [tmp_path / "frame02.tif"],
                         library_root=str(lib), seq=2))
    writer.finish()
    (filing,) = told
    assert filing.outcome == session.FILED, "told once, and told it was filed"
    assert receipts == [filing.entry], "the spooled copy is let go"
    assert [n for n, _e in writer.done] == [1, 2], "and the next job filed"
    assert len(writer.problems) == 2
    assert writer.problems[0].startswith("picture 1: filed, but it could not "
                                         "be reported (on_done: ")


def test_the_writer_runs_off_the_calling_thread():
    seen = {}

    def note(filing):
        seen["thread"] = threading.current_thread().name

    writer = session.FrameWriter(on_done=note)
    writer.submit(number=1, paths=[], image=picture(), meta={}, dpi=600,
                  library=None, film=FilmNotes(), tags=[], prescan=None,
                  inquiry=None, capture={}, seq=1)
    writer.finish()
    assert seen["thread"] != threading.current_thread().name


def test_raw_bytes_that_describe_another_image_are_not_filed(tmp_path):
    """The one failure the library exists to make impossible.

    `last_raw` holds whatever the previous pass left behind when a pass did not
    keep its own, so `capture_record()` can hand over bytes belonging to a
    different photograph. Three roll prescans were filed that way on the
    hardware -- 300 dpi RGB pixels beside a 600 dpi RGBI scan's bytes -- and the
    entry decodes to the wrong picture with nothing to say so. Better an entry
    with no raw than one with the wrong raw.
    """
    scanner = FakeScanner()
    scanner.capture_record = lambda: {
        "reference": None, "ccd_mask": None,
        "raw": b"\x00" * 64,
        # A 600 dpi RGBI pass, filed beside a 24x36x4 image.
        "raw_layout": {"format": "index", "width": 860, "lines": 573,
                       "channels": 4},
    }
    _, _, events = run(Scan(resolution=600), tmp_path, scanner=scanner)
    entry = tmp_path / library.entries(tmp_path)[0]["id"]
    assert not (entry / "raw.bin.gz").exists()
    assert any("do not describe this image" in e.text for e in kinds(events, "log"))


class _AimedScanner(FakeScanner):
    """A walk frame whose aim failed after it had taken a verification prescan.

    The driver yields the frame with the prescan it arrived with, while the
    scanner's last pass -- and so every byte `capture_record` holds -- is the
    verification's: the same frame, the same resolution, the same shape.
    ``verified=False`` is the ordinary walk, where the two are one pass.
    """

    def __init__(self, verified=True, **kw):
        super().__init__(**kw)
        self.own = picture(channels=3, seed=1)
        self.last_pixels_raw = (picture(channels=3, seed=2) if verified
                                else self.own)

    def capture_record(self):
        return {"reference": None, "ccd_mask": None, "raw": RAW,
                "raw_layout": {"format": "index", "width": 36, "lines": 24,
                               "channels": 3}}

    def scan_roll(self, frames=None, first_index=0, **kw):
        yield RollFrame(
            index=first_index, position=self.pos, image=None, meta={},
            prescan=self.own, registration={},
            error="the second nudge was refused", raw_prescan=self.own,
            prescan_meta={"resolution_dpi": 300, "channel_order": list("RGB")})


def test_a_walk_frame_is_not_filed_with_a_later_passs_bytes(tmp_path):
    """Filed together, the entry held one prescan's pixels and another's
    bytes: `reconstruct` called it a changed decode, and the frame's own
    bytes were gone. The shape guard cannot see it; identity can."""
    _s, _scanner, events = run(Roll(frames=1, dry_run=True, name="aimed"),
                               tmp_path, scanner=_AimedScanner())
    (record,) = library.entries(tmp_path)
    assert library.read_raw(tmp_path / record["id"]) is None, (
        "filed beside the verification prescan's bytes")
    assert any("later pass" in (e.text or "") for e in kinds(events, "log"))


def test_a_walk_frame_that_is_the_last_pass_keeps_its_bytes(tmp_path):
    _s, _scanner, _events = run(Roll(frames=1, dry_run=True, name="plain"),
                                tmp_path, scanner=_AimedScanner(verified=False))
    (record,) = library.entries(tmp_path)
    assert library.read_raw(tmp_path / record["id"]) == RAW


def test_a_pass_that_ended_early_keeps_its_own_bytes(tmp_path):
    """The bytes of a short read are the ones worth keeping most. Judged
    against the line count GET PARAMETERS *declared*, a pass that ended early
    decoded fewer rows and looked like another pass's bytes."""
    scanner = FakeScanner()
    scanner.capture_record = lambda: {
        "reference": None, "ccd_mask": None,
        "raw": b"\x00\x01" * 32,
        # Declared 30 rows; 24 arrived, which is what the image holds.
        "raw_layout": {"format": "index", "width": 36, "lines": 30,
                       "channels": 4, "lines_received": 4 * 24},
    }
    run(Scan(resolution=600), tmp_path, scanner=scanner)
    entry = tmp_path / library.entries(tmp_path)[0]["id"]
    assert (entry / "raw.bin.gz").exists()


def test_a_realigned_7200_dpi_pass_keeps_its_own_bytes(tmp_path):
    """The realignment trims 4 rows the bytes still hold; recorded in the
    pass's meta, it is not mistaken for another pass's bytes."""
    scanner = FakeScanner()
    real = scanner.scan

    def scan(**kw):
        image, meta = real(**kw)
        return image, dict(meta, stagger_realigned=4)

    scanner.scan = scan
    scanner.capture_record = lambda: {
        "reference": None, "ccd_mask": None,
        "raw": b"\x00\x01" * 32,
        "raw_layout": {"format": "index", "width": 36, "lines": 28,
                       "channels": 4, "lines_received": 4 * 28},
    }
    run(Scan(resolution=600), tmp_path, scanner=scanner)
    entry = tmp_path / library.entries(tmp_path)[0]["id"]
    assert (entry / "raw.bin.gz").exists()


def test_matching_raw_bytes_are_still_filed(tmp_path):
    """The guard must not throw away good bytes over a field it cannot check."""
    scanner = FakeScanner()
    scanner.capture_record = lambda: {
        "reference": None, "ccd_mask": None,
        "raw": b"\x00\x01" * 32,
        "raw_layout": {"format": "index", "width": 36},   # says nothing else
    }
    run(Scan(resolution=600), tmp_path, scanner=scanner)
    entry = tmp_path / library.entries(tmp_path)[0]["id"]
    assert (entry / "raw.bin.gz").exists()


# -- moving the film without scanning ---------------------------------------


class FakeTransportScanner(FakeScanner):
    """Records what the transport was asked to do."""

    def __init__(self, position=3, stuck=False):
        super().__init__()
        self.pos = position
        self.stuck = stuck
        self.moves = []

    def advance(self, steps=1, **kw):
        self.moves.append(("advance", steps))
        if self.stuck:
            return None
        self.pos += 1
        return self.pos

    def retreat(self, steps=1, **kw):
        self.moves.append(("retreat", steps))
        if self.stuck:
            return None
        self.pos -= 1
        return self.pos

    def position(self):
        return self.pos

    def nudge(self, millimetres):
        self.moves.append(("nudge", millimetres))
        return {"asked_mm": millimetres, "param": 1}


def test_moving_forward_a_frame_reports_the_new_position(tmp_path):
    scanner = FakeTransportScanner(position=3)
    _, scanner, events = run(Move(frames=1), tmp_path, scanner=scanner)
    assert scanner.moves == [("advance", 1)]
    # The first is the session saying where the film was when it opened, so
    # the window's readout is right before anything has moved.
    assert [e.done for e in kinds(events, "transport")] == [3, 4]


def test_moving_back_a_frame_uses_the_reverse(tmp_path):
    scanner = FakeTransportScanner(position=3)
    _, scanner, events = run(Move(frames=-1), tmp_path, scanner=scanner)
    assert scanner.moves == [("retreat", 1)]
    assert [e.done for e in kinds(events, "transport")] == [3, 2]


def test_a_transport_that_will_not_move_is_reported_not_raised(tmp_path):
    scanner = FakeTransportScanner(stuck=True)
    _, _, events = run(Move(frames=1), tmp_path, scanner=scanner)
    assert any("did not move" in e.text for e in kinds(events, "finished"))


def test_a_nudge_says_the_frame_counter_cannot_confirm_it(tmp_path):
    """The counter does not see a sub-frame move. Reporting a position as if it
    had changed would be the one misleading thing this control could do."""
    scanner = FakeTransportScanner(position=3)
    _, scanner, events = run(Move(millimetres=0.27), tmp_path, scanner=scanner)
    # 0.2719, not the 0.27 asked for: the planner hands the mover the distance
    # the hardware will actually travel rather than the one requested. Same
    # SLIDE command either way -- param_for_mm snaps both to param 1, and the
    # snap is idempotent -- but the number that changes hands is now the one
    # that is true.
    assert scanner.moves == [("nudge", pytest.approx(0.3002, abs=1e-4))]
    assert any("prescan to check it landed" in e.text
               for e in kinds(events, "finished"))
    # Position is still whatever it was; nothing pretends otherwise. (The
    # first report is the one the session makes when it opens.)
    assert [e.done for e in kinds(events, "transport")] == [3, 3]


def test_a_move_never_files_anything(tmp_path):
    run(Move(frames=1), tmp_path, scanner=FakeTransportScanner())
    assert library.entries(tmp_path) == []


@pytest.mark.parametrize("job", [Move(frames=1), Move(frames=-1),
                                 Move(millimetres=0.27)])
def test_a_move_reports_a_counter_no_strip_has_as_unknown(tmp_path, job):
    """As `_report_position` does. A stale 72 went to the window as the film
    being on frame 73, which the Roll dialog then forecast a 72-frame wind
    from."""
    scanner = FakeTransportScanner(position=72)
    _, _, events = run(job, tmp_path, scanner=scanner)
    reported = [e.done for e in kinds(events, "transport")]
    assert reported and all(done == -1 for done in reported), reported
    # And the job's own words, which the window shows beside that readout:
    # "on frame 74 of the strip -- done" next to "film on frame ?".
    finished = kinds(events, "finished")[0].text
    assert "of the strip" not in finished, finished
    for impossible in (72, 73, 74):
        assert f"frame {impossible}" not in finished, finished
    if job.frames:
        assert "no strip has" in finished, finished


def test_a_nudge_does_not_forget_which_frame_the_film_is_on(tmp_path):
    """The READ_STATE straight after a SLIDE comes back empty, and the nudge
    asked it at once -- so every nudge reported "unknown", and the window,
    which now believes that, forgot the frame and lost the Roll dialog's
    forecast. A nudge cannot change the counter; hearing nothing says
    nothing, so nothing is reported."""
    class EmptyAfterSlide(FakeTransportScanner):
        """READ_STATE empty once after any move, modelled on what
        `DirectScanner._whole_frames` says of a whole-frame one."""

        slid = False

        def nudge(self, millimetres):
            self.slid = True
            return super().nudge(millimetres)

        def position(self):
            if self.slid:
                self.slid = False
                return None
            return super().position()

    from rps7200.direct import DirectScanner

    three_units = 3 * DirectScanner.STEP_MM      # one command, param 1
    _, scanner, events = run(Move(millimetres=three_units), tmp_path,
                             scanner=EmptyAfterSlide(position=10))
    assert [way for way, _ in scanner.moves] == ["nudge"]
    assert [e.done for e in kinds(events, "transport")] == [10]


def test_moving_several_frames_steps_one_at_a_time(tmp_path):
    """So the position is confirmed at every frame, and a strip that runs out
    part-way stops there rather than being asked for the rest."""
    scanner = FakeTransportScanner(position=0)
    _, scanner, events = run(Move(frames=3), tmp_path, scanner=scanner)
    assert scanner.moves == [("advance", 1)] * 3
    assert [e.done for e in kinds(events, "transport")] == [0, 1, 2, 3]


# -- rotation ---------------------------------------------------------------


def test_a_turn_reaches_the_delivered_file_but_not_the_entry(tmp_path):
    """The entry's pixels have to keep matching the raw bytes filed beside
    them, or `library.reconstruct` is right to call it a changed decode. The
    file the operator actually wanted is a different question."""
    out = tmp_path / "out"
    out.mkdir()
    scanner = FakeScanner()
    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(tmp_path / "r"),
                    out_dir=str(out), open_scanner=lambda: scanner, verbose=False)
    s.rotation = 90
    s.start()
    s.submit(Scan(resolution=600, infrared=True))
    s.shutdown()
    s.join(timeout=15)

    from rps7200 import tiff
    delivered = sorted(out.rglob("*.tif"))
    assert len(delivered) == 1
    turned = tiff.read(str(delivered[0]))
    stored, record = library.load(tmp_path / "lib" / library.entries(
        tmp_path / "lib")[0]["id"])
    assert turned.shape[:2] == stored.shape[:2][::-1], "the file is turned"
    assert stored.shape[:2] == (24, 36), "the entry is not"
    assert record["scan"]["rotation"] == 90, "but it records what was chosen"


def test_no_turn_leaves_both_alone(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(tmp_path / "r"),
                    out_dir=str(out), open_scanner=FakeScanner, verbose=False)
    s.start()
    s.submit(Scan(resolution=600, infrared=True))
    s.shutdown()
    s.join(timeout=15)
    from rps7200 import tiff
    assert tiff.read(str(sorted(out.rglob("*.tif"))[0])).shape[:2] == (24, 36)


def test_one_frame_of_a_roll_can_be_turned_without_turning_the_rest(tmp_path):
    """A strip is not one orientation.

    A portrait among landscapes is the ordinary case, and one number for the
    whole roll can only ever get one of them right -- so the contact sheet's
    turn is per frame, and this is the check that it stays per frame all the
    way to the files.
    """
    out = tmp_path / "out"
    out.mkdir()
    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(tmp_path / "r"),
                    out_dir=str(out), open_scanner=FakeScanner, verbose=False)
    s.start()
    s.submit(Roll(frames=2, resolution=600, infrared=False, name="mixed",
                  approved=(Approved(number=1),
                            Approved(number=2, rotation=90))))
    s.shutdown()
    s.join(timeout=20)

    from rps7200 import tiff
    first = tiff.read(str(tmp_path / "r" / "mixed" / "frame01.tif"))
    second = tiff.read(str(tmp_path / "r" / "mixed" / "frame02.tif"))
    assert first.shape[:2] == (24, 36), "frame 1 was not turned"
    assert second.shape[:2] == (36, 24), "frame 2 was"


def test_each_entry_records_the_turn_its_own_file_got(tmp_path):
    """The library keeps raw pixels either way; the record says what was
    delivered. One session-wide number there would be a lie about one of the
    two frames as soon as they disagree."""
    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(tmp_path / "r"),
                    open_scanner=FakeScanner, verbose=False)
    s.start()
    s.submit(Roll(frames=2, resolution=600, infrared=False, name="mixed",
                  approved=(Approved(number=1),
                            Approved(number=2, rotation=270))))
    s.shutdown()
    s.join(timeout=20)

    turns = {}
    for meta in library.entries(tmp_path / "lib"):
        stored, record = library.load(tmp_path / "lib" / meta["id"])
        assert stored.shape[:2] == (24, 36), "no entry is ever turned"
        turns[record["film"]["frame"]] = record["scan"]["rotation"]
    assert turns == {"mixed-01": 0, "mixed-02": 270}, turns


def test_a_frames_turn_does_not_reach_the_prescan_beside_it(tmp_path):
    """`read_survey` un-rotates `prescanNN.tif` by the manifest's single
    `rotation`. A per-frame turn applied here would make that arithmetic wrong
    -- the reference would come back at an orientation the film was never at,
    and it would no longer correlate against a fresh pass of the same frame."""
    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(tmp_path / "r"),
                    open_scanner=FakeScanner, verbose=False)
    s.rotation = 180
    s.flip = False
    s._frame_rotation = {2: 90}
    s._frame_flip = {2: True}
    assert s._orientation_for(2, "frame") == (90, True), "the frame gets its own"
    assert s._orientation_for(2, "prescan") == (180, False), "its prescan does not"
    assert s._orientation_for(1, "frame") == (180, False), "and so does frame 1"


def test_a_rolls_orientations_do_not_outlive_it(tmp_path):
    """A single scan afterwards is not frame 3 of anything, and inheriting
    frame 3's turn would be a silent wrong answer."""
    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(tmp_path / "r"),
                    out_dir=str(made(tmp_path / "out")),
                    open_scanner=FakeScanner,
                    verbose=False)
    s.start()
    s.submit(Roll(frames=2, resolution=600, infrared=False, name="mixed",
                  approved=(Approved(number=2, rotation=90),)))
    s.submit(Scan(resolution=600, infrared=False))
    s.shutdown()
    s.join(timeout=20)
    assert s._frame_rotation == {}
    from rps7200 import tiff
    loose = [f for f in (tmp_path / "out").glob("*.tif") if "frame" not in f.name]
    assert len(loose) == 1
    assert tiff.read(str(loose[0])).shape[:2] == (24, 36)


# -- what the files are called ----------------------------------------------


def test_a_roll_names_its_files_by_roll_and_frame(tmp_path):
    """NegPy reads these next. What this replaced led with a timestamp and
    ended with a sequence number, so it sorted by when it was scanned and said
    nothing about what it was -- fine for one pass, useless for thirty-eight."""
    out = tmp_path / "out"
    out.mkdir()
    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(tmp_path / "r"),
                    out_dir=str(out), open_scanner=FakeScanner, verbose=False)
    s.start()
    s.submit(Roll(frames=2, resolution=3600, infrared=True,
                  name="2026-09-09-gold200"))
    s.shutdown()
    s.join(timeout=20)
    names = sorted(f.name for f in out.glob("*.tif"))
    assert names == ["2026-09-09-gold200_frame01_3600dpi_ir.tif",
                     "2026-09-09-gold200_frame02_3600dpi_ir.tif"], names


def test_a_scan_outside_a_roll_still_gets_a_name(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(tmp_path / "r"),
                    out_dir=str(out), open_scanner=FakeScanner, verbose=False)
    s.start()
    s.submit(Scan(resolution=600, infrared=False))
    s.shutdown()
    s.join(timeout=20)
    written = list(out.glob("*.tif"))
    assert len(written) == 1
    assert written[0].name.endswith("_600dpi.tif")
    assert "_ir" not in written[0].name, "three channels is not infrared"


def test_a_rescanned_frame_does_not_overwrite_the_first_attempt(tmp_path):
    """The better of the two is not always the second."""
    out = tmp_path / "out"
    out.mkdir()
    for _ in range(2):
        s = ScanSession(root=str(tmp_path / "lib"), rolls=str(tmp_path / "r"),
                        out_dir=str(out), open_scanner=FakeScanner, verbose=False)
        s.start()
        s.submit(Roll(frames=1, resolution=600, infrared=False, name="strip"))
        s.shutdown()
        s.join(timeout=20)
    names = {f.name for f in out.glob("*.tif")}
    assert names == {"strip_frame01_600dpi.tif", "strip_frame01_600dpi-2.tif"}, names


def test_a_roll_name_that_is_not_a_filename_is_made_into_one(tmp_path):
    from rps7200.session import _safe
    assert _safe("2026-09-09 gold/200") == "2026-09-09-gold-200"
    assert _safe("  ../../etc/passwd ") == "etc-passwd"
    assert _safe("") == "roll"
    assert _safe("///") == "roll"


def test_a_name_over_the_cap_keeps_its_start_and_a_hash_of_the_whole():
    """Uncapped, a long roll name failed as a WinError after the film had
    moved. Cut plainly, two names alike for 64 characters were one folder,
    and the second roll was taken for a resume of the first (Stefan,
    2026-10-09: 56 characters, a dash and seven hex digits of the SHA-256
    of the whole name, case-folded)."""
    from rps7200.session import NAME_CAP, NAME_CAP_BYTES, _safe

    typed = "Holiday in the mountains with the whole family " * 3
    # A fixed string, not two calls in one process: the same name has to
    # find the same folder in next year's session.
    assert _safe(typed) == ("Holiday-in-the-mountains-with-the-whole-family-"
                            "Holiday-i-dd7fc4f")
    assert len(_safe(typed)) == NAME_CAP
    # Bytes as well as characters: 64 characters past the basic plane are
    # 256 bytes, past every filesystem's 255 for one name.
    wide = _safe("\U00020000" * 64)
    assert len(wide.encode("utf-8")) <= NAME_CAP_BYTES
    assert wide.startswith("\U00020000") and wide[-8] == "-"
    # Alike for their first 56 characters, and still two rolls.
    a, b = "x" * 56 + "alpha" * 5, "x" * 56 + "beta" * 5
    assert _safe(a) != _safe(b) and _safe(a)[:56] == _safe(b)[:56]
    # Typed again in other case, the same hash: one folder on a disk that
    # does not tell the two apart.
    assert _safe(a.upper()).casefold() == _safe(a).casefold()
    assert _safe(a.upper())[-7:] == _safe(a)[-7:]
    # A name within the cap, or a device name, is as it was.
    assert _safe("Gold 200") == "Gold-200"
    assert _safe("x" * 64) == "x" * 64
    assert _safe("con") == "con-roll"


def test_a_roll_name_too_long_to_look_up_finds_its_capped_folder(
        tmp_path, monkeypatch):
    """`roll_dir` looks for a folder named as typed, and a name that long
    raises on POSIX (ENAMETOOLONG) where Windows says no: a folder that
    cannot be looked at is no folder, and the name is made safe and capped."""
    typed = "a long roll name " * 18
    assert len(typed) > 300
    real = Path.is_dir

    def too_long(self):
        if len(self.name) > 255:
            raise OSError(36, "File name too long")
        return real(self)

    monkeypatch.setattr(Path, "is_dir", too_long)
    folder = session.roll_dir(tmp_path, typed)
    assert folder == tmp_path / session._safe(typed)
    folder.mkdir()
    assert session.roll_dir(tmp_path, typed) == folder, "a resume found another"

    def denied(self):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(Path, "is_dir", denied)
    assert session.roll_dir(tmp_path, "Gold 200") == tmp_path / "Gold-200"


def test_a_roll_folder_over_the_cap_is_found_by_its_own_name(tmp_path):
    """The cap is for making a folder, not finding one. Duplicate's `-2` and
    a restore beside a roll of the same name put two characters on a name
    at the cap, and `roll_dir` looked such a folder up as the name cleaned
    and capped -- another folder, with another hash: finishing the copy
    from the window scanned every frame again into that."""
    rolls = made(tmp_path / "rolls")
    # A typed name over the cap, filed under its capped folder; and one of
    # 63 characters, filed as typed, whose `-2` is past the cap.
    for typed in ("Kodak Portra 400, the summer holiday at the coast " * 2,
                  "y" * 63):
        folder = session.roll_dir(rolls, typed)
        assert len(folder.name) <= session.NAME_CAP
        copy = made(folder.with_name(f"{folder.name}-2"))
        assert len(copy.name) > session.NAME_CAP
        assert session._safe(copy.name) != copy.name, "a cut name, unfound"
        assert session.roll_dir(rolls, copy.name) == copy
    # And one restored beside a newer roll of its name.
    folder = made(session.roll_dir(rolls, "z" * 70))
    (folder / "roll.json").write_text('{"roll": "%s"}' % folder.name,
                                      encoding="utf-8")
    library.trash(rolls, [folder], by="a test", kind="rolls")
    made(folder)
    ((_batch, _name, back),) = library.restore(rolls, folder.name).restored
    assert len(back) > session.NAME_CAP
    assert session.roll_dir(rolls, back) == rolls / back
    # A name over the cap with no folder of its own is still the capped one.
    assert session.roll_dir(rolls, "q" * 70) == rolls / session._safe("q" * 70)


def test_a_roll_named_after_a_dos_device_can_still_be_created(tmp_path):
    """`con`, `nul`, `com1` and the rest are device names on Windows.

    Not reserved words -- devices -- so `mkdir("con")` fails there with
    NotADirectoryError, which reads like a bug in this driver rather than a
    name the operator chose. Verified against the filesystem below rather than
    asserted, so the rule is checked where it applies and is a no-op elsewhere.
    """
    from rps7200.session import _safe

    assert _safe("con") == "con-roll"
    assert _safe("COM1") == "COM1-roll"
    assert _safe("nul") == "nul-roll"
    # Only the whole name is a device; a name that merely starts with one is
    # fine and must not be mangled.
    assert _safe("con-1") == "con-1"
    assert _safe("console") == "console"

    for name in ("con", "prn", "aux", "nul", "com1", "lpt1"):
        (tmp_path / _safe(name)).mkdir()


def test_unnamed_walks_never_share_a_folder(tmp_path):
    """The date was every unnamed roll's folder, so a second strip walked the
    same day replaced the first one's survey.json and prescans."""
    import re

    first, _s, _e = run(Roll(frames=2, dry_run=True), tmp_path)
    second, _s, _e = run(Roll(frames=3, dry_run=True), tmp_path)
    one, two = first.last_roll_dir, second.last_roll_dir
    assert one != two and one.parent == two.parent == tmp_path / "rolls"
    assert re.fullmatch(r"\d{4}-\d\d-\d\d-\d{6}(-\d+)?", one.name), one.name
    walked = json.loads((one / "survey.json").read_text(encoding="utf-8"))
    assert [f["number"] for f in walked["frames"]] == [1, 2], "overwritten"
    assert walked["roll"] == one.name


def test_a_roll_name_cannot_leave_the_rolls_folder(tmp_path):
    s, _scanner, _events = run(Roll(frames=1, resolution=600,
                                    name="../../Gold 200"), tmp_path)
    assert s.last_roll_dir == tmp_path / "rolls" / "Gold-200"
    assert [e["film"]["frame"] for e in frame_entries(tmp_path)] == [
        "Gold-200-01"], "labelled by the folder it is in"


def test_one_name_finds_one_folder():
    """The session, the window's approved.json, a reopened roll and the roll
    tool all ask this, and used to derive it three ways."""
    from pathlib import Path

    from rps7200.session import roll_dir

    rolls = Path("rolls")
    assert roll_dir(rolls, "strip") == rolls / "strip"
    assert roll_dir(rolls, " a/b\\c ") == rolls / "a-b-c"
    assert roll_dir(rolls, "con") == rolls / "con-roll"
    for nothing in ("", "   ", "..", "///"):
        made = roll_dir(rolls, nothing)
        assert made.parent == rolls and made.name[:2] == "20", nothing


def test_a_folder_named_before_names_were_cleaned_is_still_found(tmp_path):
    """`rolls/Gold 200` from before: the name that made it finds it, and a
    roll added to it goes on labelling its frames the way the walk did."""
    from rps7200.session import roll_dir

    folder = tmp_path / "rolls" / "Gold 200"
    folder.mkdir(parents=True)
    (folder / "survey.json").write_text(json.dumps(
        {"roll": "Gold 200", "numbering": "strip", "frames": []}),
        encoding="utf-8")
    assert roll_dir(tmp_path / "rolls", "Gold 200") == folder
    s, _scanner, _events = run(Roll(frames=1, resolution=600,
                                    name="Gold 200"), tmp_path)
    assert s.last_roll_dir == folder
    assert [e["film"]["frame"] for e in frame_entries(tmp_path)] == [
        "Gold 200-01"]


def test_a_prescan_records_the_film_it_was_looking_at(tmp_path):
    """A framing pass does not expose for the film -- it runs at the device's
    own settings -- but the entry should still say what was in the transport.

    It used to file `film: None`, which made a prescan of black and white
    indistinguishable from a prescan of anything else.
    """
    run(Prescan(film="bw"), tmp_path)
    entries = library.entries(tmp_path)
    assert len(entries) == 1
    record = json.loads(
        (tmp_path / str(entries[0]["id"]) / "scan.json").read_text(encoding="utf-8")
    )
    assert record["scan"]["film"] == "bw"


# --- the sub-frame move planner ---------------------------------------------
#
# The transport cannot travel an arbitrary distance: one SLIDE command delivers
# STEP_MM x param + OVERHEAD_MM for an integer param in 1..8. Three callers
# need that truth -- the mover, the contact sheet's adjuster, and any
# correction loop -- so it lives in one function and these tests pin it.


class _Winding:
    """A film that can be wound back and can stop part-way."""

    def __init__(self, at, sticks_at=None, swallows=0):
        self.at, self.sticks_at, self.swallows = at, sticks_at, swallows
        self.scans = 0

    def position(self):
        return self.at

    def retreat(self, **kw):
        if self.swallows:
            self.swallows -= 1
            return None
        if self.sticks_at is not None and self.at <= self.sticks_at:
            return None
        self.at -= 1
        return self.at


def test_the_shared_rewind_checks_each_frame_landed():
    from rps7200.session import rewind

    film = _Winding(14)
    assert rewind(film, 14) == 0
    assert film.at == 0


def test_a_rewind_that_stops_short_says_so_rather_than_reporting_success():
    """The caller must not go on: everything after assumes the film arrived."""
    from rps7200.session import rewind

    assert rewind(_Winding(14, sticks_at=11), 14) is None


def test_backlash_at_the_start_is_not_a_failed_rewind():
    """A roll leaves the transport loaded forward, so the first backward
    command is a direction change and two or three are swallowed. Measured
    2026-09-21: the same 14-frame rewind ran first time after one roll and had
    its first command swallowed after the next, the device healthy either
    way."""
    from rps7200.session import BACKLASH_COMMANDS, rewind

    film = _Winding(14, swallows=BACKLASH_COMMANDS)
    assert rewind(film, 14) == 0


def test_a_no_op_after_the_film_has_moved_is_the_end_of_the_strip():
    """Tolerated at the start, fatal once it is running -- otherwise a strip
    that ends early reads as backlash forever."""
    from rps7200.session import rewind

    assert rewind(_Winding(14, sticks_at=9, swallows=1), 14) is None


def test_the_rewind_can_say_what_it_is_doing():
    from rps7200.session import rewind

    said = []
    rewind(_Winding(3), 3, say=said.append)
    assert any("rewinding 3" in line for line in said)
    assert any("1/3" in line for line in said)
    # Without ``said``, as it always said -- `tools/scan_roll.py` prints
    # these: places, 1-based.
    assert said == ["rewinding 3 frame(s) from frame 4", "  1/3: frame 4 -> 3",
                    "  2/3: frame 3 -> 2", "  3/3: frame 2 -> 1"]


# --- a roll starts on its first frame, wherever the film is -----------------
#
# Reported 2026-09-23: with the transport on frame 10, a roll commissioned
# from 1 to 15 started where the film was and numbered it 1. `start_at` was a
# count of advances from wherever the film happened to be; the transport's
# own counter, which knew, was never asked. These drive the session with
# `StripScanner`, whose roll starts where the film is exactly as the driver's
# does -- the doubles that put frame i at position i whatever the transport
# said are how this passed every test before.


def walk(job, tmp_path, scanner, monkeypatch=None):
    """Run `job` against `scanner`; return its events and its manifest's
    (number, transport_position) pairs."""
    if monkeypatch is not None:
        monkeypatch.setattr(session, "POSITION_POLL_S", 0.0, raising=False)
    _, _, events = run(job, tmp_path, scanner=scanner)
    folder = tmp_path / "rolls" / (job.name or "strip")
    manifest = folder / ("survey.json" if job.dry_run else "roll.json")
    frames = []
    if manifest.exists():
        frames = [(f["number"], f["transport_position"]) for f in json.loads(
            manifest.read_text(encoding="utf-8"))["frames"]]
    return events, frames


def test_a_roll_from_frame_one_winds_back_to_it_first(tmp_path):
    """The reported case, at the session: the film on frame 10, a walk of 1
    to 3. It used to record frames 1, 2, 3 at transport positions 9, 10, 11."""
    from conftest import StripScanner

    scanner = StripScanner(at=9)
    _, frames = walk(Roll(frames=3, start_at=1, dry_run=True, name="strip"),
                     tmp_path, scanner)
    assert frames == [(1, 0), (2, 1), (3, 2)]
    assert scanner.moves[:9] == [("retreat", 1)] * 9
    assert scanner.rolls[0]["moves_before"] == 9, "wound back first"
    assert scanner.rolls[0]["first_index"] == 0
    # By name, for the role of every pass only debug filing keeps.
    assert scanner.rolls[0]["roll"] == "strip"


@pytest.mark.parametrize("at", [0, 2])
def test_a_roll_behind_its_first_frame_only_advances(tmp_path, at):
    """Frame 4 is three frames on from frame 1 whichever frame the film is on,
    and the film gets there before the roll begins -- so a refusal would come
    before anything was created, not part-way into a roll."""
    from conftest import StripScanner

    scanner = StripScanner(at=at)
    _, frames = walk(Roll(frames=3, start_at=4, dry_run=True, name="strip"),
                     tmp_path, scanner)
    assert frames == [(4, 3), (5, 4), (6, 5)]
    assert ("retreat", 1) not in scanner.moves
    assert scanner.rolls[0]["moves_before"] == 3 - at
    assert scanner.rolls[0]["skip"] == 0


def test_a_walk_can_add_to_the_walk_before_it(tmp_path):
    """Frames 1 to 3 walked, then 4 to the end of the strip, kept: one survey
    of every frame. `survey.json` was written afresh by every walk, so the
    second replaced the first and the sheet could never show 1 to 3 again --
    their prescans still on disk, their records gone."""
    from conftest import StripScanner

    walk(Roll(frames=3, start_at=1, dry_run=True, name="strip"), tmp_path,
         StripScanner(at=0, last=5))
    _, frames = walk(Roll(frames=None, start_at=4, dry_run=True, name="strip",
                          extend_walk=True), tmp_path, StripScanner(at=3, last=5))
    assert frames == [(n, n - 1) for n in range(1, 7)]
    folder = tmp_path / "rolls" / "strip"
    manifest = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    # The range the pair covers, so a reopened roll puts it back in the boxes.
    assert manifest["start_at"] == manifest["settings"]["start_at"] == 1
    assert manifest["settings"]["frames"] is None, "the second went to the end"
    assert sorted(p.name for p in folder.glob("prescan*.tif")) == [
        f"prescan{n:02d}.tif" for n in range(1, 7)]


def test_a_walk_added_to_an_old_one_never_writes_over_its_pictures(tmp_path):
    """A walk from before frame numbers were places on the strip named its
    files by its own count, and renumbering keeps those names: the walk that
    started on strip frame 5 left frame 5's record naming prescan01.tif. A
    walk of frames 1 to 3 added to it wrote prescan01..03 over them, and the
    sheet showed frames 1 to 3 as 5 to 7 -- often the only copy of those."""
    from conftest import StripScanner

    folder = tmp_path / "rolls" / "strip"
    folder.mkdir(parents=True)
    for n in (1, 2, 3):
        tiff.write(str(folder / f"prescan{n:02d}.tif"),
                   np.full((4, 6, 3), 10 + n, np.uint8))
    (folder / "survey.json").write_text(json.dumps({
        "roll": "strip", "dry_run": True,
        "frames": [{"number": n, "transport_position": n + 3,
                    "prescan": f"prescan{n:02d}.tif", "done": False}
                   for n in (1, 2, 3)],
    }), encoding="utf-8")
    walk(Roll(frames=3, start_at=1, dry_run=True, name="strip",
              extend_walk=True), tmp_path, StripScanner(at=0))
    manifest = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    by_number = {f["number"]: f for f in manifest["frames"]}
    assert sorted(by_number) == [1, 2, 3, 5, 6, 7]
    for number, level in ((5, 11), (6, 12), (7, 13)):
        kept = tiff.read(folder / by_number[number]["prescan"])
        assert int(kept.max()) == level, (
            f"frame {number}'s picture was written over by the new walk")
    names = [by_number[n]["prescan"] for n in (1, 2, 3, 5, 6, 7)]
    assert len(set(names)) == 6, names


def test_a_frame_walked_again_is_recorded_once_and_a_fresh_walk_replaces(
        tmp_path):
    from conftest import StripScanner

    walk(Roll(frames=3, start_at=1, dry_run=True, name="strip"), tmp_path,
         StripScanner(at=0))
    _, kept = walk(Roll(frames=2, start_at=3, dry_run=True, name="strip",
                        extend_walk=True), tmp_path, StripScanner(at=2))
    assert [n for n, _ in kept] == [1, 2, 3, 4]
    # Without the flag a walk is what it always was: the file written afresh.
    _, fresh = walk(Roll(frames=2, start_at=1, dry_run=True, name="strip"),
                    tmp_path, StripScanner(at=0))
    assert [n for n, _ in fresh] == [1, 2]


def test_a_walk_records_how_each_prescan_was_turned(tmp_path):
    """Two walks merged into one survey need not have been made the same way
    up -- "rotate all" in the sheet moves the session's turn between them -- so
    the manifest's one pair cannot un-turn all of them. Each record says."""
    def turned(s, _scanner):
        s.rotation, s.flip = 90, True

    run(Roll(frames=2, dry_run=True, name="turned"), tmp_path, extra=turned)
    manifest = json.loads((tmp_path / "rolls" / "turned" / "survey.json")
                          .read_text(encoding="utf-8"))
    assert [(f["prescan_rotation"], f["prescan_flipped"])
            for f in manifest["frames"]] == [(90, True), (90, True)]


def _turned_after_the_first(tmp_path, job):
    """Run `job`, turning the session a quarter while its second frame is
    taken -- what the window does when a prescan is turned as it lands."""
    held = {}

    def turn(i):
        if i == 1:
            held["session"].rotation = 90

    def keep(s, _scanner):
        held["session"] = s

    run(job, tmp_path, scanner=FakeScanner(frames=3, on_yield=turn),
        extra=keep)
    folder = tmp_path / "rolls" / job.name
    manifest = folder / ("survey.json" if job.dry_run else "roll.json")
    return folder, json.loads(manifest.read_text(encoding="utf-8"))


def test_a_turn_made_while_a_walk_runs_is_recorded_with_what_it_reached(
        tmp_path, monkeypatch):
    """The walk's one `rotation` is the session's at the start, and a turn
    made in the window while it runs reaches every prescan written after it.
    Reopened, those were un-turned by the start's pair.

    The turn lands where the window's can: on the Tk thread, after frame 2's
    prescan was handed to the writer and before its record is written. The
    record asked the session again there, and wrote down a turn the file
    never had."""
    real_file = ScanSession._file

    def turned_once_filed(self, seq, number, *a, **kw):
        arranged = real_file(self, seq, number, *a, **kw)
        if number == 2:
            self.rotation = 90
        return arranged

    monkeypatch.setattr(ScanSession, "_file", turned_once_filed)
    run(Roll(frames=3, dry_run=True, name="walk"), tmp_path)
    folder = tmp_path / "rolls" / "walk"
    manifest = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    assert manifest["rotation"] == 0
    assert [(f["number"], f["prescan_rotation"]) for f in manifest["frames"]] \
        == [(1, 0), (2, 0), (3, 90)]
    assert tiff.read(str(folder / "prescan02.tif")).shape[:2] == (24, 36)
    assert tiff.read(str(folder / "prescan03.tif")).shape[:2] == (36, 24)


def test_a_turn_made_while_a_roll_runs_is_recorded_with_its_frames(tmp_path):
    folder, manifest = _turned_after_the_first(
        tmp_path, Roll(frames=3, resolution=600, name="roll"))
    assert [(f["number"], f["rotation"], f["flipped"])
            for f in manifest["frames"]] == [(1, 0, False), (2, 90, False),
                                             (3, 90, False)]
    assert tiff.read(str(folder / "frame02.tif")).shape[:2] == (36, 24)


def test_an_older_walk_added_to_keeps_the_turn_its_prescans_were_written_at(
        tmp_path):
    """A walk written before records carried their own turn relied on the
    manifest's one pair, and the walk that adds to it writes its own pair
    there. The old frames are given theirs first, or they reopen turned by a
    turn they were never written at."""
    from conftest import StripScanner

    def turned(s, _scanner):
        s.rotation = 90

    run(Roll(frames=2, dry_run=True, name="old"), tmp_path,
        scanner=StripScanner(at=0), extra=turned)
    path = tmp_path / "rolls" / "old" / "survey.json"
    older = json.loads(path.read_text(encoding="utf-8"))
    for record in older["frames"]:
        del record["prescan_rotation"], record["prescan_flipped"]
    path.write_text(json.dumps(older), encoding="utf-8")

    run(Roll(frames=1, start_at=3, dry_run=True, name="old", extend_walk=True),
        tmp_path, scanner=StripScanner(at=2))
    merged = json.loads(path.read_text(encoding="utf-8"))
    assert merged["rotation"] == 0, "the new walk's pair"
    assert [(f["number"], f["prescan_rotation"]) for f in merged["frames"]] == [
        (1, 90), (2, 90), (3, 0)]


@pytest.mark.parametrize("earlier, start_at, frames, span", [
    # 1 to 10, then 11 to 12: 1 to 12
    ({"settings": {"start_at": 1, "frames": 10}}, 11, 2, (1, 12)),
    # then 11 to the end: 1 to the end
    ({"settings": {"start_at": 1, "frames": 10}}, 11, None, (1, None)),
    # an earlier walk to the end stays one
    ({"settings": {"start_at": 1, "frames": None}}, 3, 2, (1, None)),
    # 5 to 7, then 1 to 2: 1 to 7
    ({"settings": {"start_at": 5, "frames": 3}}, 1, 2, (1, 7)),
    # a file that recorded no range: its frames say it
    ({"frames": [{"number": 3}, {"number": 5}]}, 1, 2, (1, 5)),
    # nothing at all: this walk's own
    ({}, 4, 2, (4, 2)),
])
def test_two_walks_cover_the_range_of_both(earlier, start_at, frames, span):
    assert session.walk_span(earlier, start_at, frames) == span


def test_a_rewind_that_stops_short_files_nothing(tmp_path):
    """Nothing scanned, nothing filed, no folder: every frame after a short
    rewind would be numbered as a frame it is not."""
    from conftest import StripScanner

    scanner = StripScanner(at=9, sticks_at=5)
    events, frames = walk(Roll(frames=3, start_at=1, dry_run=True,
                               name="strip"), tmp_path, scanner)
    assert frames == []
    assert scanner.rolls == [], "the roll never started"
    assert library.entries(tmp_path) == []
    assert not (tmp_path / "rolls" / "strip").exists()
    failed = kinds(events, "failed")
    assert failed and "nothing was scanned" in failed[0].text


@pytest.mark.parametrize("job, said", [
    (Roll(frames=3, resolution=7200, name="strip"), "cannot be corrected"),
    (Roll(frames=3, resolution=600, infrared=True, film="bw", name="strip"),
     "infrared is blind"),
    (Roll(frames=3, resolution=600, meter="sometimes", name="strip"),
     "unknown meter mode"),
])
def test_a_roll_the_driver_will_refuse_is_refused_before_the_film_moves(
        tmp_path, job, said):
    """Refused by the driver only at the roll's first frame -- after the seek
    wound the film and the folder was made -- and at 7200 dpi frame by frame,
    each prescanned and metered first: three frames of transport, then a roll
    reported as finished."""
    from conftest import StripScanner

    scanner = StripScanner(at=9)
    events, frames = walk(job, tmp_path, scanner)
    assert scanner.moves == [], "the film was wound for a roll that cannot run"
    assert scanner.rolls == []
    assert not (tmp_path / "rolls" / "strip").exists()
    failed = kinds(events, "failed")
    assert failed and said in failed[0].text


def test_a_walk_at_7200_dpi_is_not_refused_for_its_scans(tmp_path):
    """A walk takes prescans only; the resolution it carries is not scanned."""
    from conftest import StripScanner

    events, frames = walk(Roll(frames=1, resolution=7200, dry_run=True,
                               name="strip"), tmp_path, StripScanner(at=0))
    assert not kinds(events, "failed")


def test_a_roll_refuses_when_the_transport_will_not_say(tmp_path, monkeypatch):
    """Where the film is unknown, frame 1 is a guess -- which is today's bug
    with a different number. Refused, through the failed-job path."""
    from conftest import StripScanner

    scanner = StripScanner(at=4, silent=True)
    events, frames = walk(Roll(frames=3, start_at=1, dry_run=True,
                               name="strip"), tmp_path, scanner, monkeypatch)
    assert frames == [] and scanner.rolls == [] and scanner.moves == []
    assert library.entries(tmp_path) == []
    assert not (tmp_path / "rolls" / "strip").exists()
    failed = kinds(events, "failed")
    assert failed and "would not say" in failed[0].text
    # And the likeliest reason is said: the scanner is silent while its lamp
    # warms, and this sentence used to send the operator to check the strip.
    assert "lamp" in failed[0].text


def test_seek_waits_for_the_lamp_with_status_queries_only(monkeypatch):
    """If the scanner answers NOT READY to everything for its first ~80 s,
    READ_STATE included -- `DirectScanner.wait_warm`'s docstring says so, and
    this double is built on it; nothing here measured it -- a roll started
    while the lamp warmed would ask the counter, hear nothing and refuse. It
    waits for the lamp now, and what it sends while waiting is TEST UNIT
    READY and REQUEST SENSE: nothing that moves the film and nothing that
    scans."""
    from conftest import NoWaiting, ScannerOnStrip
    from rps7200 import direct
    from rps7200.protocol import SCSI_REQUEST_SENSE, SCSI_TEST_UNIT_READY

    clock = NoWaiting()
    monkeypatch.setattr(direct, "time", clock)
    monkeypatch.setattr(session, "POSITION_POLL_S", 0.0)
    scanner = ScannerOnStrip(at=3, warm_at=80.0, clock=clock)
    assert session.seek(scanner, 1) == 1
    assert scanner.t.at == 1
    assert scanner.t.while_warming, "the lamp was warming when it began"
    assert set(scanner.t.while_warming) <= {SCSI_TEST_UNIT_READY,
                                            SCSI_REQUEST_SENSE}


def test_a_counter_no_strip_has_is_not_a_place_to_start(tmp_path):
    """A stale 72 was once read with no strip in. Winding back 72 frames from
    it would spend four minutes of commands against the end of a strip."""
    from conftest import StripScanner

    scanner = StripScanner(at=72, last=80)
    events, frames = walk(Roll(frames=3, start_at=1, dry_run=True,
                               name="strip"), tmp_path, scanner)
    assert frames == [] and scanner.moves == []
    failed = kinds(events, "failed")
    assert failed and "frame 73" in failed[0].text


def test_a_strip_that_ends_before_the_first_frame_refuses(tmp_path):
    from conftest import StripScanner

    scanner = StripScanner(at=2, last=4)
    events, frames = walk(Roll(frames=3, start_at=9, dry_run=True,
                               name="strip"), tmp_path, scanner)
    assert frames == [] and scanner.rolls == []
    failed = kinds(events, "failed")
    assert failed and "strip ended on frame 5" in failed[0].text


def test_a_film_already_there_is_not_moved(tmp_path):
    from conftest import StripScanner

    scanner = StripScanner(at=4)
    _, frames = walk(Roll(frames=2, start_at=5, dry_run=True, name="strip"),
                     tmp_path, scanner)
    assert frames == [(5, 4), (6, 5)]
    assert scanner.rolls[0]["moves_before"] == 0


def test_the_chosen_frames_are_places_on_the_strip(tmp_path):
    """`only` names frames the same way `start_at` does, so a sheet's ticks
    reach the pictures it showed whichever frame the film was left on."""
    from conftest import StripScanner

    scanner = StripScanner(at=12)
    _, frames = walk(Roll(start_at=2, only=(2, 4), infrared=False,
                          resolution=300, name="strip"), tmp_path, scanner)
    assert frames == [(2, 1), (4, 3)]


def test_the_window_hears_where_the_film_is_when_the_session_opens(tmp_path):
    """So the Roll dialog can say what the roll will do to the film. The
    readout used to hear only about the moves its own buttons made, and read
    '?' from launch until one was pressed."""
    from conftest import StripScanner

    _, _, events = run(Prescan(), tmp_path, scanner=StripScanner(at=4))
    reported = [e.done for e in kinds(events, "transport")]
    assert reported[0] == 4, "at open, before any job"
    assert reported[-1] == 4, "and again once the job has finished"


def test_a_roll_tells_the_window_where_the_film_went(tmp_path):
    from conftest import StripScanner

    _, _, events = run(Roll(frames=2, start_at=1, dry_run=True, name="strip"),
                       tmp_path, scanner=StripScanner(at=3))
    reported = [e.done for e in kinds(events, "transport")]
    assert reported[0] == 3
    assert reported[-1] == 1, "the last frame walked"
    assert 0 in reported, "and where the seek put it"


def test_the_readout_follows_a_roll_frame_by_frame(tmp_path):
    """Each frame's counter reading goes to the window as the roll reaches
    it, already read, so the readout follows the walk rather than jumping
    from where the seek landed to where the roll stopped. A counter no
    strip has is left out of it, as every other report leaves it out."""
    from dataclasses import replace

    from conftest import StripScanner

    class Stale(StripScanner):
        """Frame 2's record says 72; the film is where it always was."""

        def scan_roll(self, **kw):
            for frame in super().scan_roll(**kw):
                if frame.index == 1:
                    frame = replace(frame, position=72)
                yield frame

    _, _, events = run(Roll(frames=4, start_at=1, dry_run=True, name="strip"),
                       tmp_path, scanner=Stale(at=0))
    reported = [e.done for e in kinds(events, "transport")]
    assert 2 in reported, "frame 3 was reported as the roll reached it"
    assert 72 not in reported
    assert reported[-1] == 3, "and the last frame walked"


def test_resuming_a_roll_numbered_the_old_way_renumbers_it_by_position(
        tmp_path):
    """A roll.json written before frame numbers were places on the strip is
    counted from wherever that roll started. Merged as it stands, its frame 1
    and this run's frame 6 could be the same picture in one file."""
    from conftest import StripScanner

    folder = tmp_path / "rolls" / "strip"
    folder.mkdir(parents=True)
    (folder / "roll.json").write_text(json.dumps({
        "roll": "strip", "wanted": [1, 2, 3],
        "frames": [{"number": 1, "transport_position": 5, "done": True},
                   {"number": 2, "transport_position": 6, "done": True}],
    }), encoding="utf-8")
    _, frames = walk(Roll(frames=1, start_at=8, infrared=False,
                          resolution=300, name="strip"), tmp_path,
                     StripScanner(at=0))
    assert frames == [(6, 5), (7, 6), (8, 7)]
    manifest = json.loads((folder / "roll.json").read_text(encoding="utf-8"))
    assert manifest["wanted"] == [6, 7, 8]
    assert manifest["numbering"] == session.NUMBERING


def test_a_walk_files_each_picture_under_its_own_place(tmp_path,
                                                        monkeypatch):
    """The transport double-steps from frame 3 to frame 5, under the real
    driver's roll and the real session. The picture at frame 5 was filed as
    `prescan04.tif`, frame 4's name -- so a sheet ticking 4 commissioned a
    seek to a picture it had never shown."""
    from conftest import NoWaiting, ScannerOnStrip, strip_picture
    from rps7200 import direct, tiff

    monkeypatch.setattr(direct, "time", NoWaiting())
    scanner = ScannerOnStrip(at=0, double_steps={2})
    _, frames = walk(Roll(frames=5, start_at=1, dry_run=True, name="strip",
                          infrared=False), tmp_path, scanner, monkeypatch)
    assert frames == [(1, 0), (2, 1), (3, 2), (5, 4)]
    folder = tmp_path / "rolls" / "strip"
    assert not (folder / "prescan04.tif").exists()
    assert np.array_equal(tiff.read(str(folder / "prescan05.tif")),
                          strip_picture(4))


# --- the seek itself, and the numbering it gives manifests -------------------


def test_seek_goes_nowhere_when_the_film_is_there():
    from conftest import StripScanner

    film = StripScanner(at=6)
    assert session.seek(film, 6) == 6
    assert film.moves == []


def test_seek_refuses_when_the_film_is_not_where_it_ended_up(monkeypatch):
    """The last word is the counter's. An advance that reports a frame the
    counter then disagrees with is a roll about to mis-number itself."""
    from conftest import StripScanner

    class Slips(StripScanner):
        def advance(self, steps=1, **kw):
            landed = super().advance(steps, **kw)
            if landed == 3:
                self.at = 4                   # the counter says one further
            return landed

    monkeypatch.setattr(session, "POSITION_POLL_S", 0.0)
    with pytest.raises(session.FilmNotPlaced, match="frame 4 .* frame 5"):
        session.seek(Slips(at=0), 3)


def test_seek_asks_again_while_the_transport_says_nothing(monkeypatch):
    """READ_STATE right after a transport command comes back empty every
    time; one empty answer is not the film being lost."""
    from conftest import StripScanner

    class Slow(StripScanner):
        asked = 0

        def position(self):
            self.asked += 1
            return None if self.asked < 3 else self.at

    monkeypatch.setattr(session, "POSITION_POLL_S", 0.0)
    assert session.seek(Slow(at=2), 2) == 2


def test_the_old_numbering_is_mapped_by_position_not_by_number():
    """rolls/2026-09-23 as it is on disk: a walk begun on the counter's 5 that
    called that frame 1."""
    old = {"frames": [{"number": 1, "transport_position": 5},
                      {"number": 2, "transport_position": 6}],
           "settings": {"start_at": 1, "only": None}}
    assert session.legacy_shift(old) == 5
    new = session.renumbered(old)
    assert [f["number"] for f in new["frames"]] == [6, 7]
    assert new["settings"]["start_at"] == 6
    assert new["numbering"] == session.NUMBERING
    assert session.renumbered(new) is new, "already on the strip's numbers"


def test_one_misread_position_does_not_give_two_frames_one_number():
    """Numbers in an old manifest were counted once per advance, so they sit
    one apart. Following a lone disagreeing position gave its frame the
    number its neighbour already had -- 5, 5, 7 came back as 6, 6, 8 -- and
    two pictures under one number. The frames whose positions collide take
    the walk's own shift, and the disagreement is said rather than hidden."""
    old = {"roll": "misread",
           "frames": [{"number": 1, "transport_position": 5},
                      {"number": 2, "transport_position": 5},
                      {"number": 3, "transport_position": 7}]}
    assert session.legacy_shift(old) == 5
    new = session.renumbered(old)
    assert [f["number"] for f in new["frames"]] == [6, 7, 8]
    said = []
    session.renumbered(old, say=said.append)
    assert len(said) == 1 and "frame 2 of misread" in said[0], said


def _counted(roll, positions, **keys):
    """An old manifest whose frames were counted 1, 2, ... at `positions`."""
    return {"roll": roll, **keys,
            "frames": [{"number": n, "index": n - 1, "transport_position": p,
                        "registration": {}, "error": None, "done": True}
                       for n, p in enumerate(positions, start=1)]}


@pytest.mark.parametrize("positions, stale", [
    ([72, 1, 2, 3], 1),
    ([0, 1, 72, 3], 3),
    ([72, 1], 1),
])
def test_a_position_no_strip_has_is_not_a_frame_number(positions, stale):
    """`docs/protocol.md` section 9: the counter read a stale 72 before its
    strip went in. An old record holding it was numbered by it -- frame 73,
    past the end of every strip, so the sheet could never scan it, and a
    resume wrote 73 back for good -- and nothing said so. It is numbered where
    the frames around it put it, and said, the way the roll loop keeps its
    count past such a reading. Nor is it counted towards the manifest's
    shift: in a walk of two it tied with the real one, won, and moved
    `start_at` to 73."""
    old = _counted("stale", positions,
                   settings={"start_at": 1, "only": None})
    assert session.legacy_shift(old) == 0
    said = []
    new = session.renumbered(old, say=said.append)
    assert [f["number"] for f in new["frames"]] == list(
        range(1, len(positions) + 1))
    assert new["settings"]["start_at"] == 1
    assert len(said) == 1, said
    assert f"frame {stale} of stale" in said[0], said
    assert "72, which no strip has" in said[0], said


def test_a_position_no_strip_has_takes_the_shift_of_its_own_run():
    """The last frame of the tied 8a9ba17 file, its counter read as 72. It
    sits after the second run, strip frames 6 to 8, so it is frame 8. By the
    file's commonest shift, the first run's, it would be 6, a place the second
    run's first frame already holds; by its own reading it was 73."""
    from conftest import resumed_by_8a9ba17

    old = resumed_by_8a9ba17("tied")
    old["frames"][-1]["transport_position"] = 72
    said = []
    new = session.renumbered(old, say=said.append)
    assert [f["number"] for f in new["frames"]] == [1, 2, 3, 6, 7, 8]
    assert len(said) == 1 and "frame 6 of r" in said[0], said


@pytest.mark.parametrize("positions", [[72, 1, 2, 3], [0, 1, 72, 3]])
def test_a_resume_does_not_write_back_a_position_no_strip_has(tmp_path,
                                                             positions):
    """The write-back half, through the real session: frame 5 resumed into an
    old roll.json one of whose records holds the stale 72. That record came
    back under 'strip' as frame 73, where no roll will ever go."""
    from conftest import StripScanner

    folder = tmp_path / "rolls" / "stale"
    folder.mkdir(parents=True)
    (folder / "roll.json").write_text(
        json.dumps(_counted("stale", positions, dry_run=False)),
        encoding="utf-8")
    events, frames = walk(Roll(frames=1, start_at=5, infrared=False,
                               resolution=300, name="stale"), tmp_path,
                          StripScanner(at=4))
    assert frames == [*enumerate(positions, start=1), (5, 4)]
    logged = [e.text for e in events
              if e.kind == "log" and "which no strip has" in (e.text or "")]
    assert len(logged) == 1, logged


@pytest.mark.parametrize("where", ["top", "settings"])
@pytest.mark.parametrize("positions", [[6, 6, 7], [5, 5, 7], [5, 6, 6]])
def test_a_walks_misread_takes_the_walks_shift_wherever_it_is(positions,
                                                              where):
    """A walk's file is one run -- each walk wrote survey.json afresh -- so a
    lone disagreeing position at either end of it is read as the middle one
    is: a misread, on the walk's shift. At an end of a roll.json the session
    merged, 8a9ba17's, the same record could be a roll of one frame, and is
    read as one; a roll.json `tools/scan_roll.py` wrote is one run, as a walk
    is (see the next test). The session says it is a walk at the top level,
    the tool in `settings`."""
    old = _one_run(positions, where)
    said = []
    new = session.renumbered(old, say=said.append)
    assert [f["number"] for f in new["frames"]] == [6, 7, 8]
    assert len(said) == 1, said


def _one_run(positions, where):
    """`positions` counted 1, 2, ... in a file that is one run: a walk, which
    the session marks at the top level and `tools/scan_roll.py` in
    `settings`, or a roll.json the tool wrote -- `dry_run` false and only in
    `settings`, as every version of it wrote one, afresh."""
    old = _counted("w", positions)
    if where == "top":
        old["dry_run"] = True
    elif where == "settings":
        old["settings"] = {"dry_run": True}
    else:
        old["started"] = "2026-09-01T10:00:00+00:00"
        old["settings"] = {"dry_run": False, "start_at": 1,
                           "frames": len(positions)}
    return old


@pytest.mark.parametrize("positions", [[5, 6, 7, 7], [6, 6, 7, 8],
                                       [6, 6, 7], [5, 6, 6]])
def test_a_roll_json_the_tool_wrote_is_one_run(positions):
    """Every version of `tools/scan_roll.py` began its roll.json with no
    frames and never read one back, so it holds one roll: a lone
    disagreeing position at an end of it is a misread, as in a walk, and not
    a roll of one frame. Read as one, the last frame of 5, 6, 7, 7 was kept
    on frame 8 beside frame 3, so the window offered strip frame 9 again,
    and the log blamed two rolls filed under one name, which the tool never
    made. The session writes `dry_run` at the top level; the tool only inside
    `settings`."""
    said = []
    new = session.renumbered(_one_run(positions, "tool roll"),
                             say=said.append)
    assert [f["number"] for f in new["frames"]] == list(
        range(6, 6 + len(positions)))
    assert len(said) == 1, said
    assert "two rolls" not in said[0], said


@pytest.mark.parametrize("where", ["top", "settings", "tool roll"])
@pytest.mark.parametrize("positions, strip, moved", [
    ([5, 6, 6, 7], [6, 7, 8, 9], [3, 4]),
    ([5, 6, 6, 7, 8], [5, 6, 7, 8, 9], [1, 2]),
    ([5, 6, 7, 8, 8, 9], [6, 7, 8, 9, 10, 11], [5, 6]),
])
def test_one_run_never_gives_two_frames_one_number(positions, where, strip,
                                                   moved):
    """An advance that did not move, 5, 6, 6, 7. The frames on 6 collide and
    take the run's shift, and one of them lands on the place the next frame's
    own position gave it -- which collided with nothing. Kept there, that was
    two prescans of one walk under one number: the sheet showed two frame 8s,
    `--approved` held one of them and dropped the other without a word, and
    the log blamed two rolls filed under one name. One run numbered no two
    frames alike, so that frame goes onto the run's shift as well, and so on
    until none is left; each frame moved is said. Only in one run: in a
    merged roll.json the frame landed on can be another run's, whose shift
    this is not (test_an_8a9ba17_roll_that_went_over_a_place_twice_...)."""
    said = []
    new = session.renumbered(_one_run(positions, where), say=said.append)
    assert [f["number"] for f in new["frames"]] == strip
    assert len(said) == len(moved), said
    for line, frame in zip(said, moved):
        assert line.startswith(f"frame {frame} of w "), line
        assert "two rolls" not in line, line


@pytest.mark.parametrize("where", ["top", "settings", "tool roll"])
@pytest.mark.parametrize("positions", [[5, 6, 8, 9], [5, 6, 7, 9, 10, 11]])
def test_one_run_that_went_two_frames_at_once_keeps_its_own_positions(
        positions, where):
    """A double step inside a walk: frames 3 and 4 of 5, 6, 8, 9 are strip
    frames 9 and 10, whatever the walk counted. Read with one shift for the
    whole walk, as a0de517 read it, they were 8 and 9, and frame 3's picture
    of 9 was filed under 8. A walk is one run, and all that keeps its frames
    on their own positions is that only a frame whose position collides takes
    the run's shift; nothing here collides, so nothing is said."""
    said = []
    new = session.renumbered(_one_run(positions, where), say=said.append)
    assert [f["number"] for f in new["frames"]] == [p + 1 for p in positions]
    assert said == []


def test_one_frame_listed_twice_in_a_walk_is_not_blamed_on_two_rolls():
    """Nothing moves two records with one number apart, so both stay on their
    place and it is said -- but not as the film going over it again between
    two rolls, which one walk cannot have."""
    old = _one_run([5, 6], "top")
    old["frames"].append(dict(old["frames"][-1]))
    said = []
    new = session.renumbered(old, say=said.append)
    assert [f["number"] for f in new["frames"]] == [6, 7, 7]
    assert len(said) == 1, said
    assert said[0].startswith("frames 2 and 2 of w are both frame 7"), said
    assert "two rolls" not in said[0], said


@pytest.mark.parametrize("positions, written", [
    ([5, 6, 7, 7], [(6, 5), (7, 6), (8, 7), (9, 8)]),
    ([6, 6, 7, 8], [(6, 6), (7, 6), (8, 7), (9, 8), (10, 9)]),
])
def test_a_resume_of_a_roll_the_tool_wrote_takes_it_as_one_run(
        tmp_path, positions, written):
    """The write-back half, through the real session: the frame after the
    roll resumed with the film where it stopped. Read as two rolls, 5, 6, 7,
    7 wrote the old scan of strip frame 9 back as a second frame 8, for good,
    and 6, 6, 7, 8 wrote two frame 7s."""
    from conftest import StripScanner

    folder = tmp_path / "rolls" / "w"
    folder.mkdir(parents=True)
    (folder / "roll.json").write_text(
        json.dumps(_one_run(positions, "tool roll")), encoding="utf-8")
    last = max(positions) + 1
    _, frames = walk(Roll(frames=1, start_at=last + 1, infrared=False,
                          resolution=300, name="w"), tmp_path,
                     StripScanner(at=last))
    assert frames == written


@pytest.mark.parametrize("which, strip", [
    ("tied", [1, 2, 3, 6, 7, 8]),
    ("second-longer", [1, 2, 4, 5, 6]),
])
def test_a_roll_resumed_under_8a9ba17_is_read_frame_by_frame(which, strip):
    """Each run of a resumed 8a9ba17 roll counted from wherever the film then
    was, so its file holds two shifts honestly. The commonest applied to all
    of it read the tied file as 1 to 6 -- strip frames 6 to 8 as 4 to 6 --
    and the other as 2 to 6, the first run's frames one place on; a resume
    then wrote that down. Every frame's own position says where it was, and
    nothing in either file collides, so there is nothing to report."""
    from conftest import resumed_by_8a9ba17

    old = resumed_by_8a9ba17(which)
    said = []
    new = session.renumbered(old, say=said.append)
    assert [f["number"] for f in new["frames"]] == strip
    assert [f["index"] for f in new["frames"]] == [n - 1 for n in strip]
    assert said == []


def test_a_resume_writes_back_the_frames_an_8a9ba17_roll_really_scanned(
        tmp_path):
    """The write-back half, through the real session: frames 4 and 5 of the
    strip, never scanned, resumed into the tied file with the film on frame
    8. The file then said 'strip' over frames 1 to 6 read with one shift, so
    the new frames 4 and 5 replaced the records of strip frames 6 and 7, and
    the scan of frame 8 stayed filed as frame 6 for good."""
    from conftest import StripScanner, resumed_by_8a9ba17

    folder = tmp_path / "rolls" / "r"
    folder.mkdir(parents=True)
    (folder / "roll.json").write_text(json.dumps(resumed_by_8a9ba17("tied")),
                                      encoding="utf-8")
    _, frames = walk(Roll(frames=2, start_at=4, infrared=False,
                          resolution=300, name="r"), tmp_path,
                     StripScanner(at=7))
    assert frames == [(1, 0), (2, 1), (3, 2), (6, 5), (7, 6), (8, 7),
                      (4, 3), (5, 4)]
    manifest = json.loads((folder / "roll.json").read_text(encoding="utf-8"))
    assert manifest["numbering"] == session.NUMBERING


@pytest.mark.parametrize("which, strip, shared", [
    ("rewound", [6, 7, 8, 7, 8, 9],
     ["frames 2 and 4 of r are both frame 7 of the strip",
      "frames 3 and 5 of r are both frame 8 of the strip"]),
    ("reinserted", [6, 7, 8, 4, 5, 6],
     ["frames 1 and 6 of r are both frame 6 of the strip"]),
    ("one-frame", [6, 7, 8, 7],
     ["frames 2 and 4 of r are both frame 7 of the strip"]),
])
def test_an_8a9ba17_roll_that_went_over_a_place_twice_keeps_both_there(
        which, strip, shared):
    """Two 8a9ba17 runs into one roll that overlap on the strip, the film
    wound back or the strip put in again between them. The frames that
    collided were moved by the file's commonest shift, which was the other
    run's, and each then landed on a frame that had collided with nothing,
    which was moved in turn: the rewound file read 6 to 11, filing the scan
    of strip frame 9 as 11 -- never scanned -- and saying frame 6 of it had
    collided when nothing shared its place. Each run keeps its own shift,
    both scans of a place are kept on it, and only a place really shared is
    said."""
    from conftest import resumed_by_8a9ba17

    said = []
    new = session.renumbered(resumed_by_8a9ba17(which), say=said.append)
    assert [f["number"] for f in new["frames"]] == strip
    assert [f["index"] for f in new["frames"]] == [n - 1 for n in strip]
    assert len(said) == len(shared), said
    for line, start in zip(said, shared):
        assert line.startswith(start), line


@pytest.mark.parametrize("which, resume, film_at, written", [
    ("rewound", 10, 8,
     [(6, 5), (7, 6), (8, 7), (7, 6), (8, 7), (9, 8), (10, 9)]),
    ("reinserted", 11, 10,
     [(6, 5), (7, 6), (8, 7), (4, 3), (5, 4), (6, 5), (11, 10)]),
])
def test_a_resume_keeps_both_scans_of_a_place_an_8a9ba17_roll_went_over(
        tmp_path, which, resume, film_at, written):
    """The write-back half, through the real session, for the two files
    above. The rewound one came back as frames 6 to 11 under 'strip', so
    resuming frame 10 replaced the record of strip frame 8's second scan and
    left the scan of frame 9 filed as 11 for good; the reinserted one filed
    the second scan of strip frame 6 as 11, and resuming frame 11 replaced
    it."""
    from conftest import StripScanner, resumed_by_8a9ba17

    folder = tmp_path / "rolls" / "r"
    folder.mkdir(parents=True)
    (folder / "roll.json").write_text(json.dumps(resumed_by_8a9ba17(which)),
                                      encoding="utf-8")
    _, frames = walk(Roll(frames=1, start_at=resume, infrared=False,
                          resolution=300, name="r"), tmp_path,
                     StripScanner(at=film_at))
    assert frames == written
    manifest = json.loads((folder / "roll.json").read_text(encoding="utf-8"))
    assert manifest["numbering"] == session.NUMBERING


def test_a_manifest_that_recorded_no_positions_borrows_its_walks_shift():
    died = {"wanted": [1, 2], "frames": [{"number": 1, "done": False}]}
    assert session.legacy_shift(died) is None
    assert session.renumbered(died, fallback=5)["wanted"] == [6, 7]


def test_approved_json_is_read_from_its_folder_or_as_a_file(tmp_path):
    """The window reads a roll folder's own; `tools/scan_roll.py
    --positions` and `tools/hold_check.py` are handed the file, which need
    not be called approved.json or sit where it was written."""
    folder = tmp_path / "a-roll"
    folder.mkdir()
    decisions = {"numbering": "strip", "frames": [
        {"number": 1, "offset_mm": -0.9344, "source": "operator",
         "reference_entry": "library\\walk-01", "rotation": 90},
        {"number": 2, "offset_mm": 0.0, "source": "operator"},
        {"number": 3, "offset_mm": 0.0, "as_walked": True,
         "source": "operator"}]}
    (folder / "approved.json").write_text(json.dumps(decisions),
                                          encoding="utf-8")
    copy = tmp_path / "kept" / "positions-for-a-roll.json"
    copy.parent.mkdir()
    copy.write_text(json.dumps(decisions), encoding="utf-8")
    read = session.read_approved(folder)
    assert session.read_approved(folder / "approved.json") == read
    assert session.read_approved(copy) == read
    offsets, rotations, _flips, entries, sources = read
    # An untouched zero is no decision; his "as walked" is.
    assert offsets == {1: -0.9344, 3: 0.0}
    assert sources == {1: "operator", 3: "operator"}
    assert rotations == {1: 90} and entries == {1: "library\\walk-01"}
    # Nothing there is nothing decided, a folder or a file.
    nothing = ({}, {}, {}, {}, {})
    assert session.read_approved(tmp_path / "no-roll") == nothing
    assert session.read_approved(tmp_path / "no-such.json") == nothing


def test_an_older_approved_json_is_numbered_by_what_lies_beside_it(tmp_path):
    """One that says it numbers by the strip is read as it stands; an older
    one moves with the walk or the roll beside it, as the window reads it
    (`approved_legacy`); and one nothing beside can number has no shift --
    None, not 0, which would hold each frame to another frame's position."""
    folder = tmp_path / "old-roll"
    folder.mkdir()
    old = {"frames": [{"number": 1, "offset_mm": 0.5}]}
    (folder / "approved.json").write_text(json.dumps(old), encoding="utf-8")
    assert session.approved_shift(folder) is None
    (folder / "survey.json").write_text(json.dumps({"frames": [
        {"number": 1, "transport_position": 4},
        {"number": 2, "transport_position": 5}]}), encoding="utf-8")
    assert session.approved_shift(folder / "approved.json") == 4
    assert session.read_approved(folder, legacy=4)[0] == {5: 0.5}
    # The roll commissioned from it numbered its frames the way the file
    # did, so where it recorded positions its shift is the file's.
    (folder / "roll.json").write_text(json.dumps({"frames": [
        {"number": 1, "transport_position": 6}]}), encoding="utf-8")
    assert session.approved_shift(folder) == 6
    (folder / "approved.json").write_text(json.dumps(
        dict(old, numbering="strip")), encoding="utf-8")
    assert session.approved_shift(folder) == 0
    (folder / "approved.json").write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="approved.json"):
        session.approved_shift(folder)


def test_a_plan_says_what_the_hardware_will_actually_travel():
    """And since the cap went to param 87, it says it in one command.

    This used to be two -- param 8 then param 3 -- because a single command
    could not reach 1.5 mm. That chaining was never free: each command pays the
    ramp again and scatters again, and the scatter does not shrink with the
    size of the move, so two commands were twice the error for one distance.
    """
    from rps7200.session import deliverable_mm, plan_nudges

    plan = plan_nudges(1.5)
    assert plan == [pytest.approx(1.4629, abs=1e-4)]
    # 0.04 mm short of the 1.5 asked for, because param is an integer and 12
    # is the nearest. The honest answer, rather than a rounded promise.
    assert deliverable_mm(1.5) == pytest.approx(1.4629, abs=1e-4)


def test_nothing_exists_between_zero_and_the_smallest_move():
    """The reachable set starts at FINE_MIN_MM. Asking for less does not get
    you less -- it gets you nothing, or a whole first step. A slider that
    pretended otherwise would aim at positions that do not exist."""
    from rps7200.session import FINE_MIN_MM, deliverable_mm, plan_nudges

    assert plan_nudges(0.10) == []
    assert deliverable_mm(0.10) == 0.0
    assert plan_nudges(FINE_MIN_MM) == [pytest.approx(FINE_MIN_MM, abs=1e-4)]


def test_the_plan_keeps_the_sign():
    from rps7200.session import deliverable_mm, plan_nudges

    assert all(v < 0 for v in plan_nudges(-1.5))
    assert deliverable_mm(-1.5) == pytest.approx(-deliverable_mm(1.5), abs=1e-9)


def test_too_far_is_refused_rather_than_silently_clamped():
    """`param_for_mm` clamps at MAX_CORRECTION_PARAM with no error, so a
    caller that bypassed the planner would issue commands against a ceiling it
    could not see. The planner raises instead.

    The refusal starts further out than it did: one command now reaches 9.36 mm
    rather than 1.01, so 20 mm is three commands where it used to be past the
    limit entirely. 80 mm still is -- a sub-frame move asked to travel that far
    is a whole-frame job, and SLIDE_NEXT does those properly.
    """
    from rps7200.session import MAX_FINE_STEPS, plan_nudges

    with pytest.raises(ValueError, match="sub-linear"):
        plan_nudges(80.0)
    assert len(plan_nudges(MAX_FINE_STEPS * 1.0)) <= MAX_FINE_STEPS


def test_the_plan_is_what_the_mover_would_have_sent():
    """The planner must not drift from the hardware it predicts: every planned
    distance has to snap to the same SLIDE param it was derived from."""
    from rps7200.direct import DirectScanner
    from rps7200.session import plan_nudges

    for want in (0.3, 0.5, 0.9, 1.0, 2.2, 4.7, 8.0):
        for landed in plan_nudges(want):
            param = DirectScanner.param_for_mm(landed)
            again = DirectScanner.STEP_MM * param + DirectScanner.OVERHEAD_MM
            assert again == pytest.approx(abs(landed), abs=1e-9)


def test_measuring_geometry_does_not_drag_in_the_device():
    """`rps7200.uniformity` holds the correlation primitives that framing will
    need. It used to import MM_PER_INCH from `direct`, which imports framing --
    so the moment framing reached for uniformity the cycle closed. The constant
    lives in `protocol`; take it from there."""
    import rps7200.uniformity as un

    source = inspect.getsource(un)
    assert "from .direct import" not in source
    assert "from .protocol import MM_PER_INCH" in source


def test_the_output_folder_honours_the_format_and_the_roll_does_not(tmp_path):
    """Two files, two jobs. The folder is the operator's deliverable and takes
    the format they chose; `rolls/` is machinery -- `prescanNN.tif` is what
    reopening a survey reads back, and a lossy copy of a registration reference
    is not something to introduce quietly."""
    out = tmp_path / "out"
    out.mkdir()
    rolls = tmp_path / "r"
    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(rolls),
                    out_dir=str(out), open_scanner=FakeScanner, verbose=False)
    s.out_format = "jpeg"
    s.start()
    s.submit(Roll(resolution=600, frames=1, infrared=False, meter="none"))
    s.shutdown()
    s.join(timeout=30)

    assert sorted(p.suffix for p in out.rglob("*") if p.is_file()) == [".jpg"], \
        "the operator's folder follows the setting"
    assert [p.suffix for p in rolls.rglob("frame*")] == [".tif"], \
        "a roll's own files stay TIFF whatever the setting says"


def test_a_walks_picture_from_before_its_aim_keeps_its_own_name(tmp_path):
    """Both were named as the frame's before either was written, and the
    before-picture, written second, replaced the corrected prescan in the
    operator's folder -- under the frame's own name, with nothing to say so."""

    class Aimed(FakeScanner):
        def scan_roll(self, first_index=0, **kw):
            yield RollFrame(
                index=first_index, position=self.pos, image=None, meta={},
                prescan=np.full((24, 36, 3), 50, np.uint8), registration={},
                prescan_before=np.full((24, 36, 3), 20, np.uint8),
                prescan_meta={"resolution_dpi": 300,
                              "channel_order": list("RGB")})

    out = tmp_path / "out"
    out.mkdir()
    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(tmp_path / "r"),
                    out_dir=str(out), open_scanner=Aimed, verbose=False)
    s.start()
    s.submit(Roll(frames=1, dry_run=True, name="aim"))
    s.shutdown()
    s.join(timeout=10)
    final = out / "prescans" / "aim_frame01_300dpi.tif"
    before = out / "prescans" / "aim_frame01_300dpi-before.tif"
    assert int(tiff.read(final).max()) == 50, "the corrected prescan was replaced"
    assert int(tiff.read(before).max()) == 20


def test_the_filename_says_which_format_it_is(tmp_path):
    """`_out_name` is the only thing that decides, so it is pinned directly --
    a roll of 38 frames landing on the wrong extension is a slow thing to spot."""
    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(tmp_path / "r"),
                    open_scanner=FakeScanner, verbose=False)
    meta = {"resolution_dpi": 1800, "channels": 4}
    assert s._out_name(3, meta, "roll-a").endswith("_1800dpi_ir.tif")
    s.out_format = "jpeg"
    # Still `_ir`: it says what was *scanned*, and the JPEG's own note says what
    # arrived. Renaming it here would lose the first.
    assert s._out_name(3, meta, "roll-a").endswith("_1800dpi_ir.jpg")


# -- a mirror, which is not a fourth angle -----------------------------------


def test_a_flip_reaches_the_delivered_file_but_not_the_entry(tmp_path):
    """A strip loaded the other way up comes off this scanner reading
    backwards, and no amount of turning fixes that. The entry still keeps the
    scanner's own pixels, because they have to go on matching the raw bytes."""
    out = tmp_path / "out"
    out.mkdir()
    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(tmp_path / "r"),
                    out_dir=str(out), open_scanner=FakeScanner, verbose=False)
    s.flip = True
    s.start()
    s.submit(Scan(resolution=600, infrared=True))
    s.shutdown()
    s.join(timeout=15)

    from rps7200 import preview, tiff
    delivered = sorted(out.rglob("*.tif"))
    assert len(delivered) == 1
    written = tiff.read(str(delivered[0]))
    stored, record = library.load(tmp_path / "lib" / library.entries(
        tmp_path / "lib")[0]["id"])
    assert written.shape == stored.shape, "a mirror does not change the shape"
    assert np.array_equal(written, preview.mirror(stored)), "the file is mirrored"
    assert not np.array_equal(written, stored), "and it really is a change"
    assert record["scan"]["flipped"] is True, "the entry records what was asked"


def test_one_frame_of_a_roll_can_be_flipped_without_flipping_the_rest(tmp_path):
    """The same per-frame rule the turn follows, for the same reason: a strip
    is not one arrangement."""
    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(tmp_path / "r"),
                    open_scanner=FakeScanner, verbose=False)
    s.start()
    s.submit(Roll(frames=2, resolution=600, infrared=False, name="mixed",
                  approved=(Approved(number=1),
                            Approved(number=2, flipped=True))))
    s.shutdown()
    s.join(timeout=20)

    from rps7200 import preview, tiff
    first = tiff.read(str(tmp_path / "r" / "mixed" / "frame01.tif"))
    second = tiff.read(str(tmp_path / "r" / "mixed" / "frame02.tif"))
    # Both frames are the same fake picture, so one mirrored and one not is
    # exactly a mirror apart.
    assert not np.array_equal(first, second)
    assert np.array_equal(preview.mirror(first), second)


def test_a_turn_and_a_flip_are_applied_in_one_agreed_order(tmp_path):
    """A mirror and a quarter turn do not commute, so the file has to be
    written by the same `preview.orient` the screen was drawn with -- not by
    two steps the writer chose an order for."""
    out = tmp_path / "out"
    out.mkdir()
    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(tmp_path / "r"),
                    out_dir=str(out), open_scanner=FakeScanner, verbose=False)
    s.rotation, s.flip = 90, True
    s.start()
    s.submit(Scan(resolution=600, infrared=False))
    s.shutdown()
    s.join(timeout=15)

    from rps7200 import preview, tiff
    written = tiff.read(str(sorted(out.rglob("*.tif"))[0]))
    stored, _ = library.load(tmp_path / "lib" / library.entries(
        tmp_path / "lib")[0]["id"])
    assert np.array_equal(written, preview.orient(stored, 90, True))
    assert not np.array_equal(written, preview.rotate(preview.mirror(stored), 270)), \
        "and it is not the other order, which is 180 degrees away"


def test_a_rolls_flips_do_not_outlive_it(tmp_path):
    """As with the turns: a single scan afterwards is not frame 2 of
    anything."""
    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(tmp_path / "r"),
                    open_scanner=FakeScanner, verbose=False)
    s.start()
    s.submit(Roll(frames=2, resolution=600, infrared=False, name="mixed",
                  approved=(Approved(number=2, flipped=True),)))
    s.shutdown()
    s.join(timeout=20)
    assert s._frame_flip == {}


# -- a pass that came back the wrong way up ----------------------------------


def _picture(h=48, w=72, seed=1):
    """Something with enough structure to tell one way up from the other."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w]
    a = np.sin(x / 9) * 90 + np.cos(y / 6) * 60 + y * 1.5
    a = a + rng.normal(0, 6, (h, w))
    a = (a - a.min()) / max(1e-9, float(a.max() - a.min()))
    return np.repeat((a * 60000).astype(np.uint16)[..., None], 3, axis=2)


class ReversingScanner(FakeScanner):
    """A scanner whose scan comes back reversed against its own prescan.

    Which is a thing this one really does: MODE SELECT byte 14 bit 0 skips
    the re-home for bidirectional speed, and a pass following another bit-0
    pass reads top-and-bottom reversed with no status bit to say so.
    """

    def __init__(self, reversal=(180, False), **kw):
        super().__init__(**kw)
        self._truth = _picture()
        self._reversal = reversal

    def prescan(self, resolution=300, frame=None, keep_raw=False,
                film="negative"):
        self.calls.append(("prescan", resolution, keep_raw))
        return self._truth[::2, ::2].copy(), None

    def _reversed(self):
        from rps7200 import preview
        return np.ascontiguousarray(preview.orient(self._truth, *self._reversal))

    def scan(self, resolution=1800, infrared=True, **kw):
        self.calls.append(("scan", resolution, infrared, kw.get("auto_exposure")))
        image = self._reversed()
        return image, {"resolution_dpi": resolution, "channels": 3,
                       "channel_order": list("RGB"), "width": image.shape[1],
                       "height": image.shape[0], "depth": 16}

    def scan_roll(self, frames=None, resolution=1800, infrared=True,
                  dry_run=False, skip=0, only=None, **kw):
        """A roll of the same picture, with each frame's own prescan beside it.

        `FakeScanner` yields an unrelated random prescan per frame, which is
        fine for every other test and useless here: with no honest reference
        the detector abstains, correctly, and the test would be measuring
        nothing.
        """
        self.calls.append(("roll", frames, resolution, dry_run, skip))
        self.only = only
        image, meta = self.scan(resolution=resolution, infrared=infrared)
        for i in range(frames or self._frames):
            if only is not None and skip + i not in only:
                continue
            self.produced += 1
            yield RollFrame(
                index=skip + i, position=skip + i,
                image=None if dry_run else image, meta={} if dry_run else meta,
                prescan=self._truth[::2, ::2].copy(),
                registration={},
            )


def _delivered(out):
    """The scan's own copy. Prescans go to their own subdirectory beside it."""
    from rps7200 import tiff
    files = sorted(p for p in out.glob("*.tif"))
    assert len(files) == 1, files
    return tiff.read(str(files[0]))


@pytest.mark.parametrize("reversal", [(180, False), (0, True), (180, True)])
def test_a_reversed_scan_is_turned_to_match_its_prescan(tmp_path, reversal):
    """The scanner hands it back the wrong way up with nothing to say it has.
    The only evidence is that it does not match the framing pass taken a
    minute earlier, so that is what it is judged against."""
    from rps7200 import preview
    out = tmp_path / "out"
    out.mkdir()
    scanner = ReversingScanner(reversal=reversal)
    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(tmp_path / "r"),
                    out_dir=str(out), open_scanner=lambda: scanner, verbose=False)
    s.start()
    s.submit(Prescan(resolution=300))
    s.submit(Scan(resolution=600, infrared=False))
    s.shutdown()
    s.join(timeout=20)

    written = _delivered(out)
    assert np.array_equal(written, scanner._truth), (
        "the delivered file reads the way the prescan did")
    # And the entry keeps what the scanner actually sent.
    stored, record = library.load(tmp_path / "lib" / [
        e["id"] for e in library.entries(tmp_path / "lib")
        if e["id"].endswith("600dpi")][0])
    assert np.array_equal(
        stored, preview.orient(scanner._truth, *reversal)), "raw, as it arrived"
    assert record["scan"]["reversal"] == [reversal[0], reversal[1]]


def test_a_pass_that_came_back_right_is_left_alone(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    scanner = ReversingScanner(reversal=(0, False))
    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(tmp_path / "r"),
                    out_dir=str(out), open_scanner=lambda: scanner, verbose=False)
    s.start()
    s.submit(Prescan(resolution=300))
    s.submit(Scan(resolution=600, infrared=False))
    s.shutdown()
    s.join(timeout=20)
    assert np.array_equal(_delivered(out), scanner._truth)


def test_the_operators_own_turn_survives_the_correction(tmp_path):
    """Two arrangements on one pass: the one the scanner made necessary and
    the one the operator asked for. Composed, not fought over."""
    from rps7200 import preview
    out = tmp_path / "out"
    out.mkdir()
    scanner = ReversingScanner(reversal=(180, False))
    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(tmp_path / "r"),
                    out_dir=str(out), open_scanner=lambda: scanner, verbose=False)
    s.rotation = 90
    s.start()
    s.submit(Prescan(resolution=300))
    s.submit(Scan(resolution=600, infrared=False))
    s.shutdown()
    s.join(timeout=20)
    assert np.array_equal(_delivered(out), preview.orient(scanner._truth, 90))


def test_the_correction_can_be_switched_off(tmp_path):
    """It is a detector, and every detector written for this scanner has been
    confidently wrong on some frame."""
    from rps7200 import preview
    out = tmp_path / "out"
    out.mkdir()
    scanner = ReversingScanner(reversal=(180, False))
    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(tmp_path / "r"),
                    out_dir=str(out), open_scanner=lambda: scanner, verbose=False)
    s.match_prescan = False
    s.start()
    s.submit(Prescan(resolution=300))
    s.submit(Scan(resolution=600, infrared=False))
    s.shutdown()
    s.join(timeout=20)
    assert np.array_equal(_delivered(out), preview.orient(scanner._truth, 180))


def test_a_scan_with_no_prescan_of_its_own_is_never_turned(tmp_path):
    """There is nothing to judge it against, and guessing would be worse than
    leaving it as it came."""
    from rps7200 import preview
    out = tmp_path / "out"
    out.mkdir()
    scanner = ReversingScanner(reversal=(180, False))
    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(tmp_path / "r"),
                    out_dir=str(out), open_scanner=lambda: scanner, verbose=False)
    s.start()
    s.submit(Scan(resolution=600, infrared=False))      # no prescan first
    s.shutdown()
    s.join(timeout=20)
    assert np.array_equal(_delivered(out), preview.orient(scanner._truth, 180))


def test_a_prescan_of_a_different_picture_is_never_used_to_judge_a_scan(tmp_path):
    """`reversal_against` answers about arrangement, not identity: two
    photographs with the same broad structure correlate, and it will say which
    way round one sits against the other. Keeping the reference honest is this
    side's job, and the transport position is how it is done -- the same test
    that decides a scan stands in for a prescan."""
    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(tmp_path / "r"),
                    open_scanner=FakeScanner, verbose=False)
    s._scanner = FakeScanner()
    s._last_prescan = (np.zeros((4, 4)), 3, {})
    s._scanner.t = None

    s._position = lambda: 3
    assert s._prescan_here() is not None, "the same picture"
    s._position = lambda: 4
    assert s._prescan_here() is None, "the film has moved; it is a different one"
    s._last_prescan = None
    assert s._prescan_here() is None, "and nothing at all is not a reference"


def test_a_position_nobody_knows_is_never_the_same_place(tmp_path):
    """None == None: a prescan taken while the transport said nothing was
    the reference for any scan taken while it said nothing again -- another
    strip's picture included, which can turn a scan in every file."""
    s = ScanSession(root=str(tmp_path / "lib"), open_scanner=FakeScanner,
                    verbose=False)
    s._last_prescan = (np.zeros((4, 4)), None, {})
    s._position = lambda: None
    assert s._prescan_here() is None


@pytest.mark.parametrize("job", [Move(frames=1), Move(millimetres=0.5),
                                 Roll(frames=1, dry_run=True, name="since")])
def test_a_prescan_does_not_outlive_the_film_moving(tmp_path, job):
    """A nudge moves the film where the counter cannot see, and a roll winds
    it through the transport; the prescan before either is of somewhere
    else."""
    scanner = FakeTransportScanner(position=3)
    holder = {}
    # The prescan queued first, then the job.
    run(job, tmp_path, scanner=scanner,
        extra=lambda s, _sc: (holder.update(s=s), s.submit(Prescan())))
    assert scanner.calls[0][0] == "prescan"
    assert holder["s"]._last_prescan is None


def test_the_correction_reaches_every_file_that_leaves_here(tmp_path):
    """The reversal is recorded in the meta so the library entry can stay
    exactly what the scanner sent. That is bookkeeping, not restraint: every
    file delivered to the operator is turned by it, whatever container it is
    written in and wherever it is written to."""
    from PIL import Image

    from rps7200 import preview, tiff
    for fmt, suffix in (("tiff", ".tif"), ("jpeg", ".jpg")):
        out = tmp_path / fmt
        out.mkdir()
        scanner = ReversingScanner(reversal=(180, False))
        s = ScanSession(root=str(tmp_path / f"lib-{fmt}"),
                        rolls=str(tmp_path / f"r-{fmt}"), out_dir=str(out),
                        open_scanner=lambda sc=scanner: sc, verbose=False)
        s.out_format = fmt
        s.start()
        s.submit(Prescan(resolution=300))
        s.submit(Scan(resolution=600, infrared=False))
        s.shutdown()
        s.join(timeout=20)

        written = [p for p in out.glob(f"*{suffix}")]
        assert len(written) == 1, written
        right_way_up = scanner._truth
        as_it_arrived = preview.orient(right_way_up, 180)
        if fmt == "tiff":
            assert np.array_equal(tiff.read(str(written[0])), right_way_up)
        else:
            shown = np.asarray(Image.open(written[0]))[..., 0].astype(float)

            def like(other):
                a = shown - shown.mean()
                b = (other[..., 0] >> 8).astype(float)
                b = b - b.mean()
                return float((a * b).sum()
                             / np.sqrt((a * a).sum() * (b * b).sum()))

            assert like(right_way_up) > 0.99
            assert like(as_it_arrived) < 0.5, "and it is not the way it came"


def test_a_reversed_roll_frame_is_turned_in_the_rolls_own_file(tmp_path):
    """`rolls/<name>/frameNN.tif` is a delivered file too, and a roll has the
    prescan of each frame right beside it to judge against."""
    from rps7200 import preview, tiff
    scanner = ReversingScanner(reversal=(0, True))
    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(tmp_path / "r"),
                    open_scanner=lambda: scanner, verbose=False)
    s.start()
    s.submit(Roll(frames=1, resolution=600, infrared=False, name="strip"))
    s.shutdown()
    s.join(timeout=25)

    written = tiff.read(str(tmp_path / "r" / "strip" / "frame01.tif"))
    assert np.array_equal(written, scanner._truth)
    assert not np.array_equal(written, preview.mirror(scanner._truth))


def test_the_window_can_ask_what_aiming_would_do_without_doing_it():
    """`tools/scan_roll.py` has had this since the correction did; the window
    could only do the real thing, so there was no way to see what aiming would
    command before letting it command it."""
    from rps7200.session import Roll

    assert Roll().correct_dry_run is False
    assert Roll(correct_dry_run=True).correct_dry_run is True


# --- a reversed prescan must not turn a correct scan upside down ------------


def test_a_reversed_prescan_is_not_evidence_about_the_scan():
    """Measured on film: five of one roll's fifteen prescans came back with
    their rows reversed while every scan was fine. `reversal_against` compares
    a scan against its own prescan and cannot tell which of the two reversed,
    so it blamed the scan on all five, at margins of 0.32 to 1.21 against a
    threshold of 0.25 -- and `_note_reversal` acts on that, by its own
    docstring turning every file that leaves. Five correct frames would have
    shipped upside down.
    """
    import numpy as np

    from rps7200.session import ScanSession

    session = ScanSession.__new__(ScanSession)
    session.match_prescan = True
    session._emit = lambda *a, **k: None

    # A picture with a top and a bottom, because `reversal_against` compares
    # banded profiles and pure noise gives it nothing to tell apart.
    rng = np.random.default_rng(7)
    ramp = np.linspace(0, 220, 60)[:, None, None] * np.ones((1, 428, 3))
    scan = ramp + rng.random((60, 428, 3)) * 30
    prescan = scan[::-1]                      # the prescan is the odd one out

    blamed = session._note_reversal({}, scan, prescan)
    assert blamed.get("reversal"), "without the third opinion it blames the scan"

    spared = session._note_reversal({}, scan, prescan, prescan_reversed=True)
    assert "reversal" not in spared, "told which pass reversed, it must not turn"


def test_the_scan_is_still_turned_when_the_scan_is_the_reversed_one():
    """The detector is not being switched off -- only told which way round the
    evidence points. A genuinely reversed scan must still be caught."""
    import numpy as np

    from rps7200.session import ScanSession

    session = ScanSession.__new__(ScanSession)
    session.match_prescan = True
    session._emit = lambda *a, **k: None

    rng = np.random.default_rng(8)
    ramp = np.linspace(0, 220, 60)[:, None, None] * np.ones((1, 428, 3))
    prescan = ramp + rng.random((60, 428, 3)) * 30
    turned = prescan[::-1]

    meta = session._note_reversal({}, turned, prescan)
    assert meta.get("reversal"), "a reversed scan must still be turned back"


# --- a pass whose own lines said which way it was read -------------------------


def _judge():
    from rps7200.session import ScanSession

    said = []
    session = ScanSession.__new__(ScanSession)
    session.match_prescan = True
    session._emit = lambda kind, **k: said.append(k.get("text", ""))
    return session, said


def _pictures(seed=11):
    rng = np.random.default_rng(seed)
    ramp = np.linspace(0, 220, 60)[:, None, None] * np.ones((1, 428, 3))
    return ramp + rng.random((60, 428, 3)) * 30


@pytest.mark.parametrize("direction", ["forward", "reversed"])
def test_a_scan_whose_lines_said_which_way_is_never_turned_by_a_picture(direction):
    """The decode already put it upright. A prescan that still disagrees --
    one filed before the decode turned passes, or simply a different picture
    -- is not the carriage, and turning the scan would ship it upside down."""
    session, said = _judge()
    scan = _pictures()
    meta = {"read_direction": {"direction": direction, "turned": direction == "reversed"}}
    out = session._note_reversal(meta, scan, scan[::-1], {})
    assert "reversal" not in out
    assert any("left as it came" in line for line in said)


def test_an_agreeing_prescan_says_nothing():
    session, said = _judge()
    scan = _pictures()
    session._note_reversal({"read_direction": {"direction": "forward"}}, scan, scan, {})
    assert said == []


def test_only_a_pass_of_unknown_direction_is_still_judged_by_picture():
    session, _ = _judge()
    prescan = _pictures(12)
    for meta in ({}, {"read_direction": {"direction": "unknown"}}):
        out = session._note_reversal(meta, prescan[::-1], prescan,
                                     {"read_direction": {"direction": "forward"}})
        assert out.get("reversal"), meta


def test_the_window_scan_is_judged_against_the_prescan_with_its_record(tmp_path):
    """`_prescan_here` hands over the prescan's meta with its picture, so the
    judgement knows how the reference was read."""
    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(tmp_path / "r"),
                    open_scanner=FakeScanner, verbose=False)
    s._position = lambda: 3
    s._last_prescan = (np.zeros((4, 4)), 3, {"read_direction": {"direction": "reversed"}})
    image, meta = s._prescan_here()
    assert meta["read_direction"]["direction"] == "reversed"


def test_a_roll_frame_files_its_prescan_with_the_way_it_was_read(tmp_path):
    from conftest import load_tool

    from rps7200.library import FilmNotes

    writer = load_tool("scan_roll").FrameWriter()
    read = {"direction": "reversed", "turned": True, "lead": "B", "trail": "R"}
    writer.submit(
        number=1, paths=[], dpi=600, image=np.zeros((8, 8, 3), np.uint16),
        meta={"resolution_dpi": 600, "channels": 3},
        prescan=np.zeros((4, 8, 3), np.uint8),
        prescan_meta={"read_direction": read},
        library=str(tmp_path / "lib"), inquiry=None,
        capture={"reference": None, "ccd_mask": None, "raw": None,
                 "raw_layout": None},
        tags=["roll"], film=FilmNotes(frame="roll/01"))
    writer.finish()
    (_, entry), = writer.done
    record = json.loads((entry / "scan.json").read_text(encoding="utf-8"))
    assert record["prescan"]["read_direction"] == read


@pytest.mark.parametrize("film,process", [("negative", "c41"), ("positive", "e6"),
                                          ("bw", "bw"), ("kodachrome", "k14")])
def test_a_delivered_name_says_the_films_process(tmp_path, film, process):
    """Stefan's choice, 2026-10-04: a folder of scans says by name which are
    colour negative, slide or black and white."""
    s = ScanSession(root=str(tmp_path), rolls=str(tmp_path / "rolls"),
                    open_scanner=FakeScanner, verbose=False)
    meta = {"resolution_dpi": 3600, "channels": 4, "film": film}
    assert s._out_name(3, meta, "roll-a") == f"roll-a_frame03_{process}_3600dpi_ir.tif"
    assert f"_{process}_3600dpi_ir.tif" in s._out_name(0, meta, "")


def test_a_pass_that_does_not_say_its_film_claims_no_process(tmp_path):
    s = ScanSession(root=str(tmp_path), rolls=str(tmp_path / "rolls"),
                    open_scanner=FakeScanner, verbose=False)
    assert s._out_name(3, {"resolution_dpi": 600}, "r") == "r_frame03_600dpi.tif"


# -- other programs: the roll's folder, and compaction at close ---------------


def test_a_roll_into_a_folder_another_program_holds_moves_nothing(
        tmp_path, monkeypatch):
    """A `tools/scan_roll.py` scanning into the same folder, or a Rename of
    it: the roll is refused before the seek, naming who has it -- no SLIDE
    sent, no folder made, nothing filed."""
    from conftest import HeldElsewhere, scanner_at_commands

    from rps7200 import locks
    from rps7200.protocol import SCSI_SLIDE

    scanner, device = scanner_at_commands(monkeypatch, position=3)
    folder = tmp_path / "rolls" / "strip"
    with HeldElsewhere(locks.ROLL, folder, what="tools/scan_roll.py") as other:
        _s, _scanner, events = run(
            Roll(frames=2, start_at=1, resolution=300, infrared=False,
                 dry_run=True, name="strip"), tmp_path, scanner=scanner)
    failed = kinds(events, "failed")
    assert len(failed) == 1, [e.text for e in events]
    said = failed[0].text
    assert f"{folder} is in use by tools/scan_roll.py (" in said
    assert f"pid {other.pid}" in said
    assert said.endswith("nothing was moved or scanned")
    assert not [op for op, _data in device.sent if op == SCSI_SLIDE]
    assert device.position == 3
    assert not folder.exists()


def test_a_rolls_folder_is_held_from_its_seek_until_the_session_closes(
        tmp_path):
    """Its last frames are filed after the roll has ended, so it is held
    then too -- and let go once they are in."""
    import os

    from rps7200 import locks

    scanner = FakeScanner(frames=2)
    s = ScanSession(root=str(tmp_path), rolls=str(tmp_path / "rolls"),
                    open_scanner=lambda: scanner, verbose=False)
    folder = tmp_path / "rolls" / "strip"
    s.start()
    s.submit(Roll(frames=2, resolution=300, infrared=False, name="strip"))
    events = []
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not kinds(events, "finished"):
        events.extend(s.poll())
        time.sleep(0.01)
    assert kinds(events, "finished"), [e.text for e in events]
    assert [h["pid"] for h in locks.holders(locks.ROLL, folder)] \
        == [os.getpid()]
    s.shutdown()
    while time.monotonic() < deadline and not kinds(events, "closed"):
        events.extend(s.poll())
        time.sleep(0.01)
    s.join(timeout=5)
    assert locks.holders(locks.ROLL, folder) == []


def test_a_roll_lock_not_taken_is_asked_for_again_by_the_next_roll(
        tmp_path, monkeypatch):
    """A lock folder that could not be written for a moment: the roll goes
    ahead unguarded and says so, and the next roll into the same folder
    takes the lock -- it used to stand in for one taken, for as long as the
    window lived."""
    import os

    from rps7200 import locks

    s = ScanSession(root=str(tmp_path), rolls=str(tmp_path / "rolls"),
                    open_scanner=FakeScanner, verbose=False)
    folder = tmp_path / "rolls" / "strip"
    real_take = locks.take
    asked = []

    def take(kind, path, **kw):
        asked.append(path)
        if len(asked) == 1:
            return locks.Held(None, True, note=f"could not take the roll "
                                               f"lock on {path} (disk busy)")
        return real_take(kind, path, **kw)

    monkeypatch.setattr(locks, "take", take)
    s._hold_roll(folder)
    assert locks.holders(locks.ROLL, folder) == []
    said = [e.text for e in s.poll() if e.kind == "log"]
    assert any("disk busy" in text and "unguarded" in text for text in said)
    s._hold_roll(folder)
    assert len(asked) == 2, "the next roll did not ask again"
    assert [h["pid"] for h in locks.holders(locks.ROLL, folder)] \
        == [os.getpid()]
    s._hold_roll(folder)
    assert len(asked) == 2, "a lock held is asked for once"
    s._let_go_of_rolls()
    assert locks.holders(locks.ROLL, folder) == []


def test_compaction_at_close_waits_for_before_compact(tmp_path, monkeypatch):
    """The window's Save all, Export and full-resolution reader hold an
    entry's files open, and on Windows a compaction swapping them gave up
    part way. The session waits for them -- through `before_compact`, with
    the device already closed -- and only then compacts."""
    order = []
    real_compact = library.compact

    def compact(entry):
        order.append(("compact", library.wants_compacting(entry)))
        return real_compact(entry)

    monkeypatch.setattr(library, "compact", compact)
    scanner = FakeScanner()

    def readers_still_reading():
        calls = len(scanner.calls)
        order.append(("before", scanner.t.closed, scanner.closed))
        time.sleep(0.3)
        assert len(scanner.calls) == calls, "a command was sent meanwhile"
        order.append(("waited", time.monotonic()))

    def wait_for_readers(session, _scanner):
        session.before_compact = readers_still_reading

    _s, _scanner, events = run(Scan(resolution=300, infrared=False),
                               tmp_path, scanner=scanner,
                               extra=wait_for_readers)
    assert [step[0] for step in order] == ["before", "waited", "compact"]
    assert order[0][1:] == (True, True), "waited with the device open"
    assert order[2][1] is True, "compacted before it waited"
    assert not any(library.wants_compacting(p.parent)
                   for p in tmp_path.glob("*/scan.json")), "not compacted"


# -- the roll's own files reach the disk whole ----------------------------------


def test_a_manifest_is_written_under_this_writers_own_name(tmp_path,
                                                           monkeypatch):
    """The roll's writer thread, the scanning thread, the window and
    `tools/scan_roll.py` all write these: under one shared name, two at
    once truncated and interleaved each other's text before either renamed
    it in."""
    seen = []
    real = session._replace

    def replace(temp, path):
        seen.append(Path(temp).name)
        return real(temp, path)

    monkeypatch.setattr(session, "_replace", replace)
    path = tmp_path / "roll.json"
    session.write_manifest(path, {"frames": [1]})
    session.write_manifest(path, {"frames": [1, 2]}, keep_previous=True)
    mine = f"{os.getpid()}-{threading.get_ident()}"
    assert seen == [f".roll.json.{mine}.part", f".roll.json.bak.{mine}.part",
                    f".roll.json.{mine}.part"]


def test_two_threads_writing_one_manifest_leave_it_whole(tmp_path):
    path = tmp_path / "roll.json"
    errors = []

    def write(n):
        try:
            for i in range(30):
                session.write_manifest(path, {"writer": n, "i": i,
                                              "frames": list(range(50))})
        except PermissionError:
            pass                      # a rename refused for good: said by it
        except Exception as exc:      # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(n,)) for n in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert json.loads(path.read_text(encoding="utf-8"))["frames"] == list(
        range(50))
    assert [p.name for p in tmp_path.iterdir() if ".part" in p.name] == []


def test_a_manifest_and_its_kept_copies_reach_the_disk_before_their_names(
        tmp_path, monkeypatch):
    """`.bak` and `.legacy` are copied and renamed in: unsynced, a power cut
    could keep the name over a file of zeros -- and `.legacy` is made once,
    never again. Each rename then syncs the folder."""
    order = []
    real_sync, real_dir = library._sync, library._sync_dir
    monkeypatch.setattr(library, "_sync", lambda p: (
        order.append(("sync", Path(p).name)), real_sync(p))[-1])
    monkeypatch.setattr(library, "_sync_dir", lambda p: (
        order.append(("folder", Path(p))), real_dir(p))[-1])
    path = tmp_path / "roll.json"
    session.write_manifest(path, {"frames": [{"number": 1}]})
    order.clear()
    session.write_manifest(path, {"frames": [{"number": 2}]},
                           keep_previous=True)
    mine = f"{os.getpid()}-{threading.get_ident()}"
    assert order == [("sync", f".roll.json.bak.{mine}.part"),
                     ("folder", tmp_path), ("folder", tmp_path)]
    order.clear()
    session.keep_first_numbering(path, {"frames": [{"number": 1}]})
    assert order == [("sync", ".roll.json.legacy.part"), ("folder", tmp_path)]
    assert (tmp_path / "roll.json.legacy").exists()


# -- a walk's prescans, read one at a time ------------------------------------


def _read_each(folder, lib, manifest):
    said = []
    out = {}
    for number, path, record in session.walked_prescans(folder, manifest,
                                                        say=said.append):
        out[number] = session.read_walk_prescan(
            manifest, number, path, record, library_root=lib, say=said.append)
    return out, said


def test_a_whole_walk_prescan_is_read_as_its_file(tmp_path):
    folder, lib, manifest, pictures = walk_with_entries(tmp_path, rotation=90)
    out, said = _read_each(folder, lib, manifest)
    assert said == []
    for n, picture in pictures.items():
        assert np.array_equal(out[n], picture), "un-turned into the film's way"


@pytest.mark.parametrize("damage", ["cut short", "zeroed", "header only"])
def test_a_damaged_walk_prescan_is_made_again_from_its_own_entry(
        tmp_path, damage):
    """One file a power cut cut short refused the whole walk; a black one --
    an uncompressed file whose strips came back as zeros, which reads
    cleanly -- was held to as a reference. Each is made again from the
    walk's own entry for it, corrected with today's code, and said."""
    folder, lib, manifest, pictures = walk_with_entries(tmp_path,
                                                         rotation=90)
    path = folder / "prescan02.tif"
    whole = path.read_bytes()
    if damage == "cut short":
        path.write_bytes(whole[:len(whole) // 2])
    elif damage == "header only":
        path.write_bytes(whole[:8])
    else:
        tiff.write(str(path), np.zeros_like(tiff.read(str(path))),
                   compress=False)
    out, said = _read_each(folder, lib, manifest)
    assert np.array_equal(out[1], pictures[1])
    assert np.array_equal(out[2], pictures[2])
    entry = manifest["frames"][1]["prescan_entry"]
    [line] = said
    assert line.startswith("frame 2: ") and "prescan02.tif" in line
    assert line.endswith(f"shown from its library entry {entry}, corrected "
                         "with today's code")


def test_a_rebuilt_black_and_white_prescan_keeps_its_channels_as_written(
        tmp_path):
    """No walk prescan is written one channel, black and white included.
    Made one channel, the rebuilt frame was a 2-D picture among 3-D ones,
    which the edge reader passes over."""
    folder, lib, manifest, pictures = walk_with_entries(tmp_path, film="bw")
    written = tiff.read(str(folder / "prescan01.tif"))
    tiff.write(str(folder / "prescan01.tif"), np.zeros_like(written))
    out, _said = _read_each(folder, lib, manifest)
    assert out[1].shape == written.shape == out[2].shape
    assert np.array_equal(out[1], pictures[1])
    entry = lib / manifest["frames"][0]["prescan_entry"]
    assert np.array_equal(out[1], library.corrected(entry)[0])


def test_a_black_and_white_walk_s_rebuilt_prescan_is_shaped_as_its_sibling(
        tmp_path):
    """Through the session's own walk, not a fixture's idea of one: a black
    and white strip walked with the fakes, one prescan cut short, and the
    picture made again from its entry is the shape the intact file reads."""
    from rps7200.shading import ShadingReference

    class Referenced(FakeScanner):
        """Each pass filed with a reference, so its entry corrects."""

        def capture_record(self):
            return {"reference": ShadingReference(
                ref={c: np.full(36, 30000.0) for c in range(3)},
                mean={c: 30000.0 for c in range(3)}, pixels_per_line=36),
                "ccd_mask": None, "raw": None, "raw_layout": None}

    run(Roll(frames=2, dry_run=True, film="bw", infrared=False, name="bw"),
        tmp_path, scanner=Referenced())
    folder = tmp_path / "rolls" / "bw"
    manifest = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    assert session.wants_mono(None, manifest["settings"]["film"])
    whole = (folder / "prescan01.tif").read_bytes()
    (folder / "prescan01.tif").write_bytes(whole[:len(whole) // 2])
    out, said = _read_each(folder, tmp_path, manifest)
    assert any("shown from its library entry" in line for line in said), said
    assert out[1] is not None and out[2] is not None
    assert out[1].shape == out[2].shape and out[1].ndim == 3


@pytest.mark.parametrize("why", ["before", "another frame", "not filed",
                                 "no library", "uncorrected"])
def test_a_damaged_prescan_with_no_entry_of_its_own_is_left_off(
        tmp_path, why):
    """Never another picture in its place: only the walk's own entry for
    that prescan -- not the one from before an aim, not another frame's --
    and only corrected."""
    tags = {2: ["before"]} if why == "before" else None
    members = ({2: session.roll_membership("walk", 5, "prescan", "x")}
               if why == "another frame" else None)
    folder, lib, manifest, _pictures = walk_with_entries(
        tmp_path, tags=tags, members=members,
        bare=(2,) if why == "uncorrected" else ())
    record = manifest["frames"][1]
    if why == "not filed":
        record["prescan_entry"] = None
    (folder / "prescan02.tif").write_bytes(b"II*\x00")
    if why == "no library":
        lib = None
    out, said = _read_each(folder, lib, manifest)
    assert out[2] is None and out[1] is not None
    [line] = said
    assert line.startswith("frame 2: ") and "left off the sheet" in line
    assert {"before": "the picture from before an aim",
            "another frame": "is not a prescan of frame 2",
            "not filed": "names no library entry",
            "no library": "no library to make it again from",
            "uncorrected": "cannot be corrected (no reference)"}[why] in line


def test_a_walk_from_before_checksums_is_read_unchecked(tmp_path):
    folder, lib, manifest, _pictures = walk_with_entries(tmp_path)
    for record in manifest["frames"]:
        del record["prescan_sha256"]
    black = np.zeros_like(tiff.read(str(folder / "prescan01.tif")))
    tiff.write(str(folder / "prescan01.tif"), black)
    out, said = _read_each(folder, lib, manifest)
    assert said == [] and np.array_equal(out[1], black)


def test_a_prescan_the_walk_lists_and_the_folder_lacks_is_said(tmp_path):
    """It was skipped without a word: the frame was simply not on the
    reopened sheet."""
    folder, lib, manifest, _pictures = walk_with_entries(tmp_path)
    (folder / "prescan01.tif").unlink()
    said = []
    walked = session.walked_prescans(folder, manifest, say=said.append)
    assert [n for n, _p, _r in walked] == [2]
    assert said == [f"frame 1 is not on the sheet: prescan01.tif is listed "
                    f"by the walk but is not in {folder}"]


# -- a pass kept outside the library is found again -----------------------------


def test_a_pass_the_library_refused_says_where_it_came_from_and_is_pointed_to(
        tmp_path):
    """Kept beside an output folder, it was found by nothing but the log
    line that named it. Its entry names the library that refused it, and
    the library keeps a pointer to it."""
    lib = tmp_path / "library"
    lib.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    kept, refused = session.keep_unfiled(
        np.zeros((8, 16, 3), np.uint8),
        {"resolution_dpi": 300, "channels": 3, "width": 16, "height": 8,
         "depth": 8},
        near=[out], refused_by=lib, why="the disk is full")
    assert kept is not None and kept.parent == out / "unfiled"
    record = json.loads((kept / "scan.json").read_text(encoding="utf-8"))
    assert record["extra"]["unfiled_from"] == str(lib.resolve())
    [pointer] = (lib / library.UNFILED_ELSEWHERE).iterdir()
    said = json.loads(pointer.read_text(encoding="utf-8"))
    assert said["library"] == str(lib.resolve())
    assert said["kept"] == str(kept.resolve())
    assert said["why"] == "the disk is full"
    # None of it is an entry of the library's, nor a failure record.
    assert library.entries(lib) == []
    assert library.failure_records(lib) == []
    assert not any("unfiled-elsewhere" in p for p in library.verify(lib))


def test_a_pointer_the_library_will_not_take_goes_to_the_temporary_folder(
        tmp_path):
    import tempfile

    blocker = tmp_path / "not-a-folder"
    blocker.write_text("", encoding="utf-8")
    lib = blocker / "library"
    kept, _refused = session.keep_unfiled(
        np.zeros((8, 16, 3), np.uint8),
        {"resolution_dpi": 300, "channels": 3, "width": 16, "height": 8,
         "depth": 8},
        refused_by=lib, why="no such folder")
    assert kept is not None
    pointers = Path(tempfile.gettempdir()) / library.UNFILED_POINTERS_TEMP
    [pointer] = pointers.iterdir()
    assert json.loads(pointer.read_text(encoding="utf-8"))["kept"] == str(
        kept.resolve())


def test_a_frame_names_the_code_behind_its_reading_and_only_then(monkeypatch):
    """`read_by` goes on a frame record whose marks hold a reading -- what
    the walk took of it, or an aim's judgment -- and on no other; and a
    name that cannot be had costs a field, never the frame."""
    named = {"driver_source_sha256": "d", "edge_reader_sha256": "e"}
    monkeypatch.setattr(library, "code_identity", lambda: dict(named))
    assert session.frame_read_by({"base": {"reader": "frame_edges"}}) == {
        "read_by": named}
    assert session.frame_read_by({"correction": {"outcome": "in_place"}}) == {
        "read_by": named}
    for none in (None, {}, {"contrast": 0.3}, {"approved": {"moves": 1}},
                 {"base": None}, ["base"]):
        assert session.frame_read_by(none) == {}, none

    def broken():
        raise OSError("gone")

    monkeypatch.setattr(library, "code_identity", broken)
    assert session.frame_read_by({"base": {}}) == {
        "read_by": {"record_error": "OSError: gone"}}


# -- halting: Quit, Ctrl-C, SIGTERM and a closed console ---------------------


def _until_closed(s, timeout=60.0):
    """Every event until the session has closed."""
    events = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events.extend(s.poll())
        if any(e.kind == "closed" for e in events):
            break
        time.sleep(0.01)
    s.join(timeout=5.0)
    events.extend(s.poll())
    assert any(e.kind == "closed" for e in events), "session never closed"
    return events


def _taken_then(s, ask):
    """Call ``ask`` the moment the worker has taken a Scan off its queue --
    before the job's start, where it used to clear the stop."""
    real_get = s._jobs.get

    def get(block=True, timeout=None):
        job = real_get(block, timeout)
        if block and isinstance(job, Scan):
            ask()
        return job

    s._jobs.get = get


@pytest.mark.parametrize("ask", ["halt", "request_stop"])
def test_a_stop_asked_as_the_worker_takes_a_job_is_not_cleared(tmp_path, ask):
    """B1-T1: a Scan handed over and, in the same tick, a closed console. The
    worker had taken it and then cleared the stop as the job started, so it
    ran to its end and was filed -- a pass Windows was about to cut off part
    way. Quit's halt is never cleared, and an ordinary stop asked of a job
    the worker holds is kept as it starts."""
    scanner = FakeScanner()
    s = ScanSession(root=str(tmp_path), rolls=str(tmp_path / "rolls"),
                    open_scanner=lambda: scanner, verbose=False)
    _taken_then(s, getattr(s, ask))
    s.start()
    s.submit(Scan(resolution=600))
    s.shutdown()
    events = _until_closed(s)
    assert [c for c in scanner.calls if c[0] == "scan"] == []
    assert [e.text for e in kinds(events, "finished")] == ["stopped"]
    assert not kinds(events, "failed")
    assert library.entries(tmp_path) == []


def test_a_halt_during_preflight_stops_before_the_first_command(
        tmp_path, monkeypatch):
    """The preflight looks at folders and disks, and sends nothing. A halt
    asked while it ran was let through to the whole job: the stop was asked
    only before it. Now asked again after it, and after the reference check,
    immediately before every job's first command."""
    from conftest import scanner_at_commands

    from rps7200 import protocol as p

    scanner, device = scanner_at_commands(monkeypatch)
    s = ScanSession(root=str(tmp_path), rolls=str(tmp_path / "rolls"),
                    open_scanner=lambda: scanner, verbose=False)
    entered, release = threading.Event(), threading.Event()
    preflight = s._preflight
    sent: list = []

    def held(job):
        sent.append(len(device.sent))
        entered.set()
        assert release.wait(10)
        return preflight(job)

    monkeypatch.setattr(s, "_preflight", held)
    s.start()
    s.submit(Prescan())
    assert entered.wait(10)
    s.halt()
    release.set()
    s.shutdown()
    events = _until_closed(s)
    assert [e.text for e in kinds(events, "finished")] == ["stopped"]
    assert not kinds(events, "failed")
    status = {p.SCSI_READ_STATE, p.SCSI_READ_GAIN_OFFSET,
              p.SCSI_REQUEST_SENSE, p.SCSI_TEST_UNIT_READY}
    assert {op for op, _ in device.sent[sent[0]:]} <= status
    assert device.passes == []


def test_a_halt_carries_into_every_job_after_it(tmp_path):
    """`request_stop` alone does not carry (`test_a_stop_does_not_carry_into
    _the_next_job`), but Quit's halt does: nothing starts after it, a job
    handed over later included."""
    scanner = FakeScanner()
    s = ScanSession(root=str(tmp_path), rolls=str(tmp_path / "rolls"),
                    open_scanner=lambda: scanner, verbose=False)
    s.start()
    s.halt()
    for job in (Prescan(), Move(frames=1), Calibrate(mode="measure")):
        s.submit(job)
    s.shutdown()
    events = _until_closed(s)
    assert scanner.calls == [] and scanner.moves == []
    assert [e.text for e in kinds(events, "finished")] == ["stopped"] * 3
    assert s.halted


def test_a_stop_during_metering_starts_no_further_probe(tmp_path,
                                                         monkeypatch):
    """On the driver itself: a Stop pressed in the first metering probe was
    followed by the second probe and the whole pass -- five minutes at 7200
    dpi -- where the button says "finishes this pass". The probe in flight
    is read to its last line; nothing is started after it, and the job ends
    stopped, not failed, the device not suspect."""
    from conftest import scanner_at_commands

    holder: dict = {}

    def stop_in_the_probe(device, n):
        if n == 2 and not holder.get("asked"):
            holder["asked"] = True
            holder["s"].request_stop()

    scanner, device = scanner_at_commands(monkeypatch,
                                          on_read=stop_in_the_probe)
    s = ScanSession(root=str(tmp_path), rolls=str(tmp_path / "rolls"),
                    open_scanner=lambda: scanner, verbose=False)
    holder["s"] = s
    s.start()
    s.submit(Scan(resolution=300, infrared=False, auto_exposure=True,
                  shading=False))
    s.shutdown()
    events = _until_closed(s)
    assert holder.get("asked"), "the probe was never read"
    assert len(device.passes) == 1, "a second probe or the pass was started"
    assert [e.text for e in kinds(events, "finished")] == [
        "stopped during metering, before probe round 2"]
    assert not kinds(events, "failed")
    assert scanner.suspect is None
    assert [e for e in library.entries(tmp_path)
            if "debug" not in e["tags"]] == []


def test_a_stop_after_metering_takes_no_pass(tmp_path, monkeypatch):
    """Between the probes and the pass the driver raises `StoppedBeforePass`,
    which reached the window as a failure -- red, and spoken of as a job
    that broke. It is a stop, as asked."""
    from conftest import scanner_at_commands

    scanner, device = scanner_at_commands(monkeypatch)
    s = ScanSession(root=str(tmp_path), rolls=str(tmp_path / "rolls"),
                    open_scanner=lambda: scanner, verbose=False)
    meter = scanner.auto_exposure

    def metered(*a, **kw):
        scales = meter(*a, **kw)
        s.request_stop()
        return scales

    monkeypatch.setattr(scanner, "auto_exposure", metered)
    s.start()
    s.submit(Scan(resolution=300, infrared=False, auto_exposure=True,
                  shading=False))
    s.shutdown()
    events = _until_closed(s)
    probes = len(scanner.last_metering["rounds"])
    assert len(device.passes) == probes, "the pass was started"
    assert [e.text for e in kinds(events, "finished")] == [
        "stopped before the pass, after metering"]
    assert not kinds(events, "failed")
    assert library.entries(tmp_path) == []


def test_a_console_close_after_a_slides_rgbi_pass_takes_no_colour_pass(
        tmp_path):
    """Windows ends the process about 5 s after its console closes, so a
    slide's colour pass started then is cut off part way. Not started: the
    RGBI pass is shown and filed with its raw bytes, as an infrared pass,
    with no copy in the output folder, and the log says why."""
    from test_colour_pass import SingleSlideScanner, entries_tagged

    from rps7200.direct import FILM_POSITIVE

    holder: dict = {}

    class Closing(SingleSlideScanner):
        def scan(self, resolution=1800, infrared=True, **kw):
            if infrared:
                holder["s"].halt(console_closed=True)
            return super().scan(resolution=resolution, infrared=infrared,
                                **kw)

    scanner = Closing()
    out = tmp_path / "out"
    out.mkdir()

    def remember(s, _scanner):
        holder["s"] = s
        s.out_dir = out

    _s, _scanner, events = run(Scan(resolution=600, infrared=True,
                                    film=FILM_POSITIVE),
                               tmp_path, scanner=scanner, extra=remember)
    assert [c[2] for c in scanner.calls if c[0] == "scan"] == [True]
    assert entries_tagged(tmp_path, "colour pass") == []
    (ir,) = entries_tagged(tmp_path, "infrared pass")
    assert library.read_raw(library.entry_path(tmp_path, ir)) == b"\x04" * 32
    assert ScanSession.NO_COLOUR_PASS in ir["film"]["notes"]
    assert list(out.iterdir()) == []
    said = [e.text for e in kinds(events, "log")
            if "RGB pass is not taken" in e.text]
    assert len(said) == 1
    (result,) = [e.result for e in kinds(events, "result")]
    assert result.meta["channels"] == 4
    assert [e.text for e in kinds(events, "finished")] == [
        f"filed the RGBI pass alone -- {ScanSession.NO_COLOUR_PASS}"]


def test_an_ordinary_stop_still_takes_a_slides_colour_pass(tmp_path):
    """A Stop, Quit, Ctrl-C or SIGTERM has no deadline: the colour pass
    finishes the frame half scanned, as `colour_pass` argues, and both
    passes are filed as a pair."""
    from test_colour_pass import SingleSlideScanner, entries_tagged

    from rps7200.direct import FILM_POSITIVE

    holder: dict = {}

    class Stopping(SingleSlideScanner):
        def scan(self, resolution=1800, infrared=True, **kw):
            if infrared:
                holder["s"].halt()
            return super().scan(resolution=resolution, infrared=infrared,
                                **kw)

    scanner = Stopping()
    run(Scan(resolution=600, infrared=True, film=FILM_POSITIVE), tmp_path,
        scanner=scanner, extra=lambda s, _: holder.update(s=s))
    assert [c[2] for c in scanner.calls if c[0] == "scan"] == [True, False]
    assert len(entries_tagged(tmp_path, "infrared pass")) == 1
    assert len(entries_tagged(tmp_path, "colour pass")) == 1


# -- the jobs the session holds ------------------------------------------------


def test_outstanding_counts_a_job_from_submit_to_its_end(tmp_path):
    """The window kept its own copy of the queue from the events, a tick
    behind the worker; the session's is exact. A job counts from its
    hand-over, while it runs, and not once it is over; a stop lets go of
    the jobs it drops."""
    seen = []
    holder: dict = {}

    class Watching(FakeScanner):
        def scan(self, resolution=1800, infrared=True, **kw):
            seen.append(holder["s"].outstanding())
            return super().scan(resolution=resolution, infrared=infrared,
                                **kw)

    scanner = Watching()
    s = ScanSession(root=str(tmp_path), rolls=str(tmp_path / "rolls"),
                    open_scanner=lambda: scanner, verbose=False)
    holder["s"] = s
    first, second = Scan(resolution=600), Scan(resolution=600)
    s.submit(first)
    s.submit(second)
    assert s.outstanding() == (first, second)
    assert s.outstanding()[0] is first, "by identity: equal jobs are two"
    s.request_stop()
    assert s.outstanding() == ()
    job = Scan(resolution=600)
    s.submit(job)
    assert s.outstanding() == (job,)
    s.start()
    s.shutdown()
    events = _until_closed(s)
    assert len(seen) == 1 and seen[0][0] is job
    assert s.outstanding() == ()
    # And the busy state names the job it started.
    (busy,) = [e for e in kinds(events, "state") if e.busy]
    assert busy.job is job


def test_outstanding_is_empty_once_the_scanner_could_not_open(tmp_path):
    """Nothing will run a job then, and a job still counted kept the window
    "working" -- refusing Open, Rename and Delete of rolls -- until it quit.
    A job handed over after the close is not taken at all."""
    def no_scanner():
        raise OSError("no scanner on the bus")

    s = ScanSession(root=str(tmp_path), rolls=str(tmp_path / "rolls"),
                    open_scanner=no_scanner, verbose=False)
    s.submit(Prescan())
    s.start()
    events = _until_closed(s)
    assert kinds(events, "failed")
    assert s.outstanding() == ()
    s.submit(Scan(resolution=600))
    assert s.outstanding() == ()


def test_outstanding_is_empty_after_shutdown(tmp_path):
    """A job handed over behind the close is dropped with it."""
    scanner = FakeScanner()
    s = ScanSession(root=str(tmp_path), rolls=str(tmp_path / "rolls"),
                    open_scanner=lambda: scanner, verbose=False)
    s.shutdown()
    s.submit(Scan(resolution=600))
    s.start()
    _until_closed(s)
    assert s.outstanding() == ()
    assert [c for c in scanner.calls if c[0] == "scan"] == []


def test_a_job_let_go_of_unstarted_is_said_as_dropped(tmp_path):
    """A job a stop dropped before the worker took it was said only as a
    count in the log -- no state, no end -- and the window kept what it set
    as it handed the job over: a calibration counted as done, a walk as
    walking. Each is said now, by identity, whether a stop dropped it or the
    session closing did, and never as started."""
    scanner = FakeScanner()
    s = ScanSession(root=str(tmp_path), rolls=str(tmp_path / "rolls"),
                    open_scanner=lambda: scanner, verbose=False)
    calibrate, walk = Calibrate(), Roll(frames=2, dry_run=True)
    s.submit(calibrate)
    s.submit(walk)
    s.request_stop()
    dropped = kinds(s.poll(), "dropped")
    assert [e.job for e in dropped] == [calibrate, walk]
    assert all(e.job is job for e, job in zip(dropped, (calibrate, walk),
                                              strict=True))
    assert all(e.text for e in dropped)
    # And one handed over behind the close, dropped as the worker ends:
    # before "closed", which is the window's last word from the session.
    late = Scan(resolution=600)
    s.shutdown()
    s.submit(late)
    s.start()
    events = _until_closed(s)
    (gone,) = kinds(events, "dropped")
    assert gone.job is late
    assert events.index(gone) < events.index(kinds(events, "closed")[0])
    assert not [e for e in kinds(events, "state") if e.busy]


def test_jobs_handed_over_to_a_scanner_that_never_opened_are_dropped(
        tmp_path):
    """Said as dropped after the failure, so the window can undo what it
    set for each; nothing ran them."""
    def no_scanner():
        raise OSError("no scanner on the bus")

    s = ScanSession(root=str(tmp_path), rolls=str(tmp_path / "rolls"),
                    open_scanner=no_scanner, verbose=False)
    calibrate = Calibrate()
    s.submit(calibrate)
    s.start()
    events = _until_closed(s)
    (failed,) = kinds(events, "failed")
    (gone,) = kinds(events, "dropped")
    assert gone.job is calibrate
    assert events.index(failed) < events.index(gone)


# -- what a job writes, fixed as it starts ------------------------------------


def test_a_job_keeps_its_output_however_it_is_changed_while_it_runs(
        tmp_path):
    """GUI1-12: the session read the output folder, format and JPEG quality
    live as each frame was filed, so a folder chosen during a roll took its
    later frames there, past the checks made before the film moved. Taken
    as the job starts, every frame's copy goes where the first did, as it
    did; the change applies from the next job."""
    first, later = tmp_path / "first", tmp_path / "later"
    first.mkdir()
    later.mkdir()
    holder: dict = {}

    def change(index):
        if index == 1:
            s = holder["s"]
            s.out_dir, s.out_format, s.jpeg_quality = later, "jpeg", 70

    scanner = FakeScanner(frames=3, on_yield=change)

    def remember(s, _scanner):
        holder["s"] = s
        s.out_dir = first

    s, _, _events = run(Roll(frames=3, resolution=600, infrared=False,
                             name="kept"), tmp_path, scanner=scanner,
                        extra=remember)
    copies = sorted(p.name for p in first.iterdir())
    assert len(copies) == 3 and all(n.endswith(".tif") for n in copies)
    assert all("_frame" in n for n in copies)
    assert list(later.iterdir()) == []
    assert s.job_output() is None
    assert s.output_now() == session.Output(later, "jpeg", 70)


def test_a_job_queued_behind_takes_the_output_as_it_starts(tmp_path):
    """Not as it is handed over: a job waits only behind a calibration not
    begun, and takes what is set by the time it starts."""
    out = tmp_path / "out"
    out.mkdir()
    holder: dict = {}

    class Slow(FakeScanner):
        def ensure_shading(self, path, reuse=False, skip=False):
            holder["s"].out_dir = out
            holder["s"].out_format = "jpeg"
            return super().ensure_shading(path, reuse, skip)

    scanner = Slow()

    def remember(s, _scanner):
        holder["s"] = s
        s.submit(Calibrate(mode="measure"))

    run(Scan(resolution=600, infrared=False), tmp_path, scanner=scanner,
        extra=remember)
    assert [p.suffix for p in out.iterdir()] == [".jpg"]


def test_the_preflight_checks_the_folder_the_job_writes_to(tmp_path):
    """The folder taken as the job starts is the one its checks look at, and
    so the one it writes to: a folder gone since is refused before anything
    moves, however the window's setting has moved on meanwhile."""
    gone = tmp_path / "gone"
    s = ScanSession(root=str(tmp_path), rolls=str(tmp_path / "rolls"),
                    open_scanner=FakeScanner, verbose=False)
    s.out_dir = gone
    s._output = s.output_now()
    s.out_dir = tmp_path
    with pytest.raises(storage.DriveMissing, match="gone"):
        s._preflight(Scan(resolution=600))
    s._output = None
    s._preflight(Scan(resolution=600))


def test_a_turn_and_a_mirror_are_one_change(tmp_path):
    """Set one after the other from the window's thread while a roll filed
    a frame on the scanner's, the frame could get the new turn and the old
    mirror. `arrange` sets both in one assignment, and the session reads
    them in one look; `rotation` and `flip` still read and set each."""
    s = ScanSession(root=str(tmp_path), rolls=str(tmp_path / "rolls"),
                    open_scanner=FakeScanner, verbose=False)
    s.arrange(90, True)
    assert (s.rotation, s.flip) == (90, True)
    assert s._orientation_for(3, "scan") == (90, True)
    s.rotation = 180
    assert s._arrangement == (180, True)
    s.flip = False
    assert s._arrangement == (180, False)
    assert s._orientation_for(3, "prescan") == (180, False)
    # The roll's own record of them is one pair as well: the manifest's
    # top level and its settings agree, whatever the window does between.
    run(Roll(frames=1, resolution=600, infrared=False, name="turned"),
        tmp_path, extra=lambda s, _: s.arrange(270, True))
    manifest = json.loads((tmp_path / "rolls" / "turned" / "roll.json")
                          .read_text(encoding="utf-8"))
    assert (manifest["rotation"], manifest["flipped"]) == (270, True)
    assert (manifest["settings"]["rotation"],
            manifest["settings"]["flipped"]) == (270, True)


# -- one walk, its id, and what a walk that took nothing leaves -------------
#
# FOUND-2: a fresh walk that ended before its first frame replaced
# survey.json with an empty walk, and the next attempt copied that over
# survey.json.bak -- the one list left of the old walk's prescans' entries.
# P22-b: the sheet's decisions are found by the walk's own id, written once,
# never by when survey.json was last written. P18-b: a walk added to keeps a
# frame's earlier prescan that no library entry holds.


def _walk_only(tmp_path, name="strip"):
    """A folder holding a walk and the copy of the walk before it, every
    prescan of both filed: what a fresh walk may walk again in place."""
    folder = tmp_path / "rolls" / name
    folder.mkdir(parents=True)
    for frames in (1, 2):
        session.write_manifest(folder / "survey.json", {
            "roll": name, "numbering": session.NUMBERING, "walk_id": "old",
            "frames": [{"number": n, "prescan": f"prescan{n:02d}.tif",
                        "prescan_entry": f"entry-{n}"}
                       for n in range(1, frames + 1)]}, keep_previous=True)
    return folder, {p.name: p.read_bytes() for p in folder.iterdir()}


class _NoFrame(FakeScanner):
    """A driver whose roll yields nothing: it found no picture at once."""

    def scan_roll(self, **kw):
        self.calls.append(("roll",))
        yield from ()


class _FaultFirst(FakeScanner):
    """A transport fault before the first frame."""

    def scan_roll(self, **kw):
        # A generator, as the driver's roll is: it raises at its first step.
        raise RuntimeError("the transport did not answer")
        yield


class _StopFirst(FakeScanner):
    """A Stop that lands after the session's check that follows the seek,
    which the driver honours before its first frame."""

    session = None

    def scan_roll(self, should_stop=None, **kw):
        self.session.request_stop()
        if should_stop is not None and should_stop():
            return
        yield from super().scan_roll(should_stop=should_stop, **kw)


@pytest.mark.parametrize("scanner", [_NoFrame, _FaultFirst, _StopFirst])
def test_a_walk_that_takes_no_frame_leaves_the_walk_before_it(tmp_path,
                                                              scanner):
    folder, before = _walk_only(tmp_path)

    def remember(s, double):
        double.session = s

    for _attempt in range(2):
        run(Roll(dry_run=True, name="strip"), tmp_path, scanner=scanner(),
            extra=remember)
        assert {p.name: p.read_bytes() for p in folder.iterdir()} == before
    # One that takes a frame writes it, and keeps the walk it replaces.
    run(Roll(frames=1, dry_run=True, name="strip"), tmp_path)
    survey = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    assert [f["number"] for f in survey["frames"]] == [1]
    assert survey["walk_id"] != "old"
    assert (folder / "survey.json.bak").read_bytes() == before["survey.json"]


def test_the_walk_a_fresh_one_replaces_is_retired_at_its_first_write(
        tmp_path):
    folder = tmp_path / "rolls" / "again"
    folder.mkdir(parents=True)
    path = folder / "survey.json"
    old_record = {"number": 1}
    old = session.RollManifest(path, {"roll": "the last walk",
                                      "frames": [old_record]})
    told = old.prescan_told(old_record, folder / "prescan01.tif")
    session.queued(lambda **job: None, told)

    def live(s, _):
        s._manifests[path.resolve()] = old

    s, _, _ = run(Roll(dry_run=True, name="again"), tmp_path,
                  scanner=_NoFrame(), extra=live)
    assert s._manifests[path.resolve()] is old, "put back as it was"
    told(_filing(session.FILED, entry=tmp_path / "late-entry",
                 written=(folder / "prescan01.tif",)))
    survey = json.loads(path.read_text(encoding="utf-8"))
    assert survey["roll"] == "the last walk" and "late-entry" in json.dumps(
        survey), "still the old walk's file, which its answer reached"

    later = old.prescan_told(old_record, folder / "prescan01.tif")
    session.queued(lambda **job: None, later)
    run(Roll(frames=1, dry_run=True, name="again"), tmp_path, extra=live)
    later(_filing(session.FILED, entry=tmp_path / "later-entry",
                  written=(folder / "prescan01.tif",)))
    survey = json.loads(path.read_text(encoding="utf-8"))
    assert survey.get("walk_id"), "the new walk's file"
    assert "later-entry" not in json.dumps(survey)


def test_a_walk_has_an_id_its_additions_keep(tmp_path):
    from conftest import StripScanner

    def survey_of(name="strip"):
        return json.loads((tmp_path / "rolls" / name / "survey.json")
                          .read_text(encoding="utf-8"))

    s, _, _ = run(Roll(frames=2, dry_run=True, name="strip"), tmp_path,
                  scanner=StripScanner(at=0))
    first = survey_of()["walk_id"]
    assert re.fullmatch(r"[0-9a-f]{32}", first)
    # The writer's late answers rewrite the file, never the id.
    live = s._manifests[(tmp_path / "rolls" / "strip" / "survey.json")
                        .resolve()]
    live.amend(live.data["frames"][0], prescan_entry="late")
    assert survey_of()["walk_id"] == first
    run(Roll(frames=1, start_at=3, dry_run=True, name="strip",
             extend_walk=True), tmp_path, scanner=StripScanner(at=2))
    assert survey_of()["walk_id"] == first, "added to, the same walk"
    run(Roll(frames=1, dry_run=True, name="other"), tmp_path,
        scanner=StripScanner(at=0))
    assert survey_of("other")["walk_id"] not in (first, None)
    # A roll's record has none: it is no walk.
    run(Roll(frames=1, resolution=600, infrared=False, name="strip"),
        tmp_path)
    assert "walk_id" not in json.loads(
        (tmp_path / "rolls" / "strip" / "roll.json").read_text(
            encoding="utf-8"))


def test_a_walk_from_before_ids_added_to_is_given_one(tmp_path):
    from conftest import StripScanner

    folder = tmp_path / "rolls" / "strip"
    folder.mkdir(parents=True)
    session.write_manifest(folder / "survey.json", {
        "roll": "strip", "numbering": session.NUMBERING, "frames": []})
    run(Roll(frames=1, dry_run=True, name="strip", extend_walk=True),
        tmp_path, scanner=StripScanner(at=0))
    survey = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    assert re.fullmatch(r"[0-9a-f]{32}", survey["walk_id"])


def test_a_walk_added_to_keeps_a_prescan_no_entry_holds(tmp_path):
    """P18-b: the one copy of a legacy walk's prescan was written over by a
    walk that took its frame again. Kept beside the new one now, and listed
    as a take with no entry, so Delete counts it."""
    from conftest import StripScanner

    folder = tmp_path / "rolls" / "strip"
    folder.mkdir(parents=True)
    for n in (1, 2, 3):
        tiff.write(str(folder / f"prescan{n:02d}.tif"),
                   np.full((4, 6, 3), 10 + n, np.uint8))
    old = (folder / "prescan03.tif").read_bytes()
    session.write_manifest(folder / "survey.json", {
        "roll": "strip", "numbering": session.NUMBERING,
        "frames": [{"number": n, "transport_position": n - 1,
                    "prescan": f"prescan{n:02d}.tif"} for n in (1, 2, 3)]})
    run(Roll(frames=1, start_at=3, dry_run=True, name="strip",
             extend_walk=True), tmp_path, scanner=StripScanner(at=2))
    survey = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    third = next(f for f in survey["frames"] if f["number"] == 3)
    assert third["prescan"] == "prescan03-2.tif"
    assert (folder / "prescan03.tif").read_bytes() == old
    kept = [t for t in third["takes"] if t.get("file") == "prescan03.tif"]
    assert kept and kept[0]["kind"] == "prescan" and "entry" not in kept[0]
    assert "replaced_utc" in kept[0]
    # Walked once more: the kept one stays kept, listed once, and the take
    # the library holds is written over as it always was -- the folder keeps
    # only the latest picture of a frame (Stefan, 2026-10-09).
    run(Roll(frames=1, start_at=3, dry_run=True, name="strip",
             extend_walk=True), tmp_path, scanner=StripScanner(at=2))
    survey = json.loads((folder / "survey.json").read_text(encoding="utf-8"))
    third = next(f for f in survey["frames"] if f["number"] == 3)
    assert third["prescan"] == "prescan03-2.tif"
    assert (folder / "prescan03.tif").read_bytes() == old
    assert [t.get("file") for t in third["takes"]].count("prescan03.tif") == 1


def test_a_take_from_before_done_existed_is_still_a_take():
    """P21-r1: `rolls/scan600*` -- the roll tool, 2026-09-21 -- name their
    entries with no `done`, which `scanned_frames` counts as done, and a
    retake dropped the earlier take."""
    assert session._takes_of({"number": 1, "entry": "library\\scan600-01",
                              "error": None}) == [{"entry": "scan600-01"}]
    assert session._takes_of({"number": 1, "entry": "x",
                              "error": "failed"}) == []
    assert session._takes_of({"number": 1, "entry": "x", "done": False}) == []


# -- a roll of strips ----------------------------------------------------------
#
# Stefan's call, 2026-10-10: a film cut into strips of six, walked one by one
# in order and named once. Each strip is a folder of its roll, exactly what a
# roll folder was; frame numbers count across the roll -- strip k's first is
# one past the frames the strips before it really had -- and never move once
# given. A folder without an index is a roll from before strips, as it was.


def _index_of(roll):
    return json.loads((roll / library.STRIPS_INDEX).read_text(encoding="utf-8"))


def _strip_roll(rolls, name="Gold200", strips=(), extra=()):
    """A roll of strips: ``strips`` is ``(strip, first, last, survey)``,
    survey a dict of the strip's survey.json or None for none."""
    roll = rolls / name
    roll.mkdir(parents=True)
    listed = []
    for strip, first, last, survey in strips:
        folder = roll / library.STRIP_FOLDER.format(strip)
        folder.mkdir()
        if survey is not None:
            session.write_manifest(folder / "survey.json", dict(
                {"roll": name, "numbering": session.NUMBERING,
                 "strip": {"number": strip, "first": first}}, **survey))
        listed.append({"strip": strip, "folder": folder.name, "first": first,
                       "last": last, "ends_at": None})
    session.write_manifest(roll / library.STRIPS_INDEX, {
        "kind": session.STRIPS_KIND, "version": 1, "roll": name,
        "strips": listed, "extra": list(extra)})
    return roll


def _walked(*places, **more):
    return dict({"frames": [{"number": n} for n in places]}, **more)


def test_the_next_strip_is_planned_from_what_each_strip_really_had(tmp_path):
    rolls = tmp_path / "rolls"
    # No roll yet.
    offer = session.plan_next(rolls / "New")
    assert (offer.next.number, offer.next.first) == (1, 1)
    assert offer.next.folder == rolls / "New" / "strip-01"
    assert offer.again is None and offer.said == "a new roll: frames from 1"

    # Strip 1 walked to the end: strip 2 from 7, closing 1-6.
    roll = _strip_roll(rolls, "A", [(1, 1, None, _walked(*range(1, 7),
                                                         strip_end=7))])
    offer = session.plan_next(roll)
    assert (offer.next.number, offer.next.first) == (2, 7)
    assert offer.again is None
    closes = offer.next.closes
    assert (closes.strip, closes.first, closes.count, closes.last) == (
        1, 1, 6, 6)
    assert closes.how == "walked to the end of the strip"
    assert offer.said == "A: strip 2 next, numbered from 7"

    # Stopped after frame 4: the next strip from 5, and strip 1 offered.
    roll = _strip_roll(rolls, "B", [(1, 1, None, _walked(
        1, 2, 3, 4, stopped="stopped after frame 4, as asked"))])
    offer = session.plan_next(roll)
    assert (offer.next.number, offer.next.first) == (2, 5)
    assert offer.again is not None and offer.again.count == 4
    assert "strip 1's walk stopped after frame 4" in offer.said
    assert "starts strip 2 from 5" in offer.said

    # Claimed and nothing recorded: strip 1 again, nothing given under it.
    roll = _strip_roll(rolls, "C", [(1, 1, None, None)])
    offer = session.plan_next(roll)
    assert (offer.next.number, offer.next.first, offer.next.reuse) == (
        1, 1, True)

    # A walk from frame 2 counts frame 1; a counter jump that left place 3
    # unrecorded counts it.
    roll = _strip_roll(rolls, "D", [(1, 1, 6, None), (2, 7, None, _walked(
        2, 4, 5, 6, strip_end=7))])
    assert session.plan_next(roll).next.first == 13

    # Strip ends here at 6, a stub walked at 7: from 7.
    roll = _strip_roll(rolls, "E", [(1, 1, None, _walked(*range(1, 8),
                                                         strip_end=8))])
    index = _index_of(roll)
    index["strips"][0]["ends_at"] = 6
    session.write_index(roll, index)
    offer = session.plan_next(roll)
    assert offer.next.first == 7 and offer.next.closes.count == 6
    # And a roll_number its stub carries is no number given.
    session.write_manifest(roll / "strip-01" / "survey.json", dict(
        _walked(*range(1, 8), strip_end=8),
        frames=[{"number": n, "roll_number": n} for n in range(1, 8)]))
    assert session.plan_next(roll).next.first == 7

    # An extra raises the roll's highest; so does a number a record carries
    # that the index lost.
    roll = _strip_roll(rolls, "F", [(1, 1, 6, None), (2, 7, None, _walked(
        *range(1, 7), strip_end=7))],
        extra=[{"strip": 1, "place": 7, "number": 13}])
    assert session.plan_next(roll).next.first == 14
    session.write_manifest(roll / "strip-01" / "roll.json", {
        "numbering": "strip", "frames": [{"number": 8, "roll_number": 20}]})
    assert session.plan_next(roll).next.first == 21


def test_a_short_last_strip_warns_the_next_may_be_another_film(tmp_path):
    """Six strips, the last of four: the film ended there. Only the full
    roll's warning (36 numbers given) was said, so with 34 frames the next
    film loaded was walked into the old roll by habit, labelled Gold200-35
    onward. A warning, as the full roll's is: short strips happen inside a
    roll too."""
    rolls = tmp_path / "rolls"
    short = ("Strip 6 had 4 frames, fewer than strip 5's 6: a short strip is "
             "usually the film's last. If this strip is from another film, "
             "Cancel and press New roll.")
    closed = [(n, 6 * n - 5, 6 * n, None) for n in range(1, 6)]
    roll = _strip_roll(rolls, "A", [*closed, (6, 31, None, _walked(
        *range(1, 5), strip_end=5))])
    plan = session.plan_next(roll).next
    assert (plan.number, plan.first) == (7, 35)
    assert plan.warnings == (short,)
    # Closed already -- a Delete closed it -- it says the same.
    roll = _strip_roll(rolls, "B", [*closed, (6, 31, 34, None)])
    assert session.plan_next(roll).next.warnings == (short,)
    # Six strips of six: the full roll's line alone.
    roll = _strip_roll(rolls, "C", [*closed, (6, 31, None, _walked(
        *range(1, 7), strip_end=7))])
    (warning,) = session.plan_next(roll).next.warnings
    assert warning.startswith("C has 36 frames in 6 strips already.")
    # A walk that stopped says nothing of how long its strip was.
    roll = _strip_roll(rolls, "D", [(1, 1, 6, None), (2, 7, 12, None),
                                    (3, 13, None, _walked(
                                        1, 2, 3, stopped="stopped, as asked"))])
    assert session.plan_next(roll).next.warnings == ()
    # Nor is a strip as long as the one before it short.
    roll = _strip_roll(rolls, "E", [(1, 1, 6, None), (2, 7, None, _walked(
        *range(1, 7), strip_end=7))])
    assert session.plan_next(roll).next.warnings == ()


def test_the_full_roll_warning_counts_one_strip_as_one(tmp_path):
    """"DemoAll has 38 frames in 1 strips already", which every demo user
    read on their second strip."""
    roll = _strip_roll(tmp_path / "rolls", "DemoAll", [(1, 1, 38, None)])
    (warning,) = session.plan_next(roll).next.warnings
    assert warning.startswith("DemoAll has 38 frames in 1 strip already. ")


def test_a_live_manifest_ahead_of_its_file_is_what_is_counted(tmp_path):
    rolls = tmp_path / "rolls"
    roll = _strip_roll(rolls, "G", [(1, 1, None, _walked(1, 2))])
    path = roll / "strip-01" / "survey.json"
    live = session.RollManifest(path, json.loads(path.read_text(
        encoding="utf-8")))
    live.record({"number": 3}, awaiting=True)     # still being filed
    live.data["frames"].append({"number": 4})
    found = {path.resolve(): live}
    count = session.strip_count(roll / "strip-01",
                                live=lambda p: found.get(Path(p).resolve()))
    assert count.count == 4
    assert session.strip_count(roll / "strip-01").count == 3, "the file"


def test_a_claim_writes_the_index_and_closes_the_strip_before(tmp_path):
    rolls = tmp_path / "rolls"
    plan = session.plan_next(rolls / "Gold 200").next
    place = session.claim(plan, root=rolls, typed="Gold 200 Oct")
    index = _index_of(rolls / "Gold 200")
    assert index["kind"] == session.STRIPS_KIND and index["roll"] == "Gold 200"
    assert index["typed_name"] == "Gold 200 Oct"
    assert [(s["strip"], s["first"], s["last"]) for s in index["strips"]] == [
        (1, 1, None)]
    assert (place.strip, place.first, place.numbers()) == (1, 1, "from 1")
    assert not (rolls / "Gold 200" / "strip-01").exists(), "made by the run"
    (rolls / "Gold 200" / "strip-01").mkdir()
    session.write_manifest(rolls / "Gold 200" / "strip-01" / "survey.json",
                           _walked(*range(1, 7), strip_end=7))
    session.claim(session.plan_next(rolls / "Gold 200").next, root=rolls)
    index = _index_of(rolls / "Gold 200")
    first, second = index["strips"]
    assert (first["last"], first["count_how"]) == (
        6, "walked to the end of the strip")
    assert first["closed"] and (second["strip"], second["first"]) == (2, 7)
    assert (rolls / "Gold 200" / "strips.json.bak").exists()

    # The plan changed since it was asked -- strip 2, claimed and nothing
    # recorded, offered again; then walked to its end by another program:
    # refused, nothing written.
    stale = session.plan_next(rolls / "Gold 200").next
    assert (stale.number, stale.first, stale.reuse) == (2, 7, True)
    (rolls / "Gold 200" / "strip-02").mkdir()
    session.write_manifest(rolls / "Gold 200" / "strip-02" / "survey.json",
                           _walked(*range(1, 7), strip_end=7))
    before = (rolls / "Gold 200" / "strips.json").read_bytes()
    with pytest.raises(session.StripMoved, match="strip 3, numbered from 13"):
        session.claim(stale, root=rolls)
    # A strip folder the index does not list, holding files.
    roll = _strip_roll(rolls, "H", [(1, 1, None, _walked(1, strip_end=2))])
    (roll / "strip-02").mkdir()
    (roll / "strip-02" / "frame01.tif").write_bytes(b"x")
    with pytest.raises(session.StripFolderUsed, match="frame01.tif"):
        session.claim(session.plan_next(roll).next, root=rolls)
    # Neither copy of the index readable: nothing is numbered.
    (roll / "strips.json").write_text("{", encoding="utf-8")
    (roll / "strips.json.bak").write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="strips.json"):
        session.plan_next(roll)
    assert (rolls / "Gold 200" / "strips.json").read_bytes() == before


def test_a_roll_of_strips_names_itself_and_every_strip(tmp_path):
    rolls = tmp_path / "rolls"
    roll = _strip_roll(rolls, "Gold200", [(1, 1, 6, None), (2, 7, None, None)])
    index = _index_of(roll)
    index["typed_name"] = "Gold 200 Oct"
    session.write_index(roll, index)
    for folder in (roll, roll / "strip-02", roll / "strip-07"):
        assert session.recorded_roll_name(folder) == "Gold200"
    assert session.recorded_typed_name(roll / "strip-02",
                                       "Gold200") == "Gold 200 Oct"
    assert session.roll_key(roll / "strip-02") == "Gold200/strip-02"
    assert session.roll_name_of(roll / "strip-02") == "Gold200"
    legacy = rolls / "2026-10-08-224648"
    legacy.mkdir()
    (legacy / "survey.json").write_text('{"roll": "old"}', encoding="utf-8")
    assert session.roll_key(legacy) == legacy.name
    assert session.roll_of(legacy) is None
    assert session.recorded_roll_name(legacy) == "old"
    assert session.frame_said(8, 2, 2) == "frame 8 (strip 2 · 2)"
    assert session.frame_said(2, 1, 2) == "frame 2"
    assert session.frame_said(4, None, 4) == "frame 4"
    assert session.roll_membership("r", 2, "frame", "f") == {
        "roll": "r", "number": 2, "kind": "frame", "folder": "f"}


def _device_session(monkeypatch, tmp_path, last=5, **kw):
    """The driver itself, on the device double, under a session."""
    from conftest import scanner_at_commands

    scanner, device = scanner_at_commands(monkeypatch, last=last, **kw)
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    s = ScanSession(root=str(tmp_path / "library"),
                    rolls=str(tmp_path / "rolls"),
                    reference=str(tmp_path / "shading.npz"),
                    open_scanner=lambda: scanner, verbose=False,
                    out_dir=out)
    return s, scanner, device


def _to_close(s, jobs, timeout=120.0):
    s.start()
    for job in jobs:
        s.submit(job)
    s.shutdown()
    events = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events.extend(s.poll())
        if any(e.kind == "closed" for e in events):
            break
        time.sleep(0.01)
    s.join(timeout=5)
    events.extend(s.poll())
    assert any(e.kind == "closed" for e in events), "the session never closed"
    return events


def _members(root, kind="prescan"):
    out = {}
    for record in library.entries(root):
        member = (record.get("extra") or {}).get("roll_membership") or {}
        if member.get("kind") == kind:
            out.setdefault(member.get("strip"), []).append(
                (member, record["film"]["frame"]))
    return out


def test_two_walks_are_two_strips_of_one_roll_numbered_across_it(
        monkeypatch, tmp_path):
    """The driver's own walk, twice, each to the end of a strip of six: the
    second is strip 2, its frames 7-12 across the roll and 1-6 inside its
    folder; a scan of two of its frames delivers 8 and 10."""
    from rps7200.protocol import SCSI_SLIDE

    rolls = tmp_path / "rolls"
    roll = rolls / "Gold200"
    s, scanner, device = _device_session(monkeypatch, tmp_path)
    events = _to_close(s, [
        Calibrate(mode="measure"),
        Roll(dry_run=True, new_strip=True, out=str(roll), strip_plan=(1, 1),
             typed_name="Gold200", meter="none")])
    assert not kinds(events, "failed"), [e.text for e in kinds(events, "failed")]
    strip1 = {p.name: p.read_bytes() for p in (roll / "strip-01").iterdir()}
    assert any("strip 1 of Gold200 is" in e.text for e in kinds(events, "log"))

    s, scanner, device = _device_session(monkeypatch, tmp_path)
    events = _to_close(s, [
        Calibrate(mode="measure"),
        Roll(dry_run=True, new_strip=True, out=str(roll), strip_plan=(2, 7),
             meter="none")])
    assert not kinds(events, "failed"), [e.text for e in kinds(events, "failed")]
    assert {p.name: p.read_bytes()
            for p in (roll / "strip-01").iterdir()} == strip1
    index = _index_of(roll)
    assert [(x["strip"], x["first"], x["last"]) for x in index["strips"]] == [
        (1, 1, 6), (2, 7, None)]
    survey = json.loads((roll / "strip-02" / "survey.json").read_text(
        encoding="utf-8"))
    assert survey["strip"] == {"number": 2, "first": 7}
    assert survey["strip_end"] == 7, "walked to the end of the strip"
    assert survey["roll"] == "Gold200"
    assert [(f["number"], f["roll_number"]) for f in survey["frames"]] == [
        (n, n + 6) for n in range(1, 7)]
    assert [f["prescan"] for f in survey["frames"]] == [
        f"prescan{n:02d}.tif" for n in range(1, 7)]
    members = _members(tmp_path / "library")
    labels = sorted(frame for _m, frame in members[2])
    assert labels == [f"Gold200-{n:02d}" for n in range(7, 13)]
    member = next(m for m, frame in members[2] if frame == "Gold200-08")
    assert member == {"roll": "Gold200", "number": 2, "kind": "prescan",
                      "folder": str(roll / "strip-02"), "strip": 2,
                      "roll_number": 8}
    assert sorted(p.name for p in (tmp_path / "out" / "prescans").glob(
        "Gold200_frame07_*")) and not list(
        (tmp_path / "out" / "prescans").glob("strip-02*"))
    # Strip 2's driver lines are said of strip 2.
    assert any(e.text.startswith("strip 2 · ") for e in kinds(events, "log"))

    # Two of strip 2's frames scanned: 8 and 10 across the roll.
    s, scanner, device = _device_session(monkeypatch, tmp_path)
    events = _to_close(s, [
        Calibrate(mode="measure"),
        Roll(out=str(roll / "strip-02"), only=(2, 4), start_at=2, frames=3,
             resolution=300, infrared=False, meter="none")])
    assert not kinds(events, "failed"), [e.text for e in kinds(events, "failed")]
    assert sorted(p.name for p in (roll / "strip-02").glob("frame*.tif")) == [
        "frame02.tif", "frame04.tif"]
    frames = [e.result for e in kinds(events, "result")
              if e.result.kind == "frame"]
    assert [(r.number, r.roll_number) for r in frames] == [(2, 8), (4, 10)]
    assert frames[0].label.startswith("frame 8 (strip 2 · 2) · 300 dpi")
    delivered = sorted(p.name for p in (tmp_path / "out").glob("Gold200_*"))
    assert [n.split("_")[1] for n in delivered] == ["frame08", "frame10"]
    scanned = _members(tmp_path / "library", "frame")[2]
    assert sorted(frame for _m, frame in scanned) == ["Gold200-08",
                                                     "Gold200-10"]
    progress = json.loads((roll / "strip-02" / "roll.json").read_text(
        encoding="utf-8"))
    assert [(f["number"], f["roll_number"]) for f in progress["frames"]] == [
        (2, 8), (4, 10)]
    assert progress["strip"] == {"number": 2, "first": 7}
    assert SCSI_SLIDE in {op for op, _data in device.sent}


def _refused_on_the_device(monkeypatch, tmp_path, job, position=0):
    """The refusal ``job`` met, with nothing sent that moves the film or
    takes a pass. ``position`` is where the film is: off frame 1, a refusal
    after the seek sends a SLIDE, which at 0 is indistinguishable from one
    before it."""
    from rps7200.protocol import SCSI_SCAN, SCSI_SLIDE

    s, _scanner, device = _device_session(monkeypatch, tmp_path,
                                          position=position)
    events = _to_close(s, [job])
    opcodes = {op for op, _data in device.sent}
    assert SCSI_SLIDE not in opcodes and SCSI_SCAN not in opcodes, \
        "the film moved, or a pass ran"
    assert s._roll_locks == {}, "a lock taken was kept"
    failed = kinds(events, "failed")
    assert failed, "it was not refused"
    return failed[0].text


def test_what_a_roll_of_strips_refuses_moves_nothing(monkeypatch, tmp_path):
    rolls = tmp_path / "rolls"
    roll = _strip_roll(rolls, "Gold200", [(1, 1, None, _walked(
        *range(1, 7), strip_end=7))])
    legacy = rolls / "2026-10-08-224648"
    legacy.mkdir()
    (legacy / "survey.json").write_text("{}", encoding="utf-8")
    other = rolls / "study"
    other.mkdir()
    (other / "A01_p1.tif").write_bytes(b"x")
    said = _refused_on_the_device(monkeypatch, tmp_path, Roll(
        dry_run=True, out=str(roll)))
    assert "is a roll of strips" in said
    said = _refused_on_the_device(monkeypatch, tmp_path, Roll(
        dry_run=True, new_strip=True, out=str(legacy)))
    assert "takes no second strip" in said
    said = _refused_on_the_device(monkeypatch, tmp_path, Roll(
        dry_run=True, new_strip=True, out=str(other)))
    assert "no roll" in said
    said = _refused_on_the_device(monkeypatch, tmp_path, Roll(
        dry_run=True, new_strip=True, out=str(roll), strip_plan=(2, 9)))
    assert "strip 2, numbered from 7, not strip 2 from 9" in said
    # The next strip's folder holding files the index does not list: asked
    # by the claim alone, after the seek, the film had moved by the time
    # "Nothing was moved" was said. The film is off frame 1 here, so a
    # refusal after the seek would have sent a SLIDE.
    (roll / "strip-02").mkdir()
    (roll / "strip-02" / "frame01.tif").write_bytes(b"x")
    for job in (Roll(dry_run=True, new_strip=True, out=str(roll),
                     strip_plan=(2, 7)),
                Roll(new_strip=True, out=str(roll), strip_plan=(2, 7),
                     resolution=300, infrared=False, meter="none")):
        said = _refused_on_the_device(monkeypatch, tmp_path, job, position=3)
        assert "holds files" in said and "frame01.tif" in said, said
        assert "Nothing was moved" in said
    shutil.rmtree(roll / "strip-02")
    index = _index_of(roll)
    index["strips"][0]["ends_at"] = 5
    session.write_index(roll, index)
    said = _refused_on_the_device(monkeypatch, tmp_path, Roll(
        out=str(roll / "strip-01"), only=(6,), start_at=6, resolution=300,
        infrared=False, meter="none"))
    assert "frame 6 of strip 1 is past where strip 1 ends (5)" in said
    (roll / "strips.json").write_text("{", encoding="utf-8")
    (roll / "strips.json.bak").write_text("{", encoding="utf-8")
    said = _refused_on_the_device(monkeypatch, tmp_path, Roll(
        dry_run=True, new_strip=True, out=str(roll)))
    assert "strips.json" in said and "cannot be read" in said


def test_a_strip_is_claimed_only_once_the_film_is_placed(tmp_path):
    from conftest import StripScanner

    rolls = tmp_path / "rolls"
    _s, _scanner, events = run(Roll(dry_run=True, new_strip=True,
                                    out=str(rolls / "New")),
                               tmp_path, scanner=StripScanner(silent=True))
    assert kinds(events, "failed")
    assert not (rolls / "New").exists(), "nothing left behind"

    class StoppedAtTheSeek(StripScanner):
        session = None

        def position(self):
            self.session.request_stop()
            return super().position()

    def remember(s, scanner):
        scanner.session = s

    _s, _scanner, events = run(Roll(dry_run=True, new_strip=True,
                                    out=str(rolls / "New")),
                               tmp_path, scanner=StoppedAtTheSeek(),
                               extra=remember)
    assert not (rolls / "New" / library.STRIPS_INDEX).exists()
    assert not (rolls / "New").exists()


def test_a_frame_past_a_closed_strips_numbers_is_numbered_after_the_last(
        tmp_path, monkeypatch):
    """Strip 1 closed at 1-4 when its walk stopped, strip 2 numbered from 5;
    strip 1 walked on to its fifth frame: numbered 11, after the roll's last
    -- given once, written into the index and the record before the frame
    is filed, and never given again."""
    from conftest import StripScanner

    rolls = tmp_path / "rolls"
    roll = _strip_roll(rolls, "Gold200", [
        (1, 1, 4, _walked(1, 2, 3, 4)),
        (2, 5, None, _walked(*range(1, 7), strip_end=7))])
    _s, _scanner, events = run(
        Roll(frames=1, start_at=5, dry_run=True, extend_walk=True,
             out=str(roll / "strip-01")), tmp_path, scanner=StripScanner(at=4))
    assert not kinds(events, "failed")
    survey = json.loads((roll / "strip-01" / "survey.json").read_text(
        encoding="utf-8"))
    fifth = next(f for f in survey["frames"] if f["number"] == 5)
    assert fifth["roll_number"] == 11
    assert _index_of(roll)["extra"][0] | {"given": None} == {
        "strip": 1, "place": 5, "number": 11, "given": None}
    assert any("frame 5 of strip 1 lies past its numbers, 1-4, so it is "
               "numbered 11" in e.text for e in kinds(events, "log"))
    assert session.plan_next(roll).next.first == 12
    # Walked again: the same number, not another.
    run(Roll(frames=1, start_at=5, dry_run=True, extend_walk=True,
             out=str(roll / "strip-01")), tmp_path, scanner=StripScanner(at=4))
    assert len(_index_of(roll)["extra"]) == 1

    # An index that will not take the number: kept in the record, said, the
    # run stopped after that frame -- and never given again.
    real = session.write_index

    def refuse(roll_, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(session, "write_index", refuse)
    _s, _scanner, events = run(
        Roll(frames=2, start_at=6, dry_run=True, extend_walk=True,
             out=str(roll / "strip-01")), tmp_path, scanner=StripScanner(at=5))
    monkeypatch.setattr(session, "write_index", real)
    survey = json.loads((roll / "strip-01" / "survey.json").read_text(
        encoding="utf-8"))
    sixth = next(f for f in survey["frames"] if f["number"] == 6)
    assert sixth["roll_number"] == 12
    assert 7 not in {f["number"] for f in survey["frames"]}, "it stopped"
    assert "strips.json could not be written" in survey["stopped"]
    assert session.plan_next(roll).next.first == 13, "12 is never reissued"

    # Walked again with the disk fine: it keeps 12, the number its record
    # carries, and the index gains it. Read from the index alone, the retake
    # was given 13, and 12 was then in no record and no index at all.
    _s, _scanner, events = run(
        Roll(frames=1, start_at=6, dry_run=True, extend_walk=True,
             out=str(roll / "strip-01")), tmp_path, scanner=StripScanner(at=5))
    assert not kinds(events, "failed"), [e.text for e in kinds(events,
                                                               "failed")]
    survey = json.loads((roll / "strip-01" / "survey.json").read_text(
        encoding="utf-8"))
    sixth = next(f for f in survey["frames"] if f["number"] == 6)
    assert sixth["roll_number"] == 12
    assert [(e["place"], e["number"], e.get("from"))
            for e in _index_of(roll)["extra"]] == [(5, 11, None),
                                                   (6, 12, "record")]
    assert any("frame 6 of strip 1 keeps 12" in e.text
               for e in kinds(events, "log"))
    # Scanned, it is 12 as well.
    _s, _scanner, events = run(
        Roll(only=(6,), start_at=6, frames=1, resolution=300, infrared=False,
             meter="none", out=str(roll / "strip-01")), tmp_path,
        scanner=StripScanner(at=5))
    assert not kinds(events, "failed"), [e.text for e in kinds(events,
                                                               "failed")]
    (frame,) = [e.result for e in kinds(events, "result")
                if e.result.kind == "frame"]
    assert frame.roll_number == 12
    assert session.plan_next(roll).next.first == 13


def test_a_strip_deleted_keeps_the_numbers_only_its_records_had(
        tmp_path, monkeypatch):
    """Strip 1's fifth frame was given 11 while the index would not take
    it, so only its record kept the number. Deleted, the strip's records
    are counted no longer (`library.strips_in`), and 11 was given again to
    the next strip's first frame. Written into the index first, it stays
    given; refused, the Delete moves nothing."""
    rolls = tmp_path / "rolls"
    roll = _strip_roll(rolls, "Gold200", [
        (1, 1, 4, dict(_walked(), frames=[
            *({"number": n, "roll_number": n} for n in range(1, 5)),
            {"number": 5, "roll_number": 11}])),
        (2, 5, 10, _walked(*range(1, 7), strip_end=7))])
    place = session.place_of(roll / "strip-01")
    assert place.extra == {5: 11} and place.unindexed == {5}
    assert session.place_of(roll / "strip-01", strict=False).extra == {}

    def refuse(roll_, data):
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as full:
        full.setattr(session, "write_index", refuse)
        with pytest.raises(ValueError, match="could not be written"):
            session.close_strip(roll / "strip-01", who="a test")
    assert _index_of(roll)["extra"] == []
    said = session.close_strip(roll / "strip-01", who="a test")
    assert said.startswith("strip 1 of Gold200 keeps 11 past its numbers, "
                           "1-4, in strips.json now"), said
    assert [(e["strip"], e["place"], e["number"], e["from"])
            for e in _index_of(roll)["extra"]] == [(1, 5, 11, "record")]
    assert session.close_strip(roll / "strip-01", who="a test") is None
    library.trash(rolls, [roll / "strip-01"], by="a test", kind="strips")
    assert session.plan_next(roll).next.first == 12


def _numbers_given(roll):
    """Every number across the roll its strips' records carry, one per
    place: survey.json's and roll.json's of a place are the same frame."""
    out = []
    for folder in sorted(roll.glob("strip-*")):
        held = {}
        for name in ("survey.json", "roll.json"):
            path = folder / name
            if path.exists():
                for record in json.loads(path.read_text(
                        encoding="utf-8"))["frames"]:
                    if record.get("roll_number"):
                        held[record["number"]] = record["roll_number"]
        out.extend(held.values())
    return out


def _labelled_once(root):
    """No label is two frames': each the library holds names one place of
    one strip, however many takes of it there are."""
    places: dict[str, set] = {}
    for record in library.entries(root):
        member = (record.get("extra") or {}).get("roll_membership") or {}
        if member:
            places.setdefault(record["film"]["frame"], set()).add(
                (member.get("strip"), member.get("number")))
    twice = {label: held for label, held in places.items() if len(held) > 1}
    assert not twice, twice


@pytest.mark.parametrize("walked", ["stopped", "to its end"])
def test_an_extra_closes_the_rolls_open_last_strip(tmp_path, walked):
    """Strip 1 closed at 1-4 when its walk stopped, strip 2 open from 5 and
    walked to 6 frames, 5-10. Strip 1 added to gave its fifth 11 -- and left
    strip 2 open, counted only to the frames it had, so strip 2 added to
    numbered its seventh 5 + 7 - 1 = 11 as well, and the library held two
    Gold200-11. The extra closes strip 2 in the same index write."""
    from conftest import StripScanner

    rolls = tmp_path / "rolls"
    end = ({"stopped": "stopped after frame 6, as asked"}
           if walked == "stopped" else {"strip_end": 7})
    roll = _strip_roll(rolls, "Gold200", [
        (1, 1, 4, _walked(1, 2, 3, 4)),
        (2, 5, None, _walked(*range(1, 7), **end))])
    _s, _scanner, events = run(
        Roll(frames=1, start_at=5, dry_run=True, extend_walk=True,
             out=str(roll / "strip-01")), tmp_path, scanner=StripScanner(at=4))
    assert not kinds(events, "failed"), [e.text for e in kinds(events,
                                                               "failed")]
    index = _index_of(roll)
    assert index["strips"][1]["last"] == 10
    assert [(e["place"], e["number"]) for e in index["extra"]] == [(5, 11)]
    assert any("so it is numbered 11; strip 2 is closed at 5-10" in e.text
               for e in kinds(events, "log")), [e.text for e in kinds(
                   events, "log")]
    # Strip 2 added to, as the window offers when its walk stopped.
    _s, _scanner, events = run(
        Roll(frames=1, start_at=7, dry_run=True, extend_walk=True,
             out=str(roll / "strip-02")), tmp_path, scanner=StripScanner(at=6))
    assert not kinds(events, "failed"), [e.text for e in kinds(events,
                                                               "failed")]
    survey = json.loads((roll / "strip-02" / "survey.json").read_text(
        encoding="utf-8"))
    assert next(f for f in survey["frames"]
                if f["number"] == 7)["roll_number"] == 12
    numbers = _numbers_given(roll)
    assert sorted(numbers) == [11, 12] and len(set(numbers)) == len(numbers)
    _labelled_once(tmp_path)
    assert session.plan_next(roll).next.first == 13


@pytest.mark.parametrize("index_written", [True, False])
def test_an_extra_numbers_an_empty_last_strip_on(tmp_path, monkeypatch,
                                                 index_written):
    """Strip 1 closed at 1-6, strip 2 claimed from 7 and nothing recorded
    under it. Strip 1's seventh took 7, the number after the roll's last --
    and strip 2's first frame, scanned into its folder, 7 again. Numbered on
    past the extra as it is given; where the index would not take that, the
    run into strip 2 numbers it on itself, before anything moves."""
    from conftest import StripScanner

    rolls = tmp_path / "rolls"
    roll = _strip_roll(rolls, "Gold200", [
        (1, 1, 6, _walked(*range(1, 7))), (2, 7, None, None)])
    real = session.write_index

    def refuse(roll_, data):
        raise OSError(28, "No space left on device")

    if not index_written:
        monkeypatch.setattr(session, "write_index", refuse)
    _s, _scanner, events = run(
        Roll(frames=1, start_at=7, dry_run=True, extend_walk=True,
             out=str(roll / "strip-01")), tmp_path, scanner=StripScanner(at=6))
    monkeypatch.setattr(session, "write_index", real)
    assert not kinds(events, "failed"), [e.text for e in kinds(events,
                                                               "failed")]
    survey = json.loads((roll / "strip-01" / "survey.json").read_text(
        encoding="utf-8"))
    assert next(f for f in survey["frames"]
                if f["number"] == 7)["roll_number"] == 7
    second = _index_of(roll)["strips"][1]
    assert (second["first"], second["last"]) == (
        (8, None) if index_written else (7, None))
    _s, _scanner, events = run(
        Roll(frames=1, start_at=1, dry_run=True, extend_walk=True,
             out=str(roll / "strip-02")), tmp_path, scanner=StripScanner(at=0))
    assert not kinds(events, "failed"), [e.text for e in kinds(events,
                                                               "failed")]
    survey = json.loads((roll / "strip-02" / "survey.json").read_text(
        encoding="utf-8"))
    assert [(f["number"], f["roll_number"]) for f in survey["frames"]] == [
        (1, 8)]
    assert survey["strip"] == {"number": 2, "first": 8}
    assert _index_of(roll)["strips"][1]["first"] == 8
    numbers = _numbers_given(roll)
    assert len(set(numbers)) == len(numbers), numbers
    _labelled_once(tmp_path)


def test_an_extra_kept_only_in_its_record_still_closes_the_open_strip(
        tmp_path, monkeypatch):
    """Case (a) with the index refused at the extra: strip 1's fifth kept
    11 in its record alone, and strip 2 stayed open in the index, whose next
    frame would have been 11 too. Its run closes it first, under the lock
    and before the seek."""
    from conftest import StripScanner

    rolls = tmp_path / "rolls"
    roll = _strip_roll(rolls, "Gold200", [
        (1, 1, 4, _walked(1, 2, 3, 4)),
        (2, 5, None, _walked(*range(1, 7),
                             stopped="stopped after frame 6, as asked"))])
    real = session.write_index

    def refuse(roll_, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(session, "write_index", refuse)
    run(Roll(frames=1, start_at=5, dry_run=True, extend_walk=True,
             out=str(roll / "strip-01")), tmp_path, scanner=StripScanner(at=4))
    monkeypatch.setattr(session, "write_index", real)
    assert _index_of(roll)["strips"][1]["last"] is None, "left open"
    _s, _scanner, events = run(
        Roll(frames=1, start_at=7, dry_run=True, extend_walk=True,
             out=str(roll / "strip-02")), tmp_path, scanner=StripScanner(at=6))
    assert not kinds(events, "failed"), [e.text for e in kinds(events,
                                                               "failed")]
    assert _index_of(roll)["strips"][1]["last"] == 10
    assert any("strip 2 is closed at 5-10" in e.text
               and "11 was given elsewhere" in e.text
               for e in kinds(events, "log")), [e.text for e in kinds(
                   events, "log")]
    survey = json.loads((roll / "strip-02" / "survey.json").read_text(
        encoding="utf-8"))
    assert next(f for f in survey["frames"]
                if f["number"] == 7)["roll_number"] == 12
    numbers = _numbers_given(roll)
    assert sorted(numbers) == [11, 12]
    _labelled_once(tmp_path)


def test_a_number_that_cannot_be_read_is_refused_before_the_film_moves(
        monkeypatch, tmp_path):
    """Strip 1 closed at 1-4, strip 2 open with its survey.json damaged and
    no copy beside it: a frame past strip 1's numbers cannot be numbered.
    That was found between passes -- the frame's prescan and full pass read
    -- raised out of the roll, and the frame never filed. Refused before
    the seek now; a run inside strip 1's numbers needs none, and runs."""
    from conftest import StripScanner

    rolls = tmp_path / "rolls"
    roll = _strip_roll(rolls, "Gold200", [
        (1, 1, 4, _walked(1, 2, 3, 4)),
        (2, 5, None, _walked(*range(1, 7), strip_end=7))])
    survey = roll / "strip-02" / "survey.json"
    good = survey.read_bytes()
    survey.write_text("{", encoding="utf-8")
    for job in (Roll(start_at=5, out=str(roll / "strip-01"), resolution=300,
                     infrared=False, meter="none"),
                Roll(start_at=5, frames=1, dry_run=True, extend_walk=True,
                     out=str(roll / "strip-01"))):
        said = _refused_on_the_device(monkeypatch, tmp_path, job,
                                      position=3)
        assert "survey.json cannot be read" in said, said
        assert "frames of strip 1 past its numbers, 1-4" in said, said
        assert said.endswith("Nothing was moved."), said
    _s, _scanner, events = run(
        Roll(only=(2,), start_at=2, frames=1, resolution=300, infrared=False,
             meter="none", out=str(roll / "strip-01")), tmp_path,
        scanner=StripScanner(at=1))
    assert not kinds(events, "failed"), [e.text for e in kinds(events,
                                                               "failed")]

    # Read before the seek, and refused between passes -- a sharing
    # violation: the frame is numbered after what was read under the lock,
    # filed, and the run stops after it.
    survey.write_bytes(good)
    real = session.highest_given
    rolling: list = []

    def unreadable_once_rolling(*a, **k):
        if rolling:
            raise ValueError(f"{survey} cannot be read: a sharing violation")
        return real(*a, **k)

    class Rolling(StripScanner):
        def scan_roll(self, **kw):
            rolling.append(True)
            yield from super().scan_roll(**kw)

    monkeypatch.setattr(session, "highest_given", unreadable_once_rolling)
    _s, _scanner, events = run(
        Roll(start_at=5, frames=3, resolution=300, infrared=False,
             meter="none", out=str(roll / "strip-01")), tmp_path,
        scanner=Rolling(at=4))
    monkeypatch.setattr(session, "highest_given", real)
    assert not kinds(events, "failed"), [e.text for e in kinds(events,
                                                               "failed")]
    progress = json.loads((roll / "strip-01" / "roll.json").read_text(
        encoding="utf-8"))
    assert [(f["number"], f["roll_number"]) for f in progress["frames"]
            if f["number"] >= 5] == [(5, 11)], "the run stopped after it"
    said = json.dumps(progress)
    assert "a strip's record could not be read" in said
    assert "a sharing violation" in said
    filed = [frame for _m, frame in _members(tmp_path, "frame")[1]]
    assert "Gold200-11" in filed
    assert [(e["place"], e["number"]) for e in _index_of(roll)["extra"]] == [
        (5, 11)]


def test_a_strip_is_held_by_its_roll_and_let_go_of_with_it(tmp_path):
    from conftest import HeldElsewhere, StripScanner

    from rps7200 import locks

    rolls = tmp_path / "rolls"
    roll = _strip_roll(rolls, "Gold200", [(1, 1, None, _walked(1, 2))])
    s, scanner, events = run(Roll(frames=1, start_at=3, dry_run=True,
                                  extend_walk=True,
                                  out=str(roll / "strip-01")),
                             tmp_path, scanner=StripScanner(at=2))
    assert not kinds(events, "failed")
    # Let go of at close; asked of a session still holding it:
    s2 = ScanSession(root=str(tmp_path), rolls=str(rolls), verbose=False)
    s2._hold_roll(library.lock_folder(roll / "strip-01"))
    assert list(s2._roll_locks) == [locks.key(roll)]
    assert s2.let_go_of_roll(roll / "strip-01") is False, "a strip moved alone"
    assert s2.let_go_of_roll(roll) is True and s2._roll_locks == {}

    with HeldElsewhere(locks.ROLL, roll, what="tools/scan_roll.py"):
        _s, scanner, events = run(
            Roll(frames=1, start_at=3, dry_run=True, extend_walk=True,
                 out=str(roll / "strip-01")), tmp_path,
            scanner=StripScanner(at=2))
    (failed,) = kinds(events, "failed")
    assert "tools/scan_roll.py" in failed.text
    assert scanner.rolls == [], "refused before the walk"


def test_the_drivers_lines_name_the_strip_only_while_it_runs(tmp_path):
    from conftest import StripScanner

    class Talking(StripScanner):
        def scan_roll(self, **kw):
            self.log_hook("frame 1: contrast 0.3")
            yield from super().scan_roll(**kw)

    rolls = tmp_path / "rolls"
    roll = _strip_roll(rolls, "Gold200", [(1, 1, 6, _walked(1)),
                                          (2, 7, None, _walked(1))])
    scanner = Talking(at=0)
    s = ScanSession(root=str(tmp_path), rolls=str(rolls),
                    open_scanner=lambda: scanner, verbose=False)
    events = _to_close(s, [
        Roll(frames=1, dry_run=True, extend_walk=True,
             out=str(roll / "strip-02")),
        Roll(frames=1, dry_run=True, extend_walk=True,
             out=str(roll / "strip-01"))])
    lines = [e.text for e in kinds(events, "log") if "contrast" in e.text]
    assert lines == ["strip 2 · frame 1: contrast 0.3",
                     "frame 1: contrast 0.3"]


def test_a_walk_cut_short_never_says_its_strip_ended(tmp_path):
    """Completeness decides the next strip offered: a walk the driver gave
    up on, or ended behind its count, saw no end of its strip."""
    from conftest import StripScanner

    class GivesUp(StripScanner):
        def scan_roll(self, **kw):
            self.roll_cut_short = None
            yield from super().scan_roll(**kw)
            self.roll_cut_short = "gave up after 3 consecutive failures"

    rolls = tmp_path / "rolls"
    for name, scanner, ended in (("cut", GivesUp(last=3), False),
                                 ("whole", StripScanner(last=3), True)):
        run(Roll(dry_run=True, new_strip=True, out=str(rolls / name)),
            tmp_path, scanner=scanner)
        survey = json.loads((rolls / name / "strip-01" / "survey.json")
                            .read_text(encoding="utf-8"))
        assert ("strip_end" in survey) is ended, name
        assert ("cut_short" in survey) is not ended, name
        offer = session.plan_next(rolls / name)
        assert (offer.again is None) is ended, name
        if not ended:
            assert "cut short after frame 4" in offer.said


def test_the_driver_says_when_it_cut_a_roll_short(monkeypatch):
    from conftest import scanner_at_commands

    from rps7200.protocol import ScanReadError
    from test_roll import METER_NONE

    scanner, _ = scanner_at_commands(monkeypatch)
    scanner.calibrate_shading()

    def failing(*a, **k):
        raise ScanReadError("the read came back short")

    monkeypatch.setattr(scanner, "prescan", failing)
    frames = list(scanner.scan_roll(resolution=300, infrared=False,
                                    meter=METER_NONE, dry_run=True))
    assert len(frames) == 3 and all(f.error for f in frames)
    assert scanner.roll_cut_short == "gave up after 3 consecutive failures"

    scanner, device = scanner_at_commands(monkeypatch, last=3)
    scanner.calibrate_shading()
    frames = list(scanner.scan_roll(frames=2, resolution=300, infrared=False,
                                    meter=METER_NONE, dry_run=True))
    assert scanner.roll_cut_short is None, "it reached what it was asked for"
    real = scanner.position

    def behind():
        return max(0, real() - 2)

    monkeypatch.setattr(scanner, "position", behind)
    device.position = 0
    list(scanner.scan_roll(first_index=2, resolution=300, infrared=False,
                           meter=METER_NONE, dry_run=True))
    assert "behind the roll" in (scanner.roll_cut_short or "")


def test_an_entry_a_strip_names_is_never_pruned(tmp_path):
    """`duplicates --delete` read the rolls one level deep, and an entry
    only a strip named had a twin with the same data: it would have gone."""
    rolls = tmp_path / "rolls"
    root = tmp_path / "library"
    image = picture(channels=3, seed=4)
    twins = [library.save(image, {"resolution_dpi": 300, "channels": 3,
                                  "width": 36, "height": 24, "depth": 16},
                          root=root) for _ in range(2)]
    roll = _strip_roll(rolls, "Gold200", [(1, 1, None, _walked(1))])
    session.write_manifest(roll / "strip-01" / "roll.json", {
        "numbering": "strip", "frames": [
            {"number": 1, "done": True, "entry": f"library\\{twins[0].name}"}]})
    named = session.entries_named(rolls)
    assert twins[0].name in named
    gone = {record["id"] for record, _why in library.prunable(
        root, named=named)}
    assert twins[0].name not in gone


def test_both_passes_of_a_slide_in_a_strip_carry_its_number(tmp_path):
    from test_colour_pass import SlideScanner

    rolls = tmp_path / "rolls"
    roll = _strip_roll(rolls, "Slides", [(1, 1, 6, _walked(1)),
                                         (2, 7, None, _walked(1))])
    _s, _scanner, events = run(
        Roll(frames=1, start_at=2, resolution=600, infrared=True,
             film="positive", out=str(roll / "strip-02")),
        tmp_path, scanner=SlideScanner())
    assert not kinds(events, "failed")
    members = sorted(
        ((e["extra"]["roll_membership"], e["film"]["frame"])
         for e in library.entries(tmp_path)
         if e["extra"]["roll_membership"]["kind"] != "prescan"),
        key=lambda pair: pair[0]["kind"])
    assert [m["kind"] for m, _f in members] == ["frame", "infrared pass"]
    for member, frame in members:
        assert (member["strip"], member["number"], member["roll_number"]) == (
            2, 2, 8)
        assert frame == "Slides-08"


# -- a fresh walk never lands among another walk's records (P18-a) ------------


def _folder(rolls, name, **files):
    folder = rolls / name
    folder.mkdir(parents=True)
    for file_name, data in files.items():
        file_name = file_name.replace("_json", ".json")
        if isinstance(data, (bytes, str)):
            (folder / file_name).write_bytes(
                data if isinstance(data, bytes) else data.encode("utf-8"))
        else:
            session.write_manifest(folder / file_name, data)
    return folder


def test_a_folder_is_free_only_for_a_walk_whose_prescans_are_filed(tmp_path):
    rolls = tmp_path / "rolls"
    filed = {"frames": [{"number": 1, "prescan": "prescan01.tif",
                         "prescan_entry": "e1"}]}
    free = _folder(rolls, "free", survey_json=filed,
                   roll_json={"frames": []})
    (free / "prescan01.tif").write_bytes(b"x")
    (free / "survey.json.bak").write_text("{}", encoding="utf-8")
    assert session.folder_in_use(free) == []
    assert session.folder_in_use(rolls / "not-there") == []
    # A walk killed before any filing answered: no prescan named, free.
    assert session.folder_in_use(_folder(rolls, "killed", survey_json={
        "frames": [{"number": 1}, {"number": 2}]})) == []
    # Record by record: one legacy prescan among filed ones is used, and so
    # is one the library refused (its entry None).
    mixed = _folder(rolls, "mixed", survey_json={"frames": [
        {"number": 1, "prescan": "prescan01.tif", "prescan_entry": "e1"},
        {"number": 2, "prescan": "prescan02.tif"},
        {"number": 3, "prescan": "prescan03.tif", "prescan_entry": None}]})
    assert session.folder_in_use(mixed) == [
        "2 prescans that exist nowhere else"]
    scanned = _folder(rolls, "scanned", roll_json={"frames": [{"number": 1}]},
                      approved_json={"frames": [{"number": 1}, {"number": 2}]})
    assert session.folder_in_use(scanned) == [
        "1 scanned frame (roll.json)",
        "positions and turns for 2 frames (approved.json)"]
    copied = _folder(rolls, "copied", survey_json=filed)
    (copied / library.INCOMPLETE).write_text("{}", encoding="utf-8")
    assert session.folder_in_use(copied) == ["a Duplicate cut short"]
    study = _folder(rolls, "study")
    (study / "A01_p1.tif").write_bytes(b"x")
    assert session.folder_in_use(study) == ["1 other file"]
    broken = _folder(rolls, "broken", roll_json="{")
    assert session.folder_in_use(broken) == ["a roll.json that cannot be read"]
    # The first free -N beside a used one.
    assert session.walk_dir(rolls, "scanned") == (
        rolls / "scanned-2", session.folder_in_use(scanned))
    (rolls / "scanned-2").mkdir()
    assert session.walk_dir(rolls, "scanned")[0] == rolls / "scanned-3"
    assert session.walk_dir(rolls, "free") == (free, [])


def test_a_fresh_walk_into_a_used_folder_is_refused_before_the_seek(
        monkeypatch, tmp_path):
    rolls = tmp_path / "rolls"
    used = _folder(rolls, "used", roll_json={"frames": [{"number": 1}]})
    said = _refused_on_the_device(monkeypatch, tmp_path, Roll(
        dry_run=True, out=str(used), meter="none"))
    assert "1 scanned frame (roll.json)" in said and "used-2 is free" in said
    # Nor a fresh walk into a strip: it is walked again by adding to it.
    roll = _strip_roll(rolls, "Gold200", [(1, 1, None, _walked(1, 2))])
    said = _refused_on_the_device(monkeypatch, tmp_path, Roll(
        dry_run=True, out=str(roll / "strip-01"), meter="none"))
    assert "add to it" in said
    # A folder holding only a walk whose prescans are filed is walked again
    # in place, as it always was.
    from conftest import StripScanner

    free = _folder(rolls, "free", survey_json={"frames": []})
    _s, _scanner, events = run(Roll(frames=1, dry_run=True, out=str(free)),
                               tmp_path, scanner=StripScanner(at=0))
    assert not kinds(events, "failed")


def test_strip_ends_here_trims_a_stub_before_its_numbers_are_fixed(tmp_path):
    """Strip 1 walked to seven frames for a strip of six: the seventh is no
    frame of the film. Set on the open last strip only, never past a frame
    scanned there, and undone by counting to the end again."""
    rolls = tmp_path / "rolls"
    roll = _strip_roll(rolls, "Gold200", [(1, 1, None, _walked(
        *range(1, 8), strip_end=8))])
    assert session.plan_next(roll).next.first == 8
    said = session.set_strip_end(roll / "strip-01", 6, who="a test")
    assert "ends at frame 6" in said and "numbered from 7" in said
    assert _index_of(roll)["strips"][0]["ends_at"] == 6
    assert session.plan_next(roll).next.first == 7
    # Not past a frame already scanned there.
    session.write_manifest(roll / "strip-01" / "roll.json", {
        "numbering": "strip", "frames": [{"number": 7, "done": True,
                                          "entry": "library\\x"}]})
    with pytest.raises(ValueError, match="frame 7 of strip 1 is scanned"):
        session.set_strip_end(roll / "strip-01", 5, who="a test")
    (roll / "strip-01" / "roll.json").unlink()
    said = session.set_strip_end(roll / "strip-01", None, who="a test")
    assert "counts to the end of the strip again" in said
    assert session.plan_next(roll).next.first == 8
    # A closed strip's numbers are fixed.
    session.claim(session.plan_next(roll).next, root=rolls)
    with pytest.raises(ValueError, match="is closed at 1-7"):
        session.set_strip_end(roll / "strip-01", 6, who="a test")
    legacy = rolls / "old"
    legacy.mkdir()
    with pytest.raises(ValueError, match="no strip of a roll of strips"):
        session.set_strip_end(legacy, 6, who="a test")


# B4-T4: a place on strip 2 and later said beside the roll's number for it.
# The counter and a strip's files count places; labels, deliveries and the
# browser count the roll. "ends at frame 5: its numbers are 10-14" put the
# one beside the other.


def test_a_later_strips_count_says_its_frame_by_the_rolls_number(tmp_path):
    rolls = tmp_path / "rolls"
    roll = _strip_roll(rolls, "Gold200", [
        (1, 1, 9, None),
        (2, 10, None, _walked(*range(1, 8), strip_end=8))])
    said = session.set_strip_end(roll / "strip-02", 5, who="a test")
    assert ("strip 2 of Gold200 ends at frame 14 (strip 2 · 5): its numbers "
            "are 10-14") in said, said
    count = session.strip_count(roll / "strip-02")
    # The record keeps the place, as it always has; the words say both.
    assert count.how == "ends at frame 5, as its sheet set"
    assert count.said() == "ends at frame 14 (strip 2 · 5), as its sheet set"
    assert count.said(session.place_of(roll / "strip-02")) == count.said()
    # Strip 1 reads as it did.
    roll = _strip_roll(rolls, "Ektar", [(1, 1, None, _walked(
        1, 2, 3, 4, stopped="stopped after frame 4, as asked"))])
    count = session.strip_count(roll / "strip-01")
    assert count.said() == count.how == "walk stopped after frame 4"


def test_a_closed_strips_count_says_the_extra_its_place_was_given(tmp_path):
    """Its own first number plus the place would call place 7 of a strip
    closed at 10-15 frame 16, where it was given the roll's 20."""
    rolls = tmp_path / "rolls"
    roll = _strip_roll(rolls, "Gold200", [
        (1, 1, 9, None), (2, 10, 15, _walked(*range(1, 8)))],
        extra=[{"strip": 2, "place": 7, "number": 20}])
    strip = session.place_of(roll / "strip-02")
    count = session.strip_count(roll / "strip-02")
    assert count.how == "walk went as far as frame 7"
    assert count.said(strip) == "walk went as far as frame 20 (strip 2 · 7)"


def test_the_offer_and_the_closing_say_a_later_strip_by_its_numbers(
        tmp_path):
    rolls = tmp_path / "rolls"
    roll = _strip_roll(rolls, "Gold200", [
        (1, 1, 6, None),
        (2, 7, None, _walked(1, 2, 3, 4,
                             stopped="stopped after frame 4, as asked"))])
    offer = session.plan_next(roll)
    assert ("strip 2's walk stopped after frame 10 (strip 2 · 4) -- the next "
            "walk adds to it, or starts strip 3 from 11") in offer.said
    said = []
    session.claim(offer.next, root=rolls, say=said.append)
    assert said == ["strip 2 of Gold200 is closed at 7-10: its walk stopped "
                    "after frame 10 (strip 2 · 4)"]
    # The index keeps the place, as the record it is.
    assert _index_of(roll)["strips"][1]["count_how"] == (
        "walk stopped after frame 4")


def test_the_seek_says_a_later_strips_places_by_the_rolls_numbers():
    """A roll on strip 2 says where the film is and where it goes as its
    files will: the seek's own words, through the strip it is handed."""
    from conftest import StripScanner

    strip = session.StripPlace(Path("r"), "Gold200", 2, Path("r/strip-02"), 7)
    said = []
    session.seek(StripScanner(at=4, last=8), 1, say=said.append,
                 said=lambda p: session.place_said(strip, p))
    assert said[0] == ("the film is on frame 11 (strip 2 · 5); winding back 3 "
                       "to frame 8 (strip 2 · 2)")
    # And the rewind under it, whose lines said the places alone: "rewinding
    # 3 frame(s) from frame 5" straight after "frame 11 (strip 2 · 5)".
    assert said[1:] == [
        "rewinding 3 frame(s) from frame 11 (strip 2 · 5)",
        "  1/3: frame 11 (strip 2 · 5) -> frame 10 (strip 2 · 4)",
        "  2/3: frame 10 (strip 2 · 4) -> frame 9 (strip 2 · 3)",
        "  3/3: frame 9 (strip 2 · 3) -> frame 8 (strip 2 · 2)"]
    said.clear()
    with pytest.raises(session.FilmNotPlaced):
        session.seek(StripScanner(at=4, last=8, sticks_at=3), 1,
                     say=said.append,
                     said=lambda p: session.place_said(strip, p))
    assert said[-1] == ("  stopped after 1 of 3: still on frame 10 (strip 2 · "
                        "4)")
    with pytest.raises(session.FilmNotPlaced) as refused:
        session.seek(StripScanner(at=0, last=1), 4,
                     said=lambda p: session.place_said(strip, p))
    assert str(refused.value) == (
        "the strip ended on frame 8 (strip 2 · 2), before frame 11 (strip 2 "
        "· 5), so nothing was scanned")
    # Without a strip, as it always said.
    with pytest.raises(session.FilmNotPlaced,
                       match=r"^the strip ended on frame 2, before frame 5,"):
        session.seek(StripScanner(at=0, last=1), 4)


def test_a_roll_on_a_later_strip_says_where_it_ended_by_its_numbers(
        tmp_path):
    """The seek's refusal, the strip's end and the roll's stop, through a
    roll into strip 2 numbered from 7."""
    from conftest import StripScanner

    rolls = tmp_path / "rolls"
    roll = _strip_roll(rolls, "Gold200", [(1, 1, 6, None), (2, 7, None, None)])
    folder = roll / "strip-02"
    session.write_manifest(folder / "roll.json", {
        "roll": "Gold200", "numbering": "strip",
        "strip": {"number": 2, "first": 7},
        "settings": {"resolution": 600, "infrared": False, "frames": None,
                     "start_at": 1, "only": None},
        "stopped": "stopped after frame 4, as asked",
        "frames": [_frame(n) for n in range(1, 5)]})

    # Not put back: the counter on place 1 and going nowhere.
    _s, _sc, events = run(Roll(start_at=5, resolution=600, infrared=False,
                               out=str(folder)),
                          tmp_path, scanner=StripScanner(at=0, last=0))
    (failed,) = kinds(events, "failed")
    assert ("the strip ended on frame 7 (strip 2 · 1), before frame 11 "
            "(strip 2 · 5)") in failed.text, failed.text
    assert any("its records hold frames to 10 (strip 2 · 4)" in e.text
               for e in kinds(events, "log"))

    # Its end, where the records agree: the rest of it was nothing.
    _s, _sc, events = run(Roll(start_at=5, resolution=600, infrared=False,
                               out=str(folder)),
                          tmp_path, scanner=StripScanner(at=0, last=3))
    (finished,) = kinds(events, "finished")
    assert finished.text == ("the strip ends at frame 10 (strip 2 · 4); the "
                             "roll is finished")

    # And a stop, said where the roll reached.
    s = ScanSession(root=str(tmp_path), rolls=str(rolls),
                    open_scanner=lambda: StripScanner(at=0, last=3),
                    verbose=False)
    strip = session.place_of(folder)
    assert s._stopped_how(3, lambda p: session.place_said(strip, p)) == (
        "stopped after frame 9 (strip 2 · 3), as asked")
    assert s._stopped_how(3) == "stopped after frame 3, as asked"


def test_a_strip_is_shown_from_its_own_records_when_its_index_is_lost(
        tmp_path):
    rolls = tmp_path / "rolls"
    roll = _strip_roll(rolls, "Gold200", [(1, 1, 6, _walked(1)),
                                          (2, 7, None, _walked(1))])
    (roll / "strips.json").write_text("{", encoding="utf-8")
    (roll / "strips.json.bak").write_text("{", encoding="utf-8")
    with pytest.raises(ValueError):
        session.place_of(roll / "strip-02")
    shown = session.place_of(roll / "strip-02", strict=False)
    assert (shown.strip, shown.first) == (2, 7)
    assert session.place_of(rolls / "nothing" / "strip-02") is None
    # Only the copy left: read, never taken for a roll with no strips.
    roll = _strip_roll(rolls, "Fuji", [(1, 1, None, _walked(*range(1, 7),
                                                            strip_end=7))])
    (roll / "strips.json").rename(roll / "strips.json.bak")
    assert session.plan_next(roll).next.first == 7


def test_a_jobs_description_names_its_strip():
    assert session._describe(Roll(dry_run=True, new_strip=True,
                                  out="rolls/Gold200", strip_plan=(2, 7))) == \
        "walking strip 2 of Gold200"
    assert session._describe(Roll(out=str(Path("rolls/Gold200/strip-02")),
                                  only=(2, 4))) == \
        "scanning 2 chosen frames of strip 2 of Gold200"
    assert session._describe(Roll(dry_run=True, frames=6)) == \
        "walking a roll of 6 frames"


def test_a_strips_entry_is_marked_under_its_rolls_lock(tmp_path):
    """Everything inside a roll of strips is locked by the roll's folder: a
    Delete of a strip's frame refuses while another program writes the
    roll, as one of a roll from before strips does."""
    from conftest import HeldElsewhere

    from rps7200 import locks

    rolls, root = tmp_path / "rolls", tmp_path / "library"
    roll = _strip_roll(rolls, "Gold200", [(1, 1, 6, _walked(1)),
                                          (2, 7, None, _walked(1))])
    entry = library.save(picture(channels=3, seed=8), {
        "resolution_dpi": 300, "channels": 3, "width": 36, "height": 24,
        "depth": 16, "roll_membership": {
            "roll": "Gold200", "number": 1, "kind": "frame",
            "folder": str(roll / "strip-02"), "strip": 2,
            "roll_number": 7}}, root=root)
    session.write_manifest(roll / "strip-02" / "roll.json", {
        "numbering": "strip", "frames": [
            {"number": 1, "done": True, "entry": f"library\\{entry.name}"}]})
    scan = ScanSession(root=str(root), rolls=str(rolls), verbose=False)
    with HeldElsewhere(locks.ROLL, roll, what="tools/scan_roll.py"):
        with pytest.raises(locks.Refused):
            scan.trash_entries([entry], by="the window")
    assert entry.exists()
    done = scan.trash_entries([entry], by="the window")
    assert [(p.parent.name, n) for p, n in done.frames] == [("strip-02", 1)]


def test_a_new_roll_of_strips_outside_the_rolls_folder_is_made_there(
        tmp_path):
    """Its first strip's folder lies below a roll folder not made yet: the
    roll folder's root is what has to be there, not the strip's."""
    from conftest import StripScanner

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _s, _scanner, events = run(Roll(frames=1, dry_run=True, new_strip=True,
                                    out=str(elsewhere / "Gold200")),
                               tmp_path, scanner=StripScanner(at=0))
    assert not kinds(events, "failed"), [e.text for e in kinds(events,
                                                               "failed")]
    assert (elsewhere / "Gold200" / "strip-01" / "survey.json").exists()
    assert _index_of(elsewhere / "Gold200")["strips"][0]["first"] == 1


# -- what the review of rolls of strips found ---------------------------------


def _stubbed_strip_one(rolls):
    """Strip 1 walked seven frames for a strip of six, "Strip ends here" at
    6, and strip 2 claimed from 7 -- which closes strip 1 at 1-6."""
    roll = _strip_roll(rolls, "Gold200", [(1, 1, None, dict(
        _walked(), strip_end=8,
        frames=[{"number": n, "roll_number": n} for n in range(1, 8)]))])
    session.set_strip_end(roll / "strip-01", 6, who="a test")
    place = session.claim(session.plan_next(roll).next, root=rolls)
    assert (place.strip, place.first) == (2, 7)
    assert _index_of(roll)["strips"][0]["last"] == 6
    return roll


def test_a_stub_trimmed_stays_out_of_the_numbers_once_its_strip_is_closed(
        tmp_path):
    """"Strip ends here" counted only while its strip was open: closed, the
    stub's own number counted again. Strip 2 taken again after recording
    nothing was numbered from 8, and 7 was never any frame's; strip 1's
    stub walked again took an extra, and strip 3 began one later still."""
    rolls = tmp_path / "rolls"
    roll = _stubbed_strip_one(rolls)
    (roll / "strip-02").mkdir()
    offer = session.plan_next(roll)
    assert (offer.next.number, offer.next.first, offer.next.reuse) == (
        2, 7, True), "no number past the stub is used up"
    # The stub walked again on its closed strip: the number its place has,
    # shared with strip 2's first prescan as the walk before the trim did,
    # and no extra.
    place = session.place_of(roll / "strip-01")
    said = []
    assert place.past_its_end(7) and not place.past_its_end(6)
    assert place.number(7, say=said.append) == 7
    assert said == [] and _index_of(roll)["extra"] == []
    # A number past the trim that is not its place's is an extra the index
    # lost: counted all the same.
    session.write_manifest(roll / "strip-01" / "roll.json", {
        "numbering": "strip", "frames": [{"number": 8, "roll_number": 30}]})
    assert session.plan_next(roll).next.first == 31


def test_a_stub_walked_again_on_a_closed_strip_takes_no_extra(
        monkeypatch, tmp_path):
    from conftest import StripScanner

    rolls = tmp_path / "rolls"
    roll = _stubbed_strip_one(rolls)
    (roll / "strip-02").mkdir()
    session.write_manifest(roll / "strip-02" / "survey.json", dict(
        _walked(*range(1, 7), strip_end=7), roll="Gold200",
        strip={"number": 2, "first": 7}))
    _s, _scanner, events = run(
        Roll(frames=1, start_at=7, dry_run=True, extend_walk=True,
             out=str(roll / "strip-01")), tmp_path, scanner=StripScanner(at=6))
    assert not kinds(events, "failed"), [e.text for e in kinds(events,
                                                               "failed")]
    survey = json.loads((roll / "strip-01" / "survey.json").read_text(
        encoding="utf-8"))
    seventh = next(f for f in survey["frames"] if f["number"] == 7)
    assert seventh["roll_number"] == 7
    assert _index_of(roll)["extra"] == []
    assert not any("lies past its numbers" in e.text
                   for e in kinds(events, "log"))
    assert session.plan_next(roll).next.first == 13
    # A roll to the end of the strip is refused: it would scan the stub.
    said = _refused_on_the_device(monkeypatch, tmp_path, Roll(
        out=str(roll / "strip-01"), start_at=7, resolution=300,
        infrared=False, meter="none"))
    assert "a roll to the end of strip 1 goes past where it ends (6)" in said
    assert "its numbers, 1-6, are fixed" in said
    assert "scanned by name gets a number after the roll's last" in said
    # Scanned by name, it was a frame of the film after all: "Strip ends
    # here" was set too early, and once strip 2 fixed strip 1's numbers
    # nothing could undo it -- every scan of it was refused. It takes the
    # number after the roll's last, and strip 2, open, is closed beside it.
    _s, _scanner, events = run(
        Roll(out=str(roll / "strip-01"), only=(7,), start_at=7,
             resolution=300, infrared=False, meter="none"),
        tmp_path, scanner=StripScanner(at=6))
    assert not kinds(events, "failed"), [e.text for e in kinds(events,
                                                               "failed")]
    progress = json.loads((roll / "strip-01" / "roll.json").read_text(
        encoding="utf-8"))
    (seventh,) = [f for f in progress["frames"] if f["number"] == 7]
    assert seventh["roll_number"] == 13
    index = _index_of(roll)
    assert [(e["strip"], e["place"], e["number"]) for e in index["extra"]] == [
        (1, 7, 13)]
    assert index["strips"][1]["last"] == 12, "strip 2 closed beside it"
    assert session.plan_next(roll).next.first == 14
    (frame,) = [e.result for e in kinds(events, "result")
                if e.result.kind == "frame"]
    assert frame.roll_number == 13
    # Walked again, it keeps that number.
    run(Roll(frames=1, start_at=7, dry_run=True, extend_walk=True,
             out=str(roll / "strip-01")), tmp_path, scanner=StripScanner(at=6))
    survey = json.loads((roll / "strip-01" / "survey.json").read_text(
        encoding="utf-8"))
    assert next(f for f in survey["frames"]
                if f["number"] == 7)["roll_number"] == 13
    assert len(_index_of(roll)["extra"]) == 1


def test_an_open_strip_is_closed_before_it_goes_and_keeps_its_numbers(
        tmp_path):
    """Moved into the trash open, strip 2 read as a strip holding nothing:
    the next walk took it again from 7 and gave 7-12 out a second time,
    while its entries and delivered files carried them, and its restore was
    refused, its place taken."""
    from conftest import HeldElsewhere

    from rps7200 import locks

    rolls = tmp_path / "rolls"
    roll = _strip_roll(rolls, "Gold200", [
        (1, 1, 6, _walked(*range(1, 7), strip_end=7)),
        (2, 7, None, _walked(*range(1, 7), strip_end=7))])
    with pytest.raises(ValueError, match="open strip: close it first"):
        library.trash(rolls, [roll / "strip-02"], by="a test", kind="strips")
    assert (roll / "strip-02").is_dir() and library.trash_batches(rolls) == []
    with HeldElsewhere(locks.ROLL, roll, what="tools/scan_roll.py"):
        with pytest.raises(ValueError, match="tools/scan_roll.py"):
            session.close_strip(roll / "strip-02", who="a test")
    assert _index_of(roll)["strips"][1]["last"] is None
    said = session.close_strip(roll / "strip-02", who="a test")
    assert said.startswith("strip 2 of Gold200 is closed at 7-12: it was "
                           "walked to the end of the strip"), said
    closed = _index_of(roll)["strips"][1]
    assert (closed["last"], closed["count_how"]) == (
        12, "walked to the end of the strip")
    assert session.close_strip(roll / "strip-02", who="a test") is None
    moved = library.trash(rolls, [roll / "strip-02"], by="a test",
                          kind="strips")
    assert moved.moved == ["strip-02"]
    offer = session.plan_next(roll)
    assert (offer.next.number, offer.next.first) == (3, 13)
    # An index that cannot be read cannot say whether a strip is open.
    fuji = _strip_roll(rolls, "Fuji", [(1, 1, 6, _walked(1))])
    (fuji / "strips.json").write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot be read"):
        library.trash(rolls, [fuji / "strip-01"], by="a test", kind="strips")


def test_an_open_strip_found_in_the_trash_is_never_taken_again(
        tmp_path, monkeypatch):
    rolls = tmp_path / "rolls"
    roll = _strip_roll(rolls, "Gold200", [
        (1, 1, 6, _walked(*range(1, 7), strip_end=7)),
        (2, 7, None, _walked(*range(1, 7), strip_end=7))])
    # Moved there some way other than a Delete, which closes it first.
    monkeypatch.setattr(library, "_refuse_open_strip", lambda *a: None)
    moved = library.trash(rolls, [roll / "strip-02"], by="a test",
                          kind="strips")
    with pytest.raises(ValueError, match="in the rolls' trash") as refused:
        session.plan_next(roll)
    assert moved.batch.name in str(refused.value)
    assert "restore Gold200/strip-02" in str(refused.value)
    # A strip folder simply gone is a claim cut short: taken again.
    other = _strip_roll(rolls, "Fuji", [(1, 1, 1, _walked(1, strip_end=2)),
                                        (2, 2, None, None)])
    (other / "strip-02").rmdir()
    offer = session.plan_next(other)
    assert (offer.next.number, offer.next.first, offer.next.reuse) == (
        2, 2, True)


def test_strip_ends_here_is_refused_past_a_frame_still_being_filed(tmp_path):
    """Read as roll.json's `done` alone, a frame the writer was still filing
    as its roll ended was passed over, and the next strip's first frame was
    given the number its delivered file already had."""
    from conftest import HeldElsewhere

    from rps7200 import locks

    rolls = tmp_path / "rolls"
    roll = _strip_roll(rolls, "Gold200", [(1, 1, None, _walked(
        *range(1, 8), strip_end=8))])
    path = roll / "strip-01" / "roll.json"
    session.write_manifest(path, {"numbering": "strip", "frames": [
        {"number": 7, "done": False, "roll_number": 7, "error": None}]})
    with pytest.raises(ValueError, match="frame 7 of strip 1 is scanned"):
        session.set_strip_end(roll / "strip-01", 6, who="a test")
    # Its entry in the library's trash since: its number is given all the
    # same, on a delivered file and on a label a restore brings back.
    session.write_manifest(path, {"numbering": "strip", "frames": [
        {"number": 7, "done": True, "entry": "library\\x",
         "entry_deleted": "x"}]})
    with pytest.raises(ValueError, match="frame 7 of strip 1 is scanned"):
        session.set_strip_end(roll / "strip-01", 6, who="a test",
                              live=lambda p: None)
    # A pass that failed numbered nothing.
    session.write_manifest(path, {"numbering": "strip", "frames": [
        {"number": 7, "done": False, "error": "the read came back short"}]})
    assert "ends at frame 6" in session.set_strip_end(roll / "strip-01", 6,
                                                      who="a test")
    session.set_strip_end(roll / "strip-01", None, who="a test")
    # A record the session holds and has not written yet.
    path.unlink()
    path.with_name("roll.json.bak").unlink(missing_ok=True)
    live = session.RollManifest(path, {"numbering": "strip", "frames": []})
    live.data["frames"].append({"number": 7, "done": False, "error": None})
    found = {path.resolve(): live}
    with pytest.raises(ValueError, match="frame 7 of strip 1 is scanned"):
        session.set_strip_end(roll / "strip-01", 6, who="a test",
                              live=lambda p: found.get(Path(p).resolve()))
    # Another program holding the roll: a refusal, never an OSError.
    with HeldElsewhere(locks.ROLL, roll, what="tools/scan_roll.py"):
        with pytest.raises(ValueError, match="in use by .*tools/scan_roll.py"):
            session.set_strip_end(roll / "strip-01", 6, who="a test")
    assert _index_of(roll)["strips"][0]["ends_at"] is None


def test_a_strip_outside_its_roll_numbers_nothing(monkeypatch, tmp_path):
    """Carried out of its roll -- the window's `--open-roll` of a strip --
    or copied, a strip read as a roll from before strips, and its second
    frame was labelled as its roll's frame 2, which is strip 1's."""
    rolls = tmp_path / "rolls"
    roll = _strip_roll(rolls, "Gold200", [
        (1, 1, 6, _walked(*range(1, 7), strip_end=7)),
        (2, 7, None, _walked(*range(1, 7), strip_end=7))])
    shutil.copytree(roll / "strip-02", rolls / "strip-02")
    said = session.strip_out_of_its_roll(rolls / "strip-02")
    assert "strip 2 of Gold200 outside its roll" in said
    assert session.strip_out_of_its_roll(roll / "strip-02") is None
    legacy = rolls / "strip-03"
    legacy.mkdir()
    (legacy / "survey.json").write_text('{"roll": "x"}', encoding="utf-8")
    assert session.strip_out_of_its_roll(legacy) is None
    said = _refused_on_the_device(monkeypatch, tmp_path, Roll(
        out=str(rolls / "strip-02"), only=(2,), start_at=2, resolution=300,
        infrared=False, meter="none"))
    assert "outside its roll" in said


def test_a_new_strip_is_never_planned_inside_a_strip(monkeypatch, tmp_path):
    """Handed a strip's folder as its roll, an empty one -- a claim that
    walked nothing -- read as a new roll, and its strips went into a roll
    nested in it that nothing reading the rolls goes deep enough to find."""
    rolls = tmp_path / "rolls"
    roll = _strip_roll(rolls, "Gold200", [
        (1, 1, 6, _walked(*range(1, 7), strip_end=7)), (2, 7, None, None)])
    said = _refused_on_the_device(monkeypatch, tmp_path, Roll(
        dry_run=True, new_strip=True, out=str(roll / "strip-02"),
        strip_plan=(1, 1)))
    assert "a strip of Gold200" in said and "into its roll, Gold200" in said
    assert not (roll / "strip-02" / library.STRIPS_INDEX).exists()
    # One with records is no roll from before strips either.
    with pytest.raises(session.RollFolderInUse, match="a strip of Gold200"):
        session.plan_next(roll / "strip-01")


def test_a_folder_found_in_another_case_is_given_in_its_own_spelling(
        tmp_path, monkeypatch):
    """PLAT-06: `rolls/gold200` found the folder `Gold200` on a disk that
    folds case, and the roll box, a new roll's label and the sheet's key
    took the spelling typed -- two spellings, two rolls in the settings
    file. Given now as the folder is spelt; never by a name a disk with no
    file ids would match to every folder."""
    rolls = tmp_path / "rolls"
    (rolls / "Gold200").mkdir(parents=True)
    assert session.as_on_disk(rolls / "Gold200") == rolls / "Gold200"
    assert session.as_on_disk(rolls / "Fuji") == rolls / "Fuji", "not there"
    if (rolls / "gold200").exists():               # a disk that folds case
        assert session.roll_dir(rolls, "gold200") == rolls / "Gold200"
        assert session.as_on_disk(rolls / "GOLD200") == rolls / "Gold200"
        # A disk that reports no file ids matches nothing.
        real = os.stat

        class NoId:
            def __init__(self, st):
                self.st = st

            def __getattr__(self, name):
                return 0 if name == "st_ino" else getattr(self.st, name)

        monkeypatch.setattr(session.os, "stat",
                            lambda p, *a, **k: NoId(real(p, *a, **k)))
        assert session.as_on_disk(rolls / "GOLD200") == rolls / "GOLD200"


# -- how far a roll got, and finishing it ------------------------------------
#
# Stefan's call, 2026-10-10 ("finish"): a reopened roll finished with no last
# frame scans the frames left, passing the done ones, then seeks on and scans
# from the first frame never reached to the end of the strip -- one job, two
# runs. roll.json records each run (`runs`), and one reader says what is left
# (`session.roll_left`), for the window, its browser and the roll tool alike.


def _frame(n, done=True, error=None):
    """A roll.json frame record: done and filed, or failed."""
    if error:
        return {"number": n, "error": error, "done": False}
    return {"number": n, "error": None, "done": done,
            "entry": f"library\\e{n}" if done else None}


def _open_run(start=1, stopped=None, reached=None, **more):
    """A run to the end of the strip from ``start``, finished."""
    run_ = {"started": "2026-10-10T20:00:00+00:00",
            "finished": "2026-10-10T21:00:00+00:00",
            "settings": {"frames": None, "only": None, "start_at": start},
            "reached": reached, **more}
    if stopped:
        run_["stopped"] = stopped
    return run_


def test_what_is_left_of_a_roll_counts_the_strip_no_run_reached():
    left = session.roll_left
    # (a) A roll.json from before runs were kept, a run to the end of the
    # strip stopped after frame 9: the rest of the strip from 10. It read
    # "finished, 9" and reopened as "nothing left to scan".
    legacy = {"numbering": "strip", "stopped": "stopped after frame 9, as asked",
              "started": "x", "finished": "y",
              "settings": {"frames": None, "start_at": 1, "only": None},
              "frames": [_frame(n) for n in range(1, 10)]}
    found = left(legacy, legacy)
    assert (found.remaining, found.rest_from, found.strip_end) == ([], 10, None)
    # Not stopped: it reached the strip's end, past its last frame.
    whole = dict(legacy)
    whole.pop("stopped")
    found = left(whole, whole)
    assert (found.rest_from, found.strip_end) == (None, 10)

    # (b) "1 to 4", then a run to the end from 5 with frame 8 failing: the
    # file wanted 1-4 and the browser hid it as finished (RES-n4).
    progress = {"numbering": "strip", "wanted": [1, 2, 3, 4],
                "frames": [_frame(n, error="timed out" if n == 8 else None)
                           for n in range(1, 10)],
                "runs": [{"settings": {"frames": 4, "only": None,
                                       "start_at": 1},
                          "finished": "x", "reached": 4},
                         _open_run(5, reached=9, strip_end=10)]}
    found = left(progress, progress)
    assert found.remaining == [8] and found.rest_from is None
    assert found.wanted == list(range(1, 10))

    # (c) A run from 5 stopped after 9, then one from 1 stopped after 2:
    # 3 and 4, which none took, are left, and the strip from 10.
    progress = {"numbering": "strip",
                "frames": [_frame(n) for n in (1, 2, 5, 6, 7, 8, 9)],
                "runs": [_open_run(5, "stopped after frame 9, as asked", 9),
                         _open_run(1, "stopped after frame 2, as asked", 2)]}
    found = left(progress, progress)
    assert (found.remaining, found.rest_from) == ([3, 4], 10)

    # (d) No run to the end of the strip: exactly what it always was.
    sheet = {"numbering": "strip", "wanted": [2, 4, 6],
             "frames": [_frame(2), _frame(4, error="timed out")],
             "runs": [{"settings": {"frames": 5, "only": [2, 4, 6],
                                    "start_at": 2}, "finished": "x",
                       "reached": 4}]}
    found = left(sheet, sheet)
    assert found.wanted == session.wanted_frames(sheet, sheet) == [2, 4, 6]
    assert (found.remaining, found.rest_from) == ([4, 6], None)

    # "Strip ends here" at 6: the frames up to it are left, and no rest.
    stopped = {"numbering": "strip", "frames": [_frame(n) for n in (1, 2, 3)],
               "runs": [_open_run(1, "stopped after frame 3, as asked", 3)]}
    found = left(stopped, stopped, ends_at=6)
    assert (found.remaining, found.rest_from, found.strip_end) == (
        [4, 5, 6], None, 7)
    assert left(stopped, stopped).rest_from == 4


def test_a_roll_records_each_run_and_where_its_copies_went(tmp_path):
    out = made(tmp_path / "out")
    run(Roll(frames=2, resolution=600, name="runs"), tmp_path,
        extra=lambda s, _: setattr(s, "out_dir", out))
    first = _manifest_of(tmp_path, "runs")
    (run1,) = first["runs"]
    assert run1["settings"] == first["settings"]
    assert run1["output"] == {"folder": str(out), "format": "tiff",
                              "quality": run1["output"]["quality"]}
    assert run1["finished"] and run1["reached"] == 2
    assert "stopped" not in run1 and "strip_end" not in run1, "two asked"
    # A second run carries the first, as it carries its frames.
    run(Roll(frames=1, start_at=3, resolution=600, name="runs"), tmp_path,
        scanner=FakeScanner(frames=1))
    second = _manifest_of(tmp_path, "runs")
    assert second["runs"][0] == run1
    assert second["runs"][1]["settings"]["start_at"] == 3
    assert second["runs"][1]["output"]["folder"] is None
    # A roll.json from before runs were kept gives its top as its first.
    path = tmp_path / "rolls" / "old" / "roll.json"
    path.parent.mkdir(parents=True)
    session.write_manifest(path, {
        "roll": "old", "numbering": "strip", "started": "then",
        "finished": "later", "settings": {"frames": 2, "start_at": 1,
                                          "only": None},
        "frames": [_frame(1), _frame(2)]})
    run(Roll(frames=1, start_at=3, resolution=600, name="old"), tmp_path,
        scanner=FakeScanner(frames=1))
    runs = _manifest_of(tmp_path, "old")["runs"]
    assert [r["settings"]["start_at"] for r in runs] == [1, 3]
    assert runs[0]["started"] == "then"


def test_a_run_the_driver_gave_up_on_says_so_and_leaves_the_rest(tmp_path):
    """RES-n2: a run to the end of the strip the driver gave up on, after
    its failures in a row, ended as one that reached the strip's end."""
    from conftest import StripScanner

    class GivesUp(StripScanner):
        def scan_roll(self, **kw):
            self.roll_cut_short = None
            for n, frame in enumerate(super().scan_roll(**kw)):
                yield frame
                if n == 3:
                    self.roll_cut_short = "gave up after 3 consecutive failures"
                    return

    _s, _sc, events = run(Roll(resolution=600, infrared=False, name="gave"),
                          tmp_path, scanner=GivesUp(last=11))
    manifest = _manifest_of(tmp_path, "gave")
    assert manifest["stopped"] == "gave up after 3 consecutive failures"
    run_ = manifest["runs"][-1]
    assert run_["cut_short"] == run_["stopped"] and run_["reached"] == 4
    assert "strip_end" not in run_
    assert session.roll_left(manifest, manifest).rest_from == 5
    assert any("gave up" in e.text for e in kinds(events, "finished"))
    # One that reached the strip's end: no stop, and where it ends.
    run(Roll(resolution=600, infrared=False, name="whole"), tmp_path,
        scanner=StripScanner(last=3))
    manifest = _manifest_of(tmp_path, "whole")
    assert "stopped" not in manifest
    assert manifest["runs"][-1]["strip_end"] == 5
    found = session.roll_left(manifest, manifest)
    assert (found.rest_from, found.strip_end) == (None, 5)


def _half_done(tmp_path, name="fin"):
    """A roll that ran to the end of its strip from 1 and stopped after 9,
    with 3 and 7 failed: 1-2, 4-6 and 8-9 done."""
    path = tmp_path / "rolls" / name / "roll.json"
    path.parent.mkdir(parents=True)
    session.write_manifest(path, {
        "roll": name, "numbering": "strip", "dpi": 600, "infrared": False,
        "film": "negative", "meter": "each",
        "settings": {"resolution": 600, "infrared": False, "film": "negative",
                     "meter": "each", "frames": None, "start_at": 1,
                     "only": None},
        "stopped": "stopped after frame 9, as asked",
        "frames": [_frame(n, error="timed out" if n in (3, 7) else None)
                   for n in range(1, 10)]})
    return path


def test_the_frames_left_and_then_the_rest_of_the_strip_are_one_job(
        tmp_path):
    from conftest import StripScanner

    path = _half_done(tmp_path)
    scanner = StripScanner(at=0, last=11)
    _s, _sc, events = run(Roll(start_at=3, only=(3, 7), rest_from=10,
                               resolution=600, infrared=False, name="fin"),
                          tmp_path, scanner=scanner)
    assert not kinds(events, "failed"), [e.text for e in kinds(events,
                                                              "failed")]
    # Two runs: the frames left, passing 4-6; then a seek past 8-9 and on.
    assert [(r["first_index"], r["only"], r["frames"])
            for r in scanner.rolls] == [(2, (2, 6), None), (9, None, None)]
    frames = sorted(e.result.number for e in kinds(events, "result")
                    if e.result.kind == "frame")
    assert frames == [3, 7, 10, 11, 12]
    manifest = json.loads(path.read_text(encoding="utf-8"))
    runs = manifest["runs"]
    assert len(runs) == 3, "the one before runs were kept, and these two"
    assert runs[1]["settings"]["only"] == [3, 7] and runs[1]["reached"] == 7
    assert runs[2]["settings"]["start_at"] == 10
    assert runs[2]["strip_end"] == 13
    found = session.roll_left(manifest, manifest)
    assert (found.remaining, found.rest_from) == ([], None)
    assert len(kinds(events, "finished")) == 1
    assert any("then 10 to the end of the strip" in e.text
               for e in kinds(events, "state"))


def test_a_stop_in_the_frames_left_starts_no_rest(tmp_path):
    from conftest import StripScanner

    holder = {}

    class Stopped(StripScanner):
        def scan_roll(self, **kw):
            for frame in super().scan_roll(**kw):
                holder["s"].request_stop()
                yield frame

    _half_done(tmp_path)
    scanner = Stopped(at=0, last=11)
    _s, _sc, events = run(Roll(start_at=3, only=(3, 7), rest_from=10,
                               resolution=600, infrared=False, name="fin"),
                          tmp_path, scanner=scanner,
                          extra=lambda s, _: holder.update(s=s))
    assert len(scanner.rolls) == 1, "the rest was begun"
    (finished,) = kinds(events, "finished")
    assert "as asked" in finished.text


def test_a_rest_whose_strip_ends_just_before_it_finishes_the_roll(tmp_path):
    from conftest import StripScanner

    path = _half_done(tmp_path)
    scanner = StripScanner(at=0, last=8)            # nine frames
    _s, _sc, events = run(Roll(start_at=3, only=(3, 7), rest_from=10,
                               resolution=600, infrared=False, name="fin"),
                          tmp_path, scanner=scanner)
    assert not kinds(events, "failed")
    (finished,) = kinds(events, "finished")
    assert finished.text == "the strip ends at frame 9; the roll is finished"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["runs"][-1]["strip_end"] == 10
    assert session.roll_left(manifest, manifest).rest_from is None
    assert any("strip ends at frame 9" in e.text for e in kinds(events, "log"))

    # A seek that runs out into a folder with no roll.json writes nothing.
    _s, _sc, events = run(Roll(start_at=12, resolution=600, infrared=False,
                               name="fresh"), tmp_path,
                          scanner=StripScanner(at=0, last=8))
    assert kinds(events, "failed")
    assert not (tmp_path / "rolls" / "fresh" / "roll.json").exists()


def test_the_rest_alone_whose_strip_ends_just_before_it_finishes_the_roll(
        tmp_path):
    """A roll to the end of the strip stopped on its true last frame,
    reopened: nothing left but the rest, which the window hands over as a
    plain roll from there. Only the second part of a finishing job turned
    the strip's end into a finished roll; this one failed at it, red, the
    roll finished all the same."""
    from conftest import StripScanner

    path = tmp_path / "rolls" / "rest" / "roll.json"
    path.parent.mkdir(parents=True)
    session.write_manifest(path, {
        "roll": "rest", "numbering": "strip",
        "settings": {"resolution": 600, "infrared": False, "frames": None,
                     "start_at": 1, "only": None},
        "stopped": "stopped after frame 9, as asked",
        "frames": [_frame(n) for n in range(1, 10)]})
    before = json.loads(path.read_text(encoding="utf-8"))
    assert session.roll_left(before, before).rest_from == 10
    _s, scanner, events = run(Roll(start_at=10, resolution=600,
                                   infrared=False, name="rest"),
                              tmp_path, scanner=StripScanner(at=0, last=8))
    assert not kinds(events, "failed"), [e.text for e in kinds(events,
                                                              "failed")]
    (finished,) = kinds(events, "finished")
    assert finished.text == "the strip ends at frame 9; the roll is finished"
    assert scanner.rolls == [], "nothing was scanned"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["runs"][-1]["strip_end"] == 10
    found = session.roll_left(manifest, manifest)
    assert (found.remaining, found.rest_from, found.strip_end) == (
        [], None, 10)
    # A count of frames asked past the end is still refused: it named
    # frames the strip does not have.
    _s, _sc, events = run(Roll(start_at=10, frames=2, resolution=600,
                               infrared=False, name="rest"),
                          tmp_path, scanner=StripScanner(at=0, last=8))
    assert kinds(events, "failed") and not kinds(events, "finished")


def test_a_seek_run_out_short_of_the_strips_records_is_not_its_end(tmp_path):
    """A seek runs out wherever an advance leaves the counter alone: the
    strip not put back, put in another way, the transport stalling. Every
    one was recorded as the strip's end, and every reader took it for the
    latest word -- on a strip of a roll of strips, the next strip then
    closed it at the frames it had."""
    from conftest import StripScanner

    rolls = tmp_path / "rolls"
    roll = _strip_roll(rolls, "Gold200", [(1, 1, None, None)])
    folder = roll / "strip-01"
    path = folder / "roll.json"
    session.write_manifest(path, {
        "roll": "Gold200", "numbering": "strip",
        "strip": {"number": 1, "first": 1},
        "settings": {"resolution": 600, "infrared": False, "frames": None,
                     "start_at": 1, "only": None},
        "stopped": "stopped after frame 4, as asked",
        "frames": [_frame(n) for n in range(1, 5)]})
    written = path.read_bytes()
    count = session.strip_count(folder)
    assert not count.complete and count.count == 4

    # The film not put back: the transport on frame 1 and going nowhere.
    _s, _sc, events = run(Roll(start_at=5, resolution=600, infrared=False,
                               out=str(folder)),
                          tmp_path, scanner=StripScanner(at=0, last=0))
    (failed,) = kinds(events, "failed")
    assert "the strip ended on frame 1, before frame 5" in failed.text
    assert path.read_bytes() == written, "recorded as the strip's end"
    assert any("its records hold frames to 4" in e.text
               for e in kinds(events, "log"))
    count = session.strip_count(folder)
    assert not count.complete and count.count == 4
    assert session.plan_next(roll).again is not None, "still offered"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert session.roll_left(data, data).rest_from == 5

    # A first frame typed past the end: the strip ending on frame 4 agrees
    # with the records, but not with the frame before the one asked for.
    _s, _sc, events = run(Roll(start_at=8, resolution=600, infrared=False,
                               out=str(folder)),
                          tmp_path, scanner=StripScanner(at=0, last=3))
    assert kinds(events, "failed")
    assert path.read_bytes() == written
    assert any("the roll was to start at frame 8" in e.text
               for e in kinds(events, "log"))

    # A walk that saw frame 6 outranks a seek that ran out on frame 4.
    session.write_manifest(folder / "survey.json", {
        "roll": "Gold200", "numbering": "strip", "dry_run": True,
        "strip": {"number": 1, "first": 1},
        "stopped": "stopped after frame 6, as asked",
        "frames": [{"number": n} for n in range(1, 7)]})
    _s, _sc, events = run(Roll(start_at=5, resolution=600, infrared=False,
                               out=str(folder)),
                          tmp_path, scanner=StripScanner(at=0, last=3))
    assert kinds(events, "failed")
    assert path.read_bytes() == written


def test_what_is_left_reads_where_the_walk_found_the_strips_end():
    """A strip walked to its end at 7, a run to the end of the strip
    stopped after frame 3, then 4-6 from its sheet: "the strip from 7" was
    left for good, from the runs alone, while `strip_count` called the
    strip whole."""
    walk = {"numbering": "strip", "dry_run": True, "strip_end": 7,
            "frames": [{"number": n} for n in range(1, 7)]}
    progress = {"numbering": "strip",
                "frames": [_frame(n) for n in range(1, 7)],
                "runs": [_open_run(1, "stopped after frame 3, as asked", 3),
                         {"settings": {"frames": None, "only": [4, 5, 6],
                                       "start_at": 4},
                          "finished": "x", "reached": 6}]}
    found = session.roll_left(walk, progress)
    assert (found.remaining, found.rest_from, found.strip_end) == (
        [], None, 7)
    assert session.roll_left(progress, progress).rest_from == 7, \
        "roll.json alone knows no end"
    # The walk's end is no walk's where the manifest is a roll.json.
    assert session.roll_left(dict(progress, strip_end=7),
                             progress).rest_from == 7

    # A run asked to start past an end the strip is known to have, from
    # before a seek's end was held to the records: no rest past it.
    typed = {"numbering": "strip",
             "frames": [_frame(n) for n in range(1, 10)],
             "runs": [{"settings": {"frames": 9, "only": None,
                                    "start_at": 1},
                       "finished": "x", "reached": 9},
                      _open_run(12, "the strip ended on frame 9", None,
                                strip_end=10)]}
    found = session.roll_left(typed, typed)
    assert (found.rest_from, found.strip_end) == (None, 10)


def test_a_retake_of_done_frames_keeps_the_rolls_settings(tmp_path):
    """RES-n3: a one-frame retake at 1200 dpi RGBI in a roll of 600 RGB
    wrote 1200 RGBI as the roll's settings, which reopening put back for
    the frames left and `tools/scan_roll.py` then held a resume to."""
    from conftest import StripScanner

    run(Roll(frames=3, resolution=600, infrared=False, name="take"), tmp_path,
        scanner=StripScanner())
    before = _manifest_of(tmp_path, "take")
    run(Roll(start_at=2, frames=1, only=(2,), resolution=1200, infrared=True,
             name="take"), tmp_path, scanner=StripScanner())
    after = _manifest_of(tmp_path, "take")
    assert after["settings"] == before["settings"]
    assert (after["dpi"], after["infrared"]) == (600, False)
    retake = after["runs"][-1]
    assert retake["retake"] is True
    assert (retake["settings"]["resolution"],
            retake["settings"]["infrared"]) == (1200, True)
    assert session.manifest_settings(after)["resolution"] == 600
    # A frame not done is no retake: that run's settings are the roll's.
    run(Roll(start_at=4, frames=1, resolution=1200, infrared=True,
             name="take"), tmp_path, scanner=StripScanner())
    assert _manifest_of(tmp_path, "take")["settings"]["resolution"] == 1200


def test_settings_differ_says_what_decides_the_pixels():
    was = {"resolution": 1800, "infrared": True, "film": "negative",
           "meter": "each", "fast_infrared": True}
    same = dict(was)
    assert session.settings_differ(was, same) == []
    assert session.settings_differ(was, dict(was, resolution=3600)) == [
        "dpi 1800 where this run asks for 3600"]
    # Tied infrared only between two infrared runs.
    assert session.settings_differ(was, dict(was, fast_infrared=False)) == [
        "tied infrared True where this run asks for False"]
    assert session.settings_differ(
        dict(was, infrared=False), dict(was, infrared=False,
                                        fast_infrared=False)) == []
    # Nothing to hold to where the roll does not say it.
    assert session.settings_differ({}, dict(was, meter="once")) == []


def test_a_roll_of_strips_is_held_to_its_settings_across_its_strips(
        tmp_path):
    rolls = tmp_path / "rolls"
    roll = _strip_roll(rolls, "Gold200", [(1, 1, 6, None), (2, 7, None, None)])
    session.write_manifest(roll / "strip-01" / "roll.json", {
        "numbering": "strip", "settings": {"resolution": 1800,
                                           "infrared": False},
        "frames": [_frame(1)]})
    now = {"resolution": 3600, "infrared": True, "film": "negative",
           "meter": "each", "fast_infrared": True}
    (found,) = session.roll_differs(roll / "strip-02", now)
    path, was, differs = found
    assert path == roll / "strip-01" / "roll.json"
    assert was["resolution"] == 1800 and "dpi 1800" in differs[0]
    assert session.roll_differs(roll, now) == [found], "a new strip too"
    assert session.roll_differs(
        roll / "strip-02", dict(now, resolution=1800, infrared=False)) == []


def test_a_frames_copy_is_named_in_its_record_once_it_is_written(
        tmp_path, monkeypatch):
    """PLAT-11: the window's records named no file, so every reader took
    the frameNN.tif on the disk for this take's -- an earlier take's where
    this one's could not be written."""
    real = session.export.write

    def refuse(target, *a, **kw):
        if Path(target).name == "frame02.tif":
            raise OSError(28, "No space left on device")
        return real(target, *a, **kw)

    monkeypatch.setattr(session.export, "write", refuse)
    run(Roll(frames=2, resolution=600, name="copies"), tmp_path)
    first, second = _manifest_of(tmp_path, "copies")["frames"]
    assert first["done"] and first["file"] == "frame01.tif"
    assert first["takes"][-1]["file"] == "frame01.tif"
    assert "file_error" not in first
    assert second["done"], "its pass is in the library"
    assert "file" not in second and "No space left" in second["file_error"]


def test_a_filing_that_crashed_still_says_its_copy_was_not_written():
    filing = session.Filing(seq=1, number=1, kind="scan",
                            outcome=session.NOT_FILED,
                            error="orient: a bad turn")
    assert filing.copy_said("frame01.tif") == {"file_error":
                                               "orient: a bad turn"}
    written = session.Filing(seq=1, number=1, kind="scan",
                             outcome=session.FILED,
                             written=(Path("r") / "frame01.tif",))
    assert written.copy_said(Path("r") / "frame01.tif") == {
        "file": "frame01.tif"}
    refused = session.Filing(seq=1, number=1, kind="scan",
                             outcome=session.COPY_FAILED,
                             unwritten=((str(Path("r") / "frame01.tif"),
                                         "held open"),))
    assert refused.copy_said(Path("r") / "frame01.tif") == {
        "file_error": "held open"}


def test_a_slides_file_is_said_by_its_pictures_filing(tmp_path, monkeypatch):
    """A slide's frameNN.tif is its picture's, filed into no entry before
    the RGB pass the frame's record waits for -- whose own filing writes no
    copy, and would have said every slide frame had none."""
    from test_colour_pass import SlideScanner

    run(Roll(frames=1, resolution=600, infrared=True, film="positive",
             name="slides"), tmp_path, scanner=SlideScanner())
    (record,) = _manifest_of(tmp_path, "slides")["frames"]
    assert record["file"] == "frame01.tif" and "file_error" not in record

    # Its copy refused: said, and the frame done -- both passes are filed.
    real = session.export.write

    def refuse(target, *a, **kw):
        if Path(target).name == "frame01.tif":
            raise OSError(28, "No space left on device")
        return real(target, *a, **kw)

    monkeypatch.setattr(session.export, "write", refuse)
    run(Roll(frames=1, resolution=600, infrared=True, film="positive",
             name="refused"), tmp_path, scanner=SlideScanner())
    (record,) = _manifest_of(tmp_path, "refused")["frames"]
    assert record["done"] and "No space left" in record["file_error"]
    monkeypatch.setattr(session.export, "write", real)

    # Its filing crashed -- what `_write` did not foresee, `preview.orient`
    # raising: nothing written, and nothing said unwritten.
    real_write = session.FrameWriter._write

    def crash(self, job):
        if job.get("part") == "picture":
            raise RuntimeError("a bad turn")
        return real_write(self, job)

    monkeypatch.setattr(session.FrameWriter, "_write", crash)
    run(Roll(frames=1, resolution=600, infrared=True, film="positive",
             name="crashed"), tmp_path, scanner=SlideScanner())
    (record,) = _manifest_of(tmp_path, "crashed")["frames"]
    assert "a bad turn" in record["file_error"] and "file" not in record


def test_each_frames_answer_lands_on_its_own_record(tmp_path, monkeypatch):
    """The writer answers up to two frames late: bound late, frame 1's
    answer was asked of frame 2's file, and said frame 1 had none."""
    recorded = threading.Event()
    real_record = session.RollManifest.record

    def record(self, record_, awaiting=False):
        real_record(self, record_, awaiting=awaiting)
        if record_.get("number") == 2:
            recorded.set()

    real_write = session.FrameWriter._write

    def held(self, job):
        if job.get("kind") == "scan" and job.get("number") == 1:
            recorded.wait(10)
        return real_write(self, job)

    real = session.export.write

    def refuse(target, *a, **kw):
        if Path(target).name == "frame02.tif":
            raise OSError(28, "No space left on device")
        return real(target, *a, **kw)

    monkeypatch.setattr(session.RollManifest, "record", record)
    monkeypatch.setattr(session.FrameWriter, "_write", held)
    monkeypatch.setattr(session.export, "write", refuse)
    run(Roll(frames=2, resolution=600, name="late"), tmp_path,
        scanner=FakeScanner(frames=2))
    first, second = _manifest_of(tmp_path, "late")["frames"]
    assert first["file"] == "frame01.tif" and "file_error" not in first
    assert "No space left" in second["file_error"]


def test_a_strip_scanned_to_its_end_is_whole_by_its_runs(tmp_path):
    """The interim test of a strip's end read roll.json's top, which the
    next run wrote afresh; it is the runs' now (`roll_left`)."""
    rolls = tmp_path / "rolls"
    roll = _strip_roll(rolls, "R", [(1, 1, None, None)])
    session.write_manifest(roll / "strip-01" / "roll.json", {
        "numbering": "strip", "frames": [_frame(n) for n in range(1, 7)],
        "runs": [_open_run(1, reached=6, strip_end=7),
                 {"settings": {"frames": 1, "only": [3], "start_at": 3},
                  "finished": "x", "reached": 3, "retake": True}]})
    count = session.strip_count(roll / "strip-01")
    assert (count.count, count.complete, count.how) == (
        6, True, "scanned to the end of the strip")
    session.write_manifest(roll / "strip-01" / "roll.json", {
        "numbering": "strip", "frames": [_frame(n) for n in range(1, 5)],
        "runs": [_open_run(1, "gave up after 3 consecutive failures", 4,
                           cut_short="gave up after 3 consecutive failures")]})
    count = session.strip_count(roll / "strip-01")
    assert not count.complete and count.how == "roll was cut short after frame 4"
