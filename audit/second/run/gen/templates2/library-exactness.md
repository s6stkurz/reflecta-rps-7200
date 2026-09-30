# Does the library hold the exact bits?

[Back to the summary](README.md)

The owner's central requirement: every scan in the library with its exact raw bytes, its
shading reference, its CCD mask and every parameter and command needed, so that everything
can be recalculated and evaluated later with newer code.

## Where it stands at 03aacba

**Met** for the passes the software files itself -- a window Scan or Prescan, a
`tools/scan.py` pass and bracket, a roll frame, a walk prescan:

- the raw bytes as read (`raw.bin.gz`, or `raw.bin` until compacted);
- the raw decode as `scan.tif`, with its read direction and, at 7200 dpi, the stagger
  realignment it replays;
- the reference and mask;
- every command sent with what came back;
- when the pass began, where its reference came from, and the calibration's own bytes.

Entries are atomic and checksummed; `reconstruct` re-decodes them with today's code, and
`verify` finds cut-short entries, orphans and damaged files.

**Not met** for these kinds of pass:

- **A real roll frame's prescans.** The frame entry keeps the corrected 8-bit prescan with no
  bytes, and the raw prescan and the pre-move prescan are dropped (LIB-01, SR-04, FR-02).
  The framing decisions made from them cannot be re-derived.
- **Metering probes and hold/aim verification prescans.** They reach the library only through
  debug filing, which is off by default (DBG-3). With it on, they are filed with
  `film = negative` whatever is loaded (DBG-A1, FR-08).
- **A pass that fails after its bytes were read**, and a truncated one. The first is never
  filed (DBG-4, TP-A1); the second is filed as if complete (TP-02).
- **Hold and aim moves.** Their SLIDE bytes are recorded nowhere; only millimetres derived
  through a law that has since changed (DOC-06 in docs-plans-todo).
- **The calibration archive.** It is written but read by nothing, and linked from an entry
  only by a path relative to the working directory, and not at all for a reused reference
  (TP-07, LIB-05, CSA-09).

A **failed save** loses the pass outright (LIB-02, SR-05), and **`reconstruct`** can pass a
decode that now raises (LIB-03). Both undermine the promise from the other side.

## Every finding about exactness or completeness

{{LIB_COUNT}} findings in the categories data-integrity and library-completeness, all areas:

{{LIB_FINDINGS}}
