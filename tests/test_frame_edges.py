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
import warnings
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


def test_a_side_with_no_finite_edge_moves_nothing():
    """Carried through, a NaN edge came out of the sheet's snap as the
    largest forward move the window allows."""
    fw = centre.frame_columns(428)
    dec = centre.decide(_edges(left=float("nan")), 428, fw)
    assert dec.action == "refuse" and dec.units is None


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


#: (base, the neighbour's picture beyond it) of a walk whose frames show the
#: whole gap: the first measures it (18 columns), and the third, fifth and
#: sixth are read otherwise without that -- a gap 58 and 52 columns in that
#: only a measured gap vouches for, and one 30 wide that only the prior
#: allows. `_walk`'s frames measure none, so a reading made again from them
#: never fed gapmodel a gap, nor a lost one a different answer.
GAPPED = ((30.0, 12.0), (12.0, None), (75.0, 58.0), (25.0, 8.0), (40.0, 10.0),
          (70.0, 52.0), (9.0, None))


def _gapped_walk():
    return [(n, negative_prescan(b, seed=n, outer_left=o))
            for n, (b, o) in enumerate(GAPPED, start=1)]


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


@pytest.mark.parametrize(("left", "right"), [(12.0, 0.0), (0.0, 9.5)])
def test_a_pass_twice_the_width_is_moved_as_the_prescan_is(left, right):
    """FE-03: the positions came back scaled up and were then decided as
    428-column ones: at 856 columns a left base moved about 1.8x too far and
    a right one was refused. The demo reaches it at 600 dpi (FE-A1)."""
    img = negative_prescan(left, right, seed=13)
    big = np.kron(img, np.ones((2, 2, 1), dtype=img.dtype))
    mm, note = propose.read_frame(img, film="negative")
    mm2, note2 = propose.read_frame(big, film="negative")
    assert mm is not None and mm2 is not None, (note, note2)
    assert note2["units"] == pytest.approx(note["units"], abs=0.3)
    from rps7200.protocol import units
    assert units(mm2) == pytest.approx(units(mm), abs=0.3)


@pytest.mark.parametrize("width", [860, 862, 1292])
def test_the_devices_own_600_and_900_dpi_widths_are_refused(width):
    """The kron test above uses 856 columns, a width the device never
    returns. What it does return at 600 and 900 dpi is refused, frame by
    frame -- which is why a walk at them is warned about before it starts."""
    img = negative_prescan(12.0, seed=13, width=width)
    res = frame_edges.detect(img, film="negative")
    assert res.left.state == res.right.state == REFUSE
    assert "not a multiple" in res.left.note


def test_a_walk_the_edges_will_not_be_read_on_is_said_before_it_starts():
    assert frame_edges.READ_AT_DPI == (300,)
    assert frame_edges.unread_at(300, "negative") is None
    for dpi, film in ((600, "negative"), (900, "bw"), (150, None)):
        why = frame_edges.unread_at(dpi, film)
        assert why and f"{dpi} dpi" in why and "refused" in why, (dpi, film)
    # slides are not read at any resolution, which is another sentence
    assert frame_edges.unread_at(600, "positive") is None
    assert frame_edges.unread_at(None, "negative") is None


def test_the_widths_the_reader_names_are_the_devices_table():
    """The sentence above names the device's widths at 600 and 900 dpi, typed
    into `propose`, which is not edited without the parity run. So the copy
    is held to `framing.DEVICE_PASS_SHAPES` here instead: a table re-measured
    without the sentence following fails this, not a reader of the log."""
    why = frame_edges.unread_at(600, "negative")
    for dpi in (600, 900):
        width = framing.DEVICE_PASS_SHAPES[dpi][1]
        assert f"{width} at {dpi}" in why, (dpi, why)


# --- the walk: rps7200 is handed the reader --------------------------------

