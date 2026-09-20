"""Linearity from stored passes, and keeping the pictures apart.

`tools/linearity.py` measures whether a pass at 1.2x the exposure really is 1.2x
as bright, in every level band. The measure is a per-pixel ratio between two
passes, which carries one hard requirement: **the two passes must be the same
photograph.** Divide one frame by a different one and the quotient is the
subject, reported as a property of the sensor.

`--match` could assume that, because the only ladders on record were brackets of
a single frame. `tools/exposure_probe.py` broke the assumption -- it files four
ladders and fifteen anchor passes under one glob -- so `--probe` groups by frame,
and these hold it to that.

The join is the subtle part. Entries are filed after `close()` and named from
*flush* time, so neither their names nor their order says which frame a pass came
from; `device_settings.exposure` does. See `docs/exposure-negative-plan.md`.
"""

import json

import numpy as np
import pytest

from conftest import load_tool

linearity = load_tool("linearity")


# -- a library that needs no raw bytes ---------------------------------------


@pytest.fixture
def fake_library(tmp_path, monkeypatch):
    """Builds entries carrying an exposure, and a stand-in for the decode.

    The pixels are patched rather than written: `decode_raw` wants real raw
    bytes, a shading reference and a CCD mask, and none of that is what these
    tests are about.
    """
    root = tmp_path / "library"
    root.mkdir()
    made: dict[str, np.ndarray] = {}

    def add(name: str, exposure, level=20000):
        entry = root / name
        entry.mkdir()
        (entry / "scan.json").write_text(json.dumps({
            "scan": {"resolution_dpi": 300, "exposure_scale": [1.0, 1.0, 1.0]},
            "device_settings": {"exposure": list(exposure)},
        }), encoding="utf-8")
        # A gradient, so a band selection has something to select.
        image = np.linspace(1000, level, 100 * 100 * 3, dtype=np.float64)
        made[name] = image.reshape(100, 100, 3).astype(np.uint16)
        return entry

    monkeypatch.setattr(linearity, "pixels",
                        lambda entry, corrected: made.get(entry.name))
    return root, add


def probe_file(tmp_path, passes) -> str:
    path = tmp_path / "exposure.json"
    path.write_text(json.dumps({"target": 0.8, "passes": passes}),
                    encoding="utf-8")
    return str(path)


def a_pass(frame, rung, exposure):
    return {"frame": frame, "rung": rung, "exposure": list(exposure),
            "levels": [0.78, 0.80, 0.64]}


# -- the join ----------------------------------------------------------------


def test_the_two_anchor_passes_are_both_kept(fake_library):
    """A ladder's opening and closing `x1.00` are the same commanded exposure by
    design, so a key holding one path would throw away half the repeat pair --
    the thing `agreement_z` needs to give the frame its own noise baseline."""
    root, add = fake_library
    add("a", (32611, 47752, 65535))
    add("b", (32611, 47752, 65535))
    found = linearity.entries_by_exposure(root)
    assert sorted(p.name for p in found[(32611, 47752, 65535)]) == ["a", "b"]


def test_an_entry_with_no_recorded_exposure_is_skipped(fake_library, tmp_path):
    root, add = fake_library
    add("has-one", (100, 200, 300))
    bare = root / "has-none"
    bare.mkdir()
    (bare / "scan.json").write_text(json.dumps({"scan": {}}), encoding="utf-8")
    assert list(linearity.entries_by_exposure(root)) == [(100, 200, 300)]


def test_a_missing_library_returns_nothing_rather_than_raising(tmp_path):
    assert linearity.entries_by_exposure(tmp_path / "not-there") == {}


# -- grouping ----------------------------------------------------------------


def test_a_chain_never_crosses_between_frames(fake_library, tmp_path):
    """The whole reason `--probe` exists. Two frames, two rungs each, and the
    exposures interleave: frame 2 is denser, so its x0.55 is brighter than frame
    1's x0.66. Sorted by exposure alone the chain would run 1/0.55, 1/0.66,
    2/0.55, 2/0.66 and produce one pair that divides one photograph by another.
    """
    root, add = fake_library
    add("f1-low", (10000, 10000, 10000))
    add("f1-high", (12000, 12000, 12000))
    add("f2-low", (11000, 11000, 11000))
    add("f2-high", (13200, 13200, 13200))
    probe = probe_file(tmp_path, [
        a_pass(1, 0.55, (10000, 10000, 10000)),
        a_pass(1, 0.66, (12000, 12000, 12000)),
        a_pass(2, 0.55, (11000, 11000, 11000)),
        a_pass(2, 0.66, (13200, 13200, 13200)),
    ])
    frames, notes = linearity.by_frame(probe, root)
    assert sorted(frames) == [1, 2]
    assert [p["name"] for p in frames[1]] == ["f1-low", "f1-high"]
    assert [p["name"] for p in frames[2]] == ["f2-low", "f2-high"]
    assert notes == []


def test_the_anchor_is_left_out_of_the_chain(fake_library, tmp_path):
    """x1.00 sits 4% from x0.96. An adjacent-ratio measure handed a pair that
    close divides one noisy number by another and calls the quotient
    compression -- the saturated-top-rung mistake from the other end."""
    root, add = fake_library
    for name, exposure in (("anchor-a", (30000, 30000, 30000)),
                           ("r055", (16500, 16500, 16500)),
                           ("r096", (28800, 28800, 28800)),
                           ("anchor-b", (30000, 30000, 30000))):
        add(name, exposure)
    probe = probe_file(tmp_path, [
        a_pass(1, 1.00, (30000, 30000, 30000)),
        a_pass(1, 0.55, (16500, 16500, 16500)),
        a_pass(1, 0.96, (28800, 28800, 28800)),
        a_pass(1, 1.00, (30000, 30000, 30000)),
    ])
    frames, _ = linearity.by_frame(probe, root)
    assert [p["scale"] for p in frames[1]] == [0.55, 0.96]
    assert not any(p["scale"] == 1.0 for p in frames[1])


