"""A calibration ends when the scanner says it has no more lines, and not before.

The read loop took *any* refused read for the scanner finishing. A NOT READY,
a one-shot UNIT ATTENTION, a sense that could not be read: each left the
device mid-calibration and unmarked, and whatever blocks had arrived became
the session's reference -- the dark phase alone, averaged as a one-point
"light" reference, cached over the good file for every later ``--reuse``.
These run the driver's own `calibrate_shading` on a transport that answers
the way the device does, so every command it sends is the real one.
"""
from __future__ import annotations

import numpy as np
import pytest

from conftest import NoWaiting
from rps7200 import direct
from rps7200.direct import DirectScanner
from rps7200.protocol import (SCSI_COPY, SCSI_READ, SCSI_READ_GAIN_OFFSET,
                              SCSI_READ_STATE, SCSI_REQUEST_SENSE, ScanReadError,
                              ShadingUnavailable)
from rps7200.shading import ShadingReference
from rps7200.usb_transport import CheckCondition

#: The descriptor's width, kept small: 8 columns, 16-bit, so a line is 18 bytes.
COLUMNS = 8
STRIDE = 2 + 2 * COLUMNS
BLOCK = 4 * STRIDE

END_OF_DATA = 0x20
NOT_READY = 0x04


def _line(tag: bytes, level: int) -> bytes:
    return tag * 2 + np.full(COLUMNS, level, "<u2").tobytes()


def _block(level: int) -> bytes:
    """Four lines in the order the device interleaves them, B R G I."""
    return b"".join(_line(t, level) for t in (b"B", b"R", b"G", b"I"))


#: A whole calibration: 20 blocks unlit, then 20 lit -- 160 lines.
DARK = [_block(170)] * 20
LIT = [_block(47000)] * 20


class CalibratingTransport:
    """Answers a calibration the way the device does.

    ``blocks`` are the image READs' answers in turn, and ``ends_with`` the ASC
    of the refusal after the last of them: 0x20 is the scanner finishing.
    Everything else -- READ STATE, the descriptor, gain, the mask -- answers
    as a working device would.
    """

    def __init__(self, blocks, ends_with=END_OF_DATA):
        self.blocks = list(blocks)
        self.ends_with = ends_with
        self.sense = 0
        self.sent: list[int] = []

    def command(self, command, data=None, read_size=0, timeout_ms=0,
                max_wait_s=60.0):
        opcode = command[0]
        self.sent.append(opcode)
        if opcode == SCSI_REQUEST_SENSE:
            reply = bytearray(14)
            reply[2], reply[12] = 0x05, self.sense
            self.sense = 0
            return bytes(reply)
        if opcode == SCSI_READ_STATE:
            return bytes(13)
        if opcode == SCSI_READ_GAIN_OFFSET:
            return bytes(123)
        if opcode == SCSI_COPY:
            return bytes(read_size)
        if opcode == SCSI_READ and read_size == 32:      # the descriptor
            reply = bytearray(32)
            reply[4], reply[5] = 1, 6
            reply[8:14] = bytes([0, 16, 16, 20]) + (2 * COLUMNS).to_bytes(2, "little")
            return bytes(reply)
        if opcode == SCSI_READ and read_size == BLOCK:
            if self.blocks:
                return self.blocks.pop(0)
            self.sense = self.ends_with
            raise CheckCondition(opcode)
        if opcode == SCSI_READ:
            return bytes(read_size)
        return b""


def calibrating(monkeypatch, blocks, ends_with=END_OF_DATA):
    monkeypatch.setattr(direct, "time", NoWaiting())
    s = DirectScanner(transport=CalibratingTransport(blocks, ends_with),
                      debug=False)
    s._own_transport = False
    return s


def earlier() -> ShadingReference:
    """The reference a session already had: another day's, but whole."""
    return ShadingReference(
        ref={c: np.full(COLUMNS, 30000.0) for c in range(3)},
        mean={c: 30000.0 for c in range(3)}, pixels_per_line=COLUMNS,
        dark={c: np.full(COLUMNS, 150.0) for c in range(3)},
        dark_mean={c: 150.0 for c in range(3)})


