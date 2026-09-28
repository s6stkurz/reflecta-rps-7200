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
        h, w, c = self._shape
        image = rng.integers(20000, 30000, (h, w, 4 if kw.get("infrared") else c),
                             dtype=np.uint16)
        return image, {"duration_s": 1.0, "height": h,
                       "exposure": [9604, 6506, 6506, 7745],
                       "gain": [39, 33, 21, 21]}

    def prescan(self, resolution=300, keep_raw=False, **kw):
        # Through `self.scan`, as the driver's prescan is -- which is also
        # where a probe's stop check sits.
        image, meta = self.scan(shading=True)
        return (image >> 8).astype(np.uint8), meta

    # -- the transport ----------------------------------------------------
    position_now = 10

    def position(self):
        return self.position_now

    def advance(self, *a, **k):
        self.calls.append("advance")
        self.position_now += 1
        return self.position_now

    def retreat(self, *a, **k):
        self.calls.append("retreat")
        self.position_now -= 1
        return self.position_now

    def nudge(self, mm):
        self.calls.append("nudge")
        return {"param": 3, "asked_mm": round(mm + 0.03, 3),
                "requested_mm": mm}

    def _hold_to_approved(self, index, now, resolution, approved, keep_raw=True):
        self.prescan()
        return {"outcome": "held", "target_mm": approved.offset_mm,
                "final_mm": approved.offset_mm, "moves": 0, "spent_mm": 0.0,
                "history": [], "prescan": now}


def run(monkeypatch, name, scanner, *argv):
    tool = load_tool(name)
    monkeypatch.setattr(tool, "DirectScanner", lambda **kw: scanner)
    monkeypatch.setattr(sys, "argv", [name, *argv])
    # Set, either way: "0" is the value that is set and files nothing.
    monkeypatch.setenv("RPS7200_DEBUG", "1" if scanner.debug else "0")
    return tool.main()


#: Every probe that drives the scanner, with the arguments that keep a run to
#: a handful of passes and its output inside the test's own directory.
PROBES = {
    "byte14_probe": [],
    "gain_probe": [],
    "fast_ir_probe": [],
    "hold_probe": [],
    "transport_truth": [],
    "exposure_probe": ["--frames", "2", "--ladder-every", "0"],
    "roll_registration_walk": ["--frames", "2"],
}


def probe_argv(name, tmp_path):
    extra = list(PROBES[name])
    if name == "roll_registration_walk":
        extra += ["--out", str(tmp_path / "walk")]
    return extra


@pytest.mark.parametrize("name", sorted(PROBES))
def test_a_debug_value_that_files_nothing_is_refused(monkeypatch, tmp_path, name):
    """The gate asked whether the variable was set; the driver files only
    for 1/true/yes/on, so RPS7200_DEBUG=0 ran the whole probe unfiled."""
    calls: list[str] = []
    assert run(monkeypatch, name, ProbeScanner(calls, debug=False),
               *probe_argv(name, tmp_path)) == 2
    assert calls == [], "the device was driven by a probe that files nothing"


@pytest.mark.parametrize("name", sorted(PROBES))
def test_a_probe_gets_its_reference_before_its_first_pass(monkeypatch, tmp_path,
                                                          name):
    """Every probe's first pass is metering or a prescan, a corrected pass,
    and none of them acquired a reference: each died there with
    ShadingUnavailable once the driver stopped calibrating inside a pass."""
    calls: list[str] = []
    monkeypatch.chdir(tmp_path)
    run(monkeypatch, name, ProbeScanner(calls), *probe_argv(name, tmp_path))
    assert "reference" in calls
    first = min(calls.index(c) for c in ("meter", "scan") if c in calls)
    assert calls.index("reference") < first
    assert "scan" in calls, "no pass was ever taken"


@pytest.mark.parametrize("mode", [[], ["--resolutions", "300,600"]])
@pytest.mark.parametrize("film", ["bw", "kodachrome"])
def test_fast_ir_refuses_blind_film_before_the_device(monkeypatch, film, mode):
    """Its help promised infrared is refused outright for the stocks blind to
    it; the refusal came from `scan`, after the device had been opened,
    calibrated and metered -- in the ladder and the sweep alike."""
    calls: list[str] = []
    with pytest.raises(SystemExit) as stop:
        run(monkeypatch, "fast_ir_probe", ProbeScanner(calls),
            "--film", film, *mode)
    assert stop.value.code == 2
    assert calls == [], "the device was driven for a pass it will refuse"


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


