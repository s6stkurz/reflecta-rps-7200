"""The verdict of `tools/filing_load_test.py`, which CLAUDE.md says to trust.

The roll gzips each frame while the next one scans, and this tool is the
measurement that is meant to say whether that is safe. Its verdict could not
say "unsafe": it called a difference safe whenever it was under twice the
spread of all passes together, and that spread contains the difference -- so
a consistent 27% slowdown read "no measurable effect". Once it could, the
order it took its passes in could put it there without cause: quiet first in
every round, so whatever drifts inside a round landed on the loaded arm alone.

The timings here are made up; what is under test is the judgement drawn from
them and the order they are taken in.
"""

import pytest

from conftest import load_tool

tool = load_tool("filing_load_test")


def test_a_consistent_slowdown_is_unsafe():
    """The audit's case: +6 s on every 22 s pass, a second of jitter."""
    quiet = [22.1, 21.4, 22.9, 21.8]
    loaded = [28.3, 27.2, 28.6, 28.1]
    outcome, why = tool.verdict(quiet, loaded)
    assert outcome == "unsafe", why
    assert "do NOT overlap" in why


def test_no_effect_is_safe():
    quiet = [22.1, 21.4, 22.9, 21.8, 22.3]
    loaded = [22.2, 21.3, 23.0, 21.9, 22.2]
    outcome, why = tool.verdict(quiet, loaded)
    assert outcome == "safe", why


def test_a_noisy_run_near_the_line_says_so_rather_than_guessing():
    quiet = [22.0, 22.0, 22.0, 22.0]
    loaded = [21.0, 26.0, 21.5, 25.5]           # +1.5 s on average, +/-2.5
    outcome, why = tool.verdict(quiet, loaded)
    assert outcome == "inconclusive", why
    assert "more rounds" in why


def test_too_few_rounds_cannot_be_judged():
    outcome, why = tool.verdict([22.0, 22.1], [30.0, 30.1])
    assert outcome == "inconclusive"
    assert "at least" in why


@pytest.mark.parametrize("limit,expected", [(0.05, "unsafe"), (0.20, "safe")])
def test_the_line_is_the_stated_one(limit, expected):
    """A +10% slowdown is over a 5% line and under a 20% one: the verdict
    follows the limit it states, not a spread it computes."""
    quiet = [20.0, 20.1, 19.9, 20.0]
    loaded = [22.0, 22.1, 21.9, 22.0]
    outcome, why = tool.verdict(quiet, loaded, limit)
    assert outcome == expected, why
    assert f"{limit:.0%}" in why


class Bench:
    """A pass that costs `drift` more when it is the second of its round --
    lamp, heat, a warm cache -- and `effect` more with the grinder running."""

    def __init__(self, drift=0.0, effect=0.0):
        self.drift, self.effect = drift, effect
        self.taken = 0
        self.loaded = False

    def one(self):
        second = self.taken % 2 == 1
        self.taken += 1
        return 22.0 + self.drift * second + self.effect * self.loaded

    def load(self):
        bench = self

        class Load:
            passes = 0

            def __enter__(self):
                bench.loaded = True
                return self

            def __exit__(self, *exc):
                bench.loaded = False

        return Load()


def rounds(bench, n=4):
    return tool.run_rounds(bench.one, n, bench.load, show=lambda line: None)


def test_drift_inside_a_round_is_not_read_as_an_effect(monkeypatch):
    """Every round ran quiet first, so the loaded pass always took the second
    slot: 2 s of warm-up inside a round, and no effect at all, read as a
    consistent 9% slowdown -- "unsafe", and the pairing could not see it."""
    quiet, loaded = rounds(Bench(drift=2.0))
    outcome, why = tool.verdict(quiet, loaded)
    assert outcome == "inconclusive", why
    # And the drift is shown, not only absorbed: +2 s one way, -2 s the other.
    assert "+2.00s in rounds run quiet first, -2.00s loaded first" in why

    monkeypatch.setattr(tool, "loaded_first", lambda i: False)
    outcome, why = tool.verdict(*rounds(Bench(drift=2.0)))
    assert outcome == "unsafe", (
        f"the fixture no longer shows the defect in the old order: {why}")


def test_the_order_alternates_and_the_arms_stay_paired():
    bench = Bench(effect=5.0)
    quiet, loaded = rounds(bench, 5)
    assert [tool.loaded_first(i) for i in range(5)] == [False, True, False,
                                                        True, False]
    assert quiet == [22.0] * 5 and loaded == [27.0] * 5
    assert bench.taken == 10


