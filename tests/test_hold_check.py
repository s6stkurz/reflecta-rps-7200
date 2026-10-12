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
from rps7200 import library, tiff
from rps7200.framing import APERTURE_MM, reach_px
from rps7200.library import FilmNotes
from rps7200.protocol import MM_PER_UNIT

hold_check = load_tool("hold_check")

ROWS, COLUMNS = 287, 428            # a 300 dpi prescan, as the device sends it
SUB = 4                             # samples of the film per prescan column
#: A scan's width in the prescan's columns: the device returns about 431 of
#: them from 900 to 3600 dpi (`framing.SCAN_WINDOW_COLUMNS`), where its 300
#: dpi prescan comes back 428. A scan exactly twice the prescan wide hid
#: that the delivered panel was cut at 428.
SCAN_COLUMNS = 431


def _units(columns: float) -> float:
    """Prescan columns as units, on the hold's own scale."""
    return columns * APERTURE_MM / COLUMNS / MM_PER_UNIT


def _film(shift: float, factor: int = 1, *, planes: int = 3,
          seed: int = 7, columns: int = COLUMNS) -> np.ndarray:
    """`_drawn`, a copy each time: drawing one is most of these tests' time,
    and the same film is asked for again and again."""
    return _drawn(float(shift), factor, planes, seed, columns).copy()


@lru_cache(maxsize=64)
def _drawn(shift: float, factor: int, planes: int, seed: int,
           columns: int = COLUMNS) -> np.ndarray:
    """One film, moved ``shift`` prescan columns along +x, at ``factor``
    times the prescan's resolution and ``columns`` of the prescan's wide,
    from its first: blobs of many sizes and two hard edges, drawn on a grid
    `SUB` times finer than the prescan and box-averaged down, as a pass
    integrates what lies under each of its pixels. Float, 0 to 1 on one
    fixed scale whatever the shift, with the planes a little different from
    each other; a fourth is noise, as the infrared plane is to everything
    here.

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
    xs = (np.arange(columns * SUB) + 0.5) / SUB - shift
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
    grid = fine.reshape(ROWS * factor, per, columns * factor, per).mean(
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
    """The film at 600 dpi, as wide as a scan comes back (`SCAN_COLUMNS`),
    from the prescan's first column, which is where `onto_grid` reads a
    scan's first column from."""
    return np.round(_film(shift, factor=2, planes=planes,
                          columns=SCAN_COLUMNS) * 60000).astype(np.uint16)


def _walk_entry(lib, roll, number, shift):
    """A walk's prescan of the film ``shift`` columns along, filed as a walk
    files one."""
    return library.save(
        _prescan(shift),
        {"resolution_dpi": 300, "channels": 3,
         "channel_order": list("RGB"), "film": "negative",
         "roll_membership": {"roll": "synthetic", "number": number,
                             "kind": "prescan", "folder": str(roll)}},
        root=lib, film=FilmNotes(frame=f"walk-{number:02d}"),
        tags=["roll", "prescan"])


