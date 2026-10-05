"""`tools/hold_check.py`, on a roll and a library made up here.

No scanner and no stored library: a film is drawn here, prescanned at 300
dpi and scanned at twice that a known distance from where its position was
set, and filed with `library.save` as a roll files it. What the tool says the
delivered frame measures has to be that distance, well inside the smallest
move; and the pictures a person judges it by have to be drawn, both guides
in both panels.
"""
import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pytest

from conftest import load_tool
from rps7200 import library
from rps7200.framing import APERTURE_MM
from rps7200.library import FilmNotes
from rps7200.protocol import MM_PER_UNIT

hold_check = load_tool("hold_check")

ROWS, COLUMNS = 287, 428            # a 300 dpi prescan, as the device sends it
SUB = 4                             # samples of the film per prescan column


def _units(columns: float) -> float:
    """Prescan columns as units, on the hold's own scale."""
    return columns * APERTURE_MM / COLUMNS / MM_PER_UNIT


def _film(shift: float, factor: int = 1, *, planes: int = 3,
          seed: int = 7) -> np.ndarray:
    """`_drawn`, a copy each time: drawing one is most of these tests' time,
    and the same film is asked for again and again."""
    return _drawn(float(shift), factor, planes, seed).copy()


@lru_cache(maxsize=64)
def _drawn(shift: float, factor: int, planes: int, seed: int) -> np.ndarray:
    """One film, moved ``shift`` prescan columns along +x, at ``factor``
    times the prescan's resolution: blobs of many sizes and two hard edges,
    drawn on a grid `SUB` times finer than the prescan and box-averaged
    down, as a pass integrates what lies under each of its pixels. Float, 0
    to 1 on one fixed scale whatever the shift, with the planes a little
    different from each other; a fourth is noise, as the infrared plane is
    to everything here.

    Small blobs, and many: a film of only large soft ones correlates at 136
    as floats and at 45 once quantised to a prescan's eight bits, under the
    floor, where real film scores 90 to 140 -- this one, 115."""
    rng = np.random.default_rng(seed)
    n = 900
    # Past both ends of the window, so a frame moved by the largest move
    # still has film where it came from.
    x0 = rng.uniform(-150, COLUMNS + 150, n)
    y0 = rng.uniform(-10, ROWS + 10, n)
    size = rng.uniform(0.5, 4.0, n)
    amount = rng.normal(0.0, 1.0, n)
    xs = (np.arange(COLUMNS * SUB) + 0.5) / SUB - shift
    ys = (np.arange(ROWS * SUB) + 0.5) / SUB
    across = np.exp(-((xs[None, :] - x0[:, None]) ** 2)
                    / (2 * size[:, None] ** 2))
    down = np.exp(-((ys[None, :] - y0[:, None]) ** 2)
                  / (2 * size[:, None] ** 2))
    fine = (down * amount[:, None]).T @ across
    fine += (1.5 * np.tanh((xs - 140.3) / 0.8)
             + np.tanh((xs - 301.7) / 1.5))[None, :]
    # On the fine grid, so every resolution averages the same film.
    fine = np.clip(0.5 + fine / 12.0, 0.0, 1.0)
    per = SUB // factor
    grid = fine.reshape(ROWS * factor, per, COLUMNS * factor, per).mean(
        axis=(1, 3))
    out = np.stack([grid * gain + lift for gain, lift in
                    ((0.8, 0.15), (0.7, 0.1), (0.6, 0.05))], axis=-1)
    if planes == 4:
        noise = np.random.default_rng(seed + 1).uniform(0, 1, grid.shape)
        out = np.concatenate([out, noise[..., None]], axis=-1)
    return out


def _prescan(shift: float) -> np.ndarray:
    return np.round(_film(shift) * 255).astype(np.uint8)


def _scan(shift: float, planes: int = 3) -> np.ndarray:
    return np.round(_film(shift, factor=2, planes=planes) * 60000
                    ).astype(np.uint16)


