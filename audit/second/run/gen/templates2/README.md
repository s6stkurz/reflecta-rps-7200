# Second audit, 2026-09-27

**Code audited:** `03aacba` on `claude/clever-mayer-dy1j3o` -- the driver after two rounds of
fixes for the [first audit](../README.md) (`83dbb22`). Read-only, judged from the code and not
from the docs, with the first audit's readers and prompts: one reader per subsystem, every
finding then re-read against the code by a second, adversarial reader, a completeness critic,
and gap readers for what the areas did not cover. New for this one: a status check of every
first-audit problem, and a reader for the fixes themselves (`83dbb22..03aacba`).

Nothing here was run on the scanner.

## In one paragraph

The first audit's worst problems are closed: nothing silently files corrected pixels as raw
any more, a failed pass no longer leads the software to keep driving the device, the library
is written atomically and checksummed, calibrations are kept byte for byte, the demo files
what it is, and none of the 32 problems is still open or made worse. What is left is mostly
*coverage*: several kinds of pass still reach the library only as corrected pixels or not at
all -- above all a real roll frame's prescans -- the debug spool that is meant to catch them
has holes of its own, a failed library save still costs the picture, and a handful of Ctrl-C
and busy-guard paths remain. No finding is critical.

## Where the first audit's problems stand

{{STATUS_COUNTS}} -- of 32; none open, none regressed. Every sub-issue still open, per
problem, with the code it was checked against: **[status.md](status.md)**.

{{STATUS_TABLE}}

## What this audit found

{{STATS}}

### High severity

{{HIGH_ALL}}

They come down to a few themes, each reported independently by several areas:

1. **A real roll frame's prescans are not kept raw** (LIB-01, SR-04, FR-02, DOC-01, CSA-14,
   CLI-09, T-04). The frame entry stores the corrected 8-bit prescan as `prescan.tif` with no
   bytes, mask or label, and the raw prescan and the pre-move prescan are dropped -- yet these
   are what every framing decision was made from.
2. **The debug spool has holes** (DBG-1, DBG-2, SR-01, CSA-01, CSA-02, CLI-07, DBG-8, TP-10).
   A caller's claim deletes the spooled copy before the caller has filed it; claimed passes
   stay in the OS temp directory until the device closes; a force-abort or crash leaves the
   spool unfiled and unannounced.
3. **A failed library save loses the picture** (LIB-02, SR-05, CLI-01, CLI-06, T-03): no
   delivered copy is attempted, the in-memory raw data is dropped, and a roll keeps scanning
   for hours before saying so.
4. **Passes that fail after their bytes were read are never filed** (DBG-4, TP-A1, TP-06,
   DOC-17), and a truncated read counts as complete (TP-02, DBG-5).
5. **Calibration can end early and be believed** (TP-01, DBG-7): any refused read is taken as
   "finished", and the partial reference is adopted and cached.
6. **Ctrl-C and heavy work with the device open** (CLI-02, SR-19, SR-02, CSA-03, CSA-04,
   OUT-01, GUI2-06, TP-11, PAT-03): the first Ctrl-C is not honoured between phases, the
   window's threads die with its terminal, and the last frames of a roll -- and every delivered
   copy -- are compressed with the device open and idle.
7. **`reconstruct` passes what it cannot check** (LIB-03, CLI-05): a decoder that now raises
   is counted as "nothing to decode from" and the run exits 0.
8. **Export joins a roll to its entries by name** (GUI1-01, GUI2-01), and the roll browser's
   Open is not guarded while the scanner works (GUI1-02, GUI2-02).
9. **"Reverse the direction"**, remembered across launches, mirrors every approved hold
   target of a commissioned roll (FR-01).

### By area

{{AREAS}}

### By category

{{CATEGORIES}}

### Refuted

{{REFUTED_COUNT}} finding(s) the second reader could not confirm in the code, dropped from
the areas and listed here so nothing disappears silently:

{{REFUTED}}

## Files

- [status.md](status.md) -- every first-audit problem: what the code does now, what is left.
- [areas/](areas/) -- every finding in full, per area: evidence quoted from the code, failure
  scenario, fix, and the second reader's check; plus what the area persists and what an
  operator can and should not do.
- [dataflow.md](dataflow.md) -- how data moves through the code at `03aacba`, verified.
- [persisted-state.md](persisted-state.md) -- every file on disk: format, raw or corrected,
  writer, reader, exact or not.
- [library-exactness.md](library-exactness.md) -- whether each kind of pass can be re-derived
  from what the library keeps.
- [demo-vs-direct.md](demo-vs-direct.md), [user-errors.md](user-errors.md),
  [doc-mismatches.md](doc-mismatches.md).
- [PROGRESS.md](PROGRESS.md) -- how this run was done and restarted; [run/](run/) the scripts,
  [raw/](raw/) every agent's result as returned.

## After this audit

{{ROUND3}}