def test_a_frame_with_only_the_metered_pass_yields_no_ladder(fake_library,
                                                             tmp_path):
    """Eleven of the fifteen frames are like this. One pass cannot give a ratio,
    and inventing a ladder for them would put eleven empty rows in the report."""
    root, add = fake_library
    add("only", (30000, 30000, 30000))
    probe = probe_file(tmp_path, [a_pass(7, 1.00, (30000, 30000, 30000))])
    frames, notes = linearity.by_frame(probe, root)
    assert frames == {}
    assert notes == [], "a frame that was never laddered is not a problem"


def test_rungs_are_ordered_by_exposure_not_by_the_order_they_were_taken(
        fake_library, tmp_path):
    """The run takes x0.96 before x1.15, but the walk's own order is x1.00 first.
    `departures` compares each pass with the next, so the list has to ascend."""
    root, add = fake_library
    add("bright", (34500, 34500, 34500))
    add("dark", (16500, 16500, 16500))
    add("mid", (24000, 24000, 24000))
    probe = probe_file(tmp_path, [
        a_pass(1, 1.15, (34500, 34500, 34500)),
        a_pass(1, 0.55, (16500, 16500, 16500)),
        a_pass(1, 0.80, (24000, 24000, 24000)),
    ])
    frames, _ = linearity.by_frame(probe, root)
    assert [p["scale"] for p in frames[1]] == [0.55, 0.80, 1.15]


# -- what it says when the join fails ----------------------------------------


def test_a_pass_with_no_matching_entry_is_named_not_dropped(fake_library,
                                                            tmp_path):
    """Filing is best-effort -- `_debug_flush` logs and swallows -- so a pass
    can be in the JSON with no entry behind it. Silently shortening the ladder
    would change the measurement without saying so."""
    root, add = fake_library
    add("there", (16500, 16500, 16500))
    add("also-there", (24000, 24000, 24000))
    probe = probe_file(tmp_path, [
        a_pass(1, 0.55, (16500, 16500, 16500)),
        a_pass(1, 0.80, (24000, 24000, 24000)),
        a_pass(1, 1.15, (34500, 34500, 34500)),
    ])
    frames, notes = linearity.by_frame(probe, root)
    assert [p["scale"] for p in frames[1]] == [0.55, 0.80]
    assert len(notes) == 1
    assert "x1.15" in notes[0] and "34500" in notes[0]


def test_two_frames_sharing_an_exposure_are_reported_as_ambiguous(fake_library,
                                                                  tmp_path):
    """Possible: fifteen frames metering independently could land two on the
    same triple. The join cannot tell them apart, so it says which it used
    rather than picking one quietly."""
    root, add = fake_library
    add("one", (20000, 20000, 20000))
    add("two", (20000, 20000, 20000))
    add("other", (24000, 24000, 24000))
    probe = probe_file(tmp_path, [
        a_pass(1, 0.55, (20000, 20000, 20000)),
        a_pass(1, 0.80, (24000, 24000, 24000)),
    ])
    frames, notes = linearity.by_frame(probe, root)
    assert len(frames[1]) == 2
    assert any("share exposure" in n for n in notes)


# -- the measure itself ------------------------------------------------------


def test_a_perfectly_linear_pair_reads_zero_everywhere():
    """The calibration of the metric. If a clean x1.2 does not read 0.00%, every
    number this tool has ever printed is suspect."""
    dark = np.linspace(2000, 50000, 40000).reshape(200, 200)
    bright = np.clip(dark * 1.2, 0, 65535)
    values = linearity.departures(dark, bright)
    reported = [v for v in values if v is not None]
    assert len(reported) >= 4
    assert max(abs(v) for v in reported) < 0.01, values


def test_a_compressing_sensor_reads_negative_at_the_top():
    """A sensor that gives less than its due at high levels. The sign matters:
    the figure the target was chosen on is a *shortfall*, and a metric that
    reported it positive would argue for the opposite target."""
    dark = np.linspace(2000, 50000, 40000).reshape(200, 200)
    bright = dark * 1.2
    high = bright > 0.7 * 65535
    bright[high] *= 0.98
    values = linearity.departures(dark, np.clip(bright, 0, 65535))
    top = [v for (low, _), v in zip(linearity.BANDS, values)
           if v is not None and low >= 0.70]
    assert top and min(top) < -1.0, values


def test_saturated_pixels_are_dropped_rather_than_read_as_compression():
    """The mistake that made the slide ladder read -16%. A channel pinned at the
    rail reports no level at all, and included it turns a sub-1% effect into a
    catastrophic one."""
    dark = np.linspace(2000, 60000, 40000).reshape(200, 200)
    bright = np.clip(dark * 1.2, 0, 65535)           # the top third pins
    values = linearity.departures(dark, bright)
    reported = [v for v in values if v is not None]
    assert max(abs(v) for v in reported) < 0.01, (
        f"saturation leaked into the measure: {values}")
