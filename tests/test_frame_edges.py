"""The frame-edge detector the window and the roll use (`tools/frame_edges`).

The detector itself is held to the study's answers on real film by
`test_frame_edges_parity.py`. These tests pin what was added around it: the
centring, the context each frame is read in, the film gate, the scales, the
window's lines, and the way `rps7200` is handed the detector without importing
it. The frames are synthetic negatives (`conftest.negative_prescan`): orange
base, a textured picture denser than base, a straight full-height edge --
what the members read, which a flat grey band is not.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import numpy as np
import pytest

from conftest import load_tool, negative_prescan
from rps7200 import framing, preview
from rps7200.framing import APERTURE_MM, FRAME_WIDTH_UNITS, StripWalk, units_per_column

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import frame_edges  # noqa: E402
from tools.frame_edges import centre, propose, vote  # noqa: E402
from tools.frame_edges.sides import (  # noqa: E402
    EDGE, NO_FILM, PICTURE_TO_BORDER, REFUSE, EdgeResult, Side,
)

#: Columns of base a centred frame shows on each side: negative, it overhangs.
T = (428 - centre.frame_columns(428)) / 2.0


# --- reading the edges ------------------------------------------------------

@pytest.mark.parametrize(("left", "right"), [(12.0, 0.0), (0.0, 9.5), (5.3, 0.0), (0.0, 3.2)])
def test_an_edge_is_read_where_it_was_drawn(left, right):
    res = frame_edges.detect(negative_prescan(left, right, seed=3), film="negative")
    for side, base in (("left", left), ("right", right)):
        s = res.side(side)
        if base:
            want = base if side == "left" else 428 - base
            assert s.state == EDGE and abs(s.x - want) < 0.2, (side, s)
        else:
            assert s.state == PICTURE_TO_BORDER, (side, s)


def test_a_gap_with_the_neighbour_beyond_it_is_read_with_its_far_side():
    res = frame_edges.detect(negative_prescan(30.0, outer_left=12.0, seed=5), film="negative")
    assert res.left.state == EDGE and abs(res.left.x - 30.0) < 0.3
    assert res.left.outer is not None and abs(res.left.outer - 12.0) < 1.0


def test_the_mirror_image_gives_the_mirrored_answer():
    img = negative_prescan(11.0, seed=7)
    a = frame_edges.detect(img, film="negative")
    b = frame_edges.detect(np.ascontiguousarray(img[:, ::-1]), film="negative")
    assert b.right.state == a.left.state == EDGE
    assert abs((428 - b.right.x) - a.left.x) < 1e-9
    assert b.left.state == a.right.state


def test_the_same_frame_reads_the_same_way_twice():
    img = negative_prescan(8.0, seed=9)
    assert frame_edges.detect(img, film="negative") == frame_edges.detect(img, film="negative")


# --- centring ------------------------------------------------------------------

def _edges(left=None, right=None, lstate=None, rstate=None) -> EdgeResult:
    def side(x, state):
        if state:
            return Side(state)
        return Side(EDGE, x=x) if x is not None else Side(PICTURE_TO_BORDER)
    return EdgeResult(side(left, lstate), side(right, rstate))


UPC = units_per_column(428)


@pytest.mark.parametrize(("result", "action", "units"), [
    # base at the left: the picture goes left, far enough to overhang by -T
    (_edges(left=12.0), "left", -(12.0 - T) * UPC),
    # base at the right: to the right
    (_edges(right=418.0), "right", (10.0 - T) * UPC),
    # no base either side: the frame covers the aperture, and that is centred
    (_edges(), "none", 0.0),
])
def test_the_move_centres_the_frame(result, action, units):
    dec = centre.decide(result, 428, centre.frame_columns(428))
    assert dec.action == action
    assert dec.units == pytest.approx(units, abs=1e-9)


def test_sides_that_disagree_and_the_empty_gate_refuse():
    """Base at both edges of one frame is impossible when the frame is wider
    than the aperture: the two sides ask for moves ~12 units apart."""
    fw = centre.frame_columns(428)
    assert centre.decide(_edges(left=4.0, right=424.5), 428, fw).action == "refuse"
    assert centre.decide(_edges(left=20.0, right=400.0), 428, fw).action == "refuse"
    assert centre.decide(_edges(lstate=NO_FILM), 428, fw).action == "refuse"


def test_the_frame_is_wider_than_the_aperture():
    """Measured from prescans of one frame: 435.6 columns against 428."""
    assert centre.frame_columns(428) == pytest.approx(435.6, abs=0.1)
    assert T < 0
    assert FRAME_WIDTH_UNITS == pytest.approx(350.6)


def test_a_proposal_is_on_the_scale_the_hold_loop_verifies_in():
    """Closing the loop: the offset asked for is what `measure_shift_mm` reads
    when the picture has moved that far -- same scale, same sign."""
    strip = negative_prescan(0.0, seed=11, width=640)
    k = 16                                   # the picture moves 16 columns left
    before, after = strip[:, 100:528], strip[:, 100 + k:528 + k]
    measured, _detail = framing.measure_shift_mm(before, after)
    assert measured == pytest.approx(centre.columns_to_mm(-k, 428), abs=0.02)


# --- the vote ---------------------------------------------------------------

def test_two_members_make_an_edge_and_a_tie_goes_to_it():
    sides = {"changepoint": Side(EDGE, x=10.0), "chroma": Side(EDGE, x=10.4),
             "stepline": Side(PICTURE_TO_BORDER), "gapmodel": Side(PICTURE_TO_BORDER)}
    v = vote.vote_v2(sides)
    assert v.state == EDGE and v.x == pytest.approx(10.2)


def test_one_member_alone_is_a_border_unless_it_saw_the_neighbour():
    alone = {"changepoint": Side(EDGE, x=30.0), "chroma": Side(PICTURE_TO_BORDER),
             "stepline": Side(PICTURE_TO_BORDER), "gapmodel": Side(PICTURE_TO_BORDER)}
    assert vote.vote_v2(alone).state == PICTURE_TO_BORDER
    gap = dict(alone, changepoint=Side(EDGE, x=30.0, outer=12.0))
    v = vote.vote_v2(gap)
    assert v.state == EDGE and v.note.startswith("gap with neighbour, one vote")


def test_a_gap_contradicted_elsewhere_does_not_stand():
    sides = {"changepoint": Side(EDGE, x=30.0, outer=12.0), "chroma": Side(EDGE, x=5.0),
             "stepline": Side(REFUSE), "gapmodel": Side(PICTURE_TO_BORDER)}
    assert vote.lone_gap(sides) is None


# --- a whole walk ----------------------------------------------------------

def _walk(bases=(12.0, 0.0, 7.0, 15.0)):
    return [(n, negative_prescan(b, seed=n)) for n, b in enumerate(bases, start=1)]


def test_every_frame_is_read_against_the_others_and_summarised_once(monkeypatch):
    frames = _walk()
    made, rolls = [], []
    real_summarise, real_detect = propose.summarise, vote.detect
    monkeypatch.setattr(propose, "summarise",
                        lambda im, dtype=None: made.append(1) or real_summarise(im, dtype))
    monkeypatch.setattr(vote, "detect",
                        lambda im, ctx: rolls.append(len(ctx["roll"])) or real_detect(im, ctx))
    propose.propose_centred(frames, film="negative")
    assert len(made) == len(frames), "one summary per frame, not one per pair"
    assert rolls == [len(frames) - 1] * len(frames), "never itself as its own context"


def test_a_walk_is_proposed_in_the_windows_words():
    offsets, notes = frame_edges.propose_centred(_walk(), film="negative")
    assert set(notes) == {1, 2, 3, 4}
    assert {n["source"] for n in notes.values()} <= {"measured", "unconfirmed",
                                                     "neighbours", "none"}
    assert offsets[1] < 0 and offsets[2] == 0.0      # base left; none
    assert notes[1]["edges"]["left"]["state"] == EDGE


def test_slides_and_kodachrome_are_not_read():
    for film in ("positive", "kodachrome"):
        offsets, notes = frame_edges.propose_centred(_walk(), film=film)
        assert offsets == {}
        assert all(n["source"] == "none" and "negatives only" in n["reason"]
                   for n in notes.values())
        assert frame_edges.walk_reader(film) is None


def test_black_and_white_is_read():
    offsets, _ = frame_edges.propose_centred(_walk(), film="bw")
    assert offsets


def test_a_600_dpi_prescan_is_read_at_the_prescan_scale_and_scaled_back():
    img = negative_prescan(12.0, seed=13)
    big = np.kron(img, np.ones((2, 2, 1), dtype=img.dtype))
    res = frame_edges.detect(big, film="negative")
    assert res.left.state == EDGE and abs(res.left.x - 24.0) < 0.5
    odd = frame_edges.detect(big[:, :700], film="negative")
    assert odd.left.state == REFUSE and "not a multiple" in odd.left.note


# --- the walk: rps7200 is handed the reader --------------------------------

def test_the_walk_reader_answers_through_strip_walk():
    reader = frame_edges.walk_reader("negative")
    walk = StripWalk(reader=reader)
    frames = _walk()
    for n, im in frames:
        walk.observe(n, im)
    mm, detail = walk.judge(1, frames[0][1])
    assert mm is not None and mm < 0
    assert walk.placed[1] == mm
    assert detail["source"] == "measured" and detail["members"] == []
    # the second look reads the same frame again, the same way
    assert reader.reread(1, frames[0][1]) == pytest.approx(mm)


def test_the_drivers_second_look_uses_the_reader():
    from rps7200.direct import DirectScanner

    class Reader:
        def reread(self, number, image):
            return 9.0

    walk = StripWalk(reader=Reader())
    look = DirectScanner._rejudge_for(object(), 0, walk, target_mm=0.3)
    ok, why = look(np.zeros((4, 4, 3)))
    assert not ok and "not where any of this predicted" in why


def test_without_a_reader_the_strip_detector_is_unchanged():
    walk = StripWalk()
    assert walk.reader is None
    assert walk.judge(1, negative_prescan(12.0))[0] is None     # no base level yet


def test_the_demo_and_the_session_pass_the_reader_on():
    """The demo is the real software: it builds its walk the driver's way."""
    from rps7200 import demo, direct, session

    assert "StripWalk(reader=edge_reader(film)" in inspect.getsource(demo.DemoScanner.scan_roll)
    assert "StripWalk(reader=edge_reader(film)" in inspect.getsource(
        direct.DirectScanner.scan_roll)
    assert "edge_reader=self.edge_reader" in inspect.getsource(session)
    gui = load_tool("gui")
    assert "session.edge_reader = frame_edges.walk_reader" in inspect.getsource(gui.main)