def test_the_walk_reader_answers_through_strip_walk():
    reader = frame_edges.walk_reader("negative")
    walk = StripWalk(reader=reader)
    frames = _walk()
    observed = [walk.observe(n, im) for n, im in frames]
    mm, detail = walk.judge(1, frames[0][1])
    assert mm is not None and mm < 0
    assert walk.placed[1] == mm
    assert detail["source"] == "measured"
    # Every member's own answer, where the walk used to keep an empty list.
    assert set(detail["members"]) == set(vote.MEMBERS)
    assert detail["member_columns"] == 428
    # Read against the others, named by index, and their pictures digested.
    assert detail["against_index"] == [2, 3, 4]
    assert detail["against_sha1"] == frame_edges.context_digest(
        [(n, frame_edges.image_digest(im)) for n, im in frames[1:]])
    assert detail["image_sha1"] == frame_edges.image_digest(frames[0][1])
    # What each frame told the walk, kept to be read against again.
    for (n, im), record in zip(frames, observed):
        assert record["reader"] == "frame_edges" and record["index"] == n
        assert record["image_sha1"] == frame_edges.image_digest(im)
        assert record["summary"] == frame_edges.summary_record(
            frame_edges.summary(im))
    # the second look reads the same frame again, the same way
    assert reader.reread(1, frames[0][1]) == pytest.approx(mm)
    again, note = reader.reread_note(1, frames[0][1])
    assert again == pytest.approx(mm) and note["members"] == detail["members"]


# --- what a stored reading keeps, to be made again (P09-b, FE-05) -------------

def _debugged(members) -> EdgeResult:
    """Two edges the vote agreed on, with ``members`` as `vote.detect`'s debug."""
    return EdgeResult(Side(EDGE, x=20.0), Side(EDGE, x=428 - 20.0),
                      {"members": members})


def test_a_note_keeps_every_members_own_answer():
    """The note kept the vote's two sides; the four answers behind them lived
    in a debug dict nothing stored. A position at 0.0 stays 0.0 -- the column
    an edge sits on at the border -- and one that placed no edge is None."""
    members = {
        "changepoint": {"left": [EDGE, 0.0], "right": [EDGE, np.float64(408.12345)]},
        "chroma": {"left": [PICTURE_TO_BORDER, None], "right": [REFUSE, None],
                   "failed": "chroma failed: ZeroDivisionError: x"},
        "stepline": {"left": [EDGE, 19.9], "right": [EDGE, 408.0],
                     "not_a_negative": "the gap is darker than the picture"},
        "gapmodel": {"left": [EDGE, 20.1], "right": [EDGE, 408.2]},
    }
    mm, note = propose.centring(_debugged(members), 428)
    assert note["member_columns"] == 428
    kept = note["members"]
    assert kept["changepoint"] == {"left": [EDGE, 0.0], "right": [EDGE, 408.123]}
    assert kept["chroma"]["left"] == [PICTURE_TO_BORDER, None]
    assert kept["chroma"]["failed"].startswith("chroma failed")
    assert kept["stepline"]["not_a_negative"] == "the gap is darker than the picture"
    assert "failed" not in kept["gapmodel"]
    assert "record_error" not in note


def test_a_members_record_that_cannot_be_kept_costs_a_field_not_the_reading():
    plain = {"changepoint": {"left": [EDGE, 20.0], "right": [EDGE, 408.0]}}
    odd = {"changepoint": {"left": [EDGE, object()], "right": [EDGE, 408.0]}}
    mm, note = propose.centring(_debugged(plain), 428)
    mm_odd, note_odd = propose.centring(_debugged(odd), 428)
    assert mm_odd == mm and note_odd["source"] == note["source"]
    assert "members" not in note_odd
    assert note_odd["record_error"].startswith("members: TypeError")