def _roll(tmp_path, frames, *, named=True, positions=True, walk=None):
    """A roll folder and a library holding ``frames``: ``(number, delivered
    shift in columns, target in units, planes, outcome)``. Each frame's walk
    prescan is the film where it lies, filed as the walk files one; its scan
    is at 600 dpi, ``shift`` columns along, filed as the roll files a frame,
    with the prescan just before it -- the film where the scan found it --
    and the hold record the driver keeps. A frame held to ``off`` names no
    reference, as such a record does; `approved.json` names it instead.

    ``named`` False is a roll from before holds named their reference, and
    ``positions`` False one with no `approved.json`. ``walk`` writes the
    folder a walk of its own, `survey.json` and a filed prescan a frame:
    the film ``walk`` columns from where the holds' reference saw it -- 0
    the picture they were held against, anything else another pass."""
    lib, roll = tmp_path / "library", tmp_path / "rolls" / "synthetic"
    roll.mkdir(parents=True)
    records, approved, walked = [], [], []
    for number, shift, target, planes, outcome in frames:
        reference = _walk_entry(lib, roll, number, 0.0)
        if walk is not None:
            own = _walk_entry(lib, roll, number, walk)
            tiff.write(str(roll / f"prescan{number:02d}.tif"), _prescan(walk))
            walked.append({"number": number, "transport_position": number - 1,
                           "prescan": f"prescan{number:02d}.tif",
                           "prescan_entry": own.name})
        hold = {"target_mm": round(target * MM_PER_UNIT, 4),
                "outcome": outcome, "source": "operator"}
        if outcome != "off":
            hold.update({
                "moves": 1, "arrived_units": 0.0,
                "final_mm": round(_units(shift) * MM_PER_UNIT, 4),
                "moves_sent": [{"action": 1, "param": 3, "asked_units": 4.84}],
                "reference_shape": [ROWS, COLUMNS, 3]})
            if named:
                hold["reference_entry"] = str(reference)
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
    if positions:
        (roll / "approved.json").write_text(json.dumps({
            "roll": "synthetic", "numbering": "strip", "frames": approved}),
            encoding="utf-8")
    if walk is not None:
        (roll / "survey.json").write_text(json.dumps({
            "roll": "synthetic", "numbering": "strip", "dry_run": True,
            "settings": {"prescan_resolution": 300, "film": "negative"},
            "frames": walked}), encoding="utf-8")
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
    """`measure_shift_mm` searches `reach_px` columns either way -- 129 since
    `SEARCH_MM` went to 11.0, 105 before, when frames held near the largest
    move sat outside it and came back as wrong matches at a confidence of 5 to
    8 (2026-10-03). A frame further out still has its true match outside the
    window searched from zero. Searched from where the hold last saw it, it is
    found; and with no reading at all, from the starts beyond."""
    far = float(reach_px(COLUMNS) + 8)
    reference, now = _film(0.0), _film(far)
    blind = hold_check._measured(reference, now, 0)
    assert not blind["believed"] or abs(blind["px_fine"] - far) > 2, \
        "the reach this works around is not there"
    found = hold_check.measure(reference, now, about=int(far) - 3)
    assert found["believed"] and found["searched_from"] == int(far) - 3
    assert found["px_fine"] == pytest.approx(far, abs=0.15)
    assert found["halves"] == pytest.approx([_units(far)] * 2, abs=0.25)
    swept = hold_check.measure(reference, now)
    assert swept["believed"]
    assert swept["px_fine"] == pytest.approx(far, abs=0.15)
    # And a wrong expectation costs nothing: from zero is still tried.
    near = hold_check.measure(reference, _film(4.0), about=-90)
    assert near["searched_from"] == 0
    assert near["px_fine"] == pytest.approx(4.0, abs=0.15)


def test_a_scan_at_twice_the_resolution_is_brought_onto_the_prescan():
    """Box-averaged two to one, a 600 dpi pass of the film is the 300 dpi
    prescan of it, to the rounding of the two -- on the prescan's columns
    for measuring, and on all of the scan's for the eye."""
    scan = _scan(2.5, planes=4).astype(np.float64)
    small = hold_check.onto_grid(scan, 2.0, (ROWS, COLUMNS))
    assert small.shape == (ROWS, COLUMNS, 3), "the infrared plane went along"
    expected = _film(2.5)[..., :3] * 60000
    assert np.abs(small - expected).max() < 1.0
    whole = hold_check.onto_grid(scan, 2.0, (ROWS, None))
    assert whole.shape == (ROWS, SCAN_COLUMNS, 3)
    assert np.allclose(whole[:, :COLUMNS], small, atol=1e-3)
    wide = _film(2.5, columns=SCAN_COLUMNS)[..., :3] * 60000
    assert np.abs(whole - wide).max() < 1.0


