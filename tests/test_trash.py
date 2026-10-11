"""The trash: what Delete takes away, kept whole until it is emptied by hand.

Stefan's call, 2026-10-09. The window's Delete, the roll browser's Delete and
`tools/library.py duplicates --delete` move what they take into
``.trash/<UTC time>/`` beside where it was, with one rename each;
`library.restore` brings it back and `library.empty_trash` is the only thing
that removes it. A roll frame whose entry went is marked first, and counts as
not scanned until its entry is back (`session.scanned_frames`).

Offline throughout: entries are filed by `library.save` into temporary
libraries, as a real one is, and nothing here opens a scanner.
"""
from __future__ import annotations

import json
import os

import numpy as np
import pytest

from rps7200 import library, locks, session
from rps7200.direction import encode_index


def _entry(root, seed=1, **meta):
    """One 300 dpi pass, filed as the scanner files it: raw bytes and all."""
    image = np.random.default_rng(seed).integers(
        0, 65535, (6, 8, 3), dtype=np.uint16)
    return library.save(
        image, {"resolution_dpi": 300, "channels": 3, "width": 8, "height": 6,
                "depth": 16, "channel_order": list("RGB"), **meta},
        root=root, raw=encode_index(image),
        raw_layout={"format": "index", "bytes_per_line": 16, "width": 8,
                    "lines": 6, "channels": 3})


def _pair(root, pair_id="pair-1"):
    """A slide's two passes: the RGBI pass, then the RGB pass that is the
    frame, sharing an ``infrared_pair`` id (`ScanSession._pair`)."""
    infrared = _entry(root, 7, infrared_pair={"id": pair_id,
                                              "role": "infrared"})
    colour = _entry(root, 8, infrared_pair={"id": pair_id, "role": "colour"})
    return colour, infrared


def _bytes_of(path):
    return {p.relative_to(path).as_posix(): p.read_bytes()
            for p in path.rglob("*") if p.is_file()}


# -- moving into the trash ---------------------------------------------------


def test_an_entry_goes_into_the_trash_whole_and_nothing_reads_it_there(
        tmp_path):
    root = tmp_path / "library"
    gone = _entry(root, 1, roll_membership={"roll": "strip", "number": 3,
                                            "kind": "frame",
                                            "folder": "rolls/strip"})
    kept = _entry(root, 2)
    before = _bytes_of(gone)
    moved = library.trash(root, [gone], by="a test", why="asked to")

    assert moved.moved == [gone.name] and moved.left == {}
    assert moved.batch.parent == root / library.TRASH
    assert not gone.exists()
    assert _bytes_of(moved.batch / gone.name) == before, "moved, not copied"
    said = json.loads((moved.batch / library.TRASH_RECORD).read_text(
        encoding="utf-8"))
    assert said["state"] == "trashed" and said["by"] == "a test"
    assert said["why"] == "asked to" and said["kind"] == "entries"
    (item,) = said["items"]
    assert item["name"] == gone.name and item["bytes"] == moved.bytes > 0
    assert item["roll_membership"]["number"] == 3

    # Two levels down and a dot name: nothing that reads the library sees it.
    assert [r["id"] for r in library.entries(root)] == [kept.name]
    indexed = [r["id"] for r in json.loads(
        (root / library.INDEX).read_text(encoding="utf-8"))]
    assert indexed == [kept.name], "dropped from the index, not rebuilt"
    notes: list[str] = []
    said = library.verify(root, notes=notes) + notes
    assert not [p for p in said
                if p.startswith(f"{gone.name}:") or library.TRASH in p]
    assert library.duplicates(root) == {}
    assert library.in_trash(root, gone.name).name == moved.batch.name
    assert library.trash_summary(root)[:3] == (1, 1, moved.bytes)
    assert "the trash holds 1 entry" in library.trash_said(root)


@pytest.mark.parametrize("which", ["outside", "nested", "dot", "no record",
                                   "the trash"])
