"""What an entry records about the pass it holds, beyond the pixels.

The library's promise is that everything can be recalculated and evaluated
later. That needs the pass's own description: the commands it was sent and
what came back, the scanner that answered, where its shading reference came
from, when it was taken, and which code filed it -- and every field a caller
handed over, not only those a fixed list happened to name.
"""
from __future__ import annotations

import json

import numpy as np

from rps7200 import library
from rps7200.direct import DirectScanner, _CommandLog
from rps7200.protocol import SCSI_READ, Inquiry, _cmd
from rps7200.shading import ShadingReference


class Echo:
    """A transport that answers every command with what it was asked for."""

    def __init__(self, reply=b""):
        self.reply = reply
        self.closed = False

    def command(self, command, data=None, read_size=0, **kw):
        return self.reply[:read_size] if read_size else b""


def test_the_commands_of_a_pass_are_written_down():
    log = _CommandLog(Echo(bytes(range(32))))
    log.command(_cmd(0x15, 16), data=b"\x01\x02")          # before: not recorded
    log.start()
    log.command(_cmd(0x15, 16), data=b"\x03\x04")
    log.command(_cmd(SCSI_READ, 32), read_size=32)
    record = log.stop()
    assert [e["cdb"] for e in record["sent"]] == [
        _cmd(0x15, 16).hex(), _cmd(SCSI_READ, 32).hex()]
    assert record["sent"][0]["out"] == "0304"
    assert record["sent"][1]["in"] == bytes(range(32)).hex()
    assert log.stop() is None, "recording outlived the pass"


def test_image_data_is_counted_not_copied():
    """A 7200 dpi pass makes thousands of image READs; their bytes are the
    entry's raw.bin.gz already."""
    log = _CommandLog(Echo(b"\x00" * 4096))
    log.start()
    for _ in range(3):
        log.command(_cmd(SCSI_READ, 4096), read_size=4096)
    record = log.stop()
    assert record["sent"] == []
    reads = record["image_reads"]
    assert (reads["reads"], reads["bytes"]) == (3, 3 * 4096)
    assert "waits" not in reads, "a read answered at once waited"


class Paced(Echo):
    """Image READs that take their time, on a clock the test moves: each
    transfer costs ``inside`` seconds, and the host's pause between them is
    whatever the test lets pass."""

    def __init__(self, clock, inside):
        super().__init__(b"\x00" * 4096)
        self.clock, self.inside = clock, inside

    def command(self, command, data=None, read_size=0, **kw):
        self.clock.now += self.inside.pop(0) if read_size >= 256 else 0.0
        return super().command(command, data, read_size, **kw)


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def _paced(inside, host_pauses):
    clock = Clock()
    log = _CommandLog(Paced(clock, list(inside)))
    object.__setattr__(log, "_clock", clock)
    log.start()
    for i, _ in enumerate(inside):
        if i:
            clock.now += host_pauses[i - 1]
        log.command(_cmd(SCSI_READ, 4096), read_size=4096)
    return log.stop()["image_reads"]


def test_the_host_gaps_between_image_reads_are_recorded():
    """The hazard the open-device rule names is the host failing to ask for
    the next read in time; the time inside a transfer is the scanner's own
    pacing, and is kept apart from it."""
    reads = _paced(inside=[0.8, 2.6, 0.5, 0.7], host_pauses=[0.05, 1.5, 0.2])
    assert reads["reads"] == 4 and "waits" not in reads
    assert reads["host_gaps"] == 3
    assert reads["host_gap_max_s"] == 1.5
    assert reads["host_gap_p99_s"] == 0.2
    assert reads["in_transfer_max_s"] == 2.6


def test_a_pass_of_one_read_has_no_gap_to_report():
    reads = _paced(inside=[0.8], host_pauses=[])
    assert reads["host_gaps"] == 0 and "host_gap_max_s" not in reads
    assert reads["in_transfer_max_s"] == 0.8


def test_a_not_yet_counts_its_wait_and_the_gap_once():
    """`waits` is as it was, and a READ asked again after "not yet" is not
    a second gap."""
    from rps7200.usb_transport import NoDataYet

    clock = Clock()

    class NotYetOnce(Echo):
        def __init__(self):
            super().__init__(b"\x00" * 4096)
            self.calls = 0

        def command(self, command, data=None, read_size=0, **kw):
            self.calls += 1
            if self.calls == 2:
                raise NoDataYet("not yet")
            return super().command(command, data, read_size, **kw)

    log = _CommandLog(NotYetOnce())
    object.__setattr__(log, "_clock", clock)
    log.start()
    log.command(_cmd(SCSI_READ, 4096), read_size=4096)
    clock.now += 0.3
    try:
        log.command(_cmd(SCSI_READ, 4096), read_size=4096)
    except NoDataYet:
        pass
    clock.now += 0.02
    log.command(_cmd(SCSI_READ, 4096), read_size=4096)
    reads = log.stop()["image_reads"]
    assert reads["waits"] == 1 and reads["reads"] == 2
    assert reads["host_gaps"] == 1 and reads["host_gap_max_s"] == 0.3


