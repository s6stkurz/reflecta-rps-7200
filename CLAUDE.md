# Working on this driver

A from-scratch USB driver for the Reflecta RPS 7200 film scanner. Conventions
below are load-bearing: each exists because breaking it cost real time.

## Scans

**File every scan in the library, with its raw bytes.** `tools/scan.py` and
`tools/scan_roll.py` both default to `library/`. The scanner returns raw pixels
and its shading reference is acquired *per session*, the CCD mask *per pass* —
none of it recoverable from a TIFF. A scan saved without them can never be
re-decoded or re-corrected, which is the whole point of keeping them.

After any change to how bytes become pixels, run `tools/library.py reconstruct`:
it re-decodes every stored pass with current code and reports what no longer
matches.

**Regenerate the three comparison files after any significant change** to the
scan or correction path, in the repo root, and send them:

    1_nothing_done.tif   2_corrected.tif   3_corrected_inverted.tif

Stefan judges by eye and his read is authoritative. Several times a metric has
said "corrected" where he could see lines.

## Measuring

**Measure the file you delivered, never a recomputation of it.** A whole round
was lost to analysing a recomputed array that was clean while the shipped file
had a 40-column colour ramp — the artefact was in the write path, which the
recomputation skipped.

**Use signed, channel-relative deviation to find coloured lines.** `np.abs`
hides a violet/green pair, and per-channel maxima cannot express "this channel
departs from the others". A magnitude metric is also dominated by picture
content: the same metric read 96-169% on a frame with hard vertical edges and
2% on a flat one, and neither meant anything.

**Separate sensor from picture with the library, not with a threshold.** A
sensor defect sits at a fixed sensor column across *different film positions*;
picture content does not. Two entries from different frames settle it.

## The scanner wedges

It needs a power cycle afterwards, so avoid these:

- **Never abandon a read mid-scan.** Infrared holds the device busy for its own
  ~212 s floor however few lines were asked for, which is why a low-resolution
  IR pass once expired a 60 s timeout and wedged it.
- **Do not hold the session open through heavy local work.** Gzipping a 140 MB
  library entry with the device open and idle preceded one wedge.
- No IEEE1284 RESET, and no `STOP SCAN` — the vendor sends neither, and both
  leave the device unresponsive.

## Facts that are easy to get wrong

- The scanner **never applies its own shading correction**; it hands back a
  reference and the host divides. See `docs/shading-calibration-plan.md`.
- `SET GAIN OFFSET` does **not** persist across a scan sequence. Exposure has
  to go through each scan's `exposure_scale`.
- Meter in **RGB only, two rounds** — the vendor's sequence. An infrared probe
  costs the 212 s floor per round. Blue returns 2-3.7x brighter in RGBI at the
  same exposure, which is handled by metering blue lower, not by probing in IR.
- Exposure is a **16-bit timer**; past 65535 it wraps and the pass comes out
  darker, not brighter.
- Bump `PROTOCOL_REVISION` in `rps7200/direct.py` when the commands sent to the
  device change — not for host-side work, which is re-runnable from raw bytes.
- **There is no vignette, and no vignette correction should be added.** Measured
  2026-08-30 by rotating an IT8 through all four insertions plus an empty-transport
  flat; see `docs/vignette-plan.md`. The ~39% falloff across the frame is real but
  lives entirely in x and shading already takes it to 1.4%. Along y it is 1.1%
  *before* correction — and shading is per-column, so it cannot have flattened y.
  An optic falls off in both directions; this falls off in neither. Re-run with
  `tools/uniformity.py analyse --tag vignette-study` after any correction change:
  it rebuilds from stored raw bytes, so the answer tracks the current pipeline.

## Never commit

`captures/*.pcapng` contain keyboard HID traffic from the capture machine.
They are gitignored; keep it that way. `scans/`, `library/`, `previews/` and
`*.tif` are ignored for size.

Prefer explicit paths over `git add -A`: this repo often has parallel work in
the tree.