def test_nothing_moves_unless_every_path_is_an_entry_of_the_library(
        tmp_path, which):
    root = tmp_path / "library"
    good = _entry(root, 1)
    bad = {"outside": tmp_path / "elsewhere" / "x",
           "nested": root / "sub" / "x",
           "dot": root / ".hidden",
           "no record": root / "bare",
           "the trash": root / library.TRASH}[which]
    bad.mkdir(parents=True, exist_ok=True)
    if which != "no record":
        (bad / "scan.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="nothing was moved"):
        library.trash(root, [good, bad], by="a test")
    assert good.exists() and bad.exists()
    assert library.trash_batches(root) == []


def test_an_entry_held_open_is_waited_for_then_left_whole(tmp_path,
                                                          monkeypatch):
    """On Windows a folder with a file open inside cannot be renamed -- the
    window's own full-resolution reader, Explorer, Defender. The rename is
    tried again for a moment, as a manifest's is, and one still refused is
    left where it was, whole, and said; a delete that moved nothing leaves
    no folder in the trash."""
    root = tmp_path / "library"
    held, free = _entry(root, 1), _entry(root, 2)
    tries = []
    real = os.rename

    def rename(source, target):
        if os.path.basename(source) == held.name:
            tries.append(source)
            raise PermissionError(13, "in use", str(source))
        return real(source, target)

    monkeypatch.setattr(library.os, "rename", rename)
    monkeypatch.setattr(library, "REPLACE_RETRY_S", (0.0, 0.0))
    moved = library.trash(root, [held, free], by="a test")
    assert len(tries) == 3, "tried again before giving up"
    assert moved.moved == [free.name] and set(moved.left) == {held.name}
    assert held.exists() and (held / "scan.json").exists()

    nothing = library.trash(root, [held], by="a test")
    assert nothing.batch is None and nothing.moved == []
    assert [b.name for b in library.trash_batches(root)] == [moved.batch.name]


def test_a_mark_that_cannot_be_written_moves_nothing(tmp_path):
    """The caller's record of the delete -- the window marks each roll frame
    -- goes on the disk before anything moves; where it cannot, nothing
    does, and no folder is left in the trash."""
    root = tmp_path / "library"
    entry = _entry(root)

    def refuse(batch):
        raise OSError("the roll's record cannot be written")

    with pytest.raises(OSError, match="cannot be written"):
        library.trash(root, [entry], by="a test", before=refuse)
    assert entry.exists()
    assert not any((root / library.TRASH).iterdir())


# -- a slide's two passes ------------------------------------------------------


def test_a_slides_two_passes_go_together_colour_first(tmp_path, monkeypatch):
    """NEW-T1: the colour pass is the frame, and its picture is made with the
    infrared pass. They go in one delete, the colour pass first; a colour
    pass that stays keeps its infrared pass with it."""
    root = tmp_path / "library"
    colour, infrared = _pair(root)
    moved = library.trash(root, [infrared, colour], by="a test")
    assert moved.moved == [colour.name, infrared.name]
    assert {i["name"]: i["infrared_pair"]["role"] for i in json.loads(
        (moved.batch / library.TRASH_RECORD).read_text(encoding="utf-8")
    )["items"]} == {colour.name: "colour", infrared.name: "infrared"}

    library.restore(root, moved.batch.name)
    real = os.rename

    def rename(source, target):
        if os.path.basename(source) == colour.name:
            raise PermissionError(13, "in use", str(source))
        return real(source, target)

    monkeypatch.setattr(library.os, "rename", rename)
    monkeypatch.setattr(library, "REPLACE_RETRY_S", ())
    stayed = library.trash(root, [colour, infrared], by="a test")
    assert stayed.batch is None and colour.exists() and infrared.exists()
    assert stayed.left[infrared.name] == ("kept with its colour pass, which "
                                          "could not be moved")


def test_a_slides_infrared_pass_comes_back_first(tmp_path, monkeypatch):
    """Restored in reverse: the infrared pass before the colour pass, which
    comes back only once its partner is -- and by its own id brings that
    partner with it. Back, the pair finds each other again."""
    root = tmp_path / "library"
    colour, infrared = _pair(root)
    moved = library.trash(root, [colour, infrared], by="a test")
    order = []
    real = library._move_patiently
    monkeypatch.setattr(library, "_move_patiently", lambda s, t: (
        order.append(s.name), real(s, t))[1])
    done = library.restore(root, colour.name)
    assert order == [infrared.name, colour.name]
    assert [name for _b, name, _now in done.restored] == order
    assert not moved.batch.exists()
    record = json.loads((colour / "scan.json").read_text(encoding="utf-8"))
    assert library.infrared_partner(colour, record) == infrared

    # An infrared pass that cannot come back keeps its colour pass with it.
    moved = library.trash(root, [colour, infrared], by="a test")
    infrared.mkdir()
    done = library.restore(root, moved.batch.name)
    assert done.restored == []
    assert "in the library again" in done.refused[infrared.name] \
        or "again" in done.refused[infrared.name]
    assert "with its infrared pass" in done.refused[colour.name]
    assert library.in_trash(root, colour.name) is not None


# -- restoring -----------------------------------------------------------------


def test_restore_brings_it_back_byte_for_byte(tmp_path):
    root = tmp_path / "library"
    one, two = _entry(root, 1), _entry(root, 2)
    before = _bytes_of(one)
    moved = library.trash(root, [one, two], by="a test")
    done = library.restore(root, one.name)
    assert done.restored == [(moved.batch.name, one.name, one.name)]
    assert _bytes_of(one) == before
    assert library.reconstruct(one)[1].startswith("identical")
    # Its checksums hold; its one problem is the test pass's own -- filed
    # with no reference, as it was before it went.
    assert [p for p in library.verify(root)
            if "no shading reference" not in p] == []
    indexed = [r["id"] for r in json.loads(
        (root / library.INDEX).read_text(encoding="utf-8"))]
    assert indexed == [one.name], "the index is rebuilt with it"
    # The delete keeps a record of what it still holds.
    (batch,) = library.trash_batches(root)
    assert batch.names == [two.name]
    assert [i["name"] for i in batch.record["items"]] == [two.name]

    library.restore(root, batch.name)
    assert two.exists() and not batch.path.exists(), "an emptied delete goes"
    with pytest.raises(LookupError, match="nothing called"):
        library.restore(root, batch.name)


def test_an_index_that_cannot_be_rewritten_never_hides_what_came_back(
        tmp_path, monkeypatch, capsys):
    """A sync client or a scanner for viruses holding index.json past the
    retries raised out of `restore` after the entries were back: the tool
    died with a traceback before saying what came back, and run again said
    nothing called it was in the trash. `save` says the same thing and
    goes on; so does this."""
    import sys

    from conftest import load_tool

    root = tmp_path / "library"
    entry, again = _entry(root, 1), _entry(root, 2)
    moved = library.trash(root, [entry], by="a test")
    trashed = library.trash(root, [again], by="a test")

    def held(_root=None):
        raise PermissionError(13, "held", "index.json")

    monkeypatch.setattr(library, "reindex", held)
    done = library.restore(root, entry.name)
    assert done.restored == [(moved.batch.name, entry.name, entry.name)]
    assert entry.exists()
    assert done.index_error and "index.json" in done.index_error

    # And from the command line: what came back is said, and it is no
    # failure -- the index is derived, and `reindex` rebuilds it.
    tool = load_tool("library")
    monkeypatch.setattr(sys, "argv", ["library.py", "restore", again.name,
                                      "--root", str(root)])
    capsys.readouterr()
    assert tool.main() == 0
    said = capsys.readouterr()
    assert f"restored {again.name} from {trashed.batch.name}" in said.out
    assert "index.json" in said.err
    assert "`tools/library.py reindex` rebuilds it" in said.err
    assert again.exists()


def test_an_entry_whose_id_is_taken_again_stays_in_the_trash(tmp_path):
    root = tmp_path / "library"
    entry = _entry(root)
    moved = library.trash(root, [entry], by="a test")
    entry.mkdir()
    (entry / "scan.json").write_text("{}", encoding="utf-8")
    done = library.restore(root, moved.batch.name)
    assert done.restored == [] and entry.name in done.refused
    assert library.in_trash(root, entry.name) is not None


def test_a_roll_whose_name_is_taken_comes_back_beside_it(tmp_path):
    """A roll is its records, which keep its name inside: a folder of its
    name made since is not written over, and it comes back as the next free
    one, said."""
    rolls = tmp_path / "rolls"
    roll = rolls / "strip"
    roll.mkdir(parents=True)
    (roll / "survey.json").write_text('{"roll": "strip"}', encoding="utf-8")
    moved = library.trash(rolls, [roll], by="a test", kind="rolls")
    roll.mkdir()
    done = library.restore(rolls, "strip")
    assert done.restored == [(moved.batch.name, "strip", "strip-2")]
    assert json.loads((rolls / "strip-2" / "survey.json").read_text(
        encoding="utf-8")) == {"roll": "strip"}


# -- emptying ------------------------------------------------------------------


def test_emptying_is_a_dry_run_unless_written_and_spares_newer_deletes(
        tmp_path):
    root = tmp_path / "library"
    old, new = _entry(root, 1), _entry(root, 2)
    first = library.trash(root, [old], by="a test")
    # Made long ago, by its name: a delete's folder is named by its time.
    aged = first.batch.with_name("20200101T000000Z")
    first.batch.rename(aged)
    library.trash(root, [new], by="a test")

    dry = library.empty_trash(root)
    assert len(dry.batches) == 2 and dry.bytes > 0
    assert len(library.trash_batches(root)) == 2, "a dry run removes nothing"

    done = library.empty_trash(root, older_than_days=30, write=True)
    assert [b.name for b in done.batches] == [aged.name]
    assert not aged.exists()
    assert library.in_trash(root, new.name) is not None
    assert library.in_trash(root, old.name) is None


def test_an_emptying_cut_short_is_never_restored_and_is_finished(tmp_path):
    """Each delete is renamed `.emptying` before it is removed: what a kill
    left of it is never offered back, `leftovers` notes it, and the next
    run finishes it first."""
    root = tmp_path / "library"
    entry = _entry(root)
    moved = library.trash(root, [entry], by="a test")
    cut = moved.batch.with_name(moved.batch.name + library.EMPTYING)
    moved.batch.rename(cut)
    assert library.trash_batches(root) == []
    with pytest.raises(LookupError):
        library.restore(root, entry.name)
    left = [i for i in library.leftovers(root) if i.kind == "trash-emptying"]
    assert len(left) == 1 and "empty-trash --write" in left[0].said
    assert not left[0].problem

    done = library.empty_trash(root, write=True)
    assert done.unfinished == [cut] and not cut.exists()


def _kept_in_roll(root, rolls, name="strip"):
    """A pass the library at ``root`` refused, kept in a roll's unfiled/
    with a pointer in the library, as `session.keep_unfiled` keeps one."""
    folder = rolls / name
    folder.mkdir(parents=True)
    (folder / "survey.json").write_text(json.dumps({"roll": name}),
                                        encoding="utf-8")
    kept, refused = session.keep_unfiled(
        np.zeros((6, 8, 3), np.uint16),
        {"resolution_dpi": 300, "channels": 3, "width": 8, "height": 6,
         "depth": 16}, near=[folder], refused_by=root, why="a test")
    assert kept is not None and kept.parent == folder / "unfiled", refused
    return folder, kept


def test_a_deleted_rolls_unfiled_passes_still_fail_and_are_never_emptied(
        tmp_path):
    """Stefan's call on leftovers: a pass the library refused, kept in a
    roll's unfiled/, fails `verify` until it is filed -- its raw bytes are
    nowhere else. The roll browser's Delete took the folder into the rolls'
    trash, the pass dropped out of `leftovers`, and `empty-trash` removed
    it weeks later naming only the roll. Found there now, by the rolls' trash
    and by the library's pointer alone, and its delete is left alone."""
    root, rolls = tmp_path / "library", tmp_path / "rolls"
    _entry(root)
    folder, kept = _kept_in_roll(root, rolls)

    def unfiled(**where):
        return [i for i in library.leftovers(root, **where)
                if i.kind == "unfiled"]

    assert [i.problem for i in unfiled(rolls=rolls)] == [True]
    moved = library.trash(rolls, [folder], by="a test", kind="rolls")
    there = moved.batch / "strip" / "unfiled" / kept.name
    for where in ({"rolls": rolls}, {}):
        (left,) = unfiled(**where)
        assert left.problem and left.where == "rolls"
        assert left.path.resolve() == there.resolve()
        assert f"restore {moved.batch.name}" in left.said
        assert "empty-trash` leaves that delete alone" in left.said

    dry = library.empty_trash(rolls)
    assert dry.batches == [] and dry.bytes == 0
    assert {name: [p.resolve() for p in passes]
            for name, passes in dry.unfiled.items()} == {
        moved.batch.name: [there.resolve()]}
    done = library.empty_trash(rolls, write=True)
    assert done.batches == [] and (there / "scan.json").exists()
    # Filed by moving it into the library: nothing holds the delete back.
    os.rename(there, root / f"{kept.name}-filed")
    assert unfiled(rolls=rolls) == []
    done = library.empty_trash(rolls, write=True)
    assert [b.name for b in done.batches] == [moved.batch.name]
    assert done.unfiled == {} and not moved.batch.exists()


def test_emptying_takes_a_rolls_unfiled_passes_only_when_asked(tmp_path):
    root, rolls = tmp_path / "library", tmp_path / "rolls"
    _entry(root)
    folder, kept = _kept_in_roll(root, rolls)
    moved = library.trash(rolls, [folder], by="a test", kind="rolls")
    done = library.empty_trash(rolls, write=True, include_unfiled=True)
    assert [b.name for b in done.batches] == [moved.batch.name]
    assert list(done.unfiled) == [moved.batch.name], "said all the same"
    assert not moved.batch.exists()


def test_a_move_cut_short_is_noted_with_what_brings_it_back(tmp_path):
    root = tmp_path / "library"
    entry = _entry(root)
    moved = library.trash(root, [entry], by="a test")
    record = moved.batch / library.TRASH_RECORD
    said = json.loads(record.read_text(encoding="utf-8"))
    record.write_text(json.dumps(dict(said, state="moving")),
                      encoding="utf-8")
    (left,) = [i for i in library.leftovers(root) if i.kind == "trash-moving"]
    assert f"restore {moved.batch.name}" in left.said and not left.problem
    assert "move into the trash cut short" in library.leftovers_said([left])


# -- a roll's frames -----------------------------------------------------------


def _frame(number, entry, **more):
    return {"number": number, "done": True, "entry": str(entry), **more}


def test_a_frame_whose_entry_went_counts_as_not_scanned_until_it_is_back(
        tmp_path):
    """T-14: roll.json kept calling a deleted frame done, so finishing the
    roll skipped it. The mark beside the entry's name, while that entry is
    not in the library, makes it not done; restored, it is done again with
    nothing written. A record with no mark, or one whose mark is another
    take's, reads as it always has."""
    root = tmp_path / "library"
    entry = root / "20261009T000000Z_x"
    marker = {"at": "now", "id": entry.name, "batch": "b", "by": "a test"}
    manifest = {"frames": [
        _frame(1, f"library\\{entry.name}", entry_deleted=marker),
        _frame(2, "library\\other", entry_deleted=marker),
        _frame(3, "library\\gone-without-a-mark")]}
    assert session.scanned_frames(manifest, root) == {2, 3}
    assert session.scanned_frames(manifest) == {2, 3}, "no library: not back"
    entry.mkdir(parents=True)
    (entry / "scan.json").write_text("{}", encoding="utf-8")
    assert session.scanned_frames(manifest, root) == {1, 2, 3}
    assert session.scanned_frames(manifest) == {2, 3}


def test_forget_marks_only_a_record_naming_the_entry(tmp_path):
    path = tmp_path / "roll.json"
    manifest = session.RollManifest(path, {"frames": [
        _frame(1, "library\\a"), _frame(2, "library\\b")]})
    marker = {"at": "now", "id": "a", "batch": "b1", "by": "a test"}
    assert manifest.forget("a", marker, number=1) == ("marked", [1])
    on_disk = json.loads(path.read_text(encoding="utf-8"))["frames"]
    assert on_disk[0]["entry_deleted"] == marker, "on the disk at once"
    assert on_disk[0]["entry"] == "library\\a", "the entry's name stays"
    assert "entry_deleted" not in on_disk[1]
    # Frame 2 was scanned again since, as "b": "c" is a take before it.
    assert manifest.forget("c", marker, number=2) == ("other", [])
    assert manifest.forget("z", marker) == ("none", [])
    # Carried in every later write: the file is rewritten whole.
    manifest.ended(None)
    on_disk = json.loads(path.read_text(encoding="utf-8"))["frames"]
    assert on_disk[0]["entry_deleted"] == marker
    # A rescan replaces the record: the mark was the earlier take's.
    manifest.record({"number": 1, "done": False}, awaiting=True)
    manifest.filed(1, tmp_path / "library" / "a-again", None)
    record = json.loads(path.read_text(encoding="utf-8"))["frames"][-1]
    assert "entry_deleted" not in record
    assert [t["entry"] for t in record["takes"]] == ["a", "a-again"]


def test_forget_waits_for_a_frame_still_being_filed(tmp_path):
    """A frame recorded and not yet filed names no entry, or the take before
    it; and the writer can answer the window before this record (`_early`).
    Either way nothing is written, and the window says try again."""
    path = tmp_path / "roll.json"
    manifest = session.RollManifest(path, {"frames": []})
    manifest.record({"number": 4}, awaiting=True)
    marker = {"id": "x"}
    assert manifest.forget("x", marker, number=4) == ("awaiting", [4])
    assert manifest.awaits(4)
    manifest.filed(4, tmp_path / "x", None)
    assert not manifest.awaits(4)
    early = session.RollManifest(tmp_path / "early.json", {"frames": []})
    early.filed(5, tmp_path / "y", None)
    assert early.forget("y", marker, number=5)[0] == "awaiting"
    assert not (tmp_path / "early.json").exists(), "nothing written"


def test_every_take_of_a_frame_is_listed(tmp_path):
    """OUT-09: a rescan replaced the frame's record and nothing in the roll
    named the earlier take's entry. Every take is listed now, earliest first,
    from a record written before takes were listed as well."""
    path = tmp_path / "roll.json"
    manifest = session.RollManifest(path, {"frames": [
        _frame(2, "library\\first", file="frame02.tif")]})
    manifest.record({"number": 2}, awaiting=True)
    manifest.filed(2, tmp_path / "second", None, file="frame02.tif")
    manifest.record({"number": 2}, awaiting=True)
    manifest.filed(2, tmp_path / "third", None)
    (record,) = json.loads(path.read_text(encoding="utf-8"))["frames"]
    assert [t["entry"] for t in record["takes"]] == ["first", "second",
                                                     "third"]
    assert record["takes"][0] == {"entry": "first", "file": "frame02.tif"}
    assert record["takes"][1]["file"] == "frame02.tif"
    assert record["takes"][2]["filed_utc"]
    # A take that failed is no take.
    manifest.record({"number": 2}, awaiting=True)
    manifest.filed(2, None, "the disk is full")
    (record,) = json.loads(path.read_text(encoding="utf-8"))["frames"]
    assert len(record["takes"]) == 3 and record["done"] is False


def test_an_earlier_takes_filing_answering_after_the_retake_is_listed(
        tmp_path):
    """A roll carried on into the same folder can take a frame again before
    the first take's filing is back. That answer came to a record already
    replaced, was counted off and dropped: filed in the library, and named
    by nothing in the roll -- nor kept by `duplicates --delete` for it."""
    path = tmp_path / "rolls" / "strip" / "roll.json"
    path.parent.mkdir(parents=True)
    manifest = session.RollManifest(path, {"frames": []})
    manifest.record({"number": 3}, awaiting=True)
    manifest.record({"number": 3}, awaiting=True)
    manifest.record({"number": 3}, awaiting=True)
    manifest.filed(3, tmp_path / "first", None, file="frame03.tif")
    manifest.filed(3, None, "the disk is full")
    (record,) = json.loads(path.read_text(encoding="utf-8"))["frames"]
    assert [t["entry"] for t in record["takes"]] == ["first"]
    assert record["takes"][0]["file"] == "frame03.tif"
    assert record["done"] is False, "the latest take is still to come"
    assert manifest.awaits(3)
    manifest.filed(3, tmp_path / "third", None)
    (record,) = json.loads(path.read_text(encoding="utf-8"))["frames"]
    assert [t["entry"] for t in record["takes"]] == ["first", "third"]
    assert record["entry"] == str(tmp_path / "third") and record["done"]
    assert {"first", "third"} <= session.entries_named(tmp_path / "rolls")


def test_a_walk_lists_every_prescan_it_filed_for_a_frame(tmp_path):
    """The same for survey.json: each prescan filed for a frame -- the one
    from before an aim too -- across walks added to one another."""
    path = tmp_path / "survey.json"
    manifest = session.RollManifest(path, {"frames": [
        {"number": 1, "prescan": "prescan01.tif", "prescan_entry": "old"}]})
    record = {"number": 1}
    told = manifest.prescan_told(record, tmp_path / "prescan01.tif")
    before = manifest.prescan_told(record, tmp_path / "prescan01-before.tif",
                                   key="prescan_before",
                                   entry_key="prescan_before_entry")
    manifest.record(record)
    for done, entry in ((before, "aimed-from"), (told, "aimed")):
        done(session.Filing(seq=1, number=1, kind="prescan",
                            outcome=session.FILED, entry=tmp_path / entry))
    (on_disk,) = json.loads(path.read_text(encoding="utf-8"))["frames"]
    assert [(t["entry"], t.get("kind")) for t in on_disk["takes"]] == [
        ("old", "prescan"), ("aimed-from", "prescan_before"),
        ("aimed", "prescan")]


def _walked(manifest, record, tmp_path, name):
    """A walk prescan of ``record`` queued on the writer (`queued`): its
    answer, to be given later with the entry ``name``."""
    told = manifest.prescan_told(record, tmp_path / f"{name}.tif")
    session.queued(lambda: None, told)
    return lambda: told(session.Filing(seq=1, number=record["number"],
                                       kind="prescan", outcome=session.FILED,
                                       entry=tmp_path / name))


def test_a_walk_prescans_late_answer_is_listed_on_the_record_after_it(
        tmp_path):
    """A walk added to another walked frame 2 again while the first walk's
    prescan of it still waited behind the writer's queue. The second record
    took the first's takes before it had any; the first's answer then came
    to a record no longer in the file, and its entry was named by nothing --
    nor kept by `duplicates --delete` for it."""
    rolls = tmp_path / "rolls"
    path = rolls / "strip" / "survey.json"
    path.parent.mkdir(parents=True)
    manifest = session.RollManifest(path, {"roll": "strip", "frames": []})
    first = {"number": 2}
    answer_first = _walked(manifest, first, tmp_path, "walk-1")
    manifest.record(first)
    # The walk added to it: built on the records in hand, as `_roll` does.
    manifest.carry_on({"roll": "strip",
                       "frames": list(manifest.data["frames"])})
    second = {"number": 2}
    answer_second = _walked(manifest, second, tmp_path, "walk-2")
    manifest.record(second)
    answer_first()
    answer_second()
    (on_disk,) = json.loads(path.read_text(encoding="utf-8"))["frames"]
    assert [t["entry"] for t in on_disk["takes"]] == ["walk-1", "walk-2"]
    assert on_disk["prescan_entry"] == "walk-2"
    assert {"walk-1", "walk-2"} <= session.entries_named(rolls)
    assert not manifest.ahead_of_disk()


def test_a_walk_prescans_early_answer_is_listed_once(tmp_path):
    """The writer can answer before the scanning thread records the frame:
    that take waits in its record and is written with it, once, beside the
    takes of the record it replaces -- one from before takes were listed
    included."""
    path = tmp_path / "survey.json"
    manifest = session.RollManifest(path, {"frames": [
        {"number": 1, "prescan": "prescan01.tif", "prescan_entry": "old"}]})
    first = {"number": 1}
    answer_first = _walked(manifest, first, tmp_path, "walk-1")
    manifest.record(first)
    answer_first()
    second = {"number": 1}
    answer_second = _walked(manifest, second, tmp_path, "walk-2")
    answer_second()
    manifest.record(second)
    (on_disk,) = json.loads(path.read_text(encoding="utf-8"))["frames"]
    assert [t["entry"] for t in on_disk["takes"]] == ["old", "walk-1",
                                                      "walk-2"]
    # And with the one before still waiting when the next one's answer lands.
    third = {"number": 1}
    answer_third = _walked(manifest, third, tmp_path, "walk-3")
    manifest.record(third)
    fourth = {"number": 1}
    answer_fourth = _walked(manifest, fourth, tmp_path, "walk-4")
    answer_fourth()
    manifest.record(fourth)
    answer_third()
    (on_disk,) = json.loads(path.read_text(encoding="utf-8"))["frames"]
    entries = [t["entry"] for t in on_disk["takes"]]
    assert sorted(entries) == ["old", "walk-1", "walk-2", "walk-3", "walk-4"]
    assert len(entries) == len(set(entries)), "each take listed once"


def test_marks_are_found_by_what_names_the_entry(tmp_path):
    """A duplicate and a renamed folder carry roll.json with them, and most
    entries record their folder relative to wherever the window ran; so the
    files are found by what names the entry, never by the folder alone. A
    roll at the recorded folder whose record names another take is left
    alone. The file a session writes is marked through its manifest, which
    is ahead of the file."""
    rolls = tmp_path / "rolls"
    marker = {"id": "e1", "batch": "b"}
    files = {}
    for name, entry in (("strip", "e1"), ("strip-2", "e1"),
                        ("renamed", "library/e1"), ("reused", "e2")):
        (rolls / name).mkdir(parents=True)
        files[name] = rolls / name / "roll.json"
        session.write_manifest(files[name], {"frames": [_frame(1, entry)]})
    live = session.RollManifest(files["strip"], json.loads(
        files["strip"].read_text(encoding="utf-8")))
    marked = session.mark_deleted_frames(
        files.values(), "e1", marker,
        live=lambda p: live if p == files["strip"] else None)
    assert sorted(p.parent.name for p, _n, _o in marked) == [
        "renamed", "strip", "strip-2"]
    assert live.data["frames"][0]["entry_deleted"] == marker
    for name in ("strip-2", "renamed"):
        on_disk = json.loads(files[name].read_text(encoding="utf-8"))
        assert on_disk["frames"][0]["entry_deleted"] == marker
        assert (rolls / name / "roll.json.bak").exists(), "the file kept"
    assert "entry_deleted" not in json.loads(
        files["reused"].read_text(encoding="utf-8"))["frames"][0]


def test_a_roll_record_that_does_not_parse_is_kept_not_buried(tmp_path):
    """Read through `read_manifest`'s fallback, a roll.json that did not
    parse read as its .bak, and the write that kept "the file as it stood"
    copied the damaged newer file over the good older one and wrote the
    older one back, marked, as the roll. The damaged file is kept beside
    as unreadable, said, and the .bak stays."""
    folder = tmp_path / "rolls" / "strip"
    folder.mkdir(parents=True)
    path = folder / "roll.json"
    older = {"frames": [_frame(1, "library\\e1"), _frame(2, "library\\e2")]}
    (folder / "roll.json.bak").write_text(json.dumps(older), encoding="utf-8")
    damaged = json.dumps({"frames": older["frames"] + [
        _frame(3, "library\\e3")]}).encode("utf-8")[:-20]
    path.write_bytes(damaged)
    said: list[str] = []
    marked = session.mark_deleted_frames([path], "e1", {"id": "e1"},
                                         say=said.append)
    assert [(n, o) for _p, n, o in marked] == [(1, "marked")]
    assert (folder / "roll.json.unreadable").read_bytes() == damaged
    assert json.loads((folder / "roll.json.bak").read_text(
        encoding="utf-8")) == older, "the older file is not written over"
    now = json.loads(path.read_text(encoding="utf-8"))
    assert [f["number"] for f in now["frames"]] == [1, 2]
    assert now["frames"][0]["entry_deleted"] == {"id": "e1"}
    assert "roll.json.unreadable" in said[0] and "cannot be read" in said[0]
    assert "unreadable-manifest" in [i.kind for i in library.leftovers(
        tmp_path / "library", rolls=tmp_path / "rolls")]

    # Neither readable: nothing is written, and the Delete is refused.
    path.write_bytes(b"{")
    (folder / "roll.json.bak").write_bytes(b"[")
    with pytest.raises(ValueError, match="cannot be read"):
        session.mark_deleted_frames([path], "e2", {"id": "e2"})
    assert path.read_bytes() == b"{"


def test_a_frame_of_a_roll_in_the_trash_is_marked_too(tmp_path):
    """A roll comes back from the rolls' trash. A frame whose entry went
    while its roll was there was left unmarked, and read as done once the
    roll was restored -- with its entry gone."""
    scan = _session(tmp_path)
    root, rolls = tmp_path / "library", tmp_path / "rolls"
    entry = _frame_entry(root, rolls)
    (rolls / "strip").mkdir(parents=True)
    session.write_manifest(rolls / "strip" / "roll.json", {
        "numbering": "strip",
        "frames": [_frame(3, entry), _frame(4, "library\\other")]})
    roll = library.trash(rolls, [rolls / "strip"], by="a test", kind="rolls")
    assert [(p.parent.name, e, n) for p, e, n in scan.frames_naming([entry])
            ] == [("strip", entry.name, [3])]
    done = scan.trash_entries([entry], by="the window")
    assert [(p.parent.name, n) for p, n in done.frames] == [("strip", 3)]
    library.restore(rolls, roll.batch.name)
    progress = json.loads((rolls / "strip" / "roll.json").read_text(
        encoding="utf-8"))
    assert session.scanned_frames(progress, root) == {4}


def test_restoring_a_frame_of_a_roll_in_the_trash_says_what_it_means(
        tmp_path, monkeypatch, capsys):
    """The frame is marked in the roll the rolls' trash holds, and counts
    as scanned again once that roll is restored. `restore` read only the
    live roll folders and the one its entry was filed for, which is in the
    trash, and said nothing of it."""
    import sys

    from conftest import load_tool

    scan = _session(tmp_path)
    root, rolls = tmp_path / "library", tmp_path / "rolls"
    entry = _frame_entry(root, rolls)
    (rolls / "strip").mkdir(parents=True)
    session.write_manifest(rolls / "strip" / "roll.json", {
        "numbering": "strip",
        "frames": [_frame(3, entry), _frame(4, "library\\other")]})
    roll = library.trash(rolls, [rolls / "strip"], by="a test", kind="rolls")
    scan.trash_entries([entry], by="the window")
    marked = json.loads((roll.batch / "strip" / "roll.json").read_text(
        encoding="utf-8"))
    assert "entry_deleted" in marked["frames"][0]

    tool = load_tool("library")
    monkeypatch.setattr(sys, "argv", ["library.py", "restore", entry.name,
                                      "--root", str(root),
                                      "--rolls", str(rolls)])
    capsys.readouterr()
    assert tool.main() == 0
    said = capsys.readouterr().out
    assert f"restored {entry.name} from" in said
    assert (f"frame 3 of strip, a roll in the rolls' trash "
            f"({roll.batch.name}), counts as scanned again once that roll "
            "is restored") in said, said
    # A live roll's frame is said as it always was.
    library.restore(rolls, roll.batch.name)
    library.trash(root, [entry], by="a test")
    monkeypatch.setattr(sys, "argv", ["library.py", "restore", entry.name,
                                      "--root", str(root),
                                      "--rolls", str(rolls)])
    assert tool.main() == 0
    said = capsys.readouterr().out
    assert "frame 3 of strip counts as scanned again\n" in said, said


def test_an_earlier_take_is_named_by_no_frame(tmp_path):
    """An entry only among a frame's earlier takes is not that frame's: the
    roll keeps its later take, and nothing is marked for it."""
    scan = _session(tmp_path)
    root, rolls = tmp_path / "library", tmp_path / "rolls"
    entry = _frame_entry(root, rolls)
    (rolls / "strip").mkdir(parents=True)
    session.write_manifest(rolls / "strip" / "roll.json", {"frames": [
        _frame(3, "library\\later", takes=[{"entry": entry.name},
                                           {"entry": "later"}])]})
    assert scan.frames_naming([entry]) == []
    done = scan.trash_entries([entry], by="the window")
    assert done.trashed.moved == [entry.name] and done.frames == []
    assert "entry_deleted" not in (rolls / "strip" / "roll.json").read_text(
        encoding="utf-8")


def test_entries_named_by_rolls(tmp_path):
    rolls = tmp_path / "rolls"
    roll = rolls / "strip"
    roll.mkdir(parents=True)
    (roll / "roll.json").write_text(json.dumps({"frames": [
        _frame(1, "library\\a", takes=[{"entry": "older"}])]}),
        encoding="utf-8")
    (roll / "survey.json").write_text(json.dumps({"frames": [
        {"number": 1, "prescan_entry": "p", "prescan_before_entry": "q"}]}),
        encoding="utf-8")
    (roll / "approved.json").write_text(json.dumps({"frames": [
        {"number": 1, "reference_entry": "library/r"}]}), encoding="utf-8")
    # A roll in the rolls' trash names its entries too: restored, it reads
    # its frames as done again.
    trashed = rolls / library.TRASH / "20261009T000000Z" / "old"
    trashed.mkdir(parents=True)
    (trashed / "roll.json").write_text(json.dumps({"frames": [
        _frame(1, "z")]}), encoding="utf-8")
    assert session.entries_named(rolls) == {"a", "older", "p", "q", "r", "z"}


# -- the session's Delete ----------------------------------------------------------


def _session(tmp_path):
    return session.ScanSession(root=str(tmp_path / "library"),
                               rolls=str(tmp_path / "rolls"),
                               reference=str(tmp_path / "shading.npz"),
                               verbose=False)


def _frame_entry(root, rolls, name="strip", number=3, seed=1):
    folder = rolls / name
    return _entry(root, seed, roll_membership={
        "roll": name, "number": number, "kind": "frame",
        "folder": str(folder)})


def test_the_session_marks_the_frame_then_moves_its_entry(tmp_path):
    scan = _session(tmp_path)
    root, rolls = tmp_path / "library", tmp_path / "rolls"
    entry = _frame_entry(root, rolls)
    for name in ("strip", "strip-copy"):
        (rolls / name).mkdir(parents=True)
        session.write_manifest(rolls / name / "roll.json", {
            "numbering": "strip",
            "frames": [_frame(3, entry), _frame(4, "library\\other")]})
    done = scan.trash_entries([entry], by="the window")
    assert done.trashed.moved == [entry.name]
    assert sorted((p.parent.name, n) for p, n in done.frames) == [
        ("strip", 3), ("strip-copy", 3)]
    for name in ("strip", "strip-copy"):
        progress = json.loads((rolls / name / "roll.json").read_text(
            encoding="utf-8"))
        marker = progress["frames"][0]["entry_deleted"]
        assert marker["id"] == entry.name and marker["by"] == "the window"
        assert marker["batch"] == done.trashed.batch.name
        assert session.scanned_frames(progress, root) == {4}
    library.restore(root, entry.name)
    progress = json.loads((rolls / "strip" / "roll.json").read_text(
        encoding="utf-8"))
    assert session.scanned_frames(progress, root) == {3, 4}, "back, no write"


def test_a_mark_leaves_the_copy_a_run_kept_as_it_was(tmp_path):
    """The `.bak` is the roll as it stood before its last run, for undoing a
    resume that merged wrongly by hand. A Delete copied the file over it,
    merge and all -- and one of two entries passed the first mark along as
    the copy. A roll with no copy still gets one."""
    scan = _session(tmp_path)
    root, rolls = tmp_path / "library", tmp_path / "rolls"
    first = _frame_entry(root, rolls, number=3, seed=1)
    second = _frame_entry(root, rolls, number=4, seed=2)
    folder = rolls / "strip"
    folder.mkdir(parents=True)
    kept = folder / "roll.json.bak"
    before_the_run = json.dumps({"numbering": "strip", "frames": [
        _frame(3, first)]}).encode("utf-8")
    kept.write_bytes(before_the_run)
    session.write_manifest(folder / "roll.json", {
        "numbering": "strip", "frames": [_frame(3, first), _frame(4, second)]})
    done = scan.trash_entries([first, second], by="the window")
    assert sorted(n for _p, n in done.frames) == [3, 4]
    assert kept.read_bytes() == before_the_run, "the run's copy was replaced"
    progress = json.loads((folder / "roll.json").read_text(encoding="utf-8"))
    assert session.scanned_frames(progress, root) == set()
    # A roll that never had one is kept as it stood, once.
    other = rolls / "other"
    other.mkdir()
    third = _frame_entry(root, rolls, name="other", number=1, seed=3)
    session.write_manifest(other / "roll.json", {
        "numbering": "strip", "frames": [_frame(1, third)]})
    unmarked = (other / "roll.json").read_bytes()
    scan.trash_entries([third], by="the window")
    assert (other / "roll.json.bak").read_bytes() == unmarked


def _live_roll(scan, rolls, entries):
    """A roll this session wrote, its manifest kept (`_manifests`), and
    nothing owed to its file: frames 1, 2, ... naming ``entries``."""
    path = rolls / "strip" / "roll.json"
    path.parent.mkdir(parents=True)
    live = session.RollManifest(path, {"numbering": "strip", "frames": [
        _frame(n, entry) for n, entry in enumerate(entries, 1)]})
    live.write()
    scan._manifests[path.resolve()] = live
    assert not live.ahead_of_disk()
    return live, path


def test_a_mark_through_a_roll_renamed_away_and_back_is_kept(tmp_path):
    """A session keeps the manifest of every roll it wrote. The roll was
    renamed, a frame of it deleted there -- marked through the file -- and
    renamed back: the next Delete marked through the manifest, wrote what
    it held over the file, and the first frame read as scanned with its
    entry in the trash."""
    scan = _session(tmp_path)
    root, rolls = tmp_path / "library", tmp_path / "rolls"
    e1 = _frame_entry(root, rolls, number=1, seed=1)
    e2 = _frame_entry(root, rolls, number=2, seed=2)
    _live, path = _live_roll(scan, rolls, [e1, e2])
    away = rolls / "renamed"
    path.parent.rename(away)
    scan.trash_entries([e1], by="the window")
    away.rename(path.parent)
    assert scan.frames_naming([e2]) == [(path, e2.name, [2])]
    scan.trash_entries([e2], by="the window")
    progress = json.loads(path.read_text(encoding="utf-8"))
    assert [bool(f.get("entry_deleted")) for f in progress["frames"]] == [
        True, True]
    assert session.scanned_frames(progress, root) == set()


def test_a_mark_through_a_roll_trashed_and_restored_is_kept(tmp_path):
    scan = _session(tmp_path)
    root, rolls = tmp_path / "library", tmp_path / "rolls"
    e1 = _frame_entry(root, rolls, number=1, seed=1)
    e2 = _frame_entry(root, rolls, number=2, seed=2)
    _live, path = _live_roll(scan, rolls, [e1, e2])
    library.trash(rolls, [path.parent], by="a test", kind="rolls")
    scan.trash_entries([e1], by="the window")
    library.restore(rolls, "strip")
    scan.trash_entries([e2], by="the window")
    progress = json.loads(path.read_text(encoding="utf-8"))
    assert session.scanned_frames(progress, root) == set()


def test_a_mark_during_a_roll_rides_in_its_next_write(tmp_path):
    """Caught up only where the file moved on: a roll in progress keeps
    its own records, and the mark it made rides in its next frame's
    write."""
    scan = _session(tmp_path)
    root, rolls = tmp_path / "library", tmp_path / "rolls"
    e1 = _frame_entry(root, rolls, number=1, seed=1)
    live, path = _live_roll(scan, rolls, [e1])
    held = live.data
    scan.trash_entries([e1], by="the window")
    assert live.data is held, "the roll's own records, not the file's"
    live.record({"number": 3}, awaiting=True)
    progress = json.loads(path.read_text(encoding="utf-8"))
    assert progress["frames"][0]["entry_deleted"]["id"] == e1.name
    assert session.scanned_frames(progress, root) == set()


def test_the_session_refuses_a_frame_still_being_filed(tmp_path):
    scan = _session(tmp_path)
    root, rolls = tmp_path / "library", tmp_path / "rolls"
    entry = _frame_entry(root, rolls)
    path = rolls / "strip" / "roll.json"
    path.parent.mkdir(parents=True)
    live = session.RollManifest(path, {"frames": []})
    live.record({"number": 3}, awaiting=True)
    scan._manifests[path.resolve()] = live
    assert scan.still_filing([entry]) == session.still_filing_said(3)
    with pytest.raises(session.StillFiling):
        scan.trash_entries([entry], by="the window")
    assert entry.exists() and library.trash_batches(root) == []


def test_the_session_refuses_while_another_program_writes_a_roll_naming_it(
        tmp_path):
    from conftest import HeldElsewhere

    scan = _session(tmp_path)
    root, rolls = tmp_path / "library", tmp_path / "rolls"
    entry = _frame_entry(root, rolls)
    (rolls / "strip").mkdir(parents=True)
    session.write_manifest(rolls / "strip" / "roll.json",
                           {"frames": [_frame(3, entry)]})
    with HeldElsewhere(locks.ROLL, rolls / "strip", what="tools/scan_roll.py"):
        with pytest.raises(locks.Refused):
            scan.trash_entries([entry], by="the window")
    assert entry.exists() and library.trash_batches(root) == []
    assert "entry_deleted" not in (rolls / "strip" / "roll.json").read_text(
        encoding="utf-8")


def test_compaction_at_close_passes_over_what_went_to_the_trash(tmp_path):
    """NEW-T4: an entry filed plain and moved to the trash before the scanner
    closed -- or a roll's unfiled/ pass, gone with its folder -- was said as
    "could not compress ... it stays uncompressed and complete"."""
    root = tmp_path / "library"
    entry = _entry(root)
    library.trash(root, [entry], by="a test")
    said = []
    session.compact_after_close([entry, tmp_path / "rolls" / "x" / "unfiled"
                                 / "y"], said.append)
    assert len(said) == 2
    assert all("is no longer there" in line for line in said)
    assert not any("could not compress" in line for line in said)


def test_a_roll_is_never_given_the_trash_for_a_folder(tmp_path):
    """NEW-T2: a name that is there as typed is used as it is, and the
    rolls' trash is there -- so a roll typed ".trash" scanned into it."""
    rolls = tmp_path / "rolls"
    (rolls / library.TRASH).mkdir(parents=True)
    (rolls / ".kept").mkdir()
    assert session.roll_dir(rolls, ".trash") != rolls / library.TRASH
    assert not session.roll_dir(rolls, ".trash").name.startswith(".")
    assert session.roll_dir(rolls, ".kept") != rolls / ".kept"


# -- a roll of strips ----------------------------------------------------------
#
# Stefan's call, 2026-10-10: one roll folder per film, a folder per strip in
# it (`library.STRIP_FOLDER`), and the roll's own index of the numbers each
# strip was given (`library.STRIPS_INDEX`). Everything that reads the rolls
# reads each strip as it reads a roll from before strips
# (`library.roll_folders`); a strip deleted on its own goes into the one
# rolls' trash and comes back into its own roll and nowhere else.


def _strips(rolls, name="Gold200", ranges=((1, 1, 6), (2, 7, None)),
            frames=None):
    """A roll of strips under ``rolls``: its index listing each strip of
    ``ranges`` -- (strip, first, last) -- and each strip's survey.json
    carrying the copy of its claim, with ``frames`` (strip -> records) as
    its frames."""
    roll = rolls / name
    roll.mkdir(parents=True)
    listed = []
    for strip, first, last in ranges:
        folder = roll / library.STRIP_FOLDER.format(strip)
        folder.mkdir()
        session.write_manifest(folder / "survey.json", {
            "roll": name, "numbering": session.NUMBERING,
            "strip": {"number": strip, "first": first},
            "frames": list((frames or {}).get(strip) or [])})
        listed.append({"strip": strip, "folder": folder.name, "first": first,
                       "last": last})
    session.write_manifest(roll / library.STRIPS_INDEX, {
        "kind": "rps7200 roll of strips", "version": 1, "roll": name,
        "strips": listed, "extra": []})
    return roll


def _legacy(rolls, name):
    folder = rolls / name
    folder.mkdir(parents=True)
    session.write_manifest(folder / "survey.json", {"roll": name,
                                                    "frames": []})
    return folder


def test_a_folder_is_one_of_four_kinds(tmp_path):
    rolls = tmp_path / "rolls"
    roll = _strips(rolls)
    legacy = _legacy(rolls, "2026-10-08-224648")
    (rolls / "decided").mkdir()
    (rolls / "decided" / "approved.json").write_text("{}", encoding="utf-8")
    (rolls / "study").mkdir()
    (rolls / "study" / "A01_p1.tif").write_bytes(b"x")
    (rolls / "empty").mkdir()
    (rolls / "empty" / ".survey.json.1-2.part").write_bytes(b"")
    assert library.roll_kind(roll) == "strips"
    assert library.roll_kind(legacy) == "legacy"
    assert library.roll_kind(rolls / "decided") == "legacy"
    assert library.roll_kind(rolls / "study") == "other"
    assert library.roll_kind(rolls / "empty") == "new"
    assert library.roll_kind(rolls / "not-there") == "new"
    # Only the copy of its index left: still a roll of strips, which a
    # survey.json an older build typed into it does not change.
    (roll / library.STRIPS_INDEX).rename(roll / "strips.json.bak")
    (roll / "survey.json").write_text("{}", encoding="utf-8")
    assert library.roll_kind(roll) == "strips"
    assert library.strip_number("strip-03") == 3
    assert library.strip_number("Strip-100") == 100
    assert library.strip_number("strip-00") is None
    assert library.strip_number("strip-3b") is None


def test_every_folder_of_records_is_listed_once_and_no_roll_of_strips(
        tmp_path):
    rolls = tmp_path / "rolls"
    roll = _strips(rolls, ranges=((1, 1, 6), (2, 7, 12), (10, 13, None)))
    legacy = _legacy(rolls, "2026-10-08-224648")
    (rolls / ".kept").mkdir()
    # A strip folder with nothing in it and not listed is no strip.
    (roll / "strip-04").mkdir()
    gone = library.trash(rolls, [roll / "strip-02"], by="a test",
                         kind="strips")
    other = _strips(rolls, name="Fuji")
    whole = library.trash(rolls, [other], by="a test", kind="rolls")
    assert library.roll_folders(rolls, trash=False) == [
        legacy, roll / "strip-01", roll / "strip-10"]
    assert library.roll_folders(rolls) == [
        legacy, roll / "strip-01", roll / "strip-10",
        gone.batch / "strip-02",
        whole.batch / "Fuji" / "strip-01", whole.batch / "Fuji" / "strip-02"]


def test_a_strip_is_locked_by_its_roll_and_nothing_else_by_another(
        tmp_path):
    rolls = tmp_path / "rolls"
    roll = _strips(rolls)
    legacy = _legacy(rolls, "strip-03")
    assert library.lock_folder(roll / "strip-02") == roll
    assert library.lock_folder(roll / "strip-07") == roll, "before it is made"
    assert library.lock_folder(roll) == roll
    assert library.lock_folder(legacy) == legacy
    assert library.lock_folder(rolls / "x" / "strip-01") == \
        rolls / "x" / "strip-01"


def test_a_strip_goes_into_the_rolls_trash_with_its_roll_and_numbers(
        tmp_path):
    rolls = tmp_path / "rolls"
    roll = _strips(rolls, ranges=((1, 1, 6), (2, 7, 12), (3, 13, None)))
    before = _bytes_of(roll / "strip-02")
    moved = library.trash(rolls, [roll / "strip-02"], by="a test",
                          kind="strips")
    assert moved.batch.parent == rolls / library.TRASH
    assert _bytes_of(moved.batch / "strip-02") == before
    said = json.loads((moved.batch / library.TRASH_RECORD).read_text(
        encoding="utf-8"))
    assert said["kind"] == "strips" and said["within"] == "Gold200"
    (item,) = said["items"]
    assert (item["name"], item["strip"], item["first"], item["last"]) == (
        "strip-02", 2, 7, 12)
    (batch,) = library.trash_batches(rolls)
    assert batch.kind == "strips" and batch.within == "Gold200"
    # The index keeps the strip: its numbers stay given.
    index = json.loads((roll / library.STRIPS_INDEX).read_text(
        encoding="utf-8"))
    assert [s["strip"] for s in index["strips"]] == [1, 2, 3]
    assert "1 strip (" in library.trash_said(None, rolls)


def test_only_strips_of_one_roll_of_strips_go_as_strips(tmp_path):
    rolls = tmp_path / "rolls"
    gold = _strips(rolls)
    fuji = _strips(rolls, name="Fuji")
    legacy = _legacy(rolls, "strip-03")
    (gold / ".strip-05").mkdir()
    (tmp_path / "elsewhere" / "Gold200" / "strip-01").mkdir(parents=True)
    refused = [
        [legacy],                                   # no roll of strips
        [gold],                                     # a roll, not a strip
        [gold / "strip-01", fuji / "strip-01"],     # two rolls
        [gold / ".strip-05"],                       # a dot name
        [tmp_path / "elsewhere" / "Gold200" / "strip-01"],
    ]
    for paths in refused:
        with pytest.raises(ValueError, match="nothing was moved"):
            library.trash(rolls, paths, by="a test", kind="strips")
    assert library.trash_batches(rolls) == []
    assert (gold / "strip-01").is_dir() and (fuji / "strip-01").is_dir()


def test_a_strip_comes_back_into_its_own_roll_and_nowhere_else(tmp_path):
    rolls = tmp_path / "rolls"
    roll = _strips(rolls, ranges=((1, 1, 6), (2, 7, 12), (3, 13, None)))
    before = _bytes_of(roll / "strip-03")
    moved = library.trash(rolls, [roll / "strip-03"], by="a test",
                          kind="strips")
    done = library.restore(rolls, "strip-03")
    assert done.restored == [(moved.batch.name, "strip-03",
                              "Gold200/strip-03")]
    assert _bytes_of(roll / "strip-03") == before
    assert library.trash_batches(rolls) == []

    # Its place taken since: left in the trash, said, never strip-03-2.
    library.trash(rolls, [roll / "strip-03"], by="a test", kind="strips")
    (roll / "strip-03").mkdir()
    done = library.restore(rolls, "Gold200/strip-03")
    assert done.restored == [] and "is there again" in done.refused["strip-03"]
    assert not (roll / "strip-03-2").exists()
    (roll / "strip-03").rmdir()

    # The index of its roll no longer listing it: no strip of that roll.
    index = json.loads((roll / library.STRIPS_INDEX).read_text(
        encoding="utf-8"))
    session.write_manifest(roll / library.STRIPS_INDEX, dict(
        index, strips=[s for s in index["strips"] if s["strip"] != 3]))
    for kept in roll.glob("strips.json.bak"):
        kept.unlink()
    done = library.restore(rolls, "strip-03")
    assert "does not list strip-03" in done.refused["strip-03"]
    session.write_manifest(roll / library.STRIPS_INDEX, index)

    # Its roll gone -- renamed, or in the trash itself.
    roll.rename(rolls / "Gold200-renamed")
    done = library.restore(rolls, "strip-03")
    assert "restore the roll first" in done.refused["strip-03"]
    assert "by hand" in done.refused["strip-03"]
    (batch,) = library.trash_batches(rolls)
    assert batch.names == ["strip-03"]


def test_a_strip_stays_in_the_trash_while_another_program_has_its_roll(
        tmp_path):
    from conftest import HeldElsewhere

    rolls = tmp_path / "rolls"
    roll = _strips(rolls)
    library.trash(rolls, [roll / "strip-02"], by="a test", kind="strips")
    with HeldElsewhere(locks.ROLL, roll, what="tools/scan_roll.py"):
        done = library.restore(rolls, "strip-02")
    assert done.restored == []
    assert "tools/scan_roll.py" in done.refused["strip-02"]
    assert not (roll / "strip-02").exists()
    assert library.restore(rolls, "strip-02").restored


def test_a_strips_name_alone_is_not_enough_for_two_rolls(tmp_path):
    rolls = tmp_path / "rolls"
    gold = _strips(rolls)
    fuji = _strips(rolls, name="Fuji")
    library.trash(rolls, [gold / "strip-02"], by="a test", kind="strips")
    library.trash(rolls, [fuji / "strip-02"], by="a test", kind="strips")
    with pytest.raises(library.NamedTwice) as raised:
        library.restore(rolls, "strip-02")
    assert isinstance(raised.value, LookupError)
    assert "Gold200" in str(raised.value) and "Fuji" in str(raised.value)
    assert library.restore(rolls, "Fuji/strip-02").restored
    assert (fuji / "strip-02").is_dir() and not (gold / "strip-02").exists()
    assert library.restore(rolls, "strip-02").restored


def test_a_strip_whose_delete_lost_its_record_is_known_and_left(tmp_path):
    """Known by the claim its own records carry, never by its name: a roll
    from before strips that someone called strip-03 is a roll."""
    rolls = tmp_path / "rolls"
    roll = _strips(rolls)
    moved = library.trash(rolls, [roll / "strip-02"], by="a test",
                          kind="strips")
    (moved.batch / library.TRASH_RECORD).unlink()
    (batch,) = library.trash_batches(rolls)
    assert batch.kind == "strips" and batch.within is None
    done = library.restore(rolls, moved.batch.name)
    assert "cannot say which roll" in done.refused["strip-02"]
    assert (moved.batch / "strip-02").is_dir()

    legacy = _legacy(rolls, "strip-03")
    other = library.trash(rolls, [legacy], by="a test", kind="rolls")
    (other.batch / library.TRASH_RECORD).unlink()
    kinds = {b.name: b.kind for b in library.trash_batches(rolls)}
    assert kinds[other.batch.name] == "rolls"
    assert library.restore(rolls, other.batch.name).restored == [
        (other.batch.name, "strip-03", "strip-03")]


def test_a_roll_whose_name_another_program_holds_comes_back_beside_it(
        tmp_path):
    """P23-b: a restore filled a name the window had just found free, or one
    a `tools/scan_roll.py` held before making its folder. Each name is held
    while the roll goes in under it, and a holder's name is passed over and
    said."""
    from conftest import HeldElsewhere

    rolls = tmp_path / "rolls"
    roll = _legacy(rolls, "strip")
    moved = library.trash(rolls, [roll], by="a test", kind="rolls")
    with HeldElsewhere(locks.ROLL, rolls / "strip", what="tools/scan_roll.py"):
        done = library.restore(rolls, "strip")
    assert done.restored == [(moved.batch.name, "strip", "strip-2")]
    assert "tools/scan_roll.py" in done.notes["strip-2"]
    assert not (rolls / "strip").exists()


def test_unfiled_passes_in_a_deleted_strip_are_never_emptied(tmp_path):
    root, rolls = tmp_path / "library", tmp_path / "rolls"
    _entry(root)
    gold = _strips(rolls)
    fuji = _strips(rolls, name="Fuji")

    def kept_in(folder):
        kept, refused = session.keep_unfiled(
            np.zeros((6, 8, 3), np.uint16),
            {"resolution_dpi": 300, "channels": 3, "width": 8, "height": 6,
             "depth": 16}, near=[folder], refused_by=root, why="a test")
        assert kept is not None and kept.parent == folder / "unfiled", refused
        return kept

    in_strip = kept_in(gold / "strip-02")
    in_roll = kept_in(fuji / "strip-01")
    live = {i.path.resolve() for i in library.leftovers(root, rolls=rolls)
            if i.kind == "unfiled"}
    assert live == {in_strip.resolve(), in_roll.resolve()}
    strip = library.trash(rolls, [gold / "strip-02"], by="a test",
                          kind="strips")
    whole = library.trash(rolls, [fuji], by="a test", kind="rolls")
    found = {}
    for batch, kept in library.unfiled_in_trash(rolls):
        found.setdefault(batch.name, []).append(kept.name)
    assert found == {strip.batch.name: [in_strip.name],
                     whole.batch.name: [in_roll.name]}
    # Said, by the pointers alone too, with what brings each back.
    for where in ({"rolls": rolls}, {}):
        said = {i.path.name: i for i in library.leftovers(root, **where)
                if i.kind == "unfiled"}
        assert set(said) == {in_strip.name, in_roll.name}, where
        assert all(i.problem for i in said.values())
    done = library.empty_trash(rolls, write=True)
    assert done.batches == [] and set(done.unfiled) == {strip.batch.name,
                                                       whole.batch.name}
    done = library.empty_trash(rolls, write=True, include_unfiled=True)
    assert len(done.batches) == 2


def test_what_a_roll_of_strips_left_is_named_strip_by_strip(tmp_path):
    rolls = tmp_path / "rolls"
    roll = _strips(rolls)
    (roll / "strip-01" / ".roll.json.1-2.part").write_bytes(b"")
    (roll / "strip-02" / "roll.json.unreadable").write_bytes(b"{")
    (roll / "strips.json.unreadable").write_bytes(b"{")
    (roll / "survey.json").write_text("{}", encoding="utf-8")
    unlisted = roll / "strip-05"
    unlisted.mkdir()
    (unlisted / "roll.json").write_text("{}", encoding="utf-8")
    found = {(i.kind, i.path.relative_to(rolls).as_posix())
             for i in library.leftovers(tmp_path / "library", rolls=rolls)}
    assert found >= {
        ("stray-part", "Gold200/strip-01/.roll.json.1-2.part"),
        ("unreadable-manifest", "Gold200/strip-02/roll.json.unreadable"),
        ("unreadable-manifest", "Gold200/strips.json.unreadable"),
        ("strips-stray", "Gold200"),
        ("strips-unlisted", "Gold200/strip-05"),
    }, found
    said = library.leftovers_said(library.leftovers(tmp_path / "library",
                                                    rolls=rolls))
    assert "strip its roll does not list" in said


def test_a_roll_of_strips_copy_is_judged_by_the_records_inside_it(tmp_path):
    rolls = tmp_path / "rolls"
    copy = _strips(rolls, name="Gold200-2")
    (copy / "strips.json").unlink()
    (copy / "strips.json.bak").unlink(missing_ok=True)
    (copy / library.INCOMPLETE).write_text(json.dumps({
        "from": str(rolls / "Gold200"),
        "records": ["strip-01/survey.json", "strip-02/survey.json",
                    "strips.json"]}), encoding="utf-8")
    (cut,) = [i for i in library.leftovers(tmp_path / "library", rolls=rolls)
              if i.path == copy]
    assert cut.kind == "roll-copy", "the index is copied last of all"
    session.write_manifest(copy / "strips.json", {"strips": []})
    (whole,) = [i for i in library.leftovers(tmp_path / "library",
                                             rolls=rolls) if i.path == copy]
    assert whole.kind == "roll-copied"
    assert not library._inside_copy(copy, "../Gold200/strips.json")


def test_entries_named_and_roll_files_read_every_strip(tmp_path):
    """The `duplicates --delete` guard and the trash marks read one level
    deep: every strip's entries were named by nothing, and a strip's frame
    whose entry went was never marked."""
    rolls = tmp_path / "rolls"
    gold = _strips(rolls, ranges=((1, 1, 6), (2, 7, 12), (3, 13, None)))
    for n, name in ((1, "a"), (2, "b"), (3, "c")):
        session.write_manifest(gold / f"strip-0{n}" / "roll.json", {
            "numbering": "strip", "frames": [_frame(1, f"library\\{name}")]})
    strip = library.trash(rolls, [gold / "strip-03"], by="a test",
                          kind="strips")
    fuji = _strips(rolls, name="Fuji", ranges=((1, 1, None),))
    session.write_manifest(fuji / "strip-01" / "roll.json", {
        "numbering": "strip", "frames": [_frame(1, "library\\z")]})
    whole = library.trash(rolls, [fuji], by="a test", kind="rolls")
    assert session.entries_named(rolls) == {"a", "b", "c", "z"}
    scan = _session(tmp_path)
    files = {p.resolve() for p in scan._roll_files([])}
    assert files == {p.resolve() for p in (
        gold / "strip-01" / "roll.json", gold / "strip-02" / "roll.json",
        strip.batch / "strip-03" / "roll.json",
        whole.batch / "Fuji" / "strip-01" / "roll.json")}