# --- the window's lines ------------------------------------------------------

@pytest.mark.parametrize("degrees", [0, 90, 180, 270])
@pytest.mark.parametrize("flipped", [False, True])
def test_an_edge_line_lands_on_the_column_it_was_read_at(degrees, flipped):
    """Checked against `preview.orient` itself, so the line and the picture
    cannot disagree about which way a turned cell faces."""
    axis_mark = load_tool("gui").axis_mark
    img = np.zeros((30, 50, 3), dtype=np.uint8)
    column = 12
    img[:, column] = 255
    turned = preview.orient(img, degrees, flipped)
    axis, f = axis_mark((column + 0.5) / 50, degrees, flipped)
    if axis == "x":
        hit = np.flatnonzero(turned[:, :, 0].max(axis=0))
        assert hit.size == 1 and int(f * turned.shape[1]) == hit[0]
    else:
        hit = np.flatnonzero(turned[:, :, 0].max(axis=1))
        assert hit.size == 1 and int(f * turned.shape[0]) == hit[0]


def test_the_offsets_are_in_the_hold_loops_millimetres():
    """`offset_mm` is columns * APERTURE_MM / width -- what the loop compares."""
    assert centre.columns_to_mm(10.0, 428) == pytest.approx(10.0 * APERTURE_MM / 428)


