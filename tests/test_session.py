"""The job engine behind the GUI: one thread owns the scanner, events come back.

The point of these tests is the two things that are easy to get wrong and
expensive to get wrong on the hardware: that every pass reaches the library
exactly once, and that a stop lands *between* frames rather than inside one.
Abandoning a read mid-scan is what costs a power cycle.

`FakeScanner` stands in for `DirectScanner` at the level the session actually
uses it. It stays here rather than in conftest because it is tuned to this loop,
the same reason `FakeRoll` stays beside test_roll.
"""
import queue
import threading
import time

import numpy as np
import pytest

from rps7200 import library, session
from rps7200.direct import RollFrame
from rps7200.library import FilmNotes
from rps7200.session import (
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

    def prescan(self, resolution=300, frame=None, keep_raw=False):
        self.calls.append(("prescan", resolution, keep_raw))
        if self.progress_hook:
            self.progress_hook(287, 287)
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
        }

    def scan_roll(self, frames=None, resolution=1800, infrared=True,
                  dry_run=False, skip=0, **kw):
        self.calls.append(("roll", frames, resolution, dry_run, skip))
        for i in range(frames or self._frames):
            if self._on_yield:
                # Stands in for the operator pressing stop while this frame is
                # still being scanned.
                self._on_yield(i)
            self.produced += 1
            image, meta = (None, {}) if dry_run else self.scan(
                resolution=resolution, infrared=infrared
            )
            yield RollFrame(
                index=skip + i, position=skip + i, image=image, meta=meta,
                prescan=picture(channels=3, seed=i),
                registration={"offset_mm": 0.04, "shortfall_mm": 0.02},
            )


def run(job, tmp_path, scanner=None, extra=None, timeout=10.0):
    """Submit `job` (and any `extra`), wait for the session to close, return events."""
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


def test_a_prescan_is_filed_too(tmp_path):
    """CLAUDE.md says file every scan, without an exception for the cheap ones.
    A prescan is ~370 KB and it is the evidence about framing."""
    run(Prescan(), tmp_path)
    entries = library.entries(tmp_path)
    assert len(entries) == 1
    assert "prescan" in entries[0]["tags"]
    assert "gui" in entries[0]["tags"]


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
    entries = library.entries(tmp_path)
    # Three pictures, and nothing filed twice.
    assert len(entries) == 3
    assert all("roll" in e["tags"] and "strip1" in e["tags"] for e in entries)


def test_a_roll_writes_its_manifest_after_every_frame(tmp_path):
    """A roll takes hours. A crash should cost the frame it was on, not the roll."""
    run(Roll(frames=3, resolution=600, name="strip2"), tmp_path)
    manifest = tmp_path / "rolls" / "strip2" / "roll.json"
    assert manifest.exists()
    import json
    recorded = json.loads(manifest.read_text())
    assert len(recorded["frames"]) == 3
    assert recorded["frames"][0]["registration"]["offset_mm"] == 0.04


def test_a_dry_run_files_its_prescans(tmp_path):
    """They are the entire product of a dry run -- there is no frame to hang
    them off, and a walk that leaves nothing behind cannot be looked at later."""
    run(Roll(frames=3, dry_run=True, name="walk"), tmp_path)
    entries = library.entries(tmp_path)
    assert len(entries) == 3
    assert all("prescan" in e["tags"] for e in entries)


def test_a_real_roll_does_not_file_its_prescans_separately(tmp_path):
    """They ride along with the frame instead. Filing both would double a roll."""
    run(Roll(frames=3, dry_run=False, name="real"), tmp_path)
    entries = library.entries(tmp_path)
    assert len(entries) == 3
    assert not any("prescan" in e["tags"] for e in entries)


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
    assert len(library.entries(tmp_path)) == 2
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
    # A dead session does not politely close the device it just yanked.
    assert scanner.closed is False


# -- events -----------------------------------------------------------------


def test_the_driver_log_reaches_the_event_queue(tmp_path):
    """The GUI shows these lines without capturing stdout."""
    _, _, events = run(Calibrate(mode="measure"), tmp_path)
    assert any("shading calibrated" in e.text for e in kinds(events, "log"))


def test_progress_arrives_as_numbers_not_as_text(tmp_path):
    """A progress bar that parses the log line goes quietly dead the day the
    line is reworded."""
    _, _, events = run(Prescan(), tmp_path)
    progress = kinds(events, "progress")
    assert progress and progress[-1].done == 287 and progress[-1].total == 287


def test_a_result_carries_a_working_copy_the_ui_can_keep(tmp_path):
    _, _, events = run(Scan(resolution=600), tmp_path)
    results = kinds(events, "result")
    assert len(results) == 1
    image = results[0].result.image
    assert image is not None
    assert max(image.shape[:2]) <= 512 or max(image.shape[:2]) <= 1400
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


def test_the_device_closes_before_the_writer_spends_time_gzipping(tmp_path):
    """Gzipping a library entry with the device open and idle preceded a wedge."""
    order = []
    scanner = FakeScanner()
    real_close = scanner.close

    def watched_close():
        order.append("device closed")
        real_close()

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
    assert order == ["device closed", "writer finished"]


