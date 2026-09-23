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

make run-sheet    # the contact sheet on a stored walk: no scanner, no film,
                  # and the frame positions measured again from the prescans
                  # on every launch. `--open-roll` names a different one.

make reconstruct  # re-decode every stored scan with the current code
make verify       # check the library's checksums and completeness

# Single test
uv run pytest tests/test_shading.py::test_the_two_phases_are_separated -v
```

All commands run through `uv run`; never invoke pytest/ruff/ty directly.

**The same commands work on macOS, Linux and Windows.** Each recipe in the
Makefile is a single call into `tasks.py`, which is where the bodies live --
because GNU make on Windows hands recipes to `cmd.exe` unless a POSIX `sh`
happens to be on PATH, and an inline `VAR=1 cmd` prefix or `rm -rf` is a plain
error there. `python tasks.py test` also works directly, for a machine with no
make; that is a fallback, not the documented path.

On Windows, get the two tools first:

```powershell
pip install uv
winget install ezwinports.make      # GNU Make 4.4.1, native, no MSYS
```

Then `uv sync --all-groups` as everywhere else. `python3` does not exist on
Windows -- it is a Microsoft Store stub that exits 9009 -- which is why every
example here says `uv run python`.

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
uv run python tools/check_scanner.py  # the same ladder, as one command
```

`tests/test_hardware.py` is what that marker collects: nine tests that open the
device, claim the interface, and send INQUIRY and READ STATE. **Nothing there
calibrates, scans or moves the transport** -- those still need Stefan's
agreement each time. They skip rather than fail where there is no scanner, so
running them on a bare machine is harmless.

Fakes shared between test modules live in `tests/conftest.py`; `pythonpath` is
set so `from conftest import ...` works.

## Branches

**`main` must always work.** It is the branch that gets used, so a scan started from
it has to run. Nothing experimental lands there.

Every feature, fix or investigation goes on its own branch and is merged only once it
runs and its tests pass:

    feat/...     something new              fix/...    something broken
    docs/...     writing it down            experiment/...  might not work at all

**Claude: check which branch you are on before you touch anything.**

    git rev-parse --abbrev-ref HEAD

This is a first action, not a formality. An entire session's work went onto `main`
directly -- the protocol investigation, a registration-correction loop, automatic
library filing -- including designs that turned out to be wrong twice and had to be
corrected in place. None of that belonged on the branch people run from.

If you are on `main` and about to make a change, branch first. If you are already on
a feature branch, check it is the *right* one: this repo often has parallel work, and
a fix committed onto someone else's feature branch is hard to disentangle later.

## Scans

**File every scan in the library, with its raw bytes.** `tools/scan.py` and
`tools/scan_roll.py` both default to `library/`. The scanner returns raw pixels
and its shading reference is acquired *per session*, the CCD mask *per pass* —
none of it recoverable from a TIFF. A scan saved without them can never be
re-decoded or re-corrected, which is the whole point of keeping them.

After any change to how bytes become pixels, run `tools/library.py reconstruct`:
it re-decodes every stored pass with current code and reports what no longer
matches.

**The library holds raw pixels; everything else is corrected.** `scan.tif` in an
entry is the decode alone, with no flat-fielding, and `shading.npz` sits beside
it. Everything an operator sees, exports or saves is corrected — the GUI's
full-resolution view, Save As, the roll's `frame*.tif`, the prescans in
`rolls/` — and `library.corrected(entry)` is what computes it, with *today's*
correction code rather than whatever ran that day.

That split is load-bearing in both directions. A corrected file cannot be
un-corrected, so storing one forecloses every later improvement on every scan
ever taken; and a raw file shown to a person is the uncorrected picture this
driver stopped delivering everywhere else. `library.save` takes raw pixels and
`corrections=` is how a caller admits it is handing over something else.

