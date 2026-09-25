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
