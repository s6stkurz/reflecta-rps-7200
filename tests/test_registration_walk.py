"""The registration walk's control logic, with no scanner underneath.

What these hold it to is the part a dry run cannot show and a hardware run
shows only by going wrong: that the film is never asked to go further from a
frame's starting position than the aperture allows, that the ladder puts it
back, and that a device already reporting a scan in progress is refused rather
than walked.

The pixels are irrelevant here -- a prescan is a small flat array. What is
under test is the sequence of moves.
"""
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import load_tool

walk_tool = load_tool("roll_registration_walk")


class FakeScanner:
    """Records every move, answers every prescan with a flat frame."""

    def __init__(self):
        self.moves: list[float] = []
        self.advances = 0
        self.prescans = 0
        self._position = 0

    def prescan(self, resolution=300, keep_raw=False):
        self.prescans += 1
        image = np.full((20, 40, 3), 40 + self.prescans, np.uint8)
        return image, {"resolution_dpi": resolution}

    def nudge(self, millimetres):
        self.moves.append(float(millimetres))
        return {"param": 1, "forward": millimetres > 0,
                "asked_mm": float(millimetres)}

    def advance(self, steps=1, timeout=30.0, poll=0.5):
        self.advances += 1
        self._position += 1
        return self._position

    def position(self):
        return self._position


def make(tmp_path, label="A"):
    scanner = FakeScanner()
    return scanner, walk_tool.Walk(scanner, tmp_path, 300, label)


# -- the frame loop ---------------------------------------------------------


def test_each_frame_is_prescanned_twice(tmp_path):
    """Two passes with nothing moved between them are the noise floor, and
    every other number in the study is uninterpretable without it: the
    decision band is under three pixels wide and the floor is plausibly one."""
    scanner, walk = make(tmp_path)
    record = walk.frame(1)
    assert scanner.prescans == 2
    assert len(record["passes"]) == 2
    assert not scanner.moves, "a frame must not move the film"
    for name in record["passes"]:
        assert (tmp_path / name).exists()


def test_the_two_passes_are_written_under_different_names(tmp_path):
    _scanner, walk = make(tmp_path)
    record = walk.frame(7)
    assert record["passes"] == ["A07_p1.tif", "A07_p2.tif"]


# -- the ladder -------------------------------------------------------------


def test_the_ladder_visits_every_rung_and_comes_back(tmp_path):
    """Seven rungs spanning +-0.816 mm, then home. The commanded net has to be
    zero: a ladder that leaves the film displaced would carry its offset into
    the next frame and every reading after it."""
    scanner, walk = make(tmp_path)
    record = walk.ladder(3)
    assert len(record["rungs"]) == 2 * walk_tool.RUNGS + 1
    assert sum(scanner.moves) == pytest.approx(0.0, abs=1e-9)
    assert scanner.prescans == 2 * walk_tool.RUNGS + 1


def test_the_ladder_reverses_exactly_once(tmp_path):
    """Backlash swallows two to three commands after a direction change, so
    the ladder turns around once -- at the start, where the cost is a step that
    may not land rather than a reading that is wrong. The restore at the end
    is the second and last reversal, and no rung is measured after it."""
    scanner, walk = make(tmp_path)
    walk.ladder(1)
    # The restore is the second reversal and nothing is measured after it, so
    # what matters is the sequence up to the last rung.
    measuring = scanner.moves[:-1]
    signs = [m > 0 for m in measuring]
    flips = sum(1 for a, b in zip(signs, signs[1:], strict=False) if a != b)
    assert flips == 1, f"reversed {flips} times while measuring: {measuring}"
    assert measuring[0] < 0, "the one turn-around must be first"
    assert all(m > 0 for m in measuring[1:]), measuring
    assert scanner.moves[-1] < 0, "and the restore comes back"


def test_the_ladder_spans_beyond_what_the_aperture_allows(tmp_path):
    """Deliberately. "Refuses correctly" is something a detector has to do and
    nothing has ever tested it, so the corpus needs readings from outside the
    legal range as well as inside it."""
    from rps7200.framing import MAX_REGISTRATION_MM

    reach = walk_tool.RUNGS * walk_tool.RUNG_MM
    assert reach > MAX_REGISTRATION_MM


# -- the safety envelope ----------------------------------------------------


def test_a_move_past_the_excursion_limit_is_refused(tmp_path):
    """The safety number is how far the film sits from where the frame
    started, not how far it has travelled -- a symmetric ladder runs up
    distance without ever being far from home."""
    scanner, walk = make(tmp_path)
    with pytest.raises(RuntimeError, match="from where this frame started"):
        walk._nudge(walk_tool.MAX_EXCURSION_MM + 0.5, 0.0)
    assert not scanner.moves, "it must refuse before sending anything"


