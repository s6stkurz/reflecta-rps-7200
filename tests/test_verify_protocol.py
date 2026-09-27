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
