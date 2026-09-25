"""The verdict of `tools/filing_load_test.py`, which CLAUDE.md says to trust.

The roll gzips each frame while the next one scans, and this tool is the
measurement that is meant to say whether that is safe. Its verdict could not
say "unsafe": it called a difference safe whenever it was under twice the
spread of all passes together, and that spread contains the difference -- so
a consistent 27% slowdown read "no measurable effect". The timings here are
made up; what is under test is only the judgement drawn from them.
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
    loaded = [21.0, 26.0, 21.5, 25.5]           # +1.0 s on average, +/-2.5
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