# -- exposure_probe, chunked under the ten-minute kill ------------------------


def chunk(monkeypatch, scanner, only, json_path, *extra):
    return run(monkeypatch, "exposure_probe", scanner, "--frames", "6",
               "--ladder-every", "0", "--only", only, "--json", str(json_path),
               *extra)


def test_chunks_add_up_to_one_walk_on_the_right_frames(monkeypatch, tmp_path):
    """A chunk began wherever the transport was, so `--only 3-4` after a
    rewound `--only 1-2` scanned frames 1-2 again and filed them as 3-4; and
    each chunk rewrote the JSON with its own passes alone -- the join key back
    to the library, gone for every chunk but the last."""
    import json

    out = tmp_path / "exposure.json"
    scanner = ProbeScanner([])
    assert chunk(monkeypatch, scanner, "1-2", out) == 0
    # left on the next chunk's first frame, not rewound
    assert scanner.position_now == 10 + 2
    assert chunk(monkeypatch, scanner, "3-4", out) == 0
    passes = json.loads(out.read_text(encoding="utf-8"))["passes"]
    assert [p["frame"] for p in passes] == [1, 2, 3, 4]
    assert [p["transport_position"] for p in passes] == [10, 11, 12, 13]


def test_a_chunk_on_the_wrong_frame_is_refused_before_a_pass(monkeypatch, tmp_path):
    out = tmp_path / "exposure.json"
    scanner = ProbeScanner([])
    assert chunk(monkeypatch, scanner, "1-2", out) == 0
    scanner.position_now = 10                     # rewound by hand
    calls: list[str] = []
    scanner.calls = calls
    assert chunk(monkeypatch, scanner, "3-4", out) == 2
    assert "scan" not in calls and "reference" not in calls


def test_a_chunk_without_the_file_that_joins_it_is_refused(monkeypatch, tmp_path):
    calls: list[str] = []
    assert run(monkeypatch, "exposure_probe", ProbeScanner(calls),
               "--only", "1-2") == 2
    assert calls == []


def test_the_last_chunk_returns_the_strip_to_its_first_frame(monkeypatch, tmp_path):
    out = tmp_path / "exposure.json"
    scanner = ProbeScanner([])
    for only in ("1-2", "3-4", "5-6"):
        assert chunk(monkeypatch, scanner, only, out) == 0
    assert scanner.position_now == 10


def test_a_whole_walk_starts_wherever_the_film_is(monkeypatch, tmp_path):
    """Only a chunk has a frame it must start on; a whole walk run again into
    the same file starts at its frame 1, wherever that is now."""
    out = tmp_path / "exposure.json"
    scanner = ProbeScanner([])
    argv = ["--frames", "2", "--ladder-every", "0", "--json", str(out)]
    assert run(monkeypatch, "exposure_probe", scanner, *argv) == 0
    scanner.position_now = 40
    assert run(monkeypatch, "exposure_probe", scanner, *argv) == 0
    assert scanner.position_now == 40, "not returned to where it started"


def test_exposure_probe_leaves_a_suspect_device_where_it_is(monkeypatch, tmp_path):
    """Its rewind sent SLIDE_PREV to a scanner a stopped read left busy."""
    calls: list[str] = []
    scanner = ProbeScanner(calls, fail_on=2)
    run(monkeypatch, "exposure_probe", scanner, "--frames", "3",
        "--ladder-every", "0")
    assert "retreat" not in calls


# -- the registration walk's record ------------------------------------------


def test_the_walk_records_what_was_commanded_not_what_was_asked(monkeypatch,
                                                                tmp_path):
    """The walk overwrote the driver's `asked_mm` -- what the command
    travels -- with the request, the one field that was the ground truth."""
    import json

    out = tmp_path / "walk"
    scanner = ProbeScanner([])
    assert run(monkeypatch, "roll_registration_walk", scanner, "--frames", "1",
               "--ladder", "1", "--out", str(out)) == 0
    log = json.loads((out / "walk-A.json").read_text(encoding="utf-8"))
    rung = log["ladders"][0]["rungs"][0]
    assert rung["sent"]["asked_mm"] != rung["sent"]["requested_mm"]
    assert rung["commanded_mm"] == pytest.approx(rung["sent"]["asked_mm"])


