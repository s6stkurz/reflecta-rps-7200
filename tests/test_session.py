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
import threading
import time

from pathlib import Path

import numpy as np
import pytest

from rps7200 import library, session, tiff
from rps7200.direct import RollFrame
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

    def scan(self, resolution=1800, infrared=True, **kw):
        image, meta = super().scan(resolution=resolution, infrared=infrared, **kw)
        self.last_pixels_raw = image
        corrected = (image // 2).astype(image.dtype)
        return corrected, dict(meta, shading={"columns": 36, "clipped": 0})

    def debug_claim(self, pixels):
        self.claimed.append(pixels)


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
        scanner.last_scan_meta = scanner_meta
        scanner.last_pixels_raw = None

    _, scanner, _ = run(Prescan(), tmp_path, extra=with_meta)
    record = library.entries(tmp_path)[0]
    assert record["scan"]["protocol_revision"] == 99, \
        "the pass's own meta must reach the entry, not a hand-built stand-in"
    assert record["calibration"]["report"] == {"columns": 16, "width": 16}


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
    recorded = json.loads(manifest.read_text(encoding="utf-8"))
    assert len(recorded["frames"]) == 3
    assert recorded["frames"][0]["registration"]["offset_mm"] == 0.04


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

    scanner.scan_roll = scan_roll

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


def test_a_real_roll_does_not_file_its_prescans_separately(tmp_path):
    """They ride along with the frame instead. Filing both would double a roll."""
    run(Roll(frames=3, dry_run=False, name="real"), tmp_path)
    entries = library.entries(tmp_path)
    assert len(entries) == 3
    assert not any("prescan" in e["tags"] for e in entries)


def test_only_the_chosen_frames_are_scanned(tmp_path):
    """The whole point of surveying first: four good frames, not seventeen."""
    _, scanner, _ = run(
        Roll(frames=5, resolution=600, name="picked", only=(1, 4)), tmp_path
    )
    # The window counts pictures from 1; the driver counts them from 0.
    assert scanner.only == (0, 3)
    assert len(library.entries(tmp_path)) == 2
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
    assert len(library.entries(tmp_path)) == 3


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
    """They ride along inside the frame's entry; a stray copy is just clutter."""
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


def test_a_roll_stops_when_a_frame_cannot_be_filed(tmp_path):
    """A full disk or a vanished output folder fails every frame after it the
    same way; scanning on spent the rest of the roll on pictures that were
    then thrown away, one log line each."""
    scanner = FakeScanner(frames=6)
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    s = ScanSession(root=str(blocker / "library"), rolls=str(tmp_path / "rolls"),
                    open_scanner=lambda: scanner, verbose=False)
    # Slow the roll a little, so the first failed filing lands before the end.
    real = scanner.scan

    def scan(**kw):
        time.sleep(0.05)
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
    assert scanner.produced < 6, "the roll scanned on into a library it could not write"
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
    # A dead session does not politely close the device it just yanked.
    assert scanner.closed is False


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


def test_a_copy_that_cannot_be_written_does_not_cost_the_entry(tmp_path):
    """The library entry holds the only raw bytes; a delivered copy can be
    exported again from it. Written copies-first, an unplugged output drive
    raised before the entry was filed, and the scan was gone."""
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"not a directory")
    done: queue.Queue = queue.Queue()
    writer = session.FrameWriter(on_done=lambda *a: done.put(a))
    writer.submit(number=1, paths=[blocker / "no.tif", tmp_path / "out" / "yes.tif"],
                  image=picture(), meta={"resolution_dpi": 600}, dpi=600,
                  library=str(tmp_path / "lib"), film=FilmNotes(), tags=[],
                  prescan=None, inquiry=None, capture={}, seq=1)
    writer.finish()
    _seq, _number, entry, err = done.get_nowait()
    assert entry is not None and (entry / "scan.tif").exists()
    assert err is None
    assert (tmp_path / "out" / "yes.tif").exists(), "one bad copy stopped the rest"
    assert len(writer.errors) == 1 and "exported again" in writer.errors[0]


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


def test_one_frame_of_a_roll_can_be_turned_without_turning_the_rest(tmp_path):
    """A strip is not one orientation.

    A portrait among landscapes is the ordinary case, and one number for the
    whole roll can only ever get one of them right -- so the contact sheet's
    turn is per frame, and this is the check that it stays per frame all the
    way to the files.
    """
    out = tmp_path / "out"
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
                    out_dir=str(tmp_path / "out"), open_scanner=FakeScanner,
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
    assert [e["film"]["frame"] for e in library.entries(tmp_path)] == [
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
    assert [e["film"]["frame"] for e in library.entries(tmp_path)] == [
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


def test_the_correction_reaches_every_file_that_leaves_here(tmp_path):
    """The reversal is recorded in the meta so the library entry can stay
    exactly what the scanner sent. That is bookkeeping, not restraint: every
    file delivered to the operator is turned by it, whatever container it is
    written in and wherever it is written to."""
    from PIL import Image

    from rps7200 import preview, tiff
    for fmt, suffix in (("tiff", ".tif"), ("jpeg", ".jpg")):
        out = tmp_path / fmt
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