def test_where_each_frame_was_delivered_is_measured_and_drawn(tmp_path,
                                                              capsys):
    """Four held frames: one delivered where it was set, at a fraction of a
    column; one four units short of its target, on an RGBI pass; one left
    alone because holding was off, whose record names no reference, so the
    roll's `approved.json` has to; and one held past the columns a match is
    searched (`reach_px`). Each delivered position within half a unit of
    where the film really was, each error what it should be."""
    beyond = reach_px(COLUMNS) + 7.5
    shifts = {1: 6.5, 2: -9.25, 3: 0.0, 4: beyond}
    targets = {1: _units(6.5), 2: _units(-9.25) - 4.0, 3: 3.0,
               4: _units(beyond) - 1.0}
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
    # Known three ways: the holds that moved named theirs, and the one left
    # alone took no look to check a guess by -- but approved.json names the
    # very references the other three recorded, so it is this roll's.
    assert {n: row["reference_check"]["state"] for n, row in rows.items()} == {
        1: "recorded", 2: "recorded", 3: "confirmed", 4: "recorded"}
    assert all(row["believed"] for row in rows.values())

    printed = capsys.readouterr().out
    assert "outcome" in printed and "vs prescan" in printed
    assert (out / "summary.md").read_text(encoding="utf-8").count("\n|") >= 4
    assert (out / "summary.png").is_file()
    assert all((out / f"frame{n:02d}.png").is_file() for n in rows)


def test_each_picture_has_both_guides_in_both_panels(tmp_path):
    """The guides are what the eye judges a frame against: where the scan
    cuts (`framing.scan_window`), fixed while the sheet's picture moves by its
    target -- the same place the frame-position window draws them. Read back
    from the PNG as written, in each panel, the scan's window apart."""
    from PIL import Image

    from rps7200.framing import scan_window

    roll, lib = _roll(tmp_path, [(1, 3.0, -12.0, 3, "held")])
    out = tmp_path / "checked"
    rows = hold_check.check_roll(roll, lib, out, say=lambda _m: None)
    layout = rows[0]["layout"]
    picture = np.asarray(Image.open(rows[0]["png"]).convert("RGB"))
    cut_left, cut_right = scan_window(COLUMNS)
    for name in ("a", "b"):
        panel = layout[name]
        assert len(panel["guides"]) == 2, name
        left, right = panel["guides"]
        assert abs((right - left) - (cut_right - cut_left)
                   * hold_check.SCALE) <= 1, name
        for x in (left, right):
            column = picture[panel["top"]:panel["bottom"], x - 1]
            amber = np.all(column == hold_check.GUIDE, axis=1).mean()
            assert amber > 0.4, f"panel {name}: no guide at {x} ({amber:.2f})"
    # And the sheet's picture is moved against them, 12 units -- 15 columns,
    # 30 pixels here -- to the left: past the left guide, and short of the
    # right one, where the delivered frame fills the scan's window exactly.
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
    # Right up to the right guide: the scan covers about 431 of the
    # prescan's columns, and the panel drew 428 of them -- hatch, which says
    # "no picture", 2.5 columns inside the guide, where a sliver of base on
    # a frame a couple of units off would show.
    assert not striped("b", b_right - 2), "the scan stops short of the guide"
    assert striped("b", b_right + 3)

    # And in the zooms, (c): a band of the delivered frame reaches each
    # guide from inside, both edges.
    assert len(layout["zooms"]) == 2
    guide = hold_check.ZOOM_COLUMNS // 2 * hold_check.ZOOM       # in a zoom
    band = hold_check.BAND_ROWS * hold_check.ZOOM

    def zoomed(side, dx):
        zoom = layout["zooms"][side]
        y = zoom["top"] + band + band // 2              # the delivered band
        x = zoom["left"] + 14 + guide + dx              # past the band marks
        return all(tuple(p) in hatch for p in picture[y:y + 12, x])

    assert not zoomed(0, +1) and zoomed(0, -8), "left edge"
    assert not zoomed(1, -2) and zoomed(1, +6), "right edge"


#: Two held frames -- one held 6.5 columns along to a target a unit short of
#: it, one 3 columns back to where it was asked -- and one left alone, which
#: took no look to check a guess by.
_HELD = [(1, 6.5, _units(6.5) - 1.0, 3, "held"),
         (2, -3.0, _units(-3.0), 3, "not_converged"),
         (3, 0.0, 3.0, 3, "off")]


