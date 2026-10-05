"""The registration studies read a walk the way the window does.

`tools/registration_margin.py` is the tool named for re-fitting
`CONFIDENCE_FLOOR`, and `tools/roll_registration_study.py` reads the same
cohort. Both read `rolls/*/prescan*.tif` as the files lay on disk: turned or
mirrored the way the operator viewed them, so x-axis measurements ran along
the frame's height or with left and right swapped -- and with each frame's
`prescanNN-before.tif` counted as a frame of its own.
"""
from __future__ import annotations

import json
import re

import numpy as np
import pytest
from conftest import load_tool

from rps7200 import library, preview, tiff
from rps7200.framing import (
    CONFIDENCE_FLOOR,
    MAX_DY_PX,
    hold_plan,
    measure_shift_mm,
    reach_px,
)
from rps7200.library import FilmNotes
from rps7200.protocol import MM_PER_UNIT
from rps7200.session import NUMBERING
from rps7200.shading import ShadingReference
from rps7200.uniformity import register

margin = load_tool("registration_margin")
study = load_tool("roll_registration_study")


def picture(seed: int) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 255, (20, 32, 3),
                                                dtype=np.uint8)


def test_a_turned_walk_is_read_upright_and_its_before_pictures_left_out(tmp_path):
    folder = tmp_path / "roll"
    folder.mkdir()
    frames = {1: picture(1), 2: picture(2)}
    records = []
    for number, image in frames.items():
        # Written as the window writes a walk made turned 90 and mirrored.
        name = f"prescan{number:02d}.tif"
        tiff.write(str(folder / name), preview.orient(image, 90, True))
        tiff.write(str(folder / f"prescan{number:02d}-before.tif"),
                   preview.orient(picture(9), 90, True))
        records.append({"number": number, "prescan": name,
                        "prescan_rotation": 90, "prescan_flipped": True})
    (folder / "survey.json").write_text(json.dumps(
        {"numbering": NUMBERING, "frames": records}), encoding="utf-8")

    got = margin.cohort(folder, 300)
    assert len(got) == 2, [name for name, _ in got]
    for (_, image), original in zip(got, frames.values()):
        assert image.shape == original.shape
        assert np.array_equal(image, original.astype(np.float64))


def test_the_library_cohort_takes_only_corrected_passes(tmp_path):
    """It took every entry at the dpi through `library.corrected` and never
    asked what came back, so a deliberately raw ladder was compared against
    corrected frames -- measuring the shading, as its own comment warns."""
    from rps7200 import library
    from rps7200.direct import SHADING_SKIPPED_EXPLICIT
    from rps7200.shading import ShadingReference

    reference = ShadingReference(ref={c: np.full(32, 40000.0) for c in range(3)},
                                 mean={c: 40000.0 for c in range(3)},
                                 pixels_per_line=32)
    kept = library.save(picture(1), {"resolution_dpi": 300, "channels": 3},
                        root=tmp_path, reference=reference, ccd_mask=bytes(32))
    library.save(picture(2), {"resolution_dpi": 300, "channels": 3,
                              "shading_skipped": SHADING_SKIPPED_EXPLICIT},
                 root=tmp_path)
    library.save(picture(3), {"resolution_dpi": 300, "channels": 3,
                              "demo": True},
                 root=tmp_path, reference=reference, ccd_mask=bytes(32))
    assert [name for name, _ in margin.cohort(tmp_path, 300)] == [kept.name]


def test_a_hold_that_moved_the_wrong_way_is_not_scored_as_delivered(tmp_path,
                                                                     capsys):
    """The delivery ratio divided magnitudes -- the mistake CLAUDE.md names --
    so a frame that moved the wrong way by what it was sent scored 1.0; and an
    arrival the correlator refused went in as a reading."""
    folder = tmp_path / "roll"
    folder.mkdir()

    def held(number, arrived_px, confidence, final_mm, reason):
        # With the driver's own verdict, as every stored look has: a look
        # recorded at an older reach is judged by it, never by a floor
        # fitted at another (`looked`).
        return {"number": number, "registration": {"approved": {
            "target_mm": 0.5, "outcome": "held", "moves": 1,
            "spent_mm": 0.5, "final_mm": final_mm, "residual_mm": 0.0,
            "history": [{"px": arrived_px, "confidence": confidence,
                         "reason": reason}]}}}

    (folder / "survey.json").write_text(json.dumps({
        "numbering": NUMBERING, "prescan_resolution": 300,
        "frames": [held(1, 0, 80.0, -0.5, "matched"),   # sent +0.5, went -0.5
                   held(3, 40, 10.0, 0.5,               # refused arrival
                        "correlation too weak (10.0 below 55)")]}),
        encoding="utf-8")
    study.report_held(folder)
    out = capsys.readouterr().out
    line = next(x for x in out.splitlines() if "delivered per unit" in x)
    assert "-1.000--1.000" in line, line
    assert "wrong way" in line
    # In units, as every transport distance a person reads is: the 0.5 sent
    # is 4.7 of them.
    assert re.search(r"\d\s*mm\b", out) is None, out
    assert "+4.7" in out


