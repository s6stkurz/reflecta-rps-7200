"""The protocol probe's run around its stages: Ctrl-C, and what it keeps.

The stages themselves drive the hardware and are run only with Stefan's
agreement. What is tested here is the part every stage shares: a Ctrl-C
abandoned the read in flight, and results.json was written only once every
stage had succeeded, so one refusal lost the stages before it.
"""
from __future__ import annotations

import json
import sys

import pytest

from conftest import load_tool

tool = load_tool("verify_protocol")


class Scanner:
    def __init__(self, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def wait_ready(self, **kw):
        return True

    def wait_warm(self, **kw):
        pass


def _run(monkeypatch, tmp_path, stages, *numbers):
    monkeypatch.setattr(tool, "DirectScanner", Scanner)
    monkeypatch.setattr(tool, "STAGES", stages)
    monkeypatch.setattr(sys, "argv", ["verify_protocol.py", *map(str, numbers),
                                      "--out", str(tmp_path)])
    return tool.main()


def test_a_stage_that_fails_keeps_the_stages_before_it(monkeypatch, tmp_path):
    def refused(s):
        raise RuntimeError("the scanner refused")

    with pytest.raises(RuntimeError):
        _run(monkeypatch, tmp_path, {1: lambda s: {"one": 1}, 2: refused}, 1, 2)
    kept = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert kept == {"stage1": {"one": 1}}


def test_ctrl_c_finishes_the_stage_in_flight_and_starts_no_other(monkeypatch,
                                                                tmp_path):
    from rps7200 import console

    ran = []
    monkeypatch.setattr(console.DeferredInterrupt, "requested",
                        lambda self: bool(ran))
    stages = {1: lambda s: ran.append(1) or {"one": 1},
              2: lambda s: pytest.fail("a stage started after Ctrl-C")}
    assert _run(monkeypatch, tmp_path, stages, 1, 2) == 130
    kept = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert kept == {"stage1": {"one": 1}}


def test_earlier_runs_results_are_kept_beside_this_ones(monkeypatch, tmp_path):
    (tmp_path / "results.json").write_text(json.dumps({"stage9": {"old": 1}}),
                                          encoding="utf-8")
    assert _run(monkeypatch, tmp_path, {1: lambda s: {"one": 1}}, 1) == 0
    kept = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert kept == {"stage9": {"old": 1}, "stage1": {"one": 1}}


def test_a_shot_is_measured_from_the_file_it_leaves_written_whole(
        monkeypatch, tmp_path):
    """Read back from disk to be measured, as every delivered file is
    measured -- and written as one is (`export.write`): beside and renamed
    over, so a kill part way leaves the last whole file, never half of one."""
    import numpy as np

    from rps7200 import export, tiff

    monkeypatch.setattr(tool, "OUT", tmp_path)
    image = np.arange(4 * 6 * 3, dtype=np.uint8).reshape(4, 6, 3)

    class Shooting:
        def scan(self, **kw):
            return image, {}

    measured, _took = tool.shot(Shooting(), "one")
    assert np.array_equal(measured, image)
    real = tiff.write

    def full(path, picture, **kw):
        real(path, picture[:2], **kw)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(export.tiff, "write", full)
    with pytest.raises(OSError):
        tool.shot(Shooting(), "one")
    assert np.array_equal(tiff.read(str(tmp_path / "one.tif")), image)
    assert [p.name for p in tmp_path.iterdir()] == ["one.tif"]


def test_the_probe_types_its_figures_once_each_by_name():
    """FR-11: the figures the stages count with were typed inline -- 36.49/428
    mm a column, 7.47 and 0.591 mm, a 1.5724 ramp and 0.1057/0.1662, stage
    15's own 1.2247 and 1.948. Each is named once now, and every one is a
    measurement or that stage's own fit rather than a copy of the law, which
    is why it stays (see the next test)."""
    import inspect

    source = inspect.getsource(tool)
    lines = [text for text in source.splitlines()
             if not text.lstrip().startswith(("#", '"'))]
    for retyped in ("1.5724", "36.49", "1.2423", "0.1057"):
        assert not [text for text in lines if retyped in text], retyped
    named = {"7.47": "RETURN_01_47_03_UNITS = to_units(7.47)",
             "0.591": "STAGE7_PARAM4_UNITS = to_units(0.591)",
             "0.1662": "STAGE8_FIT = 0.1662",
             "1.2247": "STAGE15_COLUMNS_PER_UNIT, STAGE15_RAMP_UNITS = "
                       "1.2247, 1.948",
             "1.948": "STAGE15_COLUMNS_PER_UNIT, STAGE15_RAMP_UNITS = "
                      "1.2247, 1.948"}
    for literal, line in named.items():
        code = [text for text in lines if literal in text]
        assert code == [line], (literal, code)
    assert tool.UNITS_PER_PX == pytest.approx(345.235 / 428, rel=1e-4)


def test_the_probe_sends_what_it_sent_before_units():
    """Stefan's decision on units: nothing sent to the scanner changes. The
    returns after stages 7, 8 and 9 and the restores of 15 and 16 choose
    how many commands and which params; deriving them from the law in force
    changed them -- stage 7's `01 47 00 03` counted in the law's 72.84
    units, which is value 0x04's figure carried to param 71, where that
    command itself measured 7.47 mm. Held here to the expressions of
    b0b546a, over every travel a stage can report, to 0.01 px."""
    import numpy as np

    old_mm_per_px = 36.49 / 428          # the column as b0b546a typed it

    def law87(): return 0.1057 * 87 + 0.1662

    def near_half(q: float) -> bool:
        # The column is now the aperture's 36.4913 mm over 428 rather than a
        # typed 36.49, and the ramps are 0.1662/0.1057 rather than 1.5724:
        # 4e-5 apart at most, so a quotient within that of a half may round
        # the other way. A travel that lands there is chance, not a change.
        return abs((q % 1.0) - 0.5) < 1e-4 * abs(q) + 1e-6

    pixels = np.arange(0.0, 2500.0, 0.01)
    moved = 0
    for px in pixels:
        mm, units = px * old_mm_per_px, px * tool.UNITS_PER_PX
        for old_q, new in ((mm / 7.47, tool.stage7_returns(units)),
                           (mm / 0.591, tool.stage8_returns(units)),
                           (mm / law87(), tool.stage9_returns(units))):
            if not near_half(old_q):
                assert new == max(0, round(old_q)), (px, old_q, new)
            else:
                moved += 1
        # 1.5724 was stage 8's 0.1662 mm over 0.1057, to four places.
        old_q = px / 1.2423 - 1.5724
        if not near_half(old_q):
            assert tool.stage15_restore_param(px) == min(
                87, max(1, int(round(old_q)))), px
        assert tool.stage16_param(px) == max(
            1, min(tool.STAGE16_MAX_PARAM, int(round(px / 1.2247 - 1.948)))), px
    assert moved < 3 * len(pixels) // 50, "the band excuses few"
    for param in range(0, 200):
        assert tool.stage15_expected_columns(param) == pytest.approx(
            (param + 1.5724) * 1.2423, abs=1e-3)


def test_stage9_searches_where_it_did_before_units(monkeypatch):
    """Stage 9 centres its search on stage 8's fit, as b0b546a did. Centred
    on the law in force it moved a pixel -- 109 to 110 at param 87, 6 to 7
    at 9b's param 4 -- and a peak at the window's edge then changed the
    travel measured, and how many `01 57 00 04` went back. The helpers'
    grid could not see that: it is in the stage's body."""
    import numpy as np

    centres: list[float] = []

    def near(a, b, expect, window=30):
        centres.append(expect)
        return expect, 0.0

    class Device:
        def __init__(self):
            self.sent = []

        def slide(self, action, param, value):
            self.sent.append((action, param, value))

        def test_unit_ready(self):
            return True

    monkeypatch.setattr(tool, "_shift_near", near)
    monkeypatch.setattr(tool, "shot",
                        lambda s, name: (np.zeros((3, 3, 3), np.uint8), None))
    monkeypatch.setattr(tool.time, "sleep", lambda seconds: None)
    device = Device()
    tool.stage9(device)

    def before(param):                   # b0b546a's centre, in pixels
        return (0.1057 * param + 0.1662) / (36.49 / 428)

    params = [p for p in (20, 30, 50, 70, 87) for _ in range(2)] + [4] * 20
    assert [int(c) for c in centres] == [int(before(p)) for p in params]
    travelled = sum(0.1057 * p + 0.1662 for p in params[:10])
    back = round(travelled / (0.1057 * 87 + 0.1662))
    assert device.sent == ([(0x00, 0x08, 0x04)] * 5
                           + [(0x00, p, 0x04) for p in params[:10]]
                           + [(0x01, 87, 0x04)] * back
                           + [(0x00, 0x04, 0x04)] * 25)