# --- the background reader (`watch.EdgeWatch`) -------------------------------

def test_the_background_reader_reaches_the_sheets_answer():
    """A walk read in the background ends where `propose_centred` does."""
    frames = _walk((12.0, 0.0, 7.0, 15.0, 9.0))
    watch = frame_edges.EdgeWatch()
    try:
        watch.load(frames, "negative")
        assert watch.wait(60)
        progress = watch.progress()
        assert progress.state == frame_edges.DONE
        assert (progress.done, progress.total) == (5, 5)
        assert (progress.offsets, progress.notes) == frame_edges.propose_centred(
            frames, film="negative")
    finally:
        watch.close()


def test_a_walk_read_as_it_arrives_is_read_again_against_all_of_it():
    """Each frame is read as it lands, against the frames before it; once the
    walk has ended it is read against every other frame, as the sheet was."""
    frames = _walk((12.0, 0.0, 7.0, 15.0))
    watch = frame_edges.EdgeWatch()
    read_against = []
    real = frame_edges.watch.read_frame

    def spy(image, *, film, roll, frame_units):
        read_against.append(len(roll))
        return real(image, film=film, roll=roll, frame_units=frame_units)

    frame_edges.watch.read_frame = spy
    try:
        watch.begin("negative", expected=6)
        for number, image in frames:
            watch.add(number, image)
            assert watch.wait(30)
            progress = watch.progress()
            assert progress.state == frame_edges.READING, "the walk is not over"
            assert (progress.done, progress.total) == (number, 6)
        assert read_against == [0, 1, 2, 3]
        watch.finish()
        assert watch.wait(30)
        # the last frame was already read against all the others
        assert read_against[4:] == [3, 3, 3]
        progress = watch.progress()
        assert progress.state == frame_edges.DONE
        assert (progress.offsets, progress.notes) == frame_edges.propose_centred(
            frames, film="negative")
    finally:
        frame_edges.watch.read_frame = real
        watch.close()