def test_a_match_is_scored_both_ways_up_as_the_hold_scores_it(capsys):
    """`measure_shift_mm` takes the stronger of upright and rows-reversed; the
    tool scored one orientation, so the statistic it re-fits the floor on was
    not the gate's. A pass read bottom-up is the same picture to the gate."""
    rng = np.random.default_rng(3)
    base = rng.normal(0, 1, (60, 80))
    for _ in range(3):                    # texture with some structure
        base = base + np.roll(base, 1, axis=1)
    image = np.stack([base] * 3, axis=-1)
    margin.separation([("a", image), ("b", image[::-1].copy())], 300)
    out = capsys.readouterr().out
    assert "same picture" in out and "different picture" not in out, out


def test_a_folder_with_no_manifest_still_leaves_the_before_pictures_out(tmp_path):
    folder = tmp_path / "old"
    folder.mkdir()
    tiff.write(str(folder / "prescan01.tif"), picture(1))
    tiff.write(str(folder / "prescan01-before.tif"), picture(2))
    assert [name for name, _ in margin.cohort(folder, 300)] == ["prescan01.tif"]


# -- the reach every tool searches --------------------------------------------


def _spy(monkeypatch, module):
    """Replace a tool's `register` with one that records how far it was told
    to search, and answers like a real one."""
    seen = []

    def spy(a, b, max_shift=64):
        seen.append(max_shift)
        return register(a, b, max_shift=max_shift)

    monkeypatch.setattr(module, "register", spy)
    return seen


def test_every_tool_searches_as_far_as_the_driver(monkeypatch):
    """`registration_margin.reach_px(300)` worked the reach out from the
    resolution and searched 106 columns of a 300 dpi prescan, where the driver
    searched 105 -- and a confidence is only comparable at the reach it was
    searched at, which is the whole of what these tools exist to re-fit.
    Every one now asks `framing.reach_px` with the pass's width (129 columns
    since `SEARCH_MM` went to 11.0)."""
    driver = reach_px(428)
    assert driver == 129
    rng = np.random.default_rng(7)
    a = rng.integers(0, 255, (287, 428, 3)).astype(np.uint8)
    b = np.roll(a, 5, axis=1)

    seen = _spy(monkeypatch, margin)
    margin.separation([("a", a), ("b", b)], 300)
    assert seen and set(seen) == {driver}
    assert margin.Gate().reach(428) == driver

    seen = _spy(monkeypatch, study)
    study.noise_floor([{"number": 1, "images": (a, b)}], 300, 0.08)
    assert set(seen) == {driver}

    truth = load_tool("transport_truth")
    seen = _spy(monkeypatch, truth)
    truth.shift_mm(a, b, 36.49 / 428)
    assert set(seen) == {driver}


def test_the_surface_reproduces_register_to_the_bit():
    """`--holds` cuts every reach from one surface per pair, and from spectra
    it keeps per picture. Both must be `register`'s own numbers exactly, or a
    re-measured look would not reproduce the confidence the hold stored."""
    rng = np.random.default_rng(11)
    base = rng.normal(0, 1, (60, 90))
    for _ in range(3):
        base = base + np.roll(base, 1, axis=1) + np.roll(base, 1, axis=0)
    a = np.clip(np.stack([base] * 3, -1) * 8 + 128, 0, 255).astype(np.uint8)
    pictures = margin.Pictures()
    for shift, flip in ((7, False), (-12, True), (30, False)):
        b = np.roll(a, shift, axis=1)
        b = (b[::-1] if flip else b).copy()
        pictures._held[("tiff", f"a{shift}")] = a
        pictures._held[("tiff", f"b{shift}")] = b
        for reach in (5, 19, 31, margin.WHOLE_SURFACE):
            up, down = register(a, b, reach), register(a, b[::-1], reach)
            want = down if down[2] > up[2] else up
            for got in (margin.readings(a, b, [reach])[reach],
                        pictures.readings(("tiff", f"a{shift}"),
                                          ("tiff", f"b{shift}"), [reach])[reach]):
                assert (got.dy, got.dx, got.confidence) == want
                assert got.flipped == (down[2] > up[2])