def test_a_pictures_digest_is_its_pixels_whichever_way_they_lie():
    """A pass read bottom-up is turned upright as a view, which sha1 refuses
    outright ('not C-contiguous'); the digest is the pixels', not the view's."""
    img = negative_prescan(12.0, seed=3)
    upright = img[::-1]
    assert not upright.flags.c_contiguous
    assert frame_edges.image_digest(upright) == frame_edges.image_digest(
        np.ascontiguousarray(upright))
    assert frame_edges.image_digest(upright) != frame_edges.image_digest(img)
    # the dtype and shape count, not only the bytes
    assert frame_edges.image_digest(img.view(np.int8)) != frame_edges.image_digest(img)
    assert frame_edges.image_digest(object()) is None


def test_a_summary_comes_back_from_its_record_exactly():
    """Through JSON, as a walk's record keeps it: every number, the base
    level's dtype included, so a reading made against it is the same."""
    import json

    gapped = {n for n, (_base, outer) in enumerate(GAPPED, start=1)
              if outer is not None}
    for kind, n, im in ([("plain", n, im) for n, im in _walk()]
                        + [("gapped", n, im) for n, im in _gapped_walk()]):
        s = frame_edges.summary(im)
        if kind == "gapped" and n in gapped:
            # A frame showing a whole gap measures it, so the widths that
            # come back are numbers and not an empty tuple's.
            assert s.gap_widths, (kind, n)
        back = frame_edges.summary_from(json.loads(json.dumps(
            frame_edges.summary_record(s))))
        assert back.dtype == s.dtype
        assert back.gap_widths == s.gap_widths
        assert back.gap_colours == s.gap_colours
        if s.chroma_level is None:
            assert back.chroma_level is None
        else:
            assert back.chroma_level.dtype == s.chroma_level.dtype == np.float64
            assert np.array_equal(back.chroma_level, s.chroma_level)


@pytest.mark.parametrize("gapped", [False, True], ids=["no-gap", "gapped"])
def test_a_walks_reading_is_made_again_from_its_record_alone(gapped):
    """What FE-05 asked for: the walk's records -- each frame's summary as
    observed, the indexes a judgment was read against and their digest --
    give back the same members and the same move from the frame's picture,
    with no other prescan at hand.

    On a walk whose frames show whole gaps too, so the records carry the
    widths gapmodel takes the roll's gap from: there, readings rest on
    them, which the end checks, and a width lost on its way through the
    record gives another answer."""
    import dataclasses
    import json

    frames = _gapped_walk() if gapped else _walk((12.0, 0.0, 7.0, 15.0, 9.0))
    walk = StripWalk(reader=frame_edges.walk_reader("negative"))
    records, judged = {}, {}
    for n, im in frames:
        records[n] = json.loads(json.dumps(walk.observe(n, im)))
        mm, note = walk.judge(n, im)
        judged[n] = (mm, json.loads(json.dumps(note, default=float)))
    leaning = []
    for n, im in frames:
        mm, note = judged[n]
        context = note["against_index"]
        assert context == [k for k in records if k < n], "what had been seen"
        assert note["against_sha1"] == frame_edges.context_digest(
            [(k, records[k]["image_sha1"]) for k in context])
        assert note["image_sha1"] == frame_edges.image_digest(im)
        roll = [frame_edges.summary_from(records[k]["summary"]) for k in context]
        mm_again, again = frame_edges.read_frame(im, film="negative", roll=roll)
        if note["source"] == "unconfirmed":      # `judge` does not move on one
            mm_again = None
        assert mm_again == mm and again["members"] == note["members"], n
        # The same frame read against the same summaries with their gaps
        # taken out: where that is another answer, the widths kept decided.
        bare = [dataclasses.replace(s, gap_widths=()) for s in roll]
        _mm, without = frame_edges.read_frame(im, film="negative", roll=bare)
        if without["members"] != note["members"]:
            leaning.append(n)
    if gapped:
        assert any(records[k]["summary"]["gap_widths"] for k in records)
        assert leaning == [3, 5, 6], "readings that rest on the gap measured"
    else:
        assert leaning == [], "no frame here measures a gap"


