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
    trashed = rolls / library.TRASH / "20261009T000000Z" / "old"
    trashed.mkdir(parents=True)
    (trashed / "roll.json").write_text(json.dumps({"frames": [
        _frame(1, "z")]}), encoding="utf-8")
    assert session.entries_named(rolls) == {"a", "older", "p", "q", "r"}


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