def test_a_candidate_gate_is_judged_without_editing_framing(tmp_path,
                                                            monkeypatch,
                                                            capsys):
    """`--search-mm`, `--max-dy` and `--floor` stand in for the driver's three
    numbers. A pair of different pictures matched at confidence 45, three rows
    off the axis: the driver's floor refuses it, a floor of 40 with a two-row
    gate still refuses it on the gate, and a floor of 40 with three rows lets
    it through -- which is the false positive the tool exists to find."""
    folder = tmp_path / "walk"
    folder.mkdir()
    rng = np.random.default_rng(3)
    for n in (1, 2):
        tiff.write(str(folder / f"prescan0{n}.tif"),
                   rng.integers(0, 255, (287, 428, 3)).astype(np.uint8))
    asked = []

    def fake_register(a, b, max_shift=64):
        asked.append(max_shift)
        return 3, 5, 45.0

    monkeypatch.setattr(margin, "register", fake_register)
    monkeypatch.setattr(margin, "aligned_correlation",
                        lambda *args, **kw: 0.1)
    assert margin.main(["--rolls", str(folder)]) == 0
    assert margin.main(["--rolls", str(folder), "--floor", "40",
                        "--max-dy", "2"]) == 0
    assert margin.main(["--rolls", str(folder), "--floor", "40",
                        "--max-dy", "3"]) == 1
    assert "FALSE POSITIVE" in capsys.readouterr().out
    asked.clear()
    margin.main(["--rolls", str(folder), "--search-mm", "12"])
    assert set(asked) == {reach_px(428, 12.0)} != {reach_px(428)}
    out = capsys.readouterr().out
    assert "a candidate" in out and re.search(r"\d\s*mm\b", out) is None, out


def test_the_off_axis_gate_is_the_drivers_in_rows():
    gate = margin.Gate()
    assert gate.max_dy == MAX_DY_PX and gate.floor == CONFIDENCE_FLOOR
    assert gate.on_axis(MAX_DY_PX, 287) and not gate.on_axis(MAX_DY_PX + 1, 287)
    narrow = margin.Gate(max_dy=2)
    assert narrow.on_axis(2, 287) and not narrow.on_axis(3, 287)
    # In millimetres, as `measure_shift_mm` judges it: a row of a 300 dpi
    # prescan is two of a 600 dpi one.
    assert gate.on_axis(2 * MAX_DY_PX, 574)
    assert not gate.on_axis(2 * MAX_DY_PX + 2, 574)


# -- --holds: the holds the driver made, measured again -----------------------

FLAT = ShadingReference(ref={c: np.full(80, 40000.0) for c in range(3)},
                        mean={c: 40000.0 for c in range(3)},
                        pixels_per_line=80)