def test_a_reading_names_the_frames_it_was_read_against():
    frames = _walk((12.0, 0.0, 7.0, 15.0))
    _, notes = frame_edges.propose_centred(frames, film="negative")
    digests = {n: frame_edges.image_digest(im) for n, im in frames}
    for n in notes:
        others = sorted(k for k in digests if k != n)
        assert notes[n]["against"] == others
        assert notes[n]["against_sha1"] == frame_edges.context_digest(
            [(k, digests[k]) for k in others])
        assert notes[n]["image_sha1"] == digests[n]


def test_bookkeeping_that_fails_in_a_walk_costs_a_field_not_the_frame(monkeypatch):
    """A walk's record is bookkeeping: what it cannot keep is said, and the
    reading -- and the roll -- go on."""
    def broken(s):
        raise TypeError("cannot keep this")

    monkeypatch.setattr(propose, "summary_record", broken)
    frames = _walk()
    walk = StripWalk(reader=frame_edges.walk_reader("negative"))
    for n, im in frames:
        record = walk.observe(n, im)
        assert record["record_error"] == "observe: TypeError: cannot keep this"
        assert record["reader"] == "frame_edges" and "summary" not in record
        assert record["image_sha1"] == frame_edges.image_digest(im)
    # The context is still named: the digests were kept before the summary.
    mm, note = walk.judge(1, frames[0][1])
    assert mm is not None and note["against_index"] == [2, 3, 4]
    # And a context that cannot be named costs that field alone.
    monkeypatch.setattr(propose, "context_digest", lambda pairs: 1 / 0)
    mm_again, note = walk.judge(1, frames[0][1])
    assert mm_again == mm
    assert note["record_error"].startswith("against_index: ZeroDivisionError")


def test_a_row_reversed_prescan_goes_through_a_walk():
    """The view a bottom-up pass is turned upright as, which sha1 alone
    refuses: observed, judged and read again without a word."""
    frames = [(n, im[::-1]) for n, im in _walk()]
    walk = StripWalk(reader=frame_edges.walk_reader("negative"))
    for n, im in frames:
        assert walk.observe(n, im)["image_sha1"] is not None
    mm, note = walk.judge(1, frames[0][1])
    assert "record_error" not in note and note["image_sha1"] is not None
    assert walk.reader.reread_note(1, frames[0][1])[1]["image_sha1"] == note["image_sha1"]


def test_a_walk_never_moves_film_on_one_members_vote(monkeypatch):
    """`lone_gap` lets one member's gap-with-a-neighbour through, labelled
    `unconfirmed` for the sheet, where a person decides. In a walk that aims
    there is no person, and the driver moved film on it anyway -- the case
    `combine` exists to refuse. It is told nothing to do now, and why."""
    from tools.frame_edges import propose

    note = {"source": "unconfirmed", "reason": "gap with neighbour, one vote",
            "units": 6.0}
    monkeypatch.setattr(propose, "centring",
                        lambda *a, **kw: (0.6, dict(note)))
    reader = frame_edges.walk_reader("negative")
    frames = _walk()
    mm, detail = reader.judge(1, frames[0][1])
    assert mm is None
    assert "one vote" in detail["reason"] and detail["source"] == "unconfirmed"

    monkeypatch.setattr(propose, "centring",
                        lambda *a, **kw: (0.6, dict(note, source="measured")))
    assert reader.judge(1, frames[0][1])[0] == pytest.approx(0.6)


def _near_black(seed=1, density=0.01):
    """Fogged leader, or an opaque strip end, as an 8-bit corrected prescan:
    almost all 0, with sparse 1-count noise. Its relative contrast is high,
    so it is read as a frame; `gapmodel` then divides by a base level of 0."""
    rng = np.random.default_rng(seed)
    img = np.zeros((286, 428, 3), np.uint8)
    img[rng.random(img.shape) < density] = 1
    return img