def test_a_guessed_reference_that_contradicts_the_hold_is_unknown(
        tmp_path, capsys):
    """`rolls/2026-09-22`: its holds name no reference, it has no
    approved.json, and its own folder's walk is not the one it was held
    against -- `rolls/aligned-strip`'s was. Guessed from the folder's walk,
    every frame came out delivered 44 units off, believed, where the holds
    had left each within 2. The frame's own last prescan, measured against
    the guess, says so: it does not land where the hold's last look did. And
    the frame left alone, with no look of its own, is belied by the others."""
    roll, lib = _roll(tmp_path, _HELD, named=False, positions=False,
                      walk=-54.0)
    out = tmp_path / "checked"
    assert hold_check.main([str(roll), "--library", str(lib),
                            "--out", str(out)]) == 1, "nothing was checked"
    printed = capsys.readouterr().out
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    rows = {row["frame"]: row for row in summary["frames"]}
    assert sorted(rows) == [1, 2, 3]
    for number, row in rows.items():
        assert row["reference_check"]["state"] == "unknown", number
        assert not row["believed"]
        assert row["delivered"]["units"] is None and row["error_units"] is None
        assert not (out / f"frame{number:02d}.png").exists(), \
            "drawn against a picture its position was not set on"
        assert f"frame {number}: " in printed and "reference unknown" in printed
    for number in (1, 2):
        check = rows[number]["reference_check"]
        assert check["last_look_units"] == pytest.approx(
            rows[number]["last_look_units"])
        assert abs(check["measured_units"] - check["last_look_units"]) \
            == pytest.approx(_units(54.0), abs=0.5)
    assert ("the walk contradicts the last look of each of the 2 other "
            "hold(s) it was checked against") in rows[3]["reference_check"][
        "said"]
    # No delivered position in the table, and the reason in its last column.
    table = (out / "summary.md").read_text(encoding="utf-8")
    lines = [line for line in table.splitlines() if line.startswith("| 1 ")]
    assert lines and "| -- | -- |" in lines[0] and lines[0].endswith(
        "| unknown |")
    assert "0 held frame(s) checked, 3 more whose reference is unknown" \
        in printed


def test_a_reference_in_the_trash_is_said_with_what_brings_it_back(
        tmp_path, capsys):
    """The window's Delete moves an entry into the library's trash. The
    reference a hold recorded, gone there, left the frame judged on a guess
    with nothing to say the known picture was one `restore` away."""
    roll, lib = _roll(tmp_path, _HELD)
    record = json.loads((roll / "roll.json").read_text(encoding="utf-8"))
    named = Path(record["frames"][0]["registration"]["approved"][
        "reference_entry"])
    moved = library.trash(lib, [lib / named.name], by="a test")
    hold_check.main([str(roll), "--library", str(lib),
                     "--out", str(tmp_path / "checked")])
    printed = capsys.readouterr().out
    assert (f"frame 1: the reference its hold recorded, {named.name}, is in "
            "the library's trash since ") in printed
    assert f"`tools/library.py restore {named.name}` brings it back" in printed
    assert moved.batch.exists()


def test_a_guessed_reference_that_gives_the_hold_back_is_believed(tmp_path):
    """The same roll, its own walk the one it was held against: the frame's
    last prescan measured against it lands where the hold's last look did,
    and the delivered position against it is believed -- and the frame
    left alone, with no look of its own, is borne out by the others."""
    roll, lib = _roll(tmp_path, _HELD, named=False, positions=False, walk=0.0)
    out = tmp_path / "checked"
    assert hold_check.main([str(roll), "--library", str(lib),
                            "--out", str(out)]) == 0
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    rows = {row["frame"]: row for row in summary["frames"]}
    for number, shift in ((1, 6.5), (2, -3.0), (3, 0.0)):
        row = rows[number]
        assert row["reference_check"]["state"] == "confirmed", number
        assert row["reference_from"] == "the walk's prescan entry"
        assert row["believed"]
        assert row["delivered"]["units"] == pytest.approx(_units(shift),
                                                          abs=0.5)
        assert (out / f"frame{number:02d}.png").is_file()
    assert ("the walk reproduces the last look of each of the 2 other "
            "hold(s) it was checked against, and contradicts none") in rows[3][
        "reference_check"]["said"]