def test_a_real_slowdown_survives_the_alternation():
    quiet, loaded = rounds(Bench(effect=6.0), 4)
    outcome, why = tool.verdict(quiet, loaded)
    assert outcome == "unsafe", why


def test_an_odd_count_of_rounds_still_cancels_the_drift():
    """Three rounds are two of one order and one of the other; the mean of
    all three differences would keep a third of the drift, the mean of the
    two orders' means keeps none."""
    quiet, loaded = rounds(Bench(drift=2.0, effect=3.0), 3)
    _outcome, why = tool.verdict(quiet, loaded)
    assert why.startswith("loaded passes +3.00s"), why


def test_ctrl_c_finishes_the_pass_in_flight_and_starts_no_other(monkeypatch):
    """With the default handler a Ctrl-C abandoned the read in flight --
    in the run CLAUDE.md asks for before trusting the roll path."""
    import sys

    from rps7200 import console

    passes = []

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

        def scan(self, **kw):
            passes.append(kw)

    monkeypatch.setattr(tool, "DirectScanner", Scanner)
    monkeypatch.setattr(console.DeferredInterrupt, "requested",
                        lambda self: len(passes) >= 1)
    monkeypatch.setattr(sys, "argv", ["filing_load_test.py", "--mb", "1"])
    assert tool.main() == 130
    assert len(passes) == 1


@pytest.mark.parametrize("argv", [["--rounds", "0"], ["--rounds", "2"],
                                  ["--mb", "0"], ["--limit", "-5"]])
def test_what_cannot_give_a_verdict_is_refused_before_the_lamp(monkeypatch,
                                                              argv):
    """--rounds 0 warmed the lamp and then crashed on the mean of nothing."""
    import sys

    opened = []
    monkeypatch.setattr(tool, "DirectScanner",
                        lambda **kw: opened.append(kw) or None)
    monkeypatch.setattr(sys, "argv", ["filing_load_test.py", *argv])
    with pytest.raises(SystemExit) as refused:
        tool.main()
    assert refused.value.code == 2
    assert opened == []


@pytest.mark.parametrize("rounds, warned", [("4", False), ("14", True)])
def test_the_run_is_estimated_before_the_device_opens(monkeypatch, capsys,
                                                      rounds, warned):
    """CLI-15: every other tool said what a run would cost before opening
    the scanner, and this said nothing -- --rounds 14 is 28 passes and the
    lamp, past the ten-minute kill, which abandons the read in flight."""
    import sys

    from rps7200 import session

    class Opened(Exception):
        pass

    def opening(**kw):
        raise Opened

    monkeypatch.setattr(tool, "DirectScanner", opening)
    monkeypatch.setattr(sys, "argv", ["filing_load_test.py", "--mb", "1",
                                      "--rounds", rounds])
    with pytest.raises(Opened):
        tool.main()
    said = capsys.readouterr()
    # The passes it takes, at the resolution it takes them, counted.
    typical = 2 * int(rounds) * session.estimate_seconds(tool.RESOLUTION, False)
    assert f"estimated {typical / 60:.1f} min" in said.out
    assert ("in the background" in said.err) is warned


@pytest.mark.parametrize("outcome, code", [("safe", 0), ("unsafe", 1),
                                           ("inconclusive", 3)])
def test_the_exit_status_says_which_verdict(monkeypatch, outcome, code):
    """It returned 0 for all three, so a script gating the unattended roll
    path on this run read "unsafe" as a pass."""
    import sys

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

        def scan(self, **kw):
            pass

    monkeypatch.setattr(tool, "DirectScanner", Scanner)
    monkeypatch.setattr(tool, "verdict", lambda *a, **k: (outcome, "made up"))
    monkeypatch.setattr(sys, "argv", ["filing_load_test.py", "--mb", "1"])
    assert tool.main() == code


