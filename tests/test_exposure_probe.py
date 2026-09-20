"""The exposure walk, held to its shape with no scanner attached.

`tools/exposure_probe.py` costs forty minutes of Stefan's hardware with film
loaded, so everything that can be wrong about it *before* it is driven is worth
catching here: the ladder's shape, the budget the `--dry-run` quotes, and the
report's arithmetic. A ladder with the wrong rungs does not fail -- it spends the
forty minutes and answers a different question.

The band is the thing to watch in these. `EXPOSURE_TARGET`'s acceptance window is
asymmetric on purpose -- under costs a little noise, over clips and nothing
downstream can undo it -- and a report that treated the two alike would call a
clipped frame acceptable, which is the one verdict that must never be wrong.

See `docs/exposure-negative-plan.md`.
"""

import numpy as np
import pytest

from conftest import load_tool
from rps7200.direct import EXPOSURE_TARGET, OVER_TARGET_TOLERANCE

probe = load_tool("exposure_probe")


# -- the ladder --------------------------------------------------------------


def test_the_ladder_opens_and_closes_on_the_metered_exposure():
    """That pair is three things at once: the pass at what ships, the repeat
    pair `noise_split` and `agreement_z` need, and the drift check across the
    frame's own ladder. Drop either end and all three go."""
    assert probe.LADDER[0] == 1.0
    assert probe.LADDER[-1] == 1.0
    assert probe.LADDER.count(1.0) == 2, "one x1.00 is not a repeat pair"


def test_the_rungs_are_evenly_spaced_in_exposure():
    """Geometric, not linear. The measurement is a *ratio* between adjacent
    rungs, so evenly spaced ratios give every band the same weight -- linear
    spacing would crowd the bright end, which is where the bend is."""
    rungs = sorted(probe.RUNGS)
    ratios = [b / a for a, b in zip(rungs, rungs[1:])]
    assert max(ratios) - min(ratios) < 0.05, ratios


def test_the_anchor_pass_is_not_one_of_the_linearity_rungs():
    """x1.00 sits 4% from x0.96, and an adjacent-ratio measure handed a pair
    that close divides one noisy number by another and reports the result as
    compression -- the slide ladder's mistake arriving from the other end. The
    anchor is in `LADDER` because the run needs it; it is out of `RUNGS` because
    the analysis must not chain through it."""
    assert 1.0 not in probe.RUNGS
    assert set(probe.RUNGS) < set(probe.LADDER)
    assert probe.LADDER == (1.0, *probe.RUNGS, 1.0)
    nearest = min(abs(r - 1.0) for r in probe.RUNGS)
    assert nearest < 0.1, "then the anchor could have joined the chain safely"


def test_it_reaches_above_the_target_and_below_half():
    """A ladder that stops at the target cannot find where linearity bends, and
    one that stops near it has no dark end to take a base ratio from."""
    assert max(probe.LADDER) > 1.0
    assert min(probe.LADDER) <= 0.6


def test_the_bright_end_stays_under_saturation():
    """The slide ladder's failure, and the reason this one is not a copy of it:
    red pinned its median at 0.91 from the fifth rung, and every ratio above
    that read as compression when it was only the ceiling. x1.15 on a frame
    metered to 0.80 lands about 0.91 of scale, below `linearity.SATURATED`."""
    linearity = load_tool("linearity")
    assert max(probe.LADDER) * EXPOSURE_TARGET < linearity.SATURATED


# -- the plan ----------------------------------------------------------------


def test_every_frame_is_metered_and_scanned_once_at_what_metering_asked():
    """The check against the current setting, fifteen times instead of once.
    A frame that got no x1.00 pass contributes nothing to the first question."""
    schedule = probe.plan(15, 4)
    assert len(schedule) == 15
    assert [n for n, _ in schedule] == list(range(1, 16))
    for _, rungs in schedule:
        assert 1.0 in rungs


def test_the_ladder_lands_on_a_spread_subset_not_on_everything():
    """Linearity belongs to the sensor, not to the picture, so fifteen readings
    would spend half an hour learning it once -- and the frames that carry it
    have to be spread, or a warm-up trend would sit inside one part of the
    strip and look like a property of the film."""
    laddered = [n for n, rungs in probe.plan(15, 4) if len(rungs) > 1]
    assert laddered == [1, 5, 9, 13]


def test_the_ladder_can_be_turned_off_and_up():
    assert all(len(r) == 1 for _, r in probe.plan(4, 0))
    assert all(len(r) == len(probe.LADDER) for _, r in probe.plan(4, 1))


# -- the budget --------------------------------------------------------------