def test_a_member_that_raises_abstains_and_the_others_still_vote(monkeypatch):
    """What a raising member leaves is exactly what a refusing one would:
    the other three vote as they would without it, and the frame is read."""
    import types

    img = negative_prescan(12.0, seed=3)

    def refuses(image, ctx):
        return EdgeResult(Side(REFUSE, note="stub"), Side(REFUSE, note="stub"))

    def raises(image, ctx):
        raise ZeroDivisionError("float division by zero")

    monkeypatch.setitem(vote.MEMBERS, "gapmodel", types.SimpleNamespace(detect=refuses))
    without = frame_edges.detect(img, film="negative")
    monkeypatch.setitem(vote.MEMBERS, "gapmodel", types.SimpleNamespace(detect=raises))
    failed = frame_edges.detect(img, film="negative")
    assert (failed.left, failed.right) == (without.left, without.right)
    assert failed.left.state == EDGE, "the frame was still read"
    why = failed.debug["members"]["gapmodel"]["failed"]
    assert "ZeroDivisionError" in why
    assert "failed" not in failed.debug["members"]["changepoint"]


def test_a_member_that_raises_is_named_in_the_note(monkeypatch):
    """FE-01: the abstention lived only in the debug dict, which nothing read,
    so a member broken on every frame made a quiet three-member vote with a
    green light. The note the sheet shows and a roll keeps says so."""
    import types

    def raises(image, ctx):
        raise ZeroDivisionError("float division by zero")

    monkeypatch.setitem(vote.MEMBERS, "chroma", types.SimpleNamespace(detect=raises))
    frames = _walk()
    _, notes = frame_edges.propose_centred(frames, film="negative")
    for note in notes.values():
        assert "ZeroDivisionError" in note["abstained"]["chroma"]
        assert "abstained: chroma failed" in note["reason"]
    walk = StripWalk(reader=frame_edges.walk_reader("negative"))
    for n, im in frames:
        walk.observe(n, im)
    _, detail = walk.judge(1, frames[0][1])
    assert "chroma" in detail["abstained"]


def test_a_frame_every_member_calls_blank_is_not_placed_from_neighbours():
    """FE-04: the vote has no ALL_BASE branch, so a frame all four members
    called blank came out "no agreement", refused, and was then given a
    position from its neighbours where 'not placed' was promised."""
    from conftest import C41_BASE

    rng = np.random.default_rng(1)
    blank = (np.array(C41_BASE)[None, None, :] * np.ones((286, 428, 1))
             + rng.normal(0, 0.5, (286, 428, 3))).clip(0, 255).astype(np.uint8)
    frames = [(1, negative_prescan(12.0, seed=1)),
              (2, negative_prescan(7.0, seed=2)), (3, blank),
              (4, negative_prescan(9.0, seed=4)),
              (5, negative_prescan(10.0, seed=5))]
    offsets, notes = frame_edges.propose_centred(frames, film="negative")
    assert 3 not in offsets
    assert notes[3]["source"] == "none"


def test_a_number_given_twice_is_read_once_against_the_others(monkeypatch):
    """FE-08: context was chosen by list position, so two copies of frame 2
    were each the other's roll context -- its gap confirmed by itself --
    where the window, keeping the last, read it against 1 and 3 only."""
    frames = _walk((12.0, 7.0, 15.0))
    twin = negative_prescan(7.0, seed=99)
    rolls = []
    real = vote.detect
    monkeypatch.setattr(vote, "detect", lambda im, ctx: rolls.append(
        (im.shape, len(ctx["roll"]))) or real(im, ctx))
    offsets, notes = frame_edges.propose_centred(
        [frames[0], frames[1], (2, twin), frames[2]], film="negative")
    assert len(rolls) == 3 and {n for _, n in rolls} == {2}
    alone, _ = frame_edges.propose_centred(
        [frames[0], (2, twin), frames[2]], film="negative")
    assert offsets == alone


