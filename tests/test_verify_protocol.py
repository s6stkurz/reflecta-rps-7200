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


def test_the_probe_retypes_none_of_the_law():
    """FR-11: the stages chose which params to send -- the returns after
    stages 7, 8 and 9, and the restores of 15 and 16 -- from typed copies
    of the law: a 0.591 mm step (param 4 under the ruled-out ramp), 36.49/428
    mm a column, a 1.5724 ramp, stage 15's own 1.2247 and 1.948. Each is
    the protocol's or framing's now. Stage 8's fit is kept as the record of
    that stage, by name, and chooses nothing."""
    import inspect
    import re

    source = inspect.getsource(tool)
    for retyped in ("1.5724", "1.2247", "1.948", "0.591", "36.49", "1.2423",
                    "0.1057", "7.47"):
        assert retyped not in source, retyped
    fitted = [line for line in source.splitlines() if "0.1662" in line]
    assert fitted == ["STAGE8_FIT = 0.1662"], fitted
    uses = re.findall(r"STAGE8_FIT\b", source)
    assert len(uses) == 2, "named, and said once in stage 9's header"
    assert tool.UNITS_PER_PX == pytest.approx(345.235 / 428, rel=1e-4)