def _roll(tmp_path, frames):
    """A roll folder and a library holding ``frames``: ``(number, delivered
    shift in columns, target in units, planes, outcome)``. Each frame's walk
    prescan is the film where it lies, filed as the walk files one; its scan
    is at 600 dpi, ``shift`` columns along, filed as the roll files a frame,
    with the prescan just before it -- the film where the scan found it --
    and the hold record the driver keeps. A frame held to ``off`` names no
    reference, as such a record does; `approved.json` names it instead."""
    lib, roll = tmp_path / "library", tmp_path / "rolls" / "synthetic"
    roll.mkdir(parents=True)
    records, approved = [], []
    for number, shift, target, planes, outcome in frames:
        reference = library.save(
            _prescan(0.0),
            {"resolution_dpi": 300, "channels": 3,
             "channel_order": list("RGB"), "film": "negative",
             "roll_membership": {"roll": "synthetic", "number": number,
                                 "kind": "prescan", "folder": str(roll)}},
            root=lib, film=FilmNotes(frame=f"walk-{number:02d}"),
            tags=["roll", "prescan"])
        hold = {"target_mm": round(target * MM_PER_UNIT, 4),
                "outcome": outcome, "source": "operator"}
        if outcome != "off":
            hold.update({
                "moves": 1, "arrived_units": 0.0,
                "final_mm": round(_units(shift) * MM_PER_UNIT, 4),
                "moves_sent": [{"action": 1, "param": 3, "asked_units": 4.84}],
                "reference_entry": str(reference),
                "reference_shape": [ROWS, COLUMNS, 3]})
        entry = library.save(
            _scan(shift, planes),
            {"resolution_dpi": 600, "channels": planes,
             "channel_order": list("RGBI"[:planes]), "film": "negative",
             "registration": {"approved": hold},
             "roll_membership": {"roll": "synthetic", "number": number,
                                 "kind": "frame", "folder": str(roll)}},
            root=lib, film=FilmNotes(frame=f"synthetic-{number:02d}"),
            prescan=_prescan(shift), prescan_meta={"resolution_dpi": 300})
        records.append({"number": number, "index": number - 1,
                        "transport_position": number - 1, "error": None,
                        "done": True, "entry": str(entry),
                        "registration": {"approved": hold}})
        approved.append({"number": number,
                         "offset_mm": round(target * MM_PER_UNIT, 4),
                         "reference_entry": str(reference),
                         "source": "operator"})
    (roll / "roll.json").write_text(json.dumps({
        "roll": "synthetic", "numbering": "strip",
        "settings": {"prescan_resolution": 300, "film": "negative"},
        "frames": records}), encoding="utf-8")
    (roll / "approved.json").write_text(json.dumps({
        "roll": "synthetic", "numbering": "strip", "frames": approved}),
        encoding="utf-8")
    return roll, lib


@pytest.mark.parametrize("columns", [0.0, 0.25, 0.5, 3.3, -6.75])
def test_a_shift_is_measured_between_whole_columns(columns):
    """`measure_shift_mm` answers in whole columns, 0.81 units each; the
    fraction comes from the correlation surface between them, and a parabola
    through three of its columns pulled it toward the nearest whole one."""
    measured = hold_check.measure(_film(0.0), _film(columns))
    assert measured["believed"]
    assert measured["px_fine"] == pytest.approx(columns, abs=0.15)
    left, right = measured["halves"]
    assert left == pytest.approx(_units(columns), abs=0.25)
    assert right == pytest.approx(_units(columns), abs=0.25)


def test_a_frame_held_past_the_search_reach_is_found_where_the_hold_saw_it():
    """`measure_shift_mm` searches 105 columns either way. A frame held near
    the largest move sits about 110 out, and searched from zero its true
    match is outside the window -- a wrong one at a confidence of 5 to 8, on
    2026-10-03. Searched from where the hold last saw it, it is found; and
    with no reading at all, from the starts beyond."""
    reference, now = _film(0.0), _film(115.0)
    blind = hold_check._measured(reference, now, 0)
    assert not blind["believed"] or abs(blind["px_fine"] - 115.0) > 2,         "the reach this works around is not there"
    found = hold_check.measure(reference, now, about=112)
    assert found["believed"] and found["searched_from"] == 112
    assert found["px_fine"] == pytest.approx(115.0, abs=0.15)
    assert found["halves"] == pytest.approx([_units(115.0)] * 2, abs=0.25)
    swept = hold_check.measure(reference, now)
    assert swept["believed"]
    assert swept["px_fine"] == pytest.approx(115.0, abs=0.15)
    # And a wrong expectation costs nothing: from zero is still tried.
    near = hold_check.measure(reference, _film(4.0), about=-90)
    assert near["searched_from"] == 0
    assert near["px_fine"] == pytest.approx(4.0, abs=0.15)


def test_a_scan_at_twice_the_resolution_is_brought_onto_the_prescan():
    """Box-averaged two to one, a 600 dpi pass of the film is the 300 dpi
    prescan of it, to the rounding of the two."""
    small = hold_check.onto_grid(_scan(2.5, planes=4).astype(np.float64), 2.0,
                                 (ROWS, COLUMNS))
    assert small.shape == (ROWS, COLUMNS, 3), "the infrared plane went along"
    expected = _film(2.5)[..., :3] * 60000
    assert np.abs(small - expected).max() < 1.0


