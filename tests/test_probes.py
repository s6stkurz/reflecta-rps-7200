"""The hardware probes, run end to end against a stand-in that refuses what
the scanner refuses.

Every probe had been tested only through its planning and report helpers, so
none of these could be seen: each died on its first pass once the driver
stopped calibrating inside one, `RPS7200_DEBUG=0` passed every gate and filed
nothing, a Ctrl-C abandoned the read in flight, and cleanup drove a device a
stopped read had left busy. The stand-in holds DirectScanner's contract on
exactly those points and nothing else.
"""
from __future__ import annotations

import signal
import sys

import numpy as np
import pytest
from conftest import load_tool, settings

from rps7200.protocol import ScanReadError, ShadingUnavailable


class ProbeScanner:
    """Enough of DirectScanner for a probe's main(), and its refusals."""

    def __init__(self, calls, *, debug=True, interrupt_on=None, fail_on=None,
                 shape=(40, 48, 3)):
        self.calls = calls
        self.debug = debug
        self.suspect = None
        self.last_metering = {"rounds": [{"levels": [0.8, 0.8, 0.5]}]}
        self._reference = None
        self._scans = 0
        self._interrupt_on = interrupt_on
        self._fail_on = fail_on
        self._shape = shape

    # -- the session ----------------------------------------------------
    def open(self):
        self.calls.append("open")

    def close(self):
        self.calls.append("close")

    def read_state(self, *a, **k):
        class State:
            scanning, media_loaded, position, warming_up = 0, True, 0, False
        return State()

    def session_start(self):
        self.calls.append("session_start")

    def wait_warm(self):
        pass

    def ensure_shading(self, path, reuse=False, skip=False):
        self.calls.append("reference")
        self._reference = object()
        return {"action": "calibrated", "reference": self._reference,
                "path": path, "summary": "calibrated"}

    def get_gain_offset(self):
        self.calls.append("get_gain_offset")
        return settings(9604, 6506, 6506, 7745)

    def set_gain_offset(self, value):
        self.calls.append("set_gain_offset")

    # -- passes -----------------------------------------------------------
    def auto_exposure(self, **kw):
        if self._reference is None:
            raise ShadingUnavailable("no shading reference in this session")
        self.calls.append("meter")
        return [1.0, 1.0, 1.0, 1.0]

    def scan(self, shading=True, **kw):
        if shading and self._reference is None:
            raise ShadingUnavailable("no shading reference in this session")
        self._scans += 1
        self.calls.append("scan")
        if self._fail_on == self._scans:
            self.suspect = "the read stopped part way"
            raise ScanReadError("bulk read timed out")
        if self._interrupt_on == self._scans:
            # Ctrl-C lands while this pass is being read.
            signal.raise_signal(signal.SIGINT)
        rng = np.random.default_rng(self._scans)
        image = rng.integers(20000, 30000, self._shape, dtype=np.uint16)
        h = self._shape[0]
        return image, {"duration_s": 1.0, "height": h,
                       "exposure": [9604, 6506, 6506, 7745],
                       "gain": [39, 33, 21, 21]}


def run(monkeypatch, name, scanner, *argv):
    tool = load_tool(name)
    monkeypatch.setattr(tool, "DirectScanner", lambda **kw: scanner)
    monkeypatch.setattr(sys, "argv", [name, *argv])
    # Set, either way: "0" is the value that is set and files nothing.
    monkeypatch.setenv("RPS7200_DEBUG", "1" if scanner.debug else "0")
    return tool.main()


@pytest.mark.parametrize("name", ["byte14_probe", "gain_probe"])
def test_a_debug_value_that_files_nothing_is_refused(monkeypatch, name):
    """The gate asked whether the variable was set; the driver files only
    for 1/true/yes/on, so RPS7200_DEBUG=0 ran the whole probe unfiled."""
    calls: list[str] = []
    assert run(monkeypatch, name, ProbeScanner(calls, debug=False)) == 2
    assert calls == [], "the device was driven by a probe that files nothing"


@pytest.mark.parametrize("name", ["byte14_probe", "gain_probe"])
def test_a_probe_gets_its_reference_before_its_first_pass(monkeypatch, name):
    """Every probe's first pass is metering, a corrected pass, and none of
    them acquired a reference: each died there with ShadingUnavailable."""
    calls: list[str] = []
    assert run(monkeypatch, name, ProbeScanner(calls)) == 0
    assert calls.index("reference") < calls.index("meter")
    assert "scan" in calls


def test_ctrl_c_finishes_the_pass_in_flight_and_starts_no_other(monkeypatch):
    """No probe deferred it: one press abandoned the read, which wedges."""
    calls: list[str] = []
    scanner = ProbeScanner(calls, interrupt_on=3)
    previous = signal.getsignal(signal.SIGINT)
    run(monkeypatch, "byte14_probe", scanner)
    assert calls.count("scan") == 3, "a pass started after the stop, or none finished"
    assert signal.getsignal(signal.SIGINT) is previous, "Ctrl-C left deferred"


def test_byte14_sends_no_final_pass_to_a_suspect_device(monkeypatch):
    """Its unconditional last pass sent SCAN FRAME, WRITE GAIN OFFSET and MODE
    SELECT to a scanner still streaming the pass that stopped part way."""
    calls: list[str] = []
    scanner = ProbeScanner(calls, fail_on=2)
    with pytest.raises(ScanReadError):
        run(monkeypatch, "byte14_probe", scanner)
    assert calls.count("scan") == 2, "the final pass was sent anyway"


def test_gain_writes_no_register_to_a_suspect_device(monkeypatch):
    calls: list[str] = []
    scanner = ProbeScanner(calls, fail_on=2)
    assert run(monkeypatch, "gain_probe", scanner) == 0
    assert "set_gain_offset" not in calls


def test_gain_restores_the_register_after_a_clean_run(monkeypatch):
    calls: list[str] = []
    assert run(monkeypatch, "gain_probe", ProbeScanner(calls)) == 0
    assert calls[-2:] == ["set_gain_offset", "close"]


@pytest.mark.parametrize("name,ladder", [
    ("gain_probe", "21,21,300"),       # masked to 44 on the wire, filed as 300
    ("gain_probe", "21,5"),
    ("byte14_probe", "0x10,-1"),       # raised inside set_mode, mid-sequence
    ("byte14_probe", "0x10,0x05"),     # never captured
])
def test_a_ladder_rung_nothing_has_sent_is_refused(monkeypatch, name, ladder):
    calls: list[str] = []
    assert run(monkeypatch, name, ProbeScanner(calls), "--ladder", ladder) == 2
    assert calls == []