It drifted once and went unnoticed for a day: prescans were filed with a meta
built by hand in `session.py`, so 26 entries held corrected pixels while their
record said raw, and `reconstruct` called every one a changed decode — 26 false
alarms in the one check that exists to catch a real regression. Pass the meta
the scan returns, never a substitute. `tools/library.py migrate-raw` converts
legacy entries and is a dry run unless given `--write`.

### Claude: always scan with debug filing on. Always.

**`DirectScanner` files every scan in the library automatically when debug mode is
on, and debug mode is OFF by default. You must always turn it on. This is not
optional and it is not a preference.**

    RPS7200_DEBUG=1 uv run python your_script.py          # macOS, Linux
    $env:RPS7200_DEBUG=1; uv run python your_script.py    # PowerShell

or `DirectScanner(debug=True)` in code. Either works; the environment variable is
better because a script inherits it without having to remember. Note the two
shells: `VAR=1 cmd` is Bourne syntax and is a plain error in PowerShell, which
is where the value silently would not reach the script.

Why this rule exists, in one sentence: a week of probe scans left no library entries
at all, because filing lived only in `tools/scan.py` and `tools/scan_roll.py` and
every ad-hoc script bypassed them -- and when six prescans were needed as evidence
they were simply gone. The convention above says *file every scan*; it was true of
the tools and quietly false of everything else.

Off by default is deliberate: ordinary use should not be burdened, and a 1800 dpi
RGBI entry is ~63 MB -- measured across the seventeen of them here, and it is two
files, the gzipped raw bytes and the decode, not one. But **you are not ordinary
use**. You write throwaway scripts that turn out to matter, and you cannot tell in
advance which scan will be the one somebody asks for later.

**A single scan compresses nothing while the device is open.** Each is spooled to a
temporary file as it is taken -- a plain sequential write, a second or two -- and the
entries are assembled and gzipped after `close()`, because gzipping one with the
scanner open and idle preceded a wedge once.

**A roll is the exception, deliberately, and it is unmeasured.** `FrameWriter` in
`rps7200/session.py` gzips each frame on its own thread *while the next one scans*,
which is what keeps a 38-frame roll from ending in an eleven-minute wait. The
argument is that the hazard above was open and **idle**, and here the device is
busy -- plausible, and still an argument rather than a measurement.
`tools/filing_load_test.py` is the measurement: alternating identical passes on a
quiet host and one gzipping in the background, so warm-up cannot masquerade as an
effect. Run it before trusting the roll path unattended.

It is spooled rather than held in memory for a reason worth knowing: a 7200 dpi RGBI
frame is 570 MB of pixels and about as much again of raw bytes, so keeping a
seventeen-frame roll in RAM would want **19 GB**. Only paths and small metadata stay
resident.

**Regenerate the three comparison files after any significant change** to the
scan or correction path, in the repo root, and send them:

    1_nothing_done.tif   2_corrected.tif   3_corrected_inverted.tif

Stefan judges by eye and his read is authoritative. Several times a metric has
said "corrected" where he could see lines.

## The demo is the real software with different inputs

**`--demo` may change what the software is fed. It may not change what the
software does.** `DemoScanner` stands where `DirectScanner` stands, injected at
`session._open_scanner`, and everything above that seam -- the window,
`ScanSession`, `FrameWriter`, `library.save` -- runs unmodified. There is no
`if demo:` in the scan path and none is to be added.

Two rules follow, and both are checkable:

- **A number, cap, constant or decision the stand-in needs is taken from
  `DirectScanner`, never retyped.** `param_for_mm` is a `@staticmethod` for
  exactly this reason. A retyped constant is a second home, and the two drift.
- **A refusal belongs in the stand-in's answers, not in a control the window
  disables.** If the demo must say something different -- no film in the
  transport, no scanner on the bus -- let the backend raise and let the
  existing failed-job path show it. Greying a button bypasses the code the
  demo exists to exercise.

