"""A tool calibrates only once someone who can see the transport says film is in.

CyberView calibrates with the film in, and calibrating an empty transport
preceded a wedge (CLAUDE.md, "Calibrate with the film loaded"). Nothing the
scanner reports settles which it is: READ STATE's byte 8 was measured once,
and every capture was taken with film in. The window asks, with a box that
starts unticked. `tools/scan.py` went from INQUIRY straight into a
calibration, and `tools/scan_roll.py` after a seek that on frame 1 moves
nothing -- so an empty transport reading 0 passed it.
"""
from __future__ import annotations

import sys

import pytest

from conftest import load_tool
from rps7200.console import film_unconfirmed

scan_tool = load_tool("scan")
scan_roll = load_tool("scan_roll")


def test_the_flag_is_the_answer():
    assert film_unconfirmed(True, ask=lambda q: pytest.fail("asked")) is None


@pytest.mark.parametrize("answer,ok", [("y", True), ("yes", True), ("", False),
                                       ("n", False), ("maybe", False)])
def test_someone_at_the_terminal_is_asked(answer, ok):
    said = film_unconfirmed(False, ask=lambda q: answer, interactive=True)
    assert (said is None) is ok


def test_nobody_to_ask_is_a_refusal_that_names_the_flag():
    said = film_unconfirmed(False, ask=lambda q: pytest.fail("asked"),
                            interactive=False)
    assert said and "--film-loaded" in said


def _opened(monkeypatch, module):
    opened = []

    def scanner(**kw):
        opened.append(kw)
        raise AssertionError("the device was opened")

    monkeypatch.setattr(module, "DirectScanner", scanner)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    return opened


def test_scan_py_refuses_to_calibrate_unasked(tmp_path, monkeypatch, capsys):
    opened = _opened(monkeypatch, scan_tool)
    monkeypatch.setattr(sys, "argv", ["scan.py", "--out",
                                      str(tmp_path / "out.tif"),
                                      "--reference", str(tmp_path / "s.npz")])
    with pytest.raises(SystemExit) as stop:
        scan_tool.main()
    assert stop.value.code == 2 and opened == []
    assert "--film-loaded" in capsys.readouterr().err


def test_scan_roll_py_refuses_to_calibrate_unasked(tmp_path, monkeypatch,
                                                   capsys):
    opened = _opened(monkeypatch, scan_roll)
    monkeypatch.setattr(sys, "argv", ["scan_roll.py", "--out",
                                      str(tmp_path / "roll"), "--frames", "2",
                                      "--reference", str(tmp_path / "s.npz")])
    with pytest.raises(SystemExit) as stop:
        scan_roll.main()
    assert stop.value.code == 2 and opened == []
    assert "--film-loaded" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["--no-shading"], ["--reuse"],
                                  ["--frames", "0"]])
def test_no_question_where_nothing_calibrates(tmp_path, monkeypatch, argv):
    """Raw pixels, this power-on's cached reference, or no roll at all."""
    (tmp_path / "s.npz").write_bytes(b"")
    opened = _opened(monkeypatch, scan_roll)
    monkeypatch.setattr(sys, "argv", ["scan_roll.py", "--out",
                                      str(tmp_path / "roll"),
                                      "--reference", str(tmp_path / "s.npz"),
                                      "--frames", "2", *argv])
    scan_roll.main()             # the roll catches what the stand-in raised
    assert len(opened) == 1, "refused before the device was reached"


# -- what READ STATE said, kept where it was only in passing --------------


def _byte8_empty(transport_class):
    """`transport_class`, with READ STATE's byte 8 raised: an empty transport,
    as the one measurement of it read."""
    from rps7200.protocol import SCSI_READ_STATE

    class Empty(transport_class):
        def command(self, command, *args, **kwargs):
            if command[0] == SCSI_READ_STATE:
                reply = bytearray(13)
                reply[8] = 1
                return bytes(reply)
            return super().command(command, *args, **kwargs)

    return Empty


def test_a_calibration_says_and_keeps_what_byte_8_said(monkeypatch, tmp_path):
    """It read READ STATE and looked only at the lamp: the flag lay in the
    reply among the calibration's commands, where nothing read it."""
    import json

    from conftest import NoWaiting
    from test_calibration_end import DARK, LIT, CalibratingTransport

    from rps7200 import direct
    from rps7200.direct import DirectScanner

    monkeypatch.setattr(direct, "time", NoWaiting())
    s = DirectScanner(transport=_byte8_empty(CalibratingTransport)(DARK + LIT),
                      debug=False)
    s._own_transport = False
    said = []
    s.log_hook = said.append
    s.ensure_shading(tmp_path / "calibration" / "shading.npz")
    assert any("byte 8 says the transport is empty" in m for m in said), said
    folder = next(p for p in (tmp_path / "calibration").iterdir() if p.is_dir())
    record = json.loads((folder / "calibration.json").read_text(encoding="utf-8"))
    assert record["media_loaded"] is False


def test_a_scans_note_names_the_byte_that_said_empty():
    """It printed byte 6 and called that bit unreliable -- true of byte 6,
    and not the byte the driver reads, which is what had said empty."""
    from conftest import FakeTransport

    from rps7200.direct import DirectScanner

    class Stop(Exception):
        pass

    from rps7200.protocol import SCSI_READ_GAIN_OFFSET

    s = DirectScanner(transport=_byte8_empty(FakeTransport)(
        replies={SCSI_READ_GAIN_OFFSET: bytes(123)}), debug=False)
    s._own_transport = False
    said = []
    s.log_hook = said.append

    def reached(*a, **k):
        raise Stop

    s._read_pass = reached
    with pytest.raises(Stop):
        s.scan(resolution=300, infrared=False, shading=False)
    notes = [m for m in said if m.startswith("note:")]
    assert notes and "byte 8" in notes[0], said
