#!/usr/bin/env python3
"""Inspect the scan library.

    uv run python tools/library.py list
    uv run python tools/library.py verify
    uv run python tools/library.py reconstruct        # re-decode every entry
    uv run python tools/library.py duplicates         # what is redundant, and why
    uv run python tools/library.py duplicates --delete
    uv run python tools/library.py migrate-direction  # which way each pass was read
    uv run python tools/library.py compact            # gzip what a window left plain
    uv run python tools/library.py calibrations       # re-reduce every calibration
    uv run python tools/library.py tag ENTRY... --add uncalibrated-on-purpose

`reconstruct` is the one worth running after any change to how the scanner's
bytes become pixels: it decodes every stored pass with today's code and says
which entries no longer match what was saved.

`calibrations` is its counterpart for the reference: every calibration's own
lines, archived under `--calibrations` (default `calibration/`), are reduced
again with today's `calculate_shading` and compared with the reference kept
beside them. Worth running after any change to how calibration lines become
a reference. Given ENTRY ids, it says instead which archived calibration
each entry's reference came from.

`duplicates` finds entries that are the same scan of the same picture at the
same protocol revision *and* hold the same bytes -- the scanner was driven
identically and answered identically, so one of them holds nothing the other
does not. Entries asked for identically whose data differs are different
photographs (or different passes of one) and are never offered for deletion.
It only reports; `--delete` is what removes them, and `--keep N` leaves more
than one of each behind.

`migrate-direction` brings entries filed before passes were read upright from
their own line tags up to date: every entry records which way the carriage
read it, a scan stored bottom-up is turned upright from its raw bytes, and a
stored `prescan.tif` is judged against its upright scan and turned only when
that is decisive. A dry run unless given `--write`.

`compact` gzips the entries a window filed plain -- `raw.bin` and uncompressed
TIFFs, written that way because compressing with the scanner open preceded a
wedge -- and was killed before it compacted them itself, and finishes any
compaction a kill stopped part way. Run it with the scanner closed, for the
same reason. A dry run unless given `--write`.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rps7200 import library
from rps7200.console import use_utf8_stdout
from rps7200.protocol import ScanReadError


#: What `migrate-raw --write` keeps of the file it replaces.
KEPT = "scan.before-migrate-raw.tif"


def _one_shading_explains(path: Path, plain, stored) -> bool:
    """Whether the stored pixels are this decode with the entry's shading once.

    That is what a legacy entry is, labelled or not -- corrected pixels -- and
    the only difference `migrate-raw` may repair. Anything else is a decode
    that changed, or an entry with no reference to prove it did not.
    """
    import zipfile

    import numpy as np

    from rps7200.shading import apply_shading

    try:
        _image, record = library.load(path)
    except (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile):
        return False
    if record.get("reference") is None:
        return False
    shaded, _ = apply_shading(plain, record["reference"], record.get("ccd_mask"))
    return bool(np.array_equal(shaded, stored) and shaded.dtype == stored.dtype)


def main() -> int:
    use_utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action",
                    choices=["list", "verify", "reconstruct", "reindex",
                             "duplicates", "migrate-raw", "migrate-direction",
                             "compact", "calibrations", "tag"])
    ap.add_argument("entries", nargs="*", metavar="ENTRY",
                    help="tag: the entry ids (directory names) to tag; "
                         "calibrations: the entries to find calibrations for")
    ap.add_argument("--add", action="append", default=[], metavar="TAG",
                    help=f"tag: a tag to add, e.g. {library.ON_PURPOSE} for an "
                         "entry taken without a reference on purpose")
    ap.add_argument("--root", default="library")
    ap.add_argument("--calibrations", default=str(library.DEFAULT_CALIBRATIONS),
                    help="calibrations: where the calibrations are archived "
                         "(default %(default)s)")
    ap.add_argument("--delete", action="store_true",
                    help="duplicates: actually remove them (default is a dry run)")
    ap.add_argument("--write", action="store_true",
                    help="migrate-raw, migrate-direction, compact: actually "
                         "rewrite the entries (default is a dry run)")
    ap.add_argument("--keep", type=int, default=1, metavar="N",
                    help="duplicates: how many of each group to keep (default 1). "
                         "Use 2 to retain a pair for pass-to-pass comparisons")
    args = ap.parse_args()
    root = Path(args.root)

    # Re-reducing the archived calibrations needs no library at all.
    if not root.exists() and not (args.action == "calibrations"
                                  and not args.entries):
        print(f"no library at {root}", file=sys.stderr)
        return 1

    if args.action == "list":
        rows = library.entries(root)
        if not rows:
            print("library is empty")
            return 0
        print(f"{'id':52} {'dpi':>5} {'ch':>3}  film / notes")
        for r in rows:
            scan, film = r.get("scan") or {}, r.get("film") or {}
            raw = "raw" if (r.get("raw") or {}).get("file") else "NO RAW"
            desc = " / ".join(x for x in (film.get("stock"), film.get("notes")) if x)
            # `or ''` rather than a `get` default: these keys are *present* and
            # hold None on entries filed before they were recorded -- 69 of the
            # 311 here -- so a default never fires and the format spec raises on
            # NoneType. `list` died 56 rows in, which is the one command that is
            # meant to survey a library that has grown over time.
            print(f"{r.get('id') or '':52} "
                  f"{scan.get('resolution_dpi') or '':>5} "
                  f"{scan.get('channels') or '':>3}  {desc}  [{raw}]")
        print(f"\n{len(rows)} entries")

    elif args.action == "tag":
        if not args.entries or not args.add:
            print("tag needs entry ids and at least one --add TAG",
                  file=sys.stderr)
            return 2
        for name in args.entries:
            path = root / name
            if not (path / "scan.json").exists():
                print(f"! {name}: no such entry", file=sys.stderr)
                return 1
            print(f"{name}: {', '.join(library.add_tags(path, args.add))}")
        library.reindex(root)
        return 0

    elif args.action == "verify":
        problems = library.verify(root)
        for p in problems:
            print(p)
        print(f"\n{len(problems)} problem(s)" if problems else "\nlibrary is intact")
        return 1 if problems else 0

    elif args.action == "reconstruct":
        # An entry with no bytes to decode is not a decode regression, and
        # counting it as one turns this check into a metric that cries wolf --
        # the summary read "6 entries no longer decode to what was stored"
        # when all six simply had nothing stored to decode.
        #
        # The opposite mistake is worse, and it was made: a decode that now
        # raises, a scan.tif that no longer reads and raw bytes that are there
        # and corrupt were all counted as "nothing to decode from", and the
        # exit was 0. A change that broke the decode of every entry passed the
        # one check named for it. Only an entry with nothing stored is benign.
        changed = unreadable = behind = failed = damaged = 0
        for r in library.entries(root):
            path = library.entry_path(root, r)
            _, verdict = library.reconstruct(path)
            kind = verdict.kind
            if kind == library.IDENTICAL:
                mark = " "
            elif kind == library.NOTHING:
                mark, unreadable = "-", unreadable + 1
            elif kind == library.BEHIND:
                # Filed before passes were turned upright: known, not a
                # regression, and `migrate-direction` brings it up to date.
                # Counted as changed, it was a false alarm on every such entry.
                mark, behind = "~", behind + 1
            elif kind == library.FAILED:
                mark, failed = "!", failed + 1
            elif kind == library.DAMAGED:
                mark, damaged = "!", damaged + 1
            else:
                mark, changed = "!", changed + 1
            print(f"{mark} {path.name}: {verdict}")
        print(f"\n{changed} entr{'y' if changed == 1 else 'ies'} no longer "
              f"decode to what was stored" if changed
              else "\nevery entry that could be checked still decodes to "
                   "exactly what was stored")
        if failed:
            print(f"{failed} could not be decoded by today's code -- a decode "
                  f"regression until shown otherwise")
        if damaged:
            print(f"{damaged} could not be checked because a stored file is "
                  f"damaged or unreadable -- run verify")
        if unreadable:
            print(f"{unreadable} had nothing to decode from -- not a "
                  f"regression, but they cannot be re-corrected either")
        if behind:
            print(f"{behind} stored bottom-up from before passes were turned "
                  f"upright -- not a regression; see migrate-direction")
        return 1 if changed or failed or damaged else 0

    elif args.action == "duplicates":
        if args.keep < 1:
            print("--keep must be at least 1", file=sys.stderr)
            return 2
        doomed = library.prunable(root, keep=args.keep)
        if not doomed:
            print(f"no duplicates (keeping {args.keep} of each group)")
            return 0

        freed = 0
        for record, reason in doomed:
            path = library.entry_path(root, record)
            size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            freed += size
            print(f"{'removing' if args.delete else 'redundant'}: {path.name}"
                  f"  ({size / 1e6:.1f} MB)\n    {reason}")
            if args.delete:
                shutil.rmtree(path)

        print(f"\n{len(doomed)} entr{'y' if len(doomed) == 1 else 'ies'}, "
              f"{freed / 1e6:.1f} MB"
              + (" removed" if args.delete else " would be freed -- pass --delete"))
        if args.delete:
            library.reindex(root)

    elif args.action == "migrate-raw":
        # Entries hold raw pixels and recompute the correction on the way out.
        # Ones filed before that hold corrected pixels, and two kinds of them
        # are wrong in different ways:
        #
        #   labelled     `corrections_applied: ["shading"]` -- self-consistent,
        #                but not raw, so the correction can never be improved
        #                on them and `reconstruct` has to re-apply shading to
        #                compare at all.
        #   mislabelled  `corrections_applied: []` on pixels that are corrected
        #                -- these describe themselves wrongly, and `reconstruct`
        #                calls every one a changed decode.
        #
        # Both are repaired the same way and without guessing: the raw bytes
        # are still there, so scan.tif is rewritten from them. Nothing is
        # inferred and nothing is inverted -- a corrected image is never
        # un-corrected, it is simply replaced by a fresh decode of the bytes it
        # came from, which is what should have been stored.
        import numpy as np

        from rps7200 import tiff

        planned, skipped, failed = [], [], []
        for r in library.entries(root):
            path = library.entry_path(root, r)
            applied = (r.get("image") or {}).get("corrections_applied") or []
            decoded, verdict = library.reconstruct(path)
            if decoded is None:
                skipped.append((path.name, verdict))
                continue
            try:
                stored = tiff.read(str(path / "scan.tif"))
            except (OSError, ValueError) as exc:
                failed.append((path.name, f"cannot read scan.tif: {exc}"))
                continue
            # `reconstruct` hands back the decode already re-corrected when the
            # record says the pixels are corrected, so decode again plainly.
            plain = library.decode_raw(path)
            if plain is None:
                skipped.append((path.name, "no raw bytes to decode"))
                continue
            if plain.shape != stored.shape:
                failed.append((path.name,
                               f"decode is {plain.shape}, stored {stored.shape}"))
                continue
            if np.array_equal(plain, stored) and not applied:
                continue                       # already raw and says so
            kept = path / KEPT
            if (applied and kept.exists()
                    and np.array_equal(plain, stored) and plain.dtype == stored.dtype):
                # A run stopped after the fresh decode was swapped in and
                # before the record said so: raw pixels labelled corrected,
                # which `corrected()` hands out as "already" corrected. The
                # corrected original is the kept file; if one shading of this
                # decode is exactly it, only the record is left to write.
                try:
                    before = tiff.read(str(kept))
                except (OSError, ValueError) as exc:
                    failed.append((path.name, f"cannot read {KEPT}: {exc}"))
                    continue
                if not _one_shading_explains(path, plain, before):
                    failed.append((path.name,
                                   f"scan.tif is the plain decode but the record "
                                   f"says corrected, and {KEPT} is not this "
                                   f"decode shaded once; left alone"))
                    continue
                planned.append((path, plain, applied, "finish"))
                continue
            if not _one_shading_explains(path, plain, stored):
                # Not corrected pixels at all: a decode that no longer
                # reproduces what was stored, or a reference that cannot say.
                # That is the regression `reconstruct` exists to report, and
                # rewriting would launder it into the library for good.
                #
                # Labelled entries too. They were rewritten on the label's
                # word alone, so a labelled entry whose decode had changed --
                # or whose bytes were another pass's, the stale-raw case --
                # became today's decode of it, labelled raw, and `reconstruct`
                # then called it identical.
                failed.append((path.name,
                               "stored pixels are neither this decode nor this "
                               "decode shaded once with the entry's own "
                               "reference -- a decode change, or no reference "
                               "to prove it; left alone"))
                continue
            if kept.exists():
                # Never over the one corrected rendition a rewrite keeps.
                try:
                    same = np.array_equal(tiff.read(str(kept)), stored)
                except (OSError, ValueError):
                    same = False
                if not same:
                    failed.append((path.name,
                                   f"{KEPT} already holds another picture; "
                                   f"left alone"))
                    continue
            planned.append((path, plain, applied, "rewrite"))

        for path, _plain, applied, how in planned:
            why = ("finishing a rewrite an earlier run was stopped in"
                   if how == "finish"
                   else "mislabelled: corrected pixels filed as raw" if not applied
                   else "corrected pixels, and the correction cannot be improved")
            print(f"{'rewriting' if args.write else 'would rewrite'}: "
                  f"{path.name}\n    {why}")

        if args.write:
            for path, plain, _applied, how in planned:
                record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
                resolution = ((record.get("scan") or {}).get("resolution_dpi")
                              or None)
                kept = path / KEPT
                if how == "rewrite":
                    # Written beside and swapped in, and the old file kept: it
                    # may be the only corrected rendition the entry has. Kept
                    # by copying, not moving, so scan.tif is whole at every
                    # moment -- the move left an entry with no scan.tif at all
                    # when a run stopped between it and the swap -- and only
                    # where there is no kept file yet, which a re-run found
                    # and overwrote with the raw decode.
                    fresh = path / ".scan.tif.part"
                    tiff.write(str(fresh), plain, resolution=resolution)
                    if not kept.exists():
                        copy = path / f".{KEPT}.part"
                        shutil.copyfile(path / "scan.tif", copy)
                        library._replace(copy, kept)
                    library._replace(fresh, path / "scan.tif")
                image = record.setdefault("image", {})
                image["corrections_applied"] = []
                image["shape"] = list(plain.shape)
                image["dtype"] = str(plain.dtype)
                image["sha256"] = library._sha256(path / "scan.tif")
                image["replaced"] = KEPT
                # Checksummed like every other file, so `verify` sees damage
                # to the corrected original as it does to the rest.
                record.setdefault("files", {})[KEPT] = library._sha256(kept)
                library._write_atomic(path / "scan.json",
                                      json.dumps(record, indent=2, default=str))
            library.reindex(root)

        print(f"\n{len(planned)} entr{'y' if len(planned) == 1 else 'ies'} "
              + ("rewritten to raw pixels" if args.write
                 else "would be rewritten to raw pixels -- pass --write"))
        for name, why in failed:
            print(f"! {name}: {why}")
        if skipped:
            print(f"{len(skipped)} could not be decoded and were left alone")
        return 1 if failed else 0

    elif args.action == "migrate-direction":
        # One level deep, like every other command here: a library kept in
        # subfolders is migrated one subfolder at a time.
        turned = recorded = left = 0
        for scan_json in sorted(root.glob("*/scan.json")):
            path = scan_json.parent
            try:
                done = library.migrate_direction(path, write=args.write)
            # ScanReadError too: bytes whose tags the decode cannot place are
            # this entry's problem, and one of them ended the run part way,
            # after rewriting the entries before it and before the reindex.
            except (OSError, ValueError, KeyError, ScanReadError) as exc:
                print(f"! {path.name}: {exc}")
                left += 1
                continue
            for line in done:
                mark = "~" if "turned upright" in line else (
                    "!" if "left alone" in line else " ")
                turned += mark == "~"
                left += mark == "!"
                recorded += mark == " "
                print(f"{mark} {path.name}: {line}")
        verb = "" if args.write else "would be "
        print(f"\n{turned} {verb}turned upright, {recorded} {verb}recorded as "
              f"they are, {left} left alone"
              + ("" if args.write else " -- pass --write"))
        if args.write:
            library.reindex(root)

    elif args.action == "compact":
        # The window files single scans plain and compacts them once its
        # device has closed; a window killed before that left them plain for
        # good, at about twice their size, with nothing that would ever
        # finish the job -- or finish one a kill had stopped part way, whose
        # swapped TIFF then failed its checksum in verify on every run.
        plain = sorted(p.parent for p in root.glob(f"*/{library.RAW_PLAIN}")
                       if (p.parent / "scan.json").exists())
        done = failed = 0
        for path in plain:
            if not args.write:
                print(f"would compact: {path.name}")
                continue
            try:
                library.compact(path)
                done += 1
                print(f"compacted: {path.name}")
            except (OSError, ValueError, KeyError) as exc:
                failed += 1
                print(f"! {path.name}: {exc}")
        print(f"\n{len(plain)} entr{'y' if len(plain) == 1 else 'ies'} held "
              f"plain raw bytes"
              + (f"; {done} compacted" if args.write else " -- pass --write"))
        if args.write and done:
            library.reindex(root)
        return 1 if failed else 0

    elif args.action == "calibrations":
        if args.entries:
            # Which calibration's lines each entry's reference is a reduction
            # of -- by the path the record names, or, for a reference loaded
            # from the cache, which names none, by its content.
            search = (Path(args.calibrations), root.parent / "calibration")
            lost = 0
            for name in args.entries:
                path = root / name
                if not (path / "scan.json").exists():
                    print(f"! {name}: no such entry", file=sys.stderr)
                    return 1
                found = library.calibration_of(path, search=search)
                lost += found is None
                print(f"{name}: {found if found is not None else 'no archived calibration found'}")
            return 1 if lost else 0
        folders = library.calibrations(args.calibrations)
        if not folders:
            print(f"no archived calibrations under {args.calibrations}")
            return 0
        bad = 0
        for folder in folders:
            _, verdict = library.recalibrate(folder)
            kind = verdict.kind
            mark = {library.IDENTICAL: " ", library.NOTHING: "-"}.get(kind, "!")
            bad += mark == "!"
            print(f"{mark} {folder.name}: {verdict}")
        print(f"\n{bad} of {len(folders)} no longer reduce to the reference kept"
              if bad else f"\nall {len(folders)} still reduce to the reference "
              "kept beside them, or had none to compare")
        return 1 if bad else 0

    elif args.action == "reindex":
        print(f"wrote {library.reindex(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