def _slide(seed, gap):
    """A positive with the opaque gap beside it in view, as an 8-bit prescan:
    the picture inverted from a negative's, and near black where it ends."""
    neg = negative_prescan(0.0, 0.0, seed=seed).astype(float)
    pos = 200 * (0.9 - 0.8 * neg / neg.max())
    pos[:, :gap] = 2
    return pos.clip(0, 255).astype(np.uint8)


@pytest.mark.parametrize("seed", range(4))
def test_a_slide_read_as_a_negative_is_not_placed(seed):
    """FE-02: only `stepline` can tell a positive, and the vote counted its
    "not a negative" as one abstention. The rest read the black gap as
    picture to the border, and a slide scanned with the film left at its
    default came out "measured", sitting right, with the gap in the picture."""
    image = _slide(seed, gap=13 + 4 * seed)
    mm, note = propose.read_frame(image, film="negative")
    assert mm is None
    assert note["source"] == "none"
    assert "not a negative" in note["reason"]
    frames = [(1, image), *[(n, negative_prescan(b, seed=n))
                            for n, b in ((2, 12.0), (3, 7.0))]]
    offsets, notes = frame_edges.propose_centred(frames, film="negative")
    assert 1 not in offsets and notes[1]["source"] == "none"


def _doubted(left, right):
    """A reading `stepline` called "not a negative" while the other members
    still read the sides given -- the members' notes as the vote leaves them."""
    from tools.frame_edges.sides import EdgeResult
    return EdgeResult(left, right, debug={"members": {
        "stepline": {"not_a_negative": "a full-height strip at 0.0% of the "
                                       "brightest: not a negative"},
        "changepoint": {"left": [left.state], "right": [right.state]}}})


def test_a_drawn_edge_places_the_frame_even_when_doubted():
    """2026-10-07: a B&W strip came back "not a negative" on frames whose
    left edge the others read at column 18. The sheet drew that red line,
    filed a move of none, and the scans kept the black band it marked. The
    line is the automatic position: the frame is placed by it, and the doubt
    is said."""
    from tools.frame_edges.sides import EDGE, PICTURE_TO_BORDER, Side
    reading = _doubted(Side(EDGE, 18.17, 17.0, 19.0, 0.8),
                       Side(PICTURE_TO_BORDER))
    mm, note = propose.centring(reading, 428)
    assert mm is not None and mm < 0, "a left base moves the picture left"
    assert note["source"] in ("measured", "unconfirmed")
    assert note["units"] == pytest.approx(-17.7, abs=0.5)
    assert "not a negative" in note["doubt"]


def test_a_doubted_frame_with_no_edge_drawn_is_still_not_placed():
    """No red line, nothing to place it by: as before, left alone."""
    from tools.frame_edges.sides import PICTURE_TO_BORDER, Side
    reading = _doubted(Side(PICTURE_TO_BORDER), Side(PICTURE_TO_BORDER))
    mm, note = propose.centring(reading, 428)
    assert mm is None and note["source"] == "none"
    assert "not a negative" in note["reason"]


@pytest.mark.parametrize("film", ["negative", "bw"])
def test_no_member_fails_on_an_ordinary_walk(film):
    """FE-01: the synthetic walk still read with a member broken, so no test
    would notice one breaking. None may abstain on ordinary frames."""
    frames = _walk()
    if film == "bw":
        frames = [(n, np.repeat(im.mean(axis=2, keepdims=True), 3, axis=2
                                ).astype(np.uint8)) for n, im in frames]
    _, notes = frame_edges.propose_centred(frames, film=film)
    for n, im in frames:
        members = frame_edges.detect(im, film=film).debug["members"]
        assert not [k for k, v in members.items() if "failed" in v], (n, members)
        assert "abstained" not in notes[n]