Why. The demo is how this driver is judged when the scanner is off, so one
that diverges does not fail loudly -- it reports something plausible and wrong,
and the fix then asked for damages the real path. It has happened twice in one
day. `nudge` was retyped into `demo.py` and kept a cap of `param 8` and a
1.57-unit ramp after the driver moved to 87 and 1.84: a frame set 38 units out
held in one command on the hardware and came back `not_converged` in the demo,
which reads as a weak hold loop and was a stale copy. And `--look-only` greyed
the sheet's scan button, so the demo built to show the sheet-to-roll path never
ran `on_scan_chosen` -- the sole writer of `approved.json` and the sole
submitter of a `Roll`.

`make run-demo` and `make run-sheet` are the exercises. Anything they cannot
reach is untested with no device on the bus.

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

The metrics themselves -- dark masks, relative noise, the random/fixed split from
a repeat pair, the ceiling that split implies, pass agreement in sigma, colour-
opposed column deviation -- are in the **`measure-scan-quality` skill**, with the
measurement that discredited each alternative. Invoke it before measuring
anything. It has twice caught a reading that a single pass got backwards.

## Do not use millimetres for transport distances

**Millimetres are prohibited.** Stefan's instruction, 2026-09-21, and it is a
rule rather than a preference: express every sub-frame distance in **units of
the adjustment parameter**, the `param` byte of `SLIDE <action> <param> 00 04`.

One unit is the distance one increment of `param` adds. A command travels
`param + 1.84` units, the second term being a ramp paid once per command rather
than per unit -- which is why ten small commands travel 2.40x as far as one
large one for the same param total. It is real and measured: three legs of
equal param total over ten, five and one commands, corroborated by a ladder
that got 1.948 with one estimator and repeats per rung. The 1.57 this file
carried until 2026-09-22 is ruled out.

The reason is not tidiness. The transport has never moved a millimetre in its
life; it executes commands with a parameter, and every millimetre in this code
is a conversion away from what the hardware did. Those conversions have hidden
two mistakes in one day: a delivery ratio computed against a magnitude rather
than a signed distance, and a resampling scale error that made a good roll look
badly adjusted. A number held in the hardware's own unit cannot acquire either.

For reference, what the code holds today, converted: the aperture is **345.2**
units, the smallest possible move (`param 1`) is **2.84**, the largest single
correction (`param 87`) is **88.8**, and one prescan pixel at 300 dpi is
**0.80** -- so the picture resolves finer than the transport can move.

`param 0` is accepted and does nothing, measured 2026-09-22, so there is
nothing below `param 1`.

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

## Drive the scanner through this software, not around it

**The point of this project is that the software talks correctly to the
scanner.** Not that the scanner can be talked to. A script that reaches past
`ScanSession`, the GUI and `tools/` to send commands itself is testing the
script, and the script is not what anyone is going to use.

So: use the function the software already has. Want the film moved back? The
GUI has a prev-slide button. Want a roll walked? `tools/scan_roll.py --dry-run`
walks one. Want a frame held at a position? `_hold_to_approved` does that.
If the established path is wrong, **fix the established path** — that is the
work — and if it needs a probe to diagnose it, the probe exists to explain a
failure in the real path, never to replace it.

Why this rule exists. An investigation into the transport concluded that
`SLIDE_PREV` was dead: a hand-written probe sent it from the end of a strip,
watched `READ_STATE` for 45 s, saw nothing, and the finding went into a commit
message as "SLIDE_PREV is not usable from here". Stefan then opened the GUI,
pressed prev slide, and the film moved. The command was fine. What had been
measured was the probe.

That is the cheap version of the failure. The expensive version is the same
mistake landing the other way — a probe that works where the real path does
not, and a bug that ships because nothing ever exercised the code an operator
runs.

Two things follow:

- **A finding from a bypass path is provisional** until the same thing is seen
  through `tools/` or the window. Say which one produced it.
- **Prefer extending a tool to writing a new one.** `tools/scan.py`,
  `tools/scan_roll.py` and the GUI are where scanner behaviour belongs; a new
  `tools/*_probe.py` needs a reason that is not "it was quicker".