def test_a_frame_left_alone_is_known_by_what_the_roll_says_of_its_file(
        tmp_path):
    """A hold switched off takes no look, so a guess cannot be checked
    against one; only the roll can speak for the file it came from, by the
    references its other holds recorded. Where the file names others than
    they did, it is another run's, and the frame's reference is unknown;
    where it names none of theirs, nothing speaks either way, and its guess
    is used, said to be unconfirmed, and not believed."""
    roll, lib = _roll(tmp_path, [(1, 2.0, _units(2.0), 3, "held"),
                                 (2, 4.0, 3.0, 3, "off")])
    approved = json.loads((roll / "approved.json").read_text(encoding="utf-8"))
    approved["frames"][0]["reference_entry"] = str(
        _walk_entry(lib, roll, 1, 0.0))                 # another entry
    (roll / "approved.json").write_text(json.dumps(approved), encoding="utf-8")
    rows = {row["frame"]: row for row in hold_check.check_roll(
        roll, lib, tmp_path / "checked", say=lambda _m: None)}
    assert rows[1]["reference_check"]["state"] == "recorded"
    assert rows[2]["reference_check"]["state"] == "unknown"
    assert ("approved.json names other references than the roll's 1 other "
            "hold(s) recorded") in rows[2]["reference_check"]["said"]

    del approved["frames"][0]                     # frame 2's position only
    (roll / "approved.json").write_text(json.dumps(approved), encoding="utf-8")
    rows = {row["frame"]: row for row in hold_check.check_roll(
        roll, lib, tmp_path / "checked-again", say=lambda _m: None)}
    assert rows[2]["reference_check"]["state"] == "unconfirmed"
    assert rows[2]["delivered"]["believed"], "the match itself is fine"
    assert not rows[2]["believed"]
    cells = dict(zip(hold_check.COLUMNS, hold_check.table_rows(
        [rows[2]])[0]))
    assert cells["delivered"].startswith("(") and cells["reference"] == \
        "unconfirmed"


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


def test_a_strips_frames_are_found_by_its_roll_and_strip(tmp_path):
    """strip-02 of two rolls of strips are two folders: keyed by name alone
    they met, and one roll's frame 2 was laid beside the other's hold. A
    roll from before strips is keyed by its name, as before."""
    from rps7200 import session

    rolls = tmp_path / "rolls"
    for name in ("Gold200", "Fuji"):
        strip = rolls / name / "strip-02"
        strip.mkdir(parents=True)
        session.write_manifest(rolls / name / library.STRIPS_INDEX, {
            "kind": session.STRIPS_KIND, "roll": name,
            "strips": [{"strip": 2, "folder": "strip-02", "first": 7}]})
    legacy = rolls / "2026-10-08-224648"
    legacy.mkdir()
    (legacy / "roll.json").write_text("{}", encoding="utf-8")
    root = tmp_path / "library"
    for entry, member in (
            ("a", {"roll": "Gold200", "number": 2, "kind": "frame",
                   "folder": str(rolls / "Gold200" / "strip-02"),
                   "strip": 2, "roll_number": 8}),
            ("b", {"roll": "Fuji", "number": 2, "kind": "frame",
                   "folder": str(rolls / "Fuji" / "strip-02"),
                   "strip": 2, "roll_number": 8}),
            ("c", {"roll": "2026-10-08-224648", "number": 2, "kind": "frame",
                   "folder": str(legacy)})):
        (root / entry).mkdir(parents=True)
        (root / entry / "scan.json").write_text(json.dumps(
            {"extra": {"roll_membership": member}}), encoding="utf-8")
    index = hold_check.frame_index(root)
    assert index["folders"]["gold200/strip-02"][2].name == "a"
    assert index["folders"]["fuji/strip-02"][2].name == "b"
    assert index["folders"]["2026-10-08-224648"][2].name == "c"
    assert hold_check.folder_key(rolls / "Fuji" / "strip-02") \
        == "fuji/strip-02"
    assert hold_check.folder_key(legacy) == "2026-10-08-224648"