def test_a_walk_label_used_again_is_refused(monkeypatch, tmp_path):
    """It wrote over the earlier walk's prescans and log: the corpus."""
    out = tmp_path / "walk"
    out.mkdir()
    (out / "A01_p1.tif").write_bytes(b"the earlier corpus")
    calls: list[str] = []
    assert run(monkeypatch, "roll_registration_walk", ProbeScanner(calls),
               "--frames", "1", "--out", str(out)) == 2
    assert calls == []
    assert (out / "A01_p1.tif").read_bytes() == b"the earlier corpus"


def test_hold_probe_names_the_floor_the_hold_loop_uses(monkeypatch, capsys):
    """It told the operator the floor was 40 when the loop refused below 55."""
    from rps7200.framing import CONFIDENCE_FLOOR

    scanner = ProbeScanner([])

    def held(*a, **k):
        scanner.prescan()
        return {"outcome": "held", "target_mm": 0.0, "final_mm": 0.0,
                "moves": 0, "spent_mm": 0.0, "history": [{"confidence": 70.0}]}

    scanner._hold_to_approved = held
    run(monkeypatch, "hold_probe", scanner)
    out = capsys.readouterr().out
    assert f"floor is {CONFIDENCE_FLOOR:g}" in out
    assert "floor is 40" not in out


# -- the calibration is in every estimate ------------------------------------


def _minutes(out: str) -> float:
    """The first "roughly/about N minutes" a probe printed."""
    import re

    found = re.search(r"(?:roughly|about) ([\d.]+) minutes", out)
    assert found, f"no estimate printed:\n{out}"
    return float(found.group(1))


@pytest.mark.parametrize("name", sorted(PROBES))
def test_every_estimate_counts_the_calibration_the_run_will_make(
        monkeypatch, tmp_path, capsys, name):
    """Every probe calibrates first unless --reuse finds the cache, about
    3.5 minutes, and only byte14 counted it -- so a walk quoted under the
    eight-minute line could run past the ten-minute kill. --reuse with no cache
    calibrates as well, and has to be quoted as though it will."""
    from tools import probing

    monkeypatch.chdir(tmp_path)
    argv = [*probe_argv(name, tmp_path), "--dry-run"]
    quoted = {}
    for label, extra in (("calibrating", []), ("no cache", ["--reuse"])):
        assert run(monkeypatch, name, ProbeScanner([]), *argv, *extra) == 0
        quoted[label] = _minutes(capsys.readouterr().out)
    cache = tmp_path / probing.DEFAULT_REFERENCE
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"a reference")
    assert run(monkeypatch, name, ProbeScanner([]), *argv, "--reuse") == 0
    quoted["reused"] = _minutes(capsys.readouterr().out)

    # Rounded to whole minutes by some probes, so 3.5 can print as 3.
    assert quoted["calibrating"] - quoted["reused"] >= 3, quoted
    assert quoted["no cache"] == quoted["calibrating"], quoted


def test_a_walk_the_calibration_takes_past_eight_minutes_is_told_so(
        monkeypatch, tmp_path, capsys):
    """Six frames are under five minutes of prescans; with the calibration
    first they are past the line where a run has to be backgrounded, and
    the walk said nothing."""
    monkeypatch.chdir(tmp_path)
    argv = ["--frames", "6", "--out", str(tmp_path / "walk"), "--dry-run"]
    assert run(monkeypatch, "roll_registration_walk", ProbeScanner([]),
               *argv) == 0
    assert "past the ~8 minute rule" in capsys.readouterr().out


def test_reuse_with_no_cache_says_it_is_calibrating(tmp_path, capsys):
    """ensure_shading calibrates when the cache is missing, and the probe
    said nothing: 3-4 minutes of mechanism nobody had been told about."""
    import argparse

    from tools import probing

    scanner = ProbeScanner([])
    args = argparse.Namespace(reuse=True,
                              reference=str(tmp_path / "shading.npz"))
    probing.ensure_reference(scanner, args)
    assert "does not exist: calibrating" in capsys.readouterr().out
