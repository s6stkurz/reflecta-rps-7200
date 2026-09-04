# Working on this driver

A from-scratch USB driver for the Reflecta RPS 7200 film scanner. Conventions
below are load-bearing: each exists because breaking it cost real time.

## Commands

```bash
make all          # fix + lint + type + tests (run before committing)
make test         # pytest only, with coverage
make test-all     # both TIFF paths: tifffile present and absent
make lint         # ruff check
make type         # ty check (not mypy)
make fix          # safe autofixes only

make reconstruct  # re-decode every stored scan with the current code
make verify       # check the library's checksums and completeness

# Single test
uv run pytest tests/test_shading.py::test_the_two_phases_are_separated -v
```

All commands run through `uv run`; never invoke pytest/ruff/ty directly.

`make format` reformats every file and is **not** part of `make all`. This source
is hand-wrapped with aligned comment blocks; a wholesale reformat rewrites
thousands of lines, buries the real change and conflicts with any parallel
branch. Run it only on a file you are already rewriting, and only with Stefan's
agreement.

## Testing

We use `pytest`. New features should include unit tests in the `tests/`
directory. `make test` skips tests marked `hardware` by default (see `addopts`
in `pyproject.toml`), so the suite stays runnable with no scanner on the bus.

**Test the host side against stored bytes, not against the scanner.** Every
library entry keeps its raw bytes, its shading reference and its CCD mask, so
decoding, correction and merging are all re-runnable offline. A test that
genuinely needs the device is marked `@pytest.mark.hardware` and is opt-in:

```bash
uv run pytest tests/ -m hardware      # only with the scanner attached, and after asking
```

Fakes shared between test modules live in `tests/conftest.py`; `pythonpath` is
set so `from conftest import ...` works.

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

## Calibrate with the film loaded

**CyberView does everything with the film in the transport**, calibration included.
The README used to say to power on with nothing loaded; that came from reading "the
light path is clear across the calibration frame" as "the transport is empty". The
calibration frame is `(0, 3431, 10343, 6888)`, the lower part of the transport, which
the film does not cover -- so the sensor is measured, not the film, with the strip
still in. Calibrating an empty transport is a state the vendor never creates, and
doing it once preceded a wedge.

`READ_STATE` is not a substitute for asking. Its `0x40` "media present" bit did track
the film here -- `0x0d` empty, `0x4d` loaded, one variable changed -- but it is clear
throughout the vendor's power-on capture and has read clear with film demonstrably
loaded, so a set bit is evidence and a clear one is not. Only Stefan can see the
transport. Ask him.

## Ask before driving the scanner

**Presence is not permission.** Finding the device on the bus says only that it
is plugged in — not that it is warm, not that the transport holds what you
think, and not that now is a good moment. Confirm with Stefan before a scan,
a calibration or anything else that moves the mechanism, especially after a
gap. A scan costs minutes of his hardware; a wedge costs a power cycle.

Checking `inquiry()` or `read_state()` to answer "is it there" is fine. Going
straight from that into a capture is not.

Say what the run will cost in time and what it is for, then wait. If the
transport's contents matter to the result — an empty transport for a ladder
check, film loaded for a real scan — ask rather than assume, because only he
can see it.

## The scanner wedges

It needs a power cycle afterwards, so avoid these:

- **Background any scan sequence over ~8 minutes.** The harness kills a
  foreground command at 10 minutes, and a killed read is an abandoned read --
  the hazard below. Asking for a longer timeout does not help: the value is
  clamped silently, so a 14-minute run dies at 10 with no warning. That is how
  one wedge here happened, and it cost the whole run as well as a power cycle.
  Estimate first: roughly 23 s per pass at 300 dpi, 55 s at 1800, 110 s at 3600
  for RGB, and add the ~212 s infrared floor per pass when infrared is on.
- **Never abandon a read mid-scan.** Infrared holds the device busy for its own
  ~212 s floor however few lines were asked for, which is why a low-resolution
  IR pass once expired a 60 s timeout and wedged it.
- **Do not hold the session open through heavy local work.** Gzipping a 140 MB
  library entry with the device open and idle preceded one wedge.
- No IEEE1284 RESET, and no `STOP SCAN` — the vendor sends neither, and both
  leave the device unresponsive.
- **Never send `SET_SCAN_HEAD` (0xD2).** It drives a mechanism with a step count
  whose unit is unknown, and it reports *nothing*: no error, no sense condition,
  and not one byte of `READ_STATE` changes, at any step count. 10 and 100 steps
  looked like a clean no-op. 1000 turned the gears audibly and needed a power
  cycle. The silence is the trap — there is no feedback that says stop, so a
  step count cannot be calibrated by escalating it.

  It is defined in the SANE `pieusb` backend (`sanei_pieusb_cmd_set_scan_head`,
  modes 4 and 5: `00 00 hi lo` forward, `01 00 hi lo` backward) but that backend
  never calls those modes, refuses mode 2 as "unreliable, possibly dangerous",
  and uses mode 1 only beside `STOP SCAN`. CyberView sends it zero times in
  3,955 commands across all six captures. Nothing has ever driven this command
  successfully; it is not an untested feature, it is a known hazard.

  The film transport is `SLIDE` (0xD1) and is unrelated to this. Whole-frame
  `SLIDE_NEXT`/`SLIDE_PREV` are measured and safe.

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
