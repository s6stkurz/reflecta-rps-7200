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
    uv run python tools/library.py migrate-calibrations  # copy old ones into the library
    uv run python tools/library.py tag ENTRY... --add uncalibrated-on-purpose
    uv run python tools/library.py file-spool [SPOOL...]  # what was left unfiled

`reconstruct` is the one worth running after any change to how the scanner's
bytes become pixels: it decodes every stored pass with today's code and says
which entries no longer match what was saved. A pass filed tagged `failed`
because its bytes would not decode is said on a line of its own, marked `x`,
or `+` where today's decode reads them, and fails nothing.

`calibrations` is its counterpart for the reference: every calibration's own
lines -- kept in the library under `calibrations/<id>`, and the older ones
still under `--calibrations` (default `calibration/`) -- are reduced again
with today's `calculate_shading` and compared with the reference kept beside
them. Worth running after any change to how calibration lines become a
reference. Given ENTRY ids, it says instead which archived calibration each
entry's reference came from.

`migrate-calibrations` copies the calibrations archived before they were
kept in the library -- `calibration/<UTC time>/` beside the cache, or a
`--reference` directory's -- into `calibrations/<sha256 of data.bin>/`, the
id entries filed since 2026-09-28 record (an older one names a path, and is
found by content once that is gone). It copies and never moves or deletes:
the originals stay where the entries filed before it name them. `verify`
names the ones still outside, in a note that does not fail it. A dry run
unless given `--write`.

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

`file-spool` files what debug filing left behind in the library's `.spool`
-- a filing that failed, a process that died before it closed the scanner --
from the record beside each pass. With no spool named it also looks in the
system's temporary directory, where a session spools when the library cannot
be written, and says where it looked -- the library not existing included,
which it then makes if it files a pass. Run it with no window or tool open on
the scanner: a session files its own spool as it closes. A pass the tool or
window that took it had claimed is left unless `--claimed` says otherwise.

`list` and `verify` also count the records in `failures/`: one small JSON for
each pass that failed with nothing of it to file -- no line arrived, the
wedge cases, or its caller kept none -- holding every command it sent and
what came back. They are not entries, and not a problem with the library.

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
KEPT = library.MIGRATE_KEPT


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


def say_failure_records(root: Path) -> None:
    """How many passes failed with nothing to file, and where they are.

    Said, not counted as problems: each is a record of a pass, as an entry
    is, and nothing about the library is wrong for holding one. Unsaid, they
    sat where nobody looked, and the commands behind a wedge are what they
    are kept for.
    """
    for folder, records in library.failure_records(root):
        print(f"{len(records)} record(s) of a pass that failed with nothing "
              f"to file, in {folder} (newest {records[-1].name})")


