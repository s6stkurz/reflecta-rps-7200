"""Every pass records the heavy host work that ran beside it.

Stefan's decision of 2026-10-13 ("measure"): rather than a controlled load
test, every pass says which work overlapped it and for how long, so ordinary
rolls and window use answer CLAUDE.md's open-device question from the path
an operator runs. The ledger is stamped by the work and read by the driver
once, after a pass's last READ; it must never raise into either.
"""
import numpy as np
import pytest

from conftest import scanner_at_commands
from rps7200 import hostload, library, session
from rps7200.library import FilmNotes


@pytest.fixture(autouse=True)
def ledger(monkeypatch):
    """A ledger of this test's own: another test's writer thread, still
    filing, would otherwise be work beside these passes."""
    fresh = hostload.Ledger()
    monkeypatch.setattr(hostload, "LEDGER", fresh)
    return fresh


class Clock:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


# -- the ledger --------------------------------------------------------------


def test_the_seconds_of_each_kind_that_overlapped_are_read():
    clock = Clock(10.0)
    ledger = hostload.Ledger(clock)
    before = ledger.begin("filing")
    clock.now = 12.0
    ledger.end(before)                     # 10-12, before the pass
    since = ledger.now()                   # the pass begins at 12
    one = ledger.begin("filing")
    clock.now = 15.0
    ledger.end(one)                        # 3 s inside it
    ledger.begin("export-rolls")           # still running at the reading
    clock.now = 16.5
    assert ledger.during(since) == {"export-rolls": 1.5, "filing": 3.0}
    assert ledger.running() == ["export-rolls"]


def test_nothing_beside_a_pass_is_said_as_nothing():
    clock = Clock(5.0)
    ledger = hostload.Ledger(clock)
    since = ledger.now()
    clock.now = 9.0
    assert ledger.during(since) == {}


def test_a_ledger_that_cannot_be_read_says_so_rather_than_nothing():
    clock = Clock(1.0)
    ledger = hostload.Ledger(clock)
    since = ledger.now()

    def broken():
        raise OSError("no clock")

    ledger.clock = broken
    assert ledger.during(since) == {"unread": "OSError: no clock"}
    assert ledger.during(None) == {
        "unread": "the pass's start was not read"}


def test_a_stamp_never_raises_into_the_work_it_stamps():
    def broken():
        raise OSError("no clock")

    hostload.LEDGER.clock = broken
    ran = []
    with hostload.stamp("filing"):
        ran.append(True)
    assert ran == [True] and hostload.LEDGER.running() == []
    # And the work's own exception is its own, untouched.
    with pytest.raises(KeyError):
        with hostload.stamp("filing"):
            raise KeyError("the work's")


def test_old_spans_are_let_go():
    clock = Clock(0.0)
    ledger = hostload.Ledger(clock)
    for _ in range(hostload.KEEP_SPANS + 10):
        ledger.end(ledger.begin("filing"))
    assert len(ledger._done) <= hostload.KEEP_SPANS
    clock.now = hostload.KEEP_S + 100.0
    ledger.end(ledger.begin("save-all"))
    assert [kind for kind, _s, _e in ledger._done] == ["save-all"]


# -- who stamps it -----------------------------------------------------------


def test_the_writer_stamps_each_job_while_it_files(tmp_path, monkeypatch):
    seen = []
    real = library.save

    def save(*a, **kw):
        seen.append(hostload.LEDGER.running())
        return real(*a, **kw)

    monkeypatch.setattr(library, "save", save)
    writer = session.FrameWriter()
    writer.submit(number=1, paths=[], image=np.zeros((4, 4, 3), np.uint16),
                  meta={"resolution_dpi": 600}, dpi=600,
                  library=str(tmp_path / "lib"), film=FilmNotes(), tags=[],
                  prescan=None, inquiry=None,
                  capture={"raw": b"\x00\x01" * 16,
                           "raw_layout": {"format": "index"}})
    writer.finish()
    assert seen == [["filing"]]
    assert hostload.LEDGER.running() == []


# -- who reads it: the driver, once, after the last READ ----------------------


def _calibrated(monkeypatch, **kw):
    scanner, device = scanner_at_commands(monkeypatch, **kw)
    assert scanner.calibrate_shading()["reference"] is not None
    return scanner, device


def test_a_pass_with_nothing_beside_it_records_nothing(monkeypatch):
    scanner, _ = _calibrated(monkeypatch)
    _, meta = scanner.scan(resolution=300, infrared=False)
    assert meta["host_load"] == {}
    reads = meta["commands"]["image_reads"]
    assert reads["host_gaps"] == reads["reads"] - 1
    assert "host_gap_max_s" in reads and "in_transfer_max_s" in reads


def test_a_pass_records_the_filing_that_ran_beside_it(monkeypatch):
    """The roll's case: a frame's filing overlapping the next prescan."""
    scanner, _ = _calibrated(monkeypatch)
    with hostload.stamp("filing"):
        scanner.prescan(resolution=300)
    meta = scanner.last_scan_meta
    assert set(meta["host_load"]) == {"filing"}
    assert meta["host_load"]["filing"] >= 0.0


def test_the_ledger_is_read_after_the_read_loop_not_from_its_hook(
        monkeypatch):
    """A progress hook that raises -- or stamps work of its own, the closest
    a test comes to one that blocks -- runs inside the read loop; the
    reading is made once the loop is over, so what the hook did is seen as
    work beside the pass and nothing more, and the pass completes."""
    scanner, _ = _calibrated(monkeypatch)
    reads = []

    def hook(got, total):
        reads.append(hostload.LEDGER.running())
        raise RuntimeError("a progress bar that broke")

    scanner.progress_hook = hook
    _, meta = scanner.scan(resolution=300, infrared=False)
    assert reads and all(r == [] for r in reads)
    assert meta["host_load"] == {}
    assert meta["height"] > 0


def test_a_ledger_that_fails_leaves_the_pass_whole(monkeypatch):
    scanner, device = _calibrated(monkeypatch)

    def broken():
        raise OSError("no clock")

    hostload.LEDGER.clock = broken
    image, meta = scanner.scan(resolution=300, infrared=False,
                               keep_raw=True)
    assert scanner.last_raw == device.passes[-1]["blob"]
    assert meta["host_load"] == {"unread": "the pass's start was not read"}


def test_a_debug_filed_probe_carries_it_to_its_entry(monkeypatch, tmp_path):
    """Metering probes and holds are filed by debug filing alone, and are
    the passes a roll's filing overlaps most."""
    from rps7200.direct import DirectScanner

    monkeypatch.setenv(DirectScanner.DEBUG_ROOT_ENV, str(tmp_path))
    scanner, _ = _calibrated(monkeypatch, debug=True)
    with hostload.stamp("filing"):
        scanner.scan(resolution=300, infrared=False, auto_exposure=True)
    scanner.close()
    records = library.entries(tmp_path)
    assert len(records) >= 2, "the probes and the pass"
    for record in records:
        assert "filing" in record["extra"]["host_load"], record["id"]