# -- estimates --------------------------------------------------------------


def test_infrared_never_estimates_below_its_floor():
    """A pass with infrared on holds the device for ~212 s however few lines
    were asked for. That floor is why a short timeout once wedged it."""
    for dpi in (25, 300, 600, 1800):
        assert estimate_seconds(dpi, infrared=True) >= session.INFRARED_FLOOR_S


def test_the_estimates_track_what_was_measured():
    """900 dpi RGB 38.8 s, 1800 dpi RGB 69.6 s, 1800 dpi RGBI 227 s."""
    assert estimate_seconds(900, False) == pytest.approx(38.8, abs=3)
    assert estimate_seconds(1800, False) == pytest.approx(69.6, abs=3)
    assert estimate_seconds(1800, True) == pytest.approx(227, abs=10)
    assert estimate_seconds(3600, True) == pytest.approx(334, abs=20)


def test_more_resolution_never_estimates_less_time():
    previous = 0.0
    for dpi in (300, 600, 900, 1800, 3600, 7200):
        now = estimate_seconds(dpi, False)
        assert now >= previous
        previous = now


# -- the writer -------------------------------------------------------------


def test_the_writer_collects_failures_rather_than_raising(tmp_path):
    """A roll runs for hours; a frame that cannot be filed should cost that
    frame, not the thirty after it."""
    done: queue.Queue = queue.Queue()
    writer = session.FrameWriter(on_done=lambda *a: done.put(a))
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


def test_the_writer_runs_off_the_calling_thread():
    seen = {}

    def note(seq, number, entry, err):
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


def test_a_stop_reaches_the_driver_not_only_the_consumer_loop(tmp_path):
    """The session has to hand `scan_roll` a way to check before it advances;
    checking only between yields lets one more frame happen."""
    import inspect
    from rps7200.session import ScanSession
    source = inspect.getsource(ScanSession._roll)
    assert "should_stop=self._stop.is_set" in source


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
    assert [e.done for e in kinds(events, "transport")] == [4]


def test_moving_back_a_frame_uses_the_reverse(tmp_path):
    scanner = FakeTransportScanner(position=3)
    _, scanner, events = run(Move(frames=-1), tmp_path, scanner=scanner)
    assert scanner.moves == [("retreat", 1)]
    assert [e.done for e in kinds(events, "transport")] == [2]


def test_a_transport_that_will_not_move_is_reported_not_raised(tmp_path):
    scanner = FakeTransportScanner(stuck=True)
    _, _, events = run(Move(frames=1), tmp_path, scanner=scanner)
    assert any("did not move" in e.text for e in kinds(events, "finished"))


def test_a_nudge_says_the_frame_counter_cannot_confirm_it(tmp_path):
    """The counter does not see a sub-frame move. Reporting a position as if it
    had changed would be the one misleading thing this control could do."""
    scanner = FakeTransportScanner(position=3)
    _, scanner, events = run(Move(millimetres=0.27), tmp_path, scanner=scanner)
    assert scanner.moves == [("nudge", 0.27)]
    assert any("prescan to check it landed" in e.text
               for e in kinds(events, "finished"))
    # Position is still whatever it was; nothing pretends otherwise.
    assert [e.done for e in kinds(events, "transport")] == [3]


def test_a_move_never_files_anything(tmp_path):
    run(Move(frames=1), tmp_path, scanner=FakeTransportScanner())
    assert library.entries(tmp_path) == []


def test_moving_several_frames_steps_one_at_a_time(tmp_path):
    """So the position is confirmed at every frame, and a strip that runs out
    part-way stops there rather than being asked for the rest."""
    scanner = FakeTransportScanner(position=0)
    _, scanner, events = run(Move(frames=3), tmp_path, scanner=scanner)
    assert scanner.moves == [("advance", 1)] * 3
    assert [e.done for e in kinds(events, "transport")] == [1, 2, 3]


# -- rotation ---------------------------------------------------------------


def test_a_turn_reaches_the_delivered_file_but_not_the_entry(tmp_path):
    """The entry's pixels have to keep matching the raw bytes filed beside
    them, or `library.reconstruct` is right to call it a changed decode. The
    file the operator actually wanted is a different question."""
    out = tmp_path / "out"
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
    s = ScanSession(root=str(tmp_path / "lib"), rolls=str(tmp_path / "r"),
                    out_dir=str(out), open_scanner=FakeScanner, verbose=False)
    s.start()
    s.submit(Scan(resolution=600, infrared=True))
    s.shutdown()
    s.join(timeout=15)
    from rps7200 import tiff
    assert tiff.read(str(sorted(out.rglob("*.tif"))[0])).shape[:2] == (24, 36)


# -- what the files are called ----------------------------------------------


def test_a_roll_names_its_files_by_roll_and_frame(tmp_path):
    """NegPy reads these next. What this replaced led with a timestamp and
    ended with a sequence number, so it sorted by when it was scanned and said
    nothing about what it was -- fine for one pass, useless for thirty-eight."""
    out = tmp_path / "out"
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