def test_the_excursion_is_measured_from_the_frames_start_not_from_zero(tmp_path):
    """A move that is small on its own is still refused if it lands outside."""
    scanner, walk = make(tmp_path)
    near = walk_tool.MAX_EXCURSION_MM - 0.1
    with pytest.raises(RuntimeError):
        walk._nudge(0.5, near)
    assert not scanner.moves


def test_the_whole_run_has_a_travel_stop(tmp_path):
    """So a surprise cannot walk the film across the aperture while nobody is
    counting."""
    scanner, walk = make(tmp_path)
    walk.travel = walk_tool.TOTAL_TRAVEL_LIMIT_MM - 0.01
    with pytest.raises(RuntimeError, match="already moved the film"):
        walk._nudge(0.2719, 0.0)
    assert not scanner.moves


def test_travel_is_counted_in_both_directions(tmp_path):
    """Distance, not displacement -- backlash and wear do not care which way."""
    _scanner, walk = make(tmp_path)
    walk._nudge(0.2719, 0.0)
    walk._nudge(-0.2719, 0.2719)
    assert walk.travel == pytest.approx(2 * 0.2719)


# -- what it refuses to start on --------------------------------------------


def test_a_device_reporting_a_scan_in_progress_is_refused(tmp_path, monkeypatch,
                                                          capsys):
    """0x80 on byte 6 with nothing running is an abandoned pass, and the
    documented remedy is a power cycle. Walking a wedged device would spend
    fifteen minutes of transport to collect nothing."""
    class Wedged(FakeScanner):
        def open(self):
            return self

        def read_state(self):
            return SimpleNamespace(scanning=0x9d, position=2, warming_up=False)

        def close(self):
            pass

    monkeypatch.setattr(walk_tool, "DirectScanner", lambda **kw: Wedged())
    monkeypatch.setenv("RPS7200_DEBUG", "1")
    monkeypatch.setattr(sys, "argv", ["w.py", "--out", str(tmp_path)])
    assert walk_tool.main() == 2
    assert "scan in progress" in capsys.readouterr().err


def test_it_refuses_to_run_without_debug_filing(tmp_path, monkeypatch, capsys):
    """A walk that files nothing cannot be re-analysed, and this one is the
    corpus the whole study is built on."""
    monkeypatch.delenv("RPS7200_DEBUG", raising=False)
    monkeypatch.setattr(sys, "argv", ["w.py", "--out", str(tmp_path)])
    assert walk_tool.main() == 2
    assert "RPS7200_DEBUG" in capsys.readouterr().err


def test_a_dry_run_opens_no_device(tmp_path, monkeypatch, capsys):
    def explode(**kw):
        raise AssertionError("a dry run must not construct a scanner")

    monkeypatch.setattr(walk_tool, "DirectScanner", explode)
    monkeypatch.setattr(sys, "argv",
                        ["w.py", "--dry-run", "--ladder", "1,15",
                         "--out", str(tmp_path)])
    assert walk_tool.main() == 0
    out = capsys.readouterr().out
    assert "no device was opened" in out
    # The eight-minute rule has to be visible where the launch is decided.
    assert "8 minute rule" in out


def test_it_stops_when_the_film_stops_advancing(tmp_path, monkeypatch, capsys):
    """Walk B re-scanned one position fourteen times because this check was
    missing -- twelve minutes of scanner time for a corpus that looks like a
    strip and is not one.

    Contrast cannot substitute for the transport's own refusal: clear base
    past the last frame measured 0.292, well above BLANK_CONTRAST (0.02), so
    the blank test sees a picture there.
    """
    class EndsAtThree(FakeScanner):
        def open(self):
            return self

        def read_state(self):
            return SimpleNamespace(scanning=0x0d, position=0, warming_up=False)

        def advance(self, steps=1, timeout=30.0, poll=0.5):
            if self._position >= 3:
                return None                      # the end of the strip
            return super().advance(steps, timeout, poll)

        def session_start(self):
            pass

        def wait_warm(self):
            pass

        def close(self):
            pass

    scanner = EndsAtThree()
    monkeypatch.setattr(walk_tool, "DirectScanner", lambda **kw: scanner)
    monkeypatch.setenv("RPS7200_DEBUG", "1")
    monkeypatch.setattr(sys, "argv",
                        ["w.py", "--frames", "16", "--out", str(tmp_path)])
    assert walk_tool.main() == 0
    assert "end of the strip" in capsys.readouterr().out
    # Four frames scanned, not sixteen: 0,1,2,3 then the refusal.
    assert scanner.prescans == 2 * 4, scanner.prescans


def test_the_estimate_crosses_the_eight_minute_rule(tmp_path):
    """Not a cosmetic check. 16 frames at two prescans each is past the point
    where a foreground command is killed, and a killed read is an abandoned
    read -- which is how one wedge here happened."""
    assert walk_tool.estimate(16, 0) > 8 * 60