def test_where_each_frame_was_delivered_is_measured_and_drawn(tmp_path,
                                                              capsys):
    """Four held frames: one delivered where it was set, at a fraction of a
    column; one four units short of its target, on an RGBI pass; one left
    alone because holding was off, whose record names no reference, so the
    roll's `approved.json` has to; and one held past the 105 columns a match
    is searched. Each delivered position within half a unit of where the
    film really was, each error what it should be."""
    shifts = {1: 6.5, 2: -9.25, 3: 0.0, 4: 113.5}
    targets = {1: _units(6.5), 2: _units(-9.25) - 4.0, 3: 3.0,
               4: _units(113.5) - 1.0}
    roll, lib = _roll(tmp_path, [
        (1, shifts[1], targets[1], 3, "held"),
        (2, shifts[2], targets[2], 4, "not_converged"),
        (3, shifts[3], targets[3], 3, "off"),
        # Past the reach the hold searches, which its last look found.
        (4, shifts[4], targets[4], 3, "held")])
    out = tmp_path / "checked"
    assert hold_check.main([str(roll), "--library", str(lib),
                            "--out", str(out)]) == 0
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    rows = {row["frame"]: row for row in summary["frames"]}
    assert sorted(rows) == [1, 2, 3, 4]
    for number, row in rows.items():
        delivered = row["delivered"]
        assert delivered["believed"], number
        assert delivered["units"] == pytest.approx(_units(shifts[number]),
                                                   abs=0.5), number
        assert row["error_units"] == pytest.approx(
            _units(shifts[number]) - targets[number], abs=0.5), number
        # The scan lies exactly where its last prescan saw the film.
        assert row["against_prescan"]["units"] == pytest.approx(0.0, abs=0.2)
    assert rows[2]["error_units"] == pytest.approx(4.0, abs=0.5)
    assert rows[3]["reference_from"] == "approved.json", \
        "an `off` record names no reference; the roll's approved.json does"
    assert rows[3]["arrived_units"] is None and rows[3]["moves"] == 0

    printed = capsys.readouterr().out
    assert "outcome" in printed and "vs prescan" in printed
    assert (out / "summary.md").read_text(encoding="utf-8").count("\n|") >= 4
    assert (out / "summary.png").is_file()
    assert all((out / f"frame{n:02d}.png").is_file() for n in rows)


def test_each_picture_has_both_guides_in_both_panels(tmp_path):
    """The guides are what the eye judges a frame against: the aperture's
    two edges, fixed while the sheet's picture moves by its target. Read
    back from the PNG as written, in each panel, a whole frame apart."""
    from PIL import Image

    roll, lib = _roll(tmp_path, [(1, 3.0, -12.0, 3, "held")])
    out = tmp_path / "checked"
    rows = hold_check.check_roll(roll, lib, out, say=lambda _m: None)
    layout = rows[0]["layout"]
    picture = np.asarray(Image.open(rows[0]["png"]).convert("RGB"))
    for name in ("a", "b"):
        panel = layout[name]
        assert len(panel["guides"]) == 2, name
        left, right = panel["guides"]
        assert right - left == COLUMNS * hold_check.SCALE, name
        for x in (left, right):
            column = picture[panel["top"]:panel["bottom"], x - 1]
            amber = np.all(column == hold_check.GUIDE, axis=1).mean()
            assert amber > 0.4, f"panel {name}: no guide at {x} ({amber:.2f})"
    # And the sheet's picture is moved against them, 12 units -- 15 columns,
    # 30 pixels here -- to the left: past the left guide, and short of the
    # right one, where the delivered frame fills the aperture exactly.
    hatch = {tuple(c) for c in hold_check.HATCH}

    def striped(name, x):
        panel = layout[name]
        middle = (panel["top"] + panel["bottom"]) // 2
        return all(tuple(p) in hatch for p in picture[middle:middle + 12, x])

    a_left, a_right = layout["a"]["guides"]
    b_left, b_right = layout["b"]["guides"]
    assert not striped("a", a_left - 12), "not moved past the left guide"
    assert striped("a", a_right - 12), "not moved off the right guide"
    assert striped("b", b_left - 12) and not striped("b", b_left + 12)
    assert not striped("b", b_right - 12) and striped("b", b_right + 12)
    assert len(layout["zooms"]) == 2


def test_its_pictures_are_never_written_among_what_it_reads(tmp_path):
    """The roll and the library are read, never written: a PNG filed among a
    roll's frames is something the next reader takes for one of them."""
    roll, lib = _roll(tmp_path, [(1, 0.0, 0.0, 3, "held")])
    for inside in (roll / "checks", lib / "checks", roll):
        with pytest.raises(SystemExit) as refused:
            hold_check.main([str(roll), "--library", str(lib),
                             "--out", str(inside)])
        assert refused.value.code == 2
        assert not (inside / "summary.json").exists()


def test_a_roll_with_nothing_held_checks_nothing(tmp_path, capsys):
    """And says which frames it passed over, rather than nothing at all."""
    roll, lib = _roll(tmp_path, [(1, 0.0, 0.0, 3, "held")])
    manifest = json.loads((roll / "roll.json").read_text(encoding="utf-8"))
    manifest["frames"][0]["registration"] = {}
    (roll / "roll.json").write_text(json.dumps(manifest), encoding="utf-8")
    filed = Path(manifest["frames"][0]["entry"]) / "scan.json"
    record = json.loads(filed.read_text(encoding="utf-8"))
    record["registration"] = None
    filed.write_text(json.dumps(record), encoding="utf-8")
    assert hold_check.main([str(roll), "--library", str(lib),
                            "--out", str(tmp_path / "checked")]) == 1
    assert "not held, so not checked: frame(s) 1" in capsys.readouterr().out