def test_a_new_walk_forgets_the_last_and_a_new_prescan_replaces_its_frame():
    watch = frame_edges.EdgeWatch()
    try:
        watch.load(_walk((12.0, 0.0, 7.0)), "negative")
        assert watch.wait(30)
        first = watch.generation
        watch.add(2, negative_prescan(14.0, seed=21))
        assert watch.wait(30)
        replaced = watch.progress()
        assert replaced.generation == first and replaced.done == 3
        assert replaced.notes[2]["edges"]["left"]["state"] == EDGE
        watch.begin("negative")
        progress = watch.progress()
        assert progress.generation == first + 1
        assert (progress.state, progress.done, progress.notes) == (frame_edges.IDLE, 0, {})
    finally:
        watch.close()


def test_a_walk_extended_keeps_its_frames_and_reads_them_against_the_new():
    """A second walk that adds to the sheet is the same walk, longer: nothing
    is forgotten, the generation is the sheet's still, and the answer at the
    end is `propose_centred`'s over all of it -- the old frames read again
    against the new ones beside them."""
    frames = _walk((12.0, 0.0, 7.0, 15.0, 9.0))
    watch = frame_edges.EdgeWatch()
    try:
        generation = watch.load(frames[:3], "negative")
        assert watch.wait(30)
        assert watch.extend(expected=5) == generation
        assert watch.progress().state == frame_edges.READING, "more coming"
        for number, image in frames[3:]:
            watch.add(number, image)
        watch.finish()
        assert watch.wait(60)
        progress = watch.progress()
        assert progress.generation == generation
        assert (progress.state, progress.done, progress.total) == (
            frame_edges.DONE, 5, 5)
        assert (progress.offsets, progress.notes) == frame_edges.propose_centred(
            frames, film="negative")
    finally:
        watch.close()


def test_slides_are_not_read_in_the_background_either():
    watch = frame_edges.EdgeWatch()
    try:
        watch.load(_walk(), "positive")
        progress = watch.progress()
        assert progress.state == frame_edges.SKIPPED and progress.offsets == {}
        assert all("negatives only" in n["reason"] for n in progress.notes.values())
    finally:
        watch.close()


def test_a_frame_the_detector_fails_on_is_red_and_the_rest_are_read(monkeypatch):
    frames = _walk((12.0, 0.0, 7.0, 15.0))
    broken = frames[1][1]
    real = frame_edges.watch.read_frame

    def fussy(image, **kw):
        if image is broken:
            raise ValueError("no such thing")
        return real(image, **kw)

    monkeypatch.setattr(frame_edges.watch, "read_frame", fussy)
    watch = frame_edges.EdgeWatch()
    try:
        watch.load(frames, "negative")
        assert watch.wait(30)
        progress = watch.progress()
        assert progress.state == frame_edges.FAILED and progress.done == 4
        assert progress.notes[2]["source"] == "none"
        assert "no such thing" in progress.errors[0]
        assert {1, 3, 4} <= set(progress.offsets)
    finally:
        watch.close()