Offline analysis is exempt and always welcome: anything that reads stored
library entries and re-derives a number touches no device and is the preferred
way to answer a question at all.

## The scanner wedges

It needs a power cycle afterwards, so avoid these:

- **Background any scan sequence over ~8 minutes.** The harness kills a
  foreground command at 10 minutes, and a killed read is an abandoned read --
  the hazard below. Asking for a longer timeout does not help: the value is
  clamped silently, so a 14-minute run dies at 10 with no warning. That is how
  one wedge here happened, and it cost the whole run as well as a power cycle.
  Estimate first, from the medians across the library rather than from memory:
  RGB is about 22 s a pass at 300 dpi, 32 s at 600, 72 s at 900, 85 s at 1800,
  138 s at 3600 and 314 s at 7200. Infrared **tied to the resolution**, which is
  the default, adds `7.5 s + 59.9 ms/line`: about 25 s at 300 dpi, 110 s at 1800,
  214 s at 3600. Untied -- `--no-fast-ir` -- it costs a flat ~220 s whatever was
  asked for, so a low-resolution IR pass is the one that surprises you.

  Budget above the median, not at it. Scan time tracks `sum(exposure)` as well as
  line count, so a dense frame runs longer than a thin one at the same dpi: the
  1800 dpi RGB entries here span 36-162 s around that 85 s median.
- **Never abandon a read mid-scan.** An *untied* infrared pass holds the device
  busy for its own ~220 s however few lines were asked for, which is why a
  low-resolution IR pass once expired a 60 s timeout and wedged it -- that
  happened before infrared could be tied to the resolution, and `--no-fast-ir`
  still reaches it. 212 s survives in the code as `INFRARED_FLOOR_S` because it
  guards a timeout and wants the conservative end of the range; it is not what a
  pass costs.
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
- Meter in **RGB only, two rounds** — the vendor's sequence, plus one further
  round only when a channel came back clipped, because there the correction is
  a retreat rather than a measurement. Blue returns several times brighter in
  RGBI at the same exposure, which is handled by metering blue lower
  (`blue_rgbi_headroom(film)`), not by probing in IR.

  The old reason for that -- "an infrared probe costs the 212 s floor per round"
  -- has mostly gone: a probe would be tied to the resolution like any other
  pass, so at 300 dpi it costs about 25 s against RGB's 22 s. The rule stands on
  the two arguments that were always the real ones. It is the vendor's sequence,
  and blue's ratio is a *known divisor* rather than something a probe has to
  rediscover, so an IR round would spend a pass to learn a constant.
- **How much brighter blue comes back in RGBI depends on the film**, by about a
  factor of two: measured 4.98-5.02 on colour negative and ~9.6 on black and
  white. One constant for all films put 34% of a B&W scan's blue channel at the
  rail. Unmeasured films take the safe end. See `BLUE_RGBI_HEADROOM`.
- **Infrared does nothing for traditional black and white.** Silver-halide
  grain is opaque to IR, so the plane comes back holding the picture rather
  than the dust -- measured at +0.97 correlation with green -- and the pass is
  still paid for: about 110 s at 1800 dpi tied to the resolution, ~220 s untied.
  Chromogenic (C-41) B&W is the exception.
- **`SLIDE param 0` is accepted and does nothing.** Measured 2026-09-22, five
  sends, 0.00 px every time at correlation 336 where a real move scores 166-284
  — the signature of an unchanged image, not of a failed measurement. No error,
  no sense, frame counter untouched. So the per-command cost is a motion ramp
  rather than a fixed step offset, and **`param 1` (~2.6 units) is the finest
  move the transport can make** — there is no rung below it and the correction
  deadband cannot be lowered. Don't spend the question again; see
  `docs/protocol.md` §5 and `verify_protocol.py` stage 14.