def test_the_quoted_budget_covers_metering_and_the_advances():
    """A budget that counted only the passes would under-quote by a third: two
    probe rounds per frame is as much scanner time again as the pass itself."""
    schedule = probe.plan(15, 4)
    quoted = probe.budget(schedule)
    passes_alone = sum(len(r) for _, r in schedule) * probe.SECONDS_PER_PASS
    assert quoted > passes_alone * 1.3
    assert 30 * 60 < quoted < 50 * 60, f"{quoted / 60:.0f} minutes"


def test_a_chunk_fits_under_the_ten_minute_foreground_kill():
    """Why `--only` exists. The harness kills a foreground command at ten
    minutes and a killed read is an abandoned read, which is how one wedge
    happened -- so a run that is not backgrounded has to be chunked, and a
    chunk small enough has to be expressible."""
    assert probe.budget(probe.plan(2, 0)) < 8 * 60


# -- the band ----------------------------------------------------------------


def test_over_and_under_are_not_symmetric():
    """`0.72 … 0.82` around 0.80, not `0.72 … 0.88`. The asymmetry is the whole
    policy: clipping cannot be undone, so the tolerance above target is a
    quarter of the one below it."""
    assert probe.verdict(EXPOSURE_TARGET) == "in band"
    assert probe.verdict(EXPOSURE_TARGET + OVER_TARGET_TOLERANCE / 2) == "in band"
    assert probe.verdict(EXPOSURE_TARGET + 0.05) == "OVER"
    # The same distance below is still acceptable, which is the point.
    assert probe.verdict(EXPOSURE_TARGET - 0.05) == "in band"
    assert probe.verdict(EXPOSURE_TARGET - 0.2) == "under"


def test_a_missing_level_is_not_reported_as_a_landing():
    assert probe.verdict(None) == "?"


# -- reading a delivered pass ------------------------------------------------


def test_the_level_is_read_inside_the_film_not_across_the_whole_window():
    """The empty aperture is far brighter than any part of the picture, so
    reading the whole frame lets however much of it is in view set the answer --
    measured at 5.6-10.0% short on real prescans. Same crop as metering's, so
    the number here means the same thing as the number there."""
    frame = np.full((400, 300, 3), 20000, dtype=np.uint16)
    frame[:, :40] = 65535                        # the aperture beside the film
    whole = [float(np.percentile(frame[..., c], 99.5)) / 65535.0
             for c in range(3)]
    inside = probe.levels_of(frame)
    assert whole[0] > 0.9, "the fake has no aperture to be fooled by"
    assert all(v == pytest.approx(20000 / 65535, abs=0.01) for v in inside), inside


# -- the report --------------------------------------------------------------


def row(frame, rung, levels, clamped=(False, False, False)):
    return {"frame": frame, "rung": rung, "levels": list(levels),
            "clamped": list(clamped), "exposure": [0, 0, 0]}


def test_it_names_the_frames_that_missed(capsys):
    passes = [row(1, 1.0, [0.79, 0.80, 0.65]),
              row(2, 1.0, [0.55, 0.79, 0.65]),
              row(3, 1.0, [0.91, 0.80, 0.65])]
    assert probe.report(passes) == 0
    out = capsys.readouterr().out
    assert "[2, 3]" in out, out
    assert "R under" in out and "R OVER" in out


def test_blue_at_its_ceiling_is_not_counted_as_a_miss(capsys):
    """Blue lands 0.65 on every colour negative entry ever metered, pinned at
    its x10.073 timer ceiling. That is the hardware, not metering -- counting it
    would make all fifteen frames a miss and bury the ones that are."""
    assert probe.report([row(1, 1.0, [0.79, 0.80, 0.65], (0, 0, 1))]) == 0
    out = capsys.readouterr().out
    assert "none" in out
    assert "16-bit timer ceiling on 1 of 1" in out


def test_drift_is_reported_before_anything_else_is_believed(capsys):
    """The arrangement `fast_ir_probe` arrived at the hard way: it reported
    "MOVED -- this is where it stops" twice, and both were registration drift.
    A departure smaller than the ladder's own two ends disagree by is not a
    reading, so that disagreement has to be on the page above it."""
    probe.report([row(1, 1.0, [0.800, 0.80, 0.65]),
                  row(1, 1.0, [0.816, 0.80, 0.65])])
    out = capsys.readouterr().out
    assert "drift across each ladder" in out
    assert "+2.00%" in out, out
    assert "not a reading" in out


def test_a_frame_with_no_repeat_pair_claims_no_drift(capsys):
    """The fourteen non-ladder frames have one pass each. Printing a drift row
    for them would either divide by nothing or invent a 0.00% that looks
    measured."""
    probe.report([row(1, 1.0, [0.79, 0.80, 0.65]),
                  row(2, 1.0, [0.79, 0.80, 0.65])])
    assert "drift across each ladder" not in capsys.readouterr().out