def test_a_quiet_mean_of_nothing_is_reported_not_divided_by(monkeypatch):
    """A pass too quick for the clock -- a stand-in's, on Windows, whose
    coarse timer read every one as 0 s -- made the summary divide by zero
    and the run end in a traceback instead of its verdict."""
    import sys

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

        def scan(self, **kw):
            pass

    monkeypatch.setattr(tool, "DirectScanner", Scanner)
    # Both clocks: whichever the pass is timed with reads the same instant.
    monkeypatch.setattr(tool.time, "perf_counter", lambda: 0.0)
    monkeypatch.setattr(tool.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(tool, "verdict", lambda *a, **k: ("safe", "made up"))
    monkeypatch.setattr(sys, "argv", ["filing_load_test.py", "--mb", "1"])
    assert tool.main() == 0


# -- --library: the report from stored records, which opens no device --------


def _filed(root, kind, started, duration, filed=None, filing_s=10.0,
           host_load=None, gap=None, dpi=300):
    """An entry whose pass began at ``started`` (UTC) for ``duration`` s,
    filed from ``filed`` for ``filing_s`` s."""
    import os
    from datetime import datetime, timedelta, timezone

    import numpy as np

    from rps7200 import library

    begun = datetime.fromisoformat(started).replace(tzinfo=timezone.utc)
    when = (datetime.fromisoformat(filed).replace(tzinfo=timezone.utc)
            if filed else begun + timedelta(seconds=duration + 3))
    meta = {"resolution_dpi": dpi, "channels": 3, "depth": 8,
            "duration_s": duration,
            "started_utc": begun.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "roll_membership": {"roll": "r", "frame": 1, "kind": kind}}
    if host_load is not None:
        meta["host_load"] = host_load
    if gap is not None:
        meta["commands"] = {"sent": [], "image_reads": {
            "reads": 4, "bytes": 4096, "host_gaps": 3,
            "host_gap_max_s": gap, "host_gap_p99_s": gap,
            "in_transfer_max_s": 2.6}}
    entry = library.save(np.zeros((4, 4, 3), np.uint8), meta, root=root,
                         created=when)
    end = (when + timedelta(seconds=filing_s)).timestamp()
    os.utime(entry / "scan.json", (end, end))
    return entry


def test_the_library_report_tells_overlapped_passes_from_the_rest(
        tmp_path, monkeypatch, capsys):
    import sys

    _filed(tmp_path, "frame", "2026-10-01T10:00:00", 85.0,
           filed="2026-10-01T10:01:28", filing_s=10.0)
    # The next frame's prescan, taken while that frame was being filed.
    _filed(tmp_path, "prescan", "2026-10-01T10:01:30", 10.4,
           filed="2026-10-01T10:05:00", filing_s=0.5,
           host_load={"filing": 7.9}, gap=0.4)
    # One taken with nothing beside it.
    _filed(tmp_path, "prescan", "2026-10-01T11:00:00", 10.2,
           filed="2026-10-01T11:05:00", filing_s=0.5, host_load={}, gap=0.05)
    # A filing whose record a compaction rewrote much later: no end.
    _filed(tmp_path, "scan", "2026-10-01T12:00:00", 22.0, filing_s=3600.0)

    opened = []
    monkeypatch.setattr(tool, "DirectScanner",
                        lambda **kw: opened.append(kw))
    monkeypatch.setattr(sys, "argv", ["filing_load_test.py", "--library",
                                      str(tmp_path)])
    assert tool.main() == 0
    assert opened == [], "a report opened the scanner"
    out = capsys.readouterr().out
    assert "4 passes" in out and "1 whose filing's end cannot be told" in out
    assert "No verdict" in out

    report = tool.library_report(tmp_path, say=lambda line: None)
    prescans = report["groups"][("prescan", 300, 8)]
    assert prescans["overlapped"] == [10.4] and prescans["alone"] == [10.2]
    assert prescans["recorded_load"] == [10.4]
    assert prescans["recorded_none"] == [10.2]
    assert prescans["gaps_load"] == [0.4] and prescans["gaps_none"] == [0.05]
    # Only from a day on, when asked.
    from datetime import datetime, timezone
    later = tool.library_report(
        tmp_path, since=datetime(2026, 10, 1, 10, 30, tzinfo=timezone.utc),
        say=lambda line: None)
    assert later["passes"] == 2


@pytest.mark.parametrize("argv", [["--since", "2026-10-01"],
                                  ["--library", "no-such-folder"],
                                  ["--library", ".", "--since", "yesterday"]])
def test_a_report_it_cannot_make_is_refused_before_anything(monkeypatch,
                                                            argv):
    import sys

    opened = []
    monkeypatch.setattr(tool, "DirectScanner",
                        lambda **kw: opened.append(kw))
    monkeypatch.setattr(sys, "argv", ["filing_load_test.py", *argv])
    with pytest.raises(SystemExit) as refused:
        tool.main()
    assert refused.value.code == 2 and opened == []