- Exposure is a **16-bit timer**; past 65535 it wraps and the pass comes out
  darker, not brighter.
- **MODE SELECT byte 14, bit 0, can reverse every row of a scan with no
  signal that it happened.** Bit 0 clear re-homes the carriage before
  scanning, always normal; bit 0 set skips re-homing, which is free
  bidirectional speed *except* on a pass that immediately follows another
  bit-0-set pass, where the read comes back top-and-bottom reversed. This
  driver sends bit 0 set (`0x21`) on every RGBI scan, unconditionally.
  `scan_roll` and `auto_exposure` avoid triggering it only because an RGB
  pass always precedes the RGBI one -- not by design. Confirmed in real
  prior use, not just on the test ladder: see `docs/byte14-plan.md`.
  `framing.reversal_against` now catches it after the fact by comparing a
  pass against the prescan of the same frame, and `ScanSession.match_prescan`
  switches that off. It is a detector, so it refuses far more readily than it
  corrects -- a frame with nothing to correlate is left exactly as it came.
- **The gain field is a digital multiplier; it buys nothing.** Measured
  2026-09-10 on a blue ladder 21→39: signal ×1.484, random noise ×1.476, a
  shortfall of 0.53% where an analog gain would have given ~4%. It is safe to
  write and pointless to. Blue's rail limit in RGB cannot be lifted this way —
  scan RGBI, where blue is ~5× more sensitive. See `docs/analog-gain-plan.md`.
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
- **A frame is wider than the aperture: 350.6 units against 344.5.** That is
  `framing.FRAME_WIDTH_UNITS`, measured on prescans of one frame. So a centred
  frame shows **no** unexposed base at either edge, and even a sliver of base
  means the frame is several units off. The window and the roll centre with
  it.
- **Unexposed base is not the brightest thing on a negative.** Exposed C-41
  loses its orange mask, so some picture is brighter than base in a channel.
  Base is known by its colour ratio (R/G 2.1-2.3, B/G ~0.53) and a straight
  full-height edge. That is why every level-and-flatness detector here found
  silhouettes.
- **Frame edges are read by `tools/frame_edges`**, a copy of the
  `research/frame-edge` study's detector. After any change under it, run
  `FRAME_EDGE_PARITY=1 uv run pytest tests/test_frame_edges_parity.py`. It holds
  the copy to the study's stored answers on real film, and runs only where
  those frames are on disk (about three minutes). `rps7200` never imports it:
  the window hands it to the driver as `session.edge_reader`. The window
  reads a walk with `frame_edges.EdgeWatch` on its own thread as prescans
  arrive, never when the sheet opens; its final answer must equal
  `propose_centred`'s, and `tests/test_frame_edges.py` checks that it does.

## Never commit

`captures/*.pcapng` contain keyboard HID traffic from the capture machine.
They are gitignored; keep it that way. `scans/`, `library/`, `previews/` and
`*.tif` are ignored for size.

They are readable without tshark now -- `rps7200/usbpcap.py`, used by
`tools/parse_capture.py` -- which matters because tshark is not on PATH on
Windows even where Wireshark is installed, so these were unreadable on the
machine that recorded them. That reader only ever returns **control setup
packets** and payloads for a device the caller named; it never returns an
interrupt payload, which is where a keystroke is. Keep it that way too:
`tests/test_usbpcap.py` puts a keystroke on a synthetic bus and asserts it
does not come back.

What they are worth: across all six, every vendor control transfer CyberView
makes is one of the three shapes `usb_transport.py` makes and there are no
others -- 98,006 one-byte `0x0c` writes, 7,992 one-byte `0x0c` reads, 920
eight-byte `0x04` writes, to the same five ports. So the control plane is
verified against the vendor rather than against ourselves. And 3,987 commands
parsed, zero `SET_SCAN_HEAD` -- the rule at the top of this file, checked.

Prefer explicit paths over `git add -A`: this repo often has parallel work in
the tree.