def test_a_failing_clock_never_reaches_the_read():
    """The bookkeeping runs on the read path, where a raise abandons the
    read -- the wedge itself. A clock that fails leaves every read
    answered, counted and handed back, and the record says why its gaps are
    missing."""
    def broken():
        raise OSError("the clock is gone")

    log = _CommandLog(Echo(b"\x07" * 4096))
    object.__setattr__(log, "_clock", broken)
    log.start()
    replies = [log.command(_cmd(SCSI_READ, 4096), read_size=4096)
               for _ in range(3)]
    assert replies == [b"\x07" * 4096] * 3
    reads = log.stop()["image_reads"]
    assert (reads["reads"], reads["bytes"]) == (3, 3 * 4096)
    assert reads["host_gap_missing"] == "OSError: the clock is gone"
    assert "host_gap_max_s" not in reads


def test_a_broken_sample_never_reaches_the_read(monkeypatch):
    from rps7200 import direct

    def broken(self, gap):
        raise IndexError("the sample is full")

    monkeypatch.setattr(direct._ReadPacing, "_add", broken)
    reads = _paced(inside=[0.1, 0.1, 0.1], host_pauses=[0.1, 0.1])
    assert reads["reads"] == 3
    assert reads["host_gap_missing"] == "IndexError: the sample is full"


def test_a_long_pass_keeps_a_bounded_sample_and_the_true_maximum():
    from rps7200 import direct

    pacing = direct._ReadPacing(Clock())
    for i in range(3 * pacing.KEEP):
        pacing._add(0.001 * (i % 100))
    pacing._add(9.0)
    assert len(pacing.gaps) == pacing.KEEP
    said = pacing.summary()
    assert said["host_gaps"] == 3 * pacing.KEEP + 1
    assert said["host_gap_max_s"] == 9.0
    assert 0.09 <= said["host_gap_p99_s"] < 9.0


def test_a_refused_command_is_recorded_as_refused():
    class Refuses(Echo):
        def command(self, *a, **kw):
            raise RuntimeError("check condition")

    log = _CommandLog(Refuses())
    log.start()
    try:
        log.command(_cmd(0x1B, 1))
    except RuntimeError:
        pass
    # With what the device answered, not the exception's class alone.
    assert log.stop()["sent"][0]["refused"] == "RuntimeError: check condition"


def test_the_transport_is_still_the_transport():
    inner = Echo()
    log = _CommandLog(inner)
    log.closed = True
    assert inner.closed is True and log.closed is True


def test_the_scanner_records_through_its_transport():
    s = DirectScanner(transport=Echo(), debug=False)
    assert isinstance(s.t, _CommandLog)


def _save(tmp_path, **meta_extra):
    image = np.zeros((4, 4, 3), np.uint16)
    meta = {"resolution_dpi": 300, "channels": 3, "width": 4, "height": 4,
            "depth": 16, **meta_extra}
    inquiry = Inquiry(vendor="PIE", product="RPS 7200", model=0x31,
                      firmware="1.70", max_resolution=7200, ccd_width=0,
                      ccd_length=0, filters=0x10, depths=0, formats=0,
                      optional_devices=0, frame=(0, 0, 0, 0),
                      preview_resolution=300)
    path = library.save(image, meta, root=tmp_path, inquiry=inquiry)
    return json.loads((path / "scan.json").read_text(encoding="utf-8"))


def test_the_scanner_that_answered_is_recorded(tmp_path):
    record = _save(tmp_path)
    assert record["device"]["firmware"] == "1.70"
    assert record["device"]["product"] == "RPS 7200"


def test_fields_no_list_names_are_kept_not_dropped(tmp_path):
    """A bracket's membership, a roll frame's index, the demo's flag and the
    pass's commands all used to vanish at filing."""
    record = _save(tmp_path, bracket_ratio=2.0, roll_index=7, demo=True,
                   commands={"sent": [{"cdb": "1b0000000100"}]},
                   started_utc="2026-09-24T22:00:00Z")
    extra = record["extra"]
    assert extra["bracket_ratio"] == 2.0 and extra["roll_index"] == 7
    assert extra["demo"] is True
    assert extra["commands"]["sent"][0]["cdb"] == "1b0000000100"
    assert "resolution_dpi" not in extra, "a field recorded twice"


def test_a_reference_says_where_it_came_from(tmp_path):
    ref = ShadingReference(ref={0: np.full(8, 3.0)}, mean={0: 3.0},
                           pixels_per_line=8)
    ref.save(tmp_path / "shading.npz")
    s = DirectScanner(transport=Echo(), debug=False)
    s.load_shading(tmp_path / "shading.npz")
    assert s._shading_origin["action"] == "loaded"
    assert s._shading_origin["path"].endswith("shading.npz")
    assert "file_modified_utc" in s._shading_origin


def test_an_absent_git_is_not_a_clean_tree(monkeypatch):
    """`driver_dirty` said False -- clean -- when git could not be asked."""
    import subprocess

    def no_git(*a, **kw):
        raise OSError("git: not found")

    monkeypatch.setattr(subprocess, "run", no_git)
    record = library.provenance()
    assert record["driver_commit"] is None
    assert record["driver_dirty"] is None