def texture(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    base = rng.normal(0, 1, (60, 80))
    for _ in range(3):
        base = base + np.roll(base, 1, axis=1) + np.roll(base, 1, axis=0)
    base = (base - base.min()) / (base.max() - base.min()) * 200 + 20
    return np.stack([base] * 3, axis=-1).astype(np.uint8)


def file(root, image, tags, *, frame="", prescan=None, when="12:00:00",
         **meta):
    from datetime import datetime
    created = datetime.fromisoformat(f"2026-10-05T{when}+00:00")
    return library.save(image, {"resolution_dpi": 300, "channels": 3, **meta},
                        root=root, reference=FLAT, ccd_mask=bytes(80),
                        tags=tags, prescan=prescan, created=created,
                        film=FilmNotes(frame=frame), compress=False)


def member(number, kind):
    return {"roll": "R", "number": number, "kind": kind, "folder": "rolls\\R"}


def look(reference, now):
    """What the hold would have recorded of this look: the driver's own
    numbers, from the pictures the library hands back."""
    image, info = library.corrected(reference)
    assert info["corrected"] == "applied"
    return measure_shift_mm(image, now)[1]


def corrected(path):
    return library.corrected(path)[0]


def held(history, moves=0, reference=None):
    record = {"target_mm": 0.0, "outcome": "held", "moves": moves,
              "spent_mm": 0.3 * moves, "history": history}
    if reference is not None:
        record["reference_entry"] = f"library\\{reference.name}"
    return {"approved": record}


@pytest.fixture
def stored(tmp_path):
    """A library holding four holds of one roll, as the window files them.

    1 moved: its arrival filed tagged `before`, its last look as the frame
      entry's prescan, its reference named.
    2 made no move, and its record was tampered with: it cannot be checked.
    3 is from before records named a reference: only its walk prescan in the
      library, an entry with no `roll_membership`, says what it was held to.
    4 is the first picture again under another number -- a re-walk -- so a
      pairing that calls 1 and 4 different frames is wrong about the pixels.
    """
    root = tmp_path / "library"
    a, b, c = texture(1), texture(2), texture(3)
    ref1 = file(root, a, ["R", "gui", "prescan", "roll"], frame="R-01",
                roll_membership=member(1, "prescan"))
    ref2 = file(root, b, ["R", "gui", "prescan", "roll"], frame="R-02",
                roll_membership=member(2, "prescan"))
    ref3 = file(root, c, ["R", "gui", "prescan", "roll"], frame="R-03")
    ref4 = file(root, np.roll(a, 1, axis=1), ["R", "gui", "prescan", "roll"],
                frame="R-04", roll_membership=member(4, "prescan"))

    before = file(root, np.roll(a, 9, axis=1),
                  ["R", "before", "gui", "prescan", "roll"], when="12:00:40",
                  roll_membership=member(1, "prescan"),
                  started_utc="2026-10-05T12:00:30Z")
    last = np.roll(corrected(ref1), 2, axis=1)
    history = [look(ref1, corrected(before)), look(ref1, last)]
    frame1 = file(root, a, ["R", "gui", "roll"], frame="R-01", prescan=last,
                  when="12:01:40", film="negative",
                  registration=held(history, moves=1, reference=ref1),
                  roll_membership=member(1, "frame"),
                  started_utc="2026-10-05T12:01:30Z")

    now2 = np.roll(corrected(ref2), -4, axis=1)
    tampered = dict(look(ref2, now2))
    tampered["confidence"] = round(tampered["confidence"] + 1.0, 2)
    file(root, b, ["R", "gui", "roll"], frame="R-02", prescan=now2,
         when="12:03:00", film="negative",
         registration=held([tampered], reference=ref2),
         roll_membership=member(2, "frame"), started_utc="2026-10-05T12:02:50Z")

    now3 = np.roll(corrected(ref3), 6, axis=1)
    file(root, c, ["R", "gui", "roll"], frame="R-03", prescan=now3,
         when="12:04:00", film="negative",
         registration=held([look(ref3, now3)]))

    now4 = np.roll(corrected(ref4), 3, axis=1)
    file(root, a, ["R", "gui", "roll"], frame="R-04", prescan=now4,
         when="12:05:00", film="negative",
         registration=held([look(ref4, now4)], reference=ref4),
         roll_membership=member(4, "frame"), started_utc="2026-10-05T12:04:50Z")
    return root, tmp_path / "rolls", {"ref3": ref3, "before": before,
                                      "frame1": frame1}


def test_a_hold_is_paired_with_its_own_looks_and_its_rolls_other_frames(stored):
    root, rolls, named = stored
    holds, candidates = margin.stored_holds(root, rolls)
    by = {h.label_number: h for h in holds}
    assert sorted(by) == [1, 2, 3, 4]
    assert [k.kind for k in by[1].looks] == ["first", "last"]
    assert by[1].looks[0].source == ("entry", named["before"])
    assert [k.kind for k in by[2].looks] == ["only"]
    # Named, or to be found: only a walk prescan with no roll membership is
    # a candidate, and 3's own is one.
    assert by[1].origin == "named" and by[3].reference is None
    assert ("entry", named["ref3"]) in [c[0] for c in candidates["R"]]

    pairs, checks, notes = margin.measure_holds(holds, candidates, [9.0],
                                                say=lambda text: None)
    assert by[3].origin == "identified"
    assert by[3].reference == ("entry", named["ref3"])
    verdicts = {(c["hold"], c["kind"]): c["verdict"] for c in checks}
    assert verdicts == {("R #1", "first"): "reproduced",
                        ("R #1", "last"): "reproduced",
                        ("R #2", "only"): "differs",
                        ("R #3", "only"): "reproduced",
                        ("R #4", "only"): "reproduced"}
    assert all(c["surface_agrees"] for c in checks if "surface_agrees" in c)

    true = [p for p in pairs if p["pairing"] == "true"]
    # 2's own look does not reproduce what it recorded, so it is no true
    # pair -- and is not turned into a null either.
    assert sorted((p["reference"], p["kind"]) for p in true) == [
        ("R #1", "first"), ("R #1", "last"), ("R #3", "only"),
        ("R #4", "only")]
    assert all(p["arbiter"] >= margin.SAME_PICTURE for p in true)
    assert {p["whole"].px for p in true} == {9, 2, 6, 3}
    # Four references against five looks, less 2's own.
    nulls = [p for p in pairs if p["pairing"] == "null"]
    assert len(nulls) == 15
    alike = sorted((p["reference"], p["look"], p["kind"]) for p in nulls
                   if p["arbiter"] >= margin.SAME_PICTURE)
    assert alike == [("R #1", "R #4", "only"), ("R #4", "R #1", "first"),
                     ("R #4", "R #1", "last")]


def test_the_holds_report_counts_by_the_pixels_not_the_pairing(stored, capsys):
    root, rolls, _ = stored
    holds, candidates = margin.stored_holds(root, rolls)
    pairs, checks, notes = margin.measure_holds(holds, candidates,
                                                [9.0, 10.0],
                                                say=lambda text: None)
    summary = margin.report_holds(pairs, checks, notes, [9.0, 10.0],
                                  margin.Gate())
    out = capsys.readouterr().out
    film = summary["films"]["negative"]
    assert (film["true"], film["nulls"], film["alike"]) == (4, 12, 3)
    assert summary["checks"]["first looks / named"] == [2, 3]
    assert summary["checks"]["first looks / identified"] == [1, 1]
    assert summary["checks"]["last looks after a move / named"] == [1, 1]
    at9 = film["reaches"][9.0]
    assert at9["found"] == 4 and at9["lost"] == 0
    kept, total, wrong, astray = at9["floors"]["55/2"]
    assert (total, wrong, astray) == (4, 0, 0)
    assert re.search(r"\d\s*mm\b", out) is None, out


def test_a_look_past_the_reach_is_lost_not_found(tmp_path):
    """A frame that arrived further out than the reach is still a true pair
    -- the arbiter judges it at the whole surface's peak -- and at that reach
    its best peak is noise: lost, and counted against the gate if it passes."""
    pictures = margin.Pictures()
    a = texture(5)
    pictures._held[("tiff", "a")] = a
    pictures._held[("tiff", "b")] = np.roll(a, 25, axis=1)
    hold = margin.Hold(roll="R", number=1, film="negative",
                       entry=tmp_path, record={}, reference=("tiff", "a"))
    other = margin.Hold(roll="R", number=1, film="negative", entry=tmp_path,
                        record={}, reference=("tiff", "a"),
                        looks=[margin.Look(("tiff", "b"), "only", {})])
    near, far = 9.0, 13.0                 # 19 and 28 columns of 80
    assert reach_px(80, near) < 25 < reach_px(80, far)
    pair = margin._pair(hold, other, other.looks[0], pictures, [near, far],
                        True)
    assert pair["arbiter"] >= margin.SAME_PICTURE
    assert pair["whole"].px == 25 and pair["at"][far].px == 25
    assert pair["at"][near].px != 25


# -- --replay: where the carry-over put each frame ------------------------------


def _units(u):
    return u * MM_PER_UNIT


def test_the_replay_predicts_each_arrival_from_the_moves_before_it(tmp_path,
                                                                   capsys):
    """The walk aimed frame 2 by +10 units and frame 4 by -5, so the roll's
    first frame arrives at the sum of the aims after its reference, +5; frame
    2 where frame 1 was left, 0, less the aim at 2; and so on. Frame 3 then
    lands a perforation from its prediction, and frame 4 was never looked at
    because holding had gone off -- and every one of them now gets a move."""
    folder = tmp_path / "R"
    folder.mkdir()

    def aim(units):
        return {"registration": {"correction": {
            "moves": 1, "final_mm": _units(units), "moved": True,
            "moves_sent": [{"action": 0 if units > 0 else 1, "param": 3,
                            "asked_units": round(units, 2)}]}}}

    walk = [{"number": 1}, dict(number=2, **aim(10.0)), {"number": 3},
            dict(number=4, **aim(-5.0))]
    (folder / "survey.json").write_text(json.dumps(
        {"numbering": NUMBERING, "prescan_resolution": 300, "frames": walk}),
        encoding="utf-8")

    def hold(number, arrived, outcome, final=None, moves=()):
        history = [{"px": round(arrived / (36.4913 / 428 / MM_PER_UNIT)),
                    "confidence": 90.0, "reason": "matched", "reach_px": 105}]
        return {"number": number, "registration": {"approved": {
            "target_mm": 0.0, "outcome": outcome, "moves": len(moves),
            "moves_sent": [{"action": 0 if m > 0 else 1, "param": 1,
                            "asked_units": m} for m in moves],
            "history": history, "arrived_units": arrived,
            "final_mm": None if final is None else _units(final)}}}

    roll = [hold(1, 5.0, "held", final=0.0, moves=[-5.0]),
            hold(2, -10.0, "budget", final=-10.0),
            hold(3, -10.0 - 44.9, "budget", final=-54.9),
            {"number": 4, "registration": {"approved": {
                "target_mm": 0.0, "outcome": "off",
                "reason": "holding was switched off earlier in this roll"}}}]
    (folder / "roll.json").write_text(json.dumps(
        {"numbering": NUMBERING, "prescan_resolution": 300, "film": "positive",
         "frames": roll}), encoding="utf-8")

    result = study.replay(folder)
    rows = {r["frame"]: r for r in result["rows"]}
    assert rows[1]["predicted_units"] == pytest.approx(5.0)
    assert rows[2]["predicted_units"] == pytest.approx(-10.0)
    assert rows[3]["predicted_units"] == pytest.approx(-10.0)
    assert rows[4]["predicted_units"] == pytest.approx(-54.9 + 5.0)
    assert [r["perforation"] for r in rows.values()] == [False, False, True,
                                                         False]
    assert rows[3]["after_move_units"] == pytest.approx(-5.0)
    assert rows[4]["source"] == "predicted"
    # Today's first decision, on each first look: everything off its
    # position moves, whatever the stored outcome said.
    for number, row in rows.items():
        look = (row["predicted_units"] if row["source"] == "predicted"
                else row["arrived_units"])
        assert (row["decision"], row["move_units"]) == (
            "move", pytest.approx(-look)), number
        assert hold_plan(0.0, [_units(look)])[1] == "move"

    assert study.main(["--replay", str(folder)]) == 0
    out = capsys.readouterr().out
    assert "PERFORATION?" in out and "budget" in out
    assert "stored 'budget': 2 -- 2 now move; 2 move on the stored look" in out
    assert "stored 'off': 1 -- 1 now move; 1 move on the prediction" in out
    assert re.search(r"\d\s*mm\b", out) is None, out


def test_a_stored_look_is_judged_by_its_own_verdict_at_its_own_reach():
    """A confidence means something only at the reach it was searched at, so a
    stored look is read by the driver's recorded verdict; one that gives none
    is compared against the floor only where its reach is the driver's."""
    recorded = reach_px(428, margin.RECORDED_SEARCH_MM)
    assert recorded == 105
    assert study.looked({"confidence": 90.0, "reason": "matched"}, 428) == (
        True, recorded)
    assert study.looked({"confidence": 90.0, "reason":
                         "matched 0.25 mm off the film axis, which the "
                         "transport cannot do"}, 428)[0] is False
    # No verdict recorded: the floor, but only at the reach it belongs to.
    comparable = True if recorded == reach_px(428) else None
    assert study.looked({"confidence": 60.0}, 428) == (comparable, recorded)
    other = reach_px(428) + 7
    assert study.looked({"confidence": 60.0, "reach_px": other}, 428) == (
        None, other)
    assert study.say_look({"confidence": 60.0}, None, other) == (
        f"60.0@{other} ?")