def main() -> int:
    use_utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action",
                    choices=["list", "verify", "reconstruct", "reindex",
                             "duplicates", "migrate-raw", "migrate-direction",
                             "compact", "calibrations", "migrate-calibrations",
                             "tag", "file-spool"])
    ap.add_argument("entries", nargs="*", metavar="ENTRY",
                    help="tag: the entry ids (directory names) to tag; "
                         "calibrations: the entries to find calibrations for; "
                         "file-spool: the spools to file (default: every one "
                         "left in the library's .spool or the system's "
                         "temporary directory)")
    ap.add_argument("--add", action="append", default=[], metavar="TAG",
                    help=f"tag: a tag to add, e.g. {library.ON_PURPOSE} for an "
                         "entry taken without a reference on purpose")
    ap.add_argument("--root", default="library")
    ap.add_argument("--calibrations", default=str(library.DEFAULT_CALIBRATIONS),
                    help="calibrations, migrate-calibrations: where the "
                         "calibrations kept outside the library are archived "
                         "(default %(default)s)")
    ap.add_argument("--delete", action="store_true",
                    help="duplicates: actually remove them (default is a dry run)")
    ap.add_argument("--write", action="store_true",
                    help="migrate-raw, migrate-direction, compact: actually "
                         "rewrite the entries; migrate-calibrations: actually "
                         "copy them in (default is a dry run)")
    ap.add_argument("--claimed", action="store_true",
                    help="file-spool: file the passes a caller claimed too, "
                         "which it has probably filed already")
    ap.add_argument("--keep", type=int, default=1, metavar="N",
                    help="duplicates: how many of each group to keep (default 1). "
                         "Use 2 to retain a pair for pass-to-pass comparisons")
    args = ap.parse_args()
    root = Path(args.root)

    # Re-reducing the archived calibrations needs no library at all. Nor
    # does filing a spool: a library that could not be made is one reason a
    # session spools into the system's temporary directory, so the library
    # missing is exactly when file-spool has to look there, and it was told
    # "no library" before it had. `library.save` makes the root as it files.
    if not root.exists() and args.action == "file-spool":
        print(f"no library at {root} yet; it is made if a pass is filed")
    elif not root.exists() and not (args.action == "calibrations"
                                    and not args.entries):
        print(f"no library at {root}", file=sys.stderr)
        # A pass that failed with nothing to file is written down in the
        # temporary directory for the same reason, and is this library's
        # all the same.
        if args.action in ("list", "verify"):
            say_failure_records(root)
        return 1

    if args.action == "list":
        rows = library.entries(root)
        if not rows:
            print("library is empty")
            say_failure_records(root)
            return 0
        print(f"{'id':52} {'dpi':>5} {'ch':>3}  film / notes")
        for r in rows:
            scan, film = r.get("scan") or {}, r.get("film") or {}
            raw = "raw" if (r.get("raw") or {}).get("file") else "NO RAW"
            # Bytes filed beside a picture they cannot be (`raw.disagrees`):
            # kept, and marked where a survey of the library is read.
            if library.raw_disagreement(r):
                raw = "raw DISAGREES"
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
        odd = [r for r in rows if library.raw_disagreement(r)]
        if odd:
            print(f"{len(odd)} filed with raw bytes that cannot be their "
                  f"picture's -- `verify` says how")
        say_failure_records(root)

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

    elif args.action == "file-spool":
        import tempfile

        from rps7200.direct import (
            DirectScanner,
            file_spool,
            library_named,
            spooled_for,
        )

        # Beside the library, and then the system's temporary directory: a
        # session spools there when the library cannot be written
        # (`DirectScanner._debug_spool_dir`), and such a spool was found only
        # by the path its log had named. That directory is every library's
        # fallback -- and a test's -- so a spool there is filed here only
        # when its records name this library; any other is said, with how
        # to file it here anyway, and left.
        beside = root / DirectScanner.DEBUG_SPOOL_DIR
        system = Path(tempfile.gettempdir())
        spools = [Path(e) for e in args.entries]
        if not spools:
            print(f"looked in {beside} and {system}")
            spools = sorted(p for p in beside.glob("rps7200-debug-*")
                            if p.is_dir())
            mine = library_named(root)
            for spool in sorted(p for p in system.glob("rps7200-debug-*")
                                if p.is_dir()):
                meant = spooled_for(spool)
                if meant == {mine}:
                    spools.append(spool)
                elif meant:
                    named = ", ".join(sorted(m or "no library" for m in meant))
                    print(f"{spool}: left, its passes name {named}; name it "
                          f"to file it here: file-spool {spool} --root {root}")
        if not spools:
            print("no spool left behind")
            return 0
        filed = 0
        for spool in spools:
            if not spool.is_dir():
                print(f"! {spool}: no such spool", file=sys.stderr)
                return 1
            print(f"{spool}:")
            filed += len(file_spool(spool, root, claimed=args.claimed,
                                    say=lambda m: print(f"  {m}")))
        print(f"\n{filed} pass(es) filed")
        return 0

    elif args.action == "verify":
        # A note is said and does not fail the check: calibrations still kept
        # outside the library are intact, and counted as a problem they
        # turned every library filed before 2026-10-09 red with nothing
        # damaged, so the exit could not tell one from a damaged library.
        notes: list[str] = []
        problems = library.verify(root, notes=notes)
        for p in problems:
            print(p)
        for n in notes:
            print(f"note: {n}")
        print(f"\n{len(problems)} problem(s)" if problems else "\nlibrary is intact")
        say_failure_records(root)
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
        #
        # And a pass filed tagged failed because its bytes did not decode the
        # day they arrived: nothing was stored that a decode could reproduce,
        # and counted as a regression, one such pass kept this red for good.
        # Said on a line of its own and left out of the exit status -- and
        # one that today's decode can read is said too, as news.
        #
        # And one filed with raw bytes its record says cannot be its picture's
        # (`raw.disagrees`): known the day it was filed, said by `save` then
        # and by `verify` and `list` since, so counted apart and left out of
        # the exit status. Counted as changed, one such entry kept this red
        # for good. `verify` is where it stays a problem.
        changed = unreadable = behind = failed = damaged = 0
        known = decodes_now = disagreed = 0
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
            elif kind == library.KNOWN_FAILED:
                mark, known = "x", known + 1
            elif kind == library.DECODES_NOW:
                mark, decodes_now = "+", decodes_now + 1
            elif kind == library.DISAGREED:
                mark, disagreed = "?", disagreed + 1
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
        if known:
            print(f"{known} filed tagged failed because their bytes did not "
                  f"decode when they were taken, and still do not -- known, "
                  f"not a regression")
        if decodes_now:
            print(f"{decodes_now} filed tagged failed because their bytes did "
                  f"not decode then, and today's decode reads them -- not a "
                  f"regression, and worth a look")
        if disagreed:
            print(f"{disagreed} filed with raw bytes their record says cannot "
                  f"be their picture's -- known at filing, not a regression; "
                  f"see verify")
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

        planned, skipped, failed, filed_failed = [], [], [], []
        disagreed: list[str] = []
        for r in library.entries(root):
            path = library.entry_path(root, r)
            applied = (r.get("image") or {}).get("corrections_applied") or []
            decoded, verdict = library.reconstruct(path)
            # A failed pass whose scan.tif is its lines as they arrived holds
            # no corrected pixels to migrate, even where today's decode reads
            # its bytes; compared below, its lines failed the run. Counted
            # apart from what would not decode: one today's decode reads was
            # called undecodable here while `reconstruct` called it news.
            if verdict.kind in (library.KNOWN_FAILED, library.DECODES_NOW):
                filed_failed.append(path.name)
                continue
            # Filed with bytes its record says cannot be its picture's: no
            # decode of them is this scan.tif, raw or corrected, and
            # `reconstruct` calls it known, not a regression. Compared, it
            # failed this run on every pass.
            if verdict.kind == library.DISAGREED:
                disagreed.append(path.name)
                continue
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
            kept = path / KEPT
            # A run stopped between the swap and the record: the kept file is
            # there, the record does not name it, and scan.tif is not the file
            # the record checksums. On a mislabelled entry that looked exactly
            # like one already raw and saying so, and was passed over for good
            # while `verify` called its scan.tif damaged.
            said = (r.get("image") or {}).get("sha256")
            stopped = bool(kept.exists()
                           and KEPT not in (r.get("files") or {})
                           and said and library._sha256(path / "scan.tif") != said)
            if np.array_equal(plain, stored) and not applied and not stopped:
                continue                       # already raw and says so
            if ((applied or stopped) and kept.exists()
                    and np.array_equal(plain, stored) and plain.dtype == stored.dtype):
                # A run stopped after the fresh decode was swapped in and
                # before the record said so: raw pixels under a record that
                # still describes the corrected file -- labelled corrected,
                # which `corrected()` hands out as "already" corrected, or
                # checksummed as the file it replaced. The corrected original
                # is the kept file; if one shading of this decode is exactly
                # it, only the record is left to write.
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
                    #
                    # And neither temporary outlives a failure: a full disk
                    # left them in the entry, and verify then called each a
                    # partial write on every run, with nothing to remove it.
                    fresh = path / ".scan.tif.part"
                    copy = path / f".{KEPT}.part"
                    try:
                        tiff.write(str(fresh), plain, resolution=resolution)
                        if not kept.exists():
                            shutil.copyfile(path / "scan.tif", copy)
                            library._replace(copy, kept)
                        library._replace(fresh, path / "scan.tif")
                    finally:
                        library._discard(fresh)
                        library._discard(copy)
                image = record.setdefault("image", {})
                image["corrections_applied"] = []
                image["shape"] = list(plain.shape)
                image["dtype"] = str(plain.dtype)
                image["sha256"] = library._sha256(path / "scan.tif")
                image["replaced"] = KEPT
                # Checksummed like every other file, so `verify` sees damage
                # to the corrected original as it does to the rest.
                record.setdefault("files", {})[KEPT] = library._sha256(kept)
                library._write_atomic(path / "scan.json", json.dumps(
                    record, indent=2, default=library._plain))
            library.reindex(root)

        print(f"\n{len(planned)} entr{'y' if len(planned) == 1 else 'ies'} "
              + ("rewritten to raw pixels" if args.write
                 else "would be rewritten to raw pixels -- pass --write"))
        for name, why in failed:
            print(f"! {name}: {why}")
        if filed_failed:
            print(f"{len(filed_failed)} filed tagged failed, whose scan.tif "
                  f"is their lines as they arrived -- left alone")
        if disagreed:
            print(f"{len(disagreed)} filed with raw bytes their record says "
                  f"cannot be their picture's -- left alone; see verify")
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
        # By what compact can do, not by raw.bin alone: an entry filed plain
        # with no raw bytes, or one whose swapped TIFFs a stop left ahead of
        # its record, holds no raw.bin and was never offered.
        plain = sorted(p.parent for p in root.glob("*/scan.json")
                       if library.wants_compacting(p.parent))
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
              f"plain raw bytes or uncompressed pictures"
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
        # The library's own, by id, and those still outside it -- less the
        # ones already copied in, which are the same lines reduced twice.
        # Each of the library's with when it was kept: its name is a hash,
        # and says nothing of that.
        kept = library.calibrations(root / library.CALIBRATIONS)
        held = {f.name for f in kept}
        folders = [(f"{library.CALIBRATIONS}/{f.name} "
                    f"({library.calibration_time(f) or 'time unknown'})", f)
                   for f in kept]
        copied = 0
        for folder in library.calibrations(args.calibrations):
            try:
                said = json.loads((folder / library.CALIBRATION_RECORD)
                                  .read_text(encoding="utf-8")).get("sha256")
            except (OSError, ValueError, AttributeError):
                said = None
            if said in held:
                copied += 1
                continue
            folders.append((folder.name, folder))
        if not folders:
            print(f"no archived calibrations in {root / library.CALIBRATIONS} "
                  f"or under {args.calibrations}")
            return 0
        bad = 0
        for name, folder in folders:
            _, verdict = library.recalibrate(folder)
            kind = verdict.kind
            mark = {library.IDENTICAL: " ", library.NOTHING: "-"}.get(kind, "!")
            bad += mark == "!"
            print(f"{mark} {name}: {verdict}")
        print(f"\n{bad} of {len(folders)} no longer reduce to the reference kept"
              if bad else f"\nall {len(folders)} still reduce to the reference "
              "kept beside them, or had none to compare")
        if copied:
            print(f"{copied} under {args.calibrations} are copied into the "
                  f"library already, and were reduced there")
        return 1 if bad else 0

    elif args.action == "migrate-calibrations":
        # Copies, never moves: the entries filed before this name the old
        # folders by path, and moving them would make every one of those
        # links lie -- for 1.7 MB a calibration.
        done = library.migrate_calibrations(
            root, (Path(args.calibrations), root.parent / "calibration"),
            write=args.write)
        if not done:
            print("no calibrations kept outside the library")
            return 0
        failed = 0
        for source, target, what in done:
            mark = "!" if what.startswith("damaged") else (
                " " if what.startswith(("already", "the same")) else "~")
            failed += mark == "!"
            print(f"{mark} {source}: {what}"
                  + (f" -> {target}" if target is not None
                     and what in ("copied", "would copy") else ""))
        moving = sum(what in ("copied", "would copy") for _s, _t, what in done)
        print(f"\n{moving} calibration{'s' if moving != 1 else ''} "
              + ("copied into the library" if args.write
                 else "would be copied into the library -- pass --write")
              + (f"; {failed} damaged, left where "
                 f"{'they are' if failed != 1 else 'it is'} and not copied"
                 if failed else ""))
        return 1 if failed else 0

    elif args.action == "reindex":
        print(f"wrote {library.reindex(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