def test_a_whole_calibration_becomes_the_reference(monkeypatch):
    s = calibrating(monkeypatch, DARK + LIT)
    result = s.calibrate_shading()
    assert result.get("incomplete") is None
    assert s.shading is result["reference"] is not None
    assert round(s.shading.mean[0]) == 47000 and round(s.shading.dark_mean[0]) == 170
    assert s.suspect is None


def test_a_refusal_that_is_not_the_end_leaves_the_device_marked(monkeypatch):
    """NOT READY after three blocks is not the scanner finishing: it may be
    mid-calibration still, and nothing may drive it."""
    s = calibrating(monkeypatch, DARK[:3], ends_with=NOT_READY)
    s.shading = earlier()
    with pytest.raises(ScanReadError):
        s.calibrate_shading()
    assert s.suspect is not None
    assert s.shading.mean[0] == 30000.0, "the reference in force was replaced"


def test_a_calibration_that_ended_in_its_dark_phase_is_not_taken(monkeypatch):
    s = calibrating(monkeypatch, DARK[:3])
    s.shading = earlier()
    result = s.calibrate_shading(keep_data=True)
    assert result["reference"] is None
    assert "one phase" in result["incomplete"]
    assert s.shading.mean[0] == 30000.0, "the dark lines became the reference"
    assert s.suspect is None, "the scanner said it had finished"


def test_a_calibration_with_no_lines_leaves_the_reference_it_had(monkeypatch):
    s = calibrating(monkeypatch, [])
    s.shading = earlier()
    result = s.calibrate_shading()
    assert result["reference"] is None and result["incomplete"]
    assert s.shading is not None and s.shading.mean[0] == 30000.0


def test_an_incomplete_calibration_is_kept_but_not_cached(monkeypatch, tmp_path):
    """Its bytes are evidence and are kept; the cache every later --reuse
    reads is left as it was, and the job fails rather than reporting."""
    cache = tmp_path / "calibration" / "shading.npz"
    before = DirectScanner(transport=CalibratingTransport([]), debug=False)
    before.shading = earlier()
    before.save_shading(cache)
    kept = cache.read_bytes()

    s = calibrating(monkeypatch, DARK[:3])
    with pytest.raises(ShadingUnavailable, match="incomplete") as caught:
        s.ensure_shading(cache)
    assert cache.read_bytes() == kept
    assert s.shading is None
    assert "refused until a calibration succeeds" in str(caught.value)
    archived = [p for p in cache.parent.iterdir() if p.is_dir()]
    assert len(archived) == 1
    assert (archived[0] / "data.bin").read_bytes() == b"".join(DARK[:3])


def test_a_mask_refused_after_a_whole_calibration_costs_only_the_mask(
        monkeypatch, tmp_path):
    """The scanner had said it was finished; a COPY refused after that threw
    the whole calibration away -- 3-4 minutes, and its bytes with it. Every
    pass reads its own mask, which is the one a correction uses."""
    class NoMask(CalibratingTransport):
        def command(self, command, *args, **kwargs):
            if command[0] == SCSI_COPY:
                self.sense = 0x29
                raise CheckCondition(SCSI_COPY)
            return super().command(command, *args, **kwargs)

    monkeypatch.setattr(direct, "time", NoWaiting())
    s = DirectScanner(transport=NoMask(DARK + LIT), debug=False)
    s._own_transport = False
    s.ensure_shading(tmp_path / "calibration" / "shading.npz")
    assert s.shading is not None and s.suspect is None
    folder = next(p for p in (tmp_path / "calibration").iterdir() if p.is_dir())
    assert (folder / "data.bin").read_bytes() == b"".join(DARK + LIT)
    assert not (folder / "ccd_mask.bin").exists()