def test_a_near_black_frame_costs_nothing_on_the_roll_path():
    """It passes the blank check, so a roll with correction on judges it --
    and `gapmodel`'s ZeroDivisionError was not in `scan_roll`'s net, so the
    roll ended there, with the film sometimes already moved. Now it is a
    frame the detector cannot place: left as it came, and the walk goes on.

    Without a warning, too: `changepoint`'s sliver margin divided 0 by 0 on
    it, a debug value only, but a warning in every roll that met one. It is
    recorded rather than raised, because a member that raises abstains."""
    frames = _walk()
    dark = _near_black()
    walk = StripWalk(reader=frame_edges.walk_reader("negative"))
    for n, im in frames:
        walk.observe(n, im)
    walk.observe(5, dark)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        mm, detail = walk.judge(5, dark)
    assert [str(w.message) for w in caught] == []
    assert mm is None
    assert detail["source"] in ("refused", "none")
    # the driver's second look, after a move onto leader, abstains the same way
    assert walk.reader.reread(5, dark) is None
    # and the frames around it are still read, before it and after it
    assert walk.judge(1, frames[0][1])[0] is not None

    offsets, notes = frame_edges.propose_centred([*frames, (5, dark)],
                                                 film="negative")
    assert 5 not in offsets or notes[5]["source"] == "neighbours"
    assert offsets[1] < 0


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
    # With aiming off nothing is read, under either detector.
    walk.stop("off")
    assert walk.judge(1, negative_prescan(12.0)) == (None, {"reason": "off",
                                                            "members": []})


class _NoNotes:
    """The walk reader as it was before it gave its second look's note."""

    def __init__(self, film):
        self.inner = frame_edges.walk_reader(film)

    def observe(self, number, image):
        return self.inner.observe(number, image)

    def judge(self, number, image):
        return self.inner.judge(number, image)

    def reread(self, number, image):
        return self.inner.reread(number, image)


def _aimed_roll(reader):
    """Three frames of a strip, each with 30 columns of base on its left,
    aimed by the driver's own roll; the film never follows a move."""
    from test_roll import METER_NONE, FakeRoll

    s = FakeRoll([negative_prescan(30.0, seed=n) for n in range(3)])
    s.HOLD_SETTLE_S = 0.0
    frames = list(s.scan_roll(frames=3, meter=METER_NONE, correct=True,
                              edge_reader=reader))
    return s, frames


def test_a_holds_second_look_is_filed_and_sends_nothing():
    """It abandoned a hold saying only "further out": what it read is filed
    now, beside each verification it judged -- and taking it sends exactly
    what the driver sent without it."""
    s, frames = _aimed_roll(frame_edges.walk_reader)
    plain, plain_frames = _aimed_roll(_NoNotes)
    assert [x for x in s.slid if x[0] in (0x00, 0x01)], "the frames were aimed"
    assert s.slid == plain.slid, "the second look's record sent nothing"
    moved = [f.registration["correction"] for f in frames
             if f.registration["correction"].get("moves")]
    assert moved
    for aim in moved:
        looks = [h.get("second_look") for h in aim["history"][1:]]
        assert len(looks) == aim["moves"] and all(looks), aim["history"]
        for look in looks:
            assert set(look["members"]) == set(vote.MEMBERS)
            assert look["image_sha1"] and look["mm"] is not None
            assert look["against_sha1"] == aim["ensemble"]["against_sha1"]
    for f in plain_frames:
        assert all("second_look" not in h
                   for h in f.registration["correction"].get("history", ()))
    # Each frame's record keeps what it told the others, and its judgment
    # what it was read against.
    for f in frames:
        assert f.registration["base"]["summary"] is not None
        assert f.registration["correction"]["ensemble"]["against_index"] == list(
            range(f.index))


