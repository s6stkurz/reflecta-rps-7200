# rps7200

Console and Python scanning for the **Reflecta RPS 7200** film scanner, capturing
**RGB and raw infrared together** in one pass at 16 bits.

The infrared plane is handed to you untouched rather than being spent internally on dust
removal, so you can run your own IR-based cleanup.

> **This is hobby reverse engineering, and it drives your hardware.** See
> [Legal](#legal) before you point it at a scanner you care about.

## Why this exists instead of just calling `scanimage`

The scanner works under SANE's `pieusb` backend, but three things stop the stock
command-line frontend from getting the data out.

**1. `scanimage` cannot write the 4-channel RGBI frame.** In `pieusb.c`, RGBI mode sets
`colors = 4` but reports the frame type as `SANE_FRAME_RGB` — the source comment reads
`/* was: SANE_FRAME_RGBI */`. `scanimage` hardcodes 3 channels for that frame type, so it
writes a header claiming 3 samples/pixel over a stream carrying 4. The result is sheared,
with the IR smeared through the visible channels.

**2. The device name changes on every open.** The backend resets the scanner during
discovery, so it re-enumerates and its SANE name moves — `020:057`, `020:060`, `020:010`,
… A name from one `scanimage -L` is already stale by the next command. Enumeration and
open have to happen in the same process.

**3. Prescan calibration lives on the open handle.** `sanei_pieusb_analyze_preview` stores
the per-channel bounds on the scanner handle, and `calibration="from preview"` reads them
back. Close the handle and the calibration is gone, so a shell loop cannot prescan and
then scan.

This package does not use SANE at all. It drives the scanner directly over USB the way the
vendor software does, holds one session across prescan and scan, and writes the 4-channel
data itself.

## Install

```sh
pip install -e .
```

numpy is the only hard dependency, plus a bundled libusb on Windows, where there is
nowhere conventional for a system one to live. The GUI adds nothing, because Tk ships with
Python and its `PhotoImage` reads the raw PPM bytes `rps7200.preview` produces. libusb is
loaded the first time something actually talks to the device, so decoding a stored scan,
merging a bracket or writing a TIFF works on a machine with no scanner drivers at all.
`tifffile` is optional and used automatically when present; the built-in TIFF
reader/writer is complete on its own.

## Supported platforms

**macOS, Linux and Windows.** Everything that does not touch the device — decoding,
correcting, the TIFF and DNG writers, the library, the window in `--demo` — works on all
three with nothing but Python and numpy. Driving the scanner needs libusb, and on two of
the three it needs one more thing.

The development commands are the same everywhere. Each Makefile recipe is one call into
`tasks.py`, because GNU make on Windows uses `cmd.exe` unless a POSIX `sh` is on PATH.

### macOS

```sh
brew install libusb
pip install uv && uv sync --all-groups
```

### Linux

```sh
sudo apt install libusb-1.0-0 python3-tk      # or libusbx, on Fedora and RHEL
sudo cp packaging/60-rps7200.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
pip install uv && uv sync --all-groups
```

The udev rule is what lets you open the scanner without being root; replug it afterwards.
The device is vendor-specific (`bDeviceClass 0xff`), so the kernel binds no driver and
nothing needs detaching — permissions are the whole problem.

### Windows

```powershell
pip install uv
winget install ezwinports.make      # GNU Make, native, no MSYS needed
uv sync --all-groups
```

**The scanner's driver has to be replaced, for now.** Windows binds its own `usbscan.sys`
to it — via an INF from Pacific Image, the one CyberView installs — and libusb cannot open
a device another driver holds. Every comparable project does the same thing; there is no
way round it that has been made to work.

> **Read this first.** Replacing the driver **stops CyberView and VueScan seeing the
> scanner**, because they talk to it through the driver you are replacing. It is
> reversible, but not by itself — see *Putting it back*.

1. **Plug the scanner in and switch it on first.** Zadig only lists devices that are
   enumerated.
2. Get [Zadig](https://zadig.akeo.ie/) — one `.exe` — and run it **as administrator**.
3. **Options → List All Devices.** This is the step everyone misses: by default Zadig
   hides devices that already have a driver, which is exactly what this one is.
4. Pick **Multiple Frames Film Scanner**, and check the line underneath reads `USB ID
   05E3 0144`.
5. Set the target driver to **WinUSB** (libusbK works too), then **Replace Driver**.

Then check it worked. This sends nothing but INQUIRY and READ STATE and moves nothing:

```powershell
uv run python tools/check_scanner.py
```

It climbs a ladder, stops at the first rung that fails, and prints what the failure means
— including, before Zadig, the whole paragraph about replacing the driver. After it, the
same command reaches rung 5 and reports model, firmware and CCD size. `uv run pytest
tests/ -m hardware` asserts the same nine things as a test run.

#### Putting it back

Zadig has no undo. In **Device Manager** the scanner is under *Universal Serial Bus
devices* after the swap, **not** *Imaging devices*, which is where it was and where people
look. *Uninstall device*, tick **Delete the driver software for this device**, then replug.
Windows re-binds the original Pacific Image driver and CyberView sees it again.

## Use

One calibration per power-on, then scan. **Load the film first**, wait for the lamp
(about 80 s), then calibrate and scan without unloading it — CyberView does everything
with the film in the transport, and calibrating an empty one is a state the vendor
software never creates. The calibration still measures the sensor rather than the film:
its frame is `(0, 3431, 10343, 6888)`, the lower part of the transport, which the film
does not cover.

```sh
# calibrate, scan, correct, and file the result with its raw bytes
uv run python tools/scan.py --dpi 1800 --ir \
    --stock "Kodak Gold 200" --frame 3 --notes "test frame"

uv run python tools/scan.py --dpi 600                  # faster, RGB only
uv run python tools/scan.py --dpi 1800 --no-shading    # raw pixels, for comparison
uv run python tools/scan.py --dpi 1800 --reuse         # reuse the cached reference
uv run python tools/scan.py --film positive            # a slide: keeps its colour cast
uv run python tools/scan.py --dpi 1800 --ir --no-fast-ir   # the old untied IR pass
```

`--fast-ir` is the default and is the largest single cost switch in the tool: it ties the
infrared plane to the scan resolution instead of paying a flat floor. See
[Resolution](#resolution).

### What `--film` changes

It is a metering and delivery decision, not a mode the scanner has. Every pass is RGB or
RGBI; there is no hardware black and white mode worth using, and CyberView does not use
one either.

| `--film` | metering | infrared | delivered |
|---|---|---|---|
| `negative` | per channel — takes the orange mask off before the ADC | allowed | 3 or 4 channels |
| `bw` | per channel — silver halide has no colour record to protect | **refused** | one channel, the average of R, G and B (`--mono G` for a single one) |
| `positive` | locked — the cast *is* the picture | allowed | 3 or 4 channels |
| `kodachrome` | locked | **refused** | 3 channels |

**Infrared is refused on black and white and Kodachrome** rather than warned about.
Silver grain blocks infrared exactly as dust does and Kodachrome's cyan layer absorbs it,
so the plane comes back holding the photograph instead of what is lying on top of it —
measured at +0.97 correlation with green. Chromogenic black and white (XP2, BW400CN,
anything C-41) is dye-based and cleans properly, so scan it as `--film negative`.

**Black and white is deliberately not locked**, unlike a slide or a Kodachrome whose cast
*is* the picture. Silver halide has no dye layers, so what looks like a cast is the film
base and the sensor's own response; holding the channels together to preserve it cost
green and blue about half a stop each on repeat pairs at 900 dpi. This diverges from
`nkscan`, which locks monochrome — right for a Coolscan, whose exposure register is 26
bits wide with no channel that runs out, and wrong here, where red's ceiling is a rail the
lock propagates to the other two.

A whole strip or roll, unattended:

```sh
uv run python tools/scan_roll.py --dry-run --frames 6                 # prescan and advance only
uv run python tools/scan_roll.py --dpi 1800 --ir --frames 6 \
    --roll 2026-08-28-gold200 --stock "Kodak Gold 200"
```

Every scan is filed in `library/` by default, with the raw bytes the scanner sent, the
session's shading reference and that pass's CCD mask. None of those can be recovered from
a TIFF, and without them a scan can never be re-decoded or re-corrected:

```sh
uv run python tools/library.py list          # what is stored
uv run python tools/library.py verify        # checksums and completeness
uv run python tools/library.py reconstruct   # re-decode every scan with current code
uv run python tools/library.py duplicates    # what is redundant, and why

uv run python tools/make_comparison.py scan.tif flat.tif   # raw / corrected / inverted
```

`make_comparison.py` needs Pillow (`uv sync --extra jpeg`, or the dev group) and takes its
two files as arguments.

From Python:

```python
from rps7200.direct import DirectScanner

with DirectScanner() as s:
    s.calibrate_shading()                        # once per power-on
    image, meta = s.scan(resolution=1800, infrared=True)
    rgb, ir = image[..., :3], image[..., 3]      # (H,W,3) and (H,W), uint16
```

## The window

```sh
make run                        # the scanner
make run-demo                   # no scanner: stored library entries drive the window
make run-sheet                  # no scanner, no film: the contact sheet on a
                                # stored walk, re-measured on every launch
```

The demo decodes each entry's **raw bytes**, not the TIFF beside them, so it runs the same
deinterleave and the same shading correction a real pass runs. It picks its picture by
film type, answers at the resolution asked for, refuses what the device refuses, and where
it cannot honour a request with the bytes in hand it drops them and says so rather than
filing raw that decodes to a different photograph. Its output goes under `demo/`.

Prescan, scan, walk a roll, and look at what came off. The filmstrip along the bottom
holds every pass of the session; the channel selector switches between RGB and R, G, B or
**infrared alone**, which is the one plane no ordinary viewer will show you. The preview
is inverted by default so a negative can be judged by eye, and that inversion is display
only — what reaches `library/` is the raw negative. Inverting for real is NegPy's job.

Opening the window claims the device, asks it who it is and reads the transport's frame
counter (`READ_STATE`) for the readout, and nothing else: no calibration, no lamp, and
nothing moves until a button is pressed.

**The contact sheet.** A dry run walks the strip prescanning and advancing only — about 20
seconds a frame — and opens every picture it found in a grid with its frame number and its
measured contrast. (A position you set by hand replaces the contrast with the offset in
units of the transport's own adjustment parameter; a frame already scanned reads
*scanned*.) Tick what is worth having and only
those frames are scanned: the roll goes to the first ticked frame by the transport's
counter, winding back or advancing from wherever the film is, and an unticked frame costs
its ~7 s advance instead of the minutes a scan would. Seventeen frames at 3600 dpi RGBI
is about an hour and a half, and a strip with four keepers should not cost the same as
one with seventeen. The walk writes `survey.json` and a `prescanNN.tif` per frame,
so a strip can be looked at again tomorrow instead of walked again.

**A roll that died can be finished, however much later.** *Rolls …* is a table of every
roll on disk — Roll, Frames, Resolution, Film, Created, Last opened, Size — sortable by
any column, with a search box and an "only unfinished" tick. Unfinished rolls are marked
in amber, and so is one whose library entries have gone, because that is the one that
cannot be exported. A selection can be **exported** (every frame re-corrected from the
library at full resolution with today's correction code, not a copy of what was written at
the time), **duplicated** to the next free `-2`, **renamed**, revealed in the file manager,
or **deleted**. Delete removes the roll folder and never touches `library/`: the raw bytes
stay and the frames can be rebuilt, and the only thing that goes for good is
`approved.json`. It refuses while the scanner is working or while that roll is open.

Opening a roll with frames left brings back its contact sheet and approvals, marks what is
already scanned, and restores **the roll's own settings** from its manifest rather than the
window's: resolution and prescan resolution, film, infrared and infrared-at-scan-
resolution, metering, the mono channel, and the frame count and start-at. A year later the
window has moved on to other film and the manifest still describes that roll. It
calibrates again first — a reference describes the sensor at the exposure and gain that
measured it, and months on neither is the same — though a saved one can be loaded instead.

**Hover the picture for the numbers under the pointer**, every channel's value including
infrared. The histogram answers "is anything against the ceiling"; this answers "what is
*this*". It says `(approx)` where it is reading a reduced copy, because a working copy's
pixels are resampled averages.

**The histogram is always on screen**, top right, showing where values actually sit,
unstretched, with how much of each channel is at nothing, at full scale and near it. The
preview is stretched so a negative can be judged by eye, and a stretch puts the brightest
pixel at white whether it was against the ceiling or merely near it — on this scanner blue
reaches the rail first and looks no different for it. Infrared is not in it: it is a dust
measurement rather than an exposure.

**Every panel says what you have changed, and puts it back.** A panel header reads `Scan ·
3` with a `↺` beside it when three of its controls differ from what the window shipped
with, and both appear only then. The arrow resets that panel, right-clicking a single
control offers `Reset to '1800'` for just that one, and **Restore settings …** puts
everything back. Your shortcuts and presets are left alone. The defaults are *captured*
when the window finishes building itself and before any settings file is read, rather than
written down a second time, because a table of default values would disagree with the
controls the first time either moved.

**A control that cannot apply greys out with the reason rather than disappearing.** JPEG
quality stays readable while TIFF is selected; the infrared-at-scan-resolution box stays
readable on an RGB pass, because it is a setting that is still *set* and the driver gates
it. A setting you cannot see the state of is worse than a dead control you can.

**Everything has a key, and the keys are yours.** ⌘ on a Mac, Ctrl elsewhere: ⌘R and ⌘⇧R
turn the picture, ⌘M flips it, ⌘0 straightens it, ⌘F fits it, ⌘1 shows one scanned pixel
per screen pixel, ⌘I inverts, ⌘C steps the channels, ⌘S saves and ⌘⇧S saves all. The
arrows are bare — they walk the filmstrip, move between contact-sheet frames and step the
film in the position window, where Space ticks a frame and Return keeps it and moves on.
Every right-click menu shows its key as it is *now* rather than as it shipped, and
**Shortcuts …** lists them all: click a key to change it, × to clear, ↺ to restore. Only
what you changed is written to `gui-settings.json`, so a default improved later still
reaches you.

**No key submits without asking.** Prescan (⌘Return), scan (⌘⇧Return) and a whole roll
(⌘B) all have keys, and every one of them confirms first and says what the run will cost,
because a slip on a keyboard is not a decision to spend minutes of hardware. Moving film
and calibrating have no key at all: there is no undo for a moved negative. Escape stops
after the current pass.

**Stopping.** A pass in flight cannot be interrupted safely, and an abandoned read is what
costs a power cycle. *Stop* is cooperative — it ends a roll after the frame in flight and
a single scan after the pass finishes — and says which it will do. *Force abort* closes
the transport out from under the read, which is the only thing that actually unblocks it;
the frame is lost and the scanner will almost certainly need a power cycle at its own
switch. It asks you to type ABORT first.

**Right-click arranges a picture wherever it is shown**, and **the arrangement belongs to
the photograph, not to the window**: turn a frame in the contact sheet and the preview
behind it turns too, and a scan comes back the way its prescan was left however many other
pictures were arranged in between. Quarter turns and a left-right flip, kept per frame, so
a portrait among landscapes comes out right; the turn reaches the files you get and never
the library entry, whose pixels have to keep matching the raw bytes beside them. Turns
survive closing the window, in `approved.json`.

**A pass that comes back the wrong way up is turned to match its prescan.** The scanner
does this with nothing to say it has — `MODE SELECT` byte 14 bit 0 skips the re-home for
bidirectional speed, and a pass following another bit-0 pass reads reversed with no status
bit and no sense condition. The only evidence is that the picture does not match the
framing pass of the same frame, so that is what it is judged against. It refuses far more
readily than it corrects: being wrong stands a photograph on its head, so a frame with
nothing to correlate is left exactly as it came.

The resolution box offers 300, 600, 900, 1200, 1800, 3600 and 7200, and accepts any whole
number from 25 to 7200 typed in; the device refuses what it dislikes with sense
`0x26/0x82` before a byte of image data moves. **Bracketing is absent from the window**
deliberately — see `docs/multi-exposure-plan.md`, which measured it and found it does not
pay — though `tools/scan.py` still has `--bracket` and `--stops` behind it.

**It remembers the setup**: resolution, infrared, film, exposure, metering, where files
go, the window size and the pane widths, from `gui-settings.json` beside the library
(`RPS7200_SETTINGS` moves it, `--settings` overrides it). Scan settings can be saved as
named presets. A missing or corrupt file opens the window on its defaults rather than not
opening it. What is deliberately *not* remembered is the roll name, frame, subject and
notes: those describe one shot, and a stale value would file today's scan under
yesterday's name.

**Files are named by roll and frame**, because NegPy reads them next:

    2026-09-09-gold200_frame03_3600dpi_ir.tif

with prescans in a `prescans/` subdirectory. A frame rescanned after a failure gets a
suffix rather than overwriting the first attempt — the better of the two is not always the
second. The library entry keeps its own timestamped id, which is what makes it findable
years later.

## How scans are corrected

**The scanner returns raw pixels and never corrects them itself.** It measures its own
per-column sensor response during a calibration pass and hands that measurement back, but
applying it is the host's job. Run the calibration and discard the result — as this driver
did for a long time — and nothing changes in the image, which reads like a broken
calibration rather than a missing step.

The reference is two-point per column: a dark level with the lamp off and a light level
with it on, from which the host builds a per-column gain and offset. Applying it took the
worst column defect from 13.01% to 1.44%. Blue has no fixed column pattern to remove.
`docs/shading-calibration-plan.md` has the derivation and the numbers.

**The library holds raw pixels; everything a person sees is corrected.** `scan.tif` in an
entry is the decode alone with `shading.npz` beside it, and `library.corrected(entry)` is
what computes the usable image — with *today's* correction code rather than whatever ran
that day. A corrected file cannot be un-corrected, so storing one would foreclose every
later improvement on every scan ever taken.

There is **no vignette and no vignette correction**, which was measured rather than
assumed: the ~39% falloff across the frame lives entirely in x, where shading already
takes it to 1.4%, and along y it is 1.1% *before* correction. An optic falls off in both
directions; this falls off in neither. See `docs/vignette-plan.md`.

## Exposure

`--auto-exposure` probes at 300 dpi and aims each channel's 99.5th percentile at **0.80**
of full scale, measured **inside the film** rather than across the whole transport window.
The probe is always RGB, in two rounds, plus one further round only if a channel came back
clipped — there the correction is a retreat rather than a measurement.

- **Exposure is a 16-bit timer that wraps.** Past 65535 a pass comes back *darker*, not
  brighter. `READ GAIN/OFFSET` hands back a fixed reference — `9604, 6506, 6506, 7745` —
  rather than what is in force, so every scan is `base × scale` and exposure cannot
  compound. Red's base is highest, so its ceiling is ×6.82 where green and blue get
  ×10.07, and red runs out first.
- **The band above the target is tighter than the band below it.** 0.08 under, 0.02 over.
  Landing under costs a little noise; landing over clips, and nothing downstream undoes
  that. With a symmetric band the top of the window went to 0.88 and a frame duly landed
  at 87% with samples at the rail.
- **Blue comes back several times brighter in an RGBI pass than in the RGB probe**, so its
  target is divided before an infrared scan: by **5.2** on colour negative, where the
  measurement is 4.98–5.02, and by **11.0** on anything unmeasured, where black and white
  measures ~9.6. Every divisor sits above its measurement on purpose, because too low
  clips blue — one constant for every film put 34% of a B&W scan's blue channel at the
  rail.
- **Metering looks inside the film, and finds it once.** It keeps 81% of the pass of
  whatever the film detector returned. The empty aperture beside a strip is far brighter
  than any part of a negative — 143/153/153 against the film's 34/15/7 on a C-41 prescan —
  so metering the whole window lets however much aperture is in view set the exposure: it
  read the percentile 5.9–11.1% high and the scan came out 5.6–10.0% short, silently and
  differently per frame. The film is located on the first probe *while it is still dark*,
  because metering's job is to brighten the film until that contrast is gone.

What the probe measured is filed with the scan, so these numbers stay checkable from
ordinary work. Fifteen frames of colour negative were walked that way:
metering landed in band on all fifteen, and the sensor's departure from linear above 70%
of scale is **−0.6% to −0.8%**, not the 1.5–1.9% long quoted for it. See
`docs/exposure-negative-plan.md`.

## Resolution

| dpi | pixels | RGB | RGBI | driven |
|---|---|---|---|---|
| 300 | 431 × 287 | ~22 s | ~25 s | yes |
| 600 | 862 × 574 | ~34 s | ~44 s | yes |
| 1800 | 2586 × 1722 | ~85 s | ~110 s | yes |
| 3600 | 5172 × 3444 | ~138 s | ~214 s | yes |
| 7200 | 10344 × 6887 | ~314 s | — | yes, and not worth it |

**Scan time tracks line count, and exposure moves it.** Roughly `8 + 0.036 × lines` for
RGB; an infrared pass tied to the resolution adds `7.5 s + 59.9 ms/line`. It used to look
flat in resolution, and that was the *untied* infrared floor — a flat ~220 s whatever was
asked for — which `--fast-ir` removes and which is no longer the default. Untied, a 300
dpi RGBI pass cost 219 s; tied, it costs 25 s. At 3600 dpi the saving is only 3.4%, so the
switch matters most where the pass is cheapest. (212 s survives in the code as the
conservative end of that range, because it guards a timeout rather than describing a cost.)

**7200 dpi has been driven and is not recommended**: softer than 3600, twice the time and
four times the bytes. It also **cannot be shading-corrected** — the device caps its
reference at 5172 columns and will not produce one for a 10344-column pass — so `scan()`
refuses a 7200 dpi pass with shading on rather than shipping it uncorrected. 3600 dpi is
the least aliased rate. See `docs/dpi-tradeoff-plan.md` and `docs/7200dpi-plan.md`.

The device accepts far more rates than the window lists; a dpi probe has driven 150, 400,
450, 1000, 1440, 2000, 2400 and 3000 as well.

## Output

16-bit TIFF by default, RGB or RGBI, written by `rps7200/tiff.py` or by `tifffile` when it
is installed — held to identical behaviour by `tests/test_tiff.py`, which runs every
write/read pairing of the two against each other. Writes are deflate-compressed with a
horizontal predictor, which is lossless and 8–18% smaller (13.4% on average, RGBI best);
`docs/tiff-compression-plan.md` verified the pixels come back identical.

JPEG output needs Pillow (`uv sync --extra jpeg`) and falls back to TIFF with a note
rather than losing the scan. A DNG companion can be written beside the TIFF with the same
stem; it is uncompressed, because the writer only deflates float data.

The fourth channel is tagged `ExtraSamples = unspecified`, which is what makes the IR
plane survive a round trip through readers that would otherwise treat it as alpha.
`rolls/` output is always TIFF.

## Filing every scan automatically

`tools/scan.py` and `tools/scan_roll.py` file in `library/` by default. Anything else —
an ad-hoc script, a probe — files too if `RPS7200_DEBUG=1` is set, which is worth doing
for anything whose result might matter later:

```sh
RPS7200_DEBUG=1 uv run python your_script.py          # macOS, Linux
$env:RPS7200_DEBUG=1; uv run python your_script.py    # PowerShell
```

It is off by default because ordinary use should not be burdened: an 1800 dpi RGBI entry
is about 63 MB. `RPS7200_DEBUG_ROOT` moves where they go.

Each scan is spooled to a temporary file as it is taken — a plain sequential write — and
the entries are assembled and gzipped **after the device is closed**, because gzipping one
with the scanner open and idle preceded a wedge. It is spooled rather than held in memory
because a 7200 dpi RGBI frame is 570 MB of pixels and about as much again of raw bytes, so
a seventeen-frame roll in RAM would want 19 GB.

### What a roll costs on disk

Film grain barely compresses — the median `raw.bin.gz` across the library is about 94% of
the raw size — so a library entry is close to twice the pixel size. A 38-frame roll, with
deflate-compressed TIFFs:

| dpi | entry | 38 frames |
|---|---|---|
| 1800 RGBI | ~63 MB | ~2.4 GB |
| 3600 RGBI | ~246 MB | ~9.3 GB |

## Whole-roll scanning

`tools/scan_roll.py` walks a strip: prescan, decide, scan, advance. A frame is about 3 to
5 minutes all in — ~16 s prescan, up to 48 s metering, the scan itself, ~7 s advance — so
a 36-frame roll at 1800 dpi RGBI is a couple of hours and one calibration (3–4 min) covers
all of it.

Frame numbers are places on the strip: frame N is where the transport's own counter
(`READ_STATE` byte 2) reads N-1, counted from where the strip went in. It has been seen
resetting to 0 as a strip goes in — once, in `full_17_strip` (`docs/protocol.md` §9) — so
the numbers are right as long as the strip is in the way it was when it was walked, and
only the operator can see that. A roll — from the window or from `--start-at N` here —
waits for the lamp, reads that counter and winds or advances the film to its first frame,
and refuses with nothing scanned if the counter will not answer or the film does not
arrive. A frame is numbered by that counter throughout, so a film that jumps two places
mid-roll is filed where it landed and the log names the frame it went past; a counter
that reads behind the roll's count ends the roll, naming the frames it went back over,
rather than scan them twice. A roll used to start wherever the film happened to be and
call that frame 1. Manifests written before this say so by lacking
`"numbering": "strip"`, and each frame in them is moved onto the strip's numbers by its
own recorded transport position rather than read by its number. Two readings are not
followed, and both are logged: a position no strip has, such as the stale 72, and one that
gives a number another frame's does where the frames around it put it elsewhere, which is
how a misread counter looks — those take the shift of their own walk or run. A walk, or a
`roll.json` this tool wrote, is one run, which numbered no two frames alike, so there a
frame that such a move lands on is moved along too, and that is logged. Two frames whose
own runs put them on one place in a `roll.json` the window merged, because the film went
over it twice between two rolls filed under one name, are both kept there, and that is
logged too.

`--start-at` resumes a roll that stopped, `--max-failures 3` gives up after three bad
frames rather than grinding through a whole strip, and a resumed roll carries forward what
the earlier session already did instead of overwriting its manifest.

### What drives the transport

Whole-frame moves are `SLIDE_NEXT` and `SLIDE_PREV` (`04 01 00 01` / `05 01 00 01`),
measured and safe. Sub-frame positioning also works and was calibrated from the host:
`SLIDE 00 <param> 00 04` forward and `01 <param> 00 04` back move `0.1057 × param +
0.1662` mm, to ±0.02 mm. It ships as `nudge()` and the hold loop behind
`scan_roll.py --correct`, which is **off by default** — the vendor does not reposition
during a roll either, so drift is reported and only corrected when asked.

**`SET_SCAN_HEAD` (0xD2) is never sent by anything here**, and should not be. It drives a
mechanism with a step count whose unit is unknown and reports nothing at all: no error, no
sense condition, no byte of `READ_STATE` changing. 10 and 100 steps looked like a clean
no-op; 1000 turned the gears audibly and needed a power cycle. CyberView sends it zero
times across every capture, and the `pieusb` backend refuses the neighbouring mode as
"unreliable, possibly dangerous".

`State.media_loaded` is byte 8 and **is inverted** — 1 with an empty transport, 0 with
film. The captures cannot corroborate it, because all nine were taken with film in, so the
driver reports it and lets the scanner refuse rather than gating on it.

### Registration

`film_bounds()` finds the frame in the aperture by looking for the bright clear gap beside
it, using the same contrast the metering crop relies on. Across 3850 pairs no wrong match
ever beat a confidence of 55, and the weakest right match scored 54.9, which is where the
floor sits. Seventeen slides showed no drift at all: every reading fell inside the
aperture's own 0.49 mm of slack. See `docs/registration-confidence-plan.md`.

## Scanner details

Read from the device's own INQUIRY response:

| | |
|---|---|
| vendor / product | `PIE` / `MF Scanner` |
| USB id | `0x05e3:0x0144` (Genesys Logic USB→SCSI bridge) |
| model | `0x0031` |
| firmware | `1.70` |
| optical resolution | 7200 dpi |
| filters | Infrared, Red, Green, Blue |
| colour depths | 16 / 12 / 8 / 1 bit |
| optional devices | ADF |
| fast preview | 300 dpi |
| scan area | 36.4913 × 24.2993 mm |

`Reflecta` / `RPS 7200` is what this driver writes into DNG `Make`/`Model`; it is not what
the device calls itself.

## Documentation

Each of these is an investigation with its measurements kept, not a design note. Four of
them record something that was tried and **rejected**, which is the part worth reading
before proposing it again.

| Doc | Contents | Status |
|---|---|---|
| `protocol.md` | The wire protocol, decoded from 8,133 vendor SCSI commands across nine captures | reference |
| `scanner-options-survey.md` | Every SilverFast/VueScan/pieusb scanning option and what this driver does about each | reference |
| `shading-calibration-plan.md` | Applying the scanner's own two-point reference — what removed the column stripes | shipped |
| `exposure-negative-plan.md` | Is the 0.80 exposure target right on colour negative? Fifteen metered frames walked | shipped |
| `fast-infrared-plan.md` | The quality bit that ties the infrared plane to the scan resolution | shipped |
| `dpi-tradeoff-plan.md` | Which resolution is worth it, measured by aliasing at each pass's Nyquist | shipped |
| `tiff-compression-plan.md` | Lossless deflate plus horizontal predictor, pixels verified identical | shipped |
| `whole-roll-plan.md` | Driving the transport for a whole roll, and hunting phantom drift | shipped |
| `registration-confidence-plan.md` | Can frame-finding be confidently wrong on self-similar frames? | shipped |
| `7200dpi-plan.md` | Why 7200 dpi cannot be shading-corrected, and the even/odd column stagger fix | mixed |
| `multi-exposure-plan.md` | N-exposure bracketing and inverse-variance merge, built and measured | **rejected** |
| `analog-gain-plan.md` | Is the gain field analog? A five-rung blue ladder answers | **rejected** |
| `vignette-plan.md` | Whether this scanner has a vignette, measured by rotating an IT8 | **rejected** |
| `byte14-plan.md` | Does MODE SELECT byte 14 change the line rate? And the silent row reversal | **rejected** |

## Development

```sh
make all          # fix + lint + type + tests, before committing
make test         # pytest with coverage
make test-all     # both TIFF paths: tifffile present and absent
make fix          # safe autofixes only
make lint         # ruff
make type         # ty (not mypy)
make clean        # caches and build artefacts
make reconstruct  # re-decode every stored scan with current code
make verify       # the library's checksums and completeness
```

`make format` reformats every file and is deliberately **not** part of `make all`: this
source is hand-wrapped, and a wholesale reformat rewrites thousands of lines and buries
the real change.

The suite runs with no scanner on the bus — `addopts = -m 'not hardware'` deselects the
nine tests in `tests/test_hardware.py`, which open the device and send nothing but INQUIRY
and READ STATE. CI runs the whole thing on macOS, Linux and Windows.

Host-side behaviour is tested against **stored bytes, not against the scanner**: every
library entry keeps its raw bytes, shading reference and CCD mask, so decoding, correction
and merging are all re-runnable offline.

## Notes and limitations

- Dust removal is not done here. The infrared plane is delivered raw, which is the point.
- Infrared does nothing for traditional silver-halide black and white, and is refused
  there. Chromogenic C-41 black and white is the exception.
- The gain field is a digital multiplier and buys nothing: measured ×1.484 signal against
  ×1.476 noise. Blue's rail limit in RGB cannot be lifted that way — scan RGBI, where blue
  is about 5× more sensitive.
- `SET GAIN OFFSET` does not persist across a scan sequence; exposure goes through each
  scan's `exposure_scale`.
- The captures are not distributed. They record traffic from every device on the bus,
  keyboard HID reports included.

### Recovering a wedged scanner

Power-cycle it at its own switch. Unplugging USB is not enough — the lamp and the carriage
are held by the scanner's own controller.

Avoid the four things that have caused it: abandoning a read mid-scan, holding the session
open through heavy local work, `IEEE1284 RESET` or `STOP SCAN` (the vendor sends neither,
and both leave the device unresponsive), and `SET_SCAN_HEAD`.

## Licence

GPL-3.0-or-later — see [LICENSE](LICENSE). Copyright © 2026 Stefan Kurzella.

Two pieces of this stand on other people's work:

- The shading correction in `rps7200/shading.py` follows the algorithm in SANE's `pieusb`
  backend (`pieusb_calculate_shading`, `sanei_pieusb_correct_shading`), and the INQUIRY
  field offsets follow `sanei_pieusb_cmd_inquiry`. No pieusb code is copied or
  distributed — its published source was read as a protocol reference and reimplemented in
  Python. That backend is GPL-2.0-**or-later**, and the "or later" is what makes GPL-3 an
  option; this project takes it, for the patent grant and the clearer terms.
- `rps7200/bracket.py` adapts the inverse-variance merge from
  [pyopticfilm](https://github.com/jboneng/pyopticfilm)'s `exposure_merge.py`, on its
  `feat/me-n-brackets` branch. The structure is theirs; the noise constants and thresholds
  are ours, measured on this sensor. pyopticfilm is GPL-3.0-or-later, the same licence as
  this project.

The rest was derived from the scanner's own behaviour and from USB captures of the vendor
software, for interoperability.

## Legal

Not affiliated with, endorsed by or connected to **Reflecta**, **Pacific Image
Electronics**, or the makers of **CyberView**, **VueScan** or **SilverFast** in any way.
Those names, and any film stock named in the examples, are trademarks of their respective
owners and are used here only to say what this software talks to and what it was tested
against.

This is hobby reverse engineering. Nothing here is a supported product. I am not
responsible for damage to your scanner, your film, your computer or anything else, and on
Windows this asks you to replace a working driver with one of your own choosing, which
will stop the vendor software seeing the device until you put it back. Use it at your own
risk.