def test_a_record_that_fails_in_a_roll_costs_a_field_not_the_roll(monkeypatch):
    """New bookkeeping runs inside the roll's frame net, which catches the
    device's failures and ValueError only: one that raised anything else
    would have ended the roll. It costs the field and says so."""
    from conftest import scanner_at_commands
    from test_roll import METER_NONE

    def broken(s):
        raise TypeError("cannot keep this")

    scanner, _device = scanner_at_commands(monkeypatch)
    scanner.calibrate_shading()
    # The device double's prescan is 431 columns, which the detector does not
    # summarise; each frame is given a real summary so its record is kept.
    one = propose.summary(negative_prescan(12.0, seed=1))
    monkeypatch.setattr(propose, "summary", lambda image, dtype=None: one)
    monkeypatch.setattr(propose, "summary_record", broken)
    frames = list(scanner.scan_roll(frames=2, resolution=300, infrared=False,
                                    meter=METER_NONE, dry_run=True, correct=True,
                                    edge_reader=frame_edges.walk_reader))
    assert [f.index for f in frames] == [0, 1]
    for f in frames:
        assert f.registration["base"]["record_error"] == (
            "observe: TypeError: cannot keep this")
        assert "ensemble" in f.registration["correction"]


def test_the_demo_and_the_session_pass_the_reader_on():
    """The demo is the real software: it builds its walk the driver's way,
    because the loop it runs is the driver's own."""
    from rps7200 import demo, direct, session

    assert demo.DemoScanner._drivers_roll is direct.DirectScanner.scan_roll
    assert "self._drivers_roll(" in inspect.getsource(demo.DemoScanner.scan_roll)
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


def test_the_background_reader_names_the_same_context_as_the_sheet():
    """Each note names the frames it was read against and digests their
    pictures, as `propose_centred`'s does -- on a walk added to, and beside a
    frame that gives the others no summary (a width the detector refuses),
    which neither counts as context."""
    frames = _walk((12.0, 0.0, 7.0, 15.0))
    odd = (5, negative_prescan(9.0, seed=5, width=430))
    assert frame_edges.summary(odd[1]) is None
    watch = frame_edges.EdgeWatch()
    try:
        watch.load(frames[:2], "negative")
        assert watch.wait(30)
        watch.extend(expected=5)
        for number, image in [*frames[2:], odd]:
            watch.add(number, image)
        watch.finish()
        assert watch.wait(60)
        notes = watch.progress().notes
        _, sheet = frame_edges.propose_centred([*frames, odd], film="negative")
        assert notes == sheet
        assert notes[1]["against"] == [2, 3, 4], "not the frame with no summary"
        assert notes[5]["against"] == [1, 2, 3, 4]
        assert all(n["against_sha1"] and n["image_sha1"] for n in notes.values())
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


def test_a_read_that_fails_once_and_then_succeeds_is_not_left_red(monkeypatch):
    """An error was cleared only by a new prescan of that frame, so a first
    read that failed stayed after the re-read at finish() succeeded: every
    frame had its position and the light said FAILED, with a stale line."""
    frames = _walk((12.0, 0.0, 7.0, 15.0))
    flaky = frames[1][1]
    real = frame_edges.watch.read_frame
    failed = []

    def once(image, **kw):
        if image is flaky and not failed:
            failed.append(True)
            raise ValueError("a passing fault")
        return real(image, **kw)

    monkeypatch.setattr(frame_edges.watch, "read_frame", once)
    watch = frame_edges.EdgeWatch()
    try:
        watch.begin("negative", expected=len(frames))
        # One at a time, as a walk delivers them: frame 2 is first read
        # before the frames after it exist, and read again at the finish
        # against all of them.
        for number, image in frames:
            watch.add(number, image)
            assert watch.wait(30)
        watch.finish()
        assert watch.wait(60)
        progress = watch.progress()
        assert failed, "the first read never failed"
        assert progress.state == frame_edges.DONE, progress.errors
        assert progress.errors == ()
        assert progress.done == len(frames)
    finally:
        watch.close()
