# rps7200

Console and Python scanning for the **Reflecta RPS 7200** film scanner, capturing
**RGB and raw infrared together** in one pass at 16 bits.

The infrared plane is handed to you untouched rather than being spent internally on dust
removal, so you can run your own IR-based cleanup.

## Why this exists instead of just calling `scanimage`

The scanner works fine under SANE's `pieusb` backend, but three things stop the stock
command-line frontend from getting the data out.

**1. `scanimage` cannot write the 4-channel RGBI frame.** In `pieusb.c`, RGBI mode sets
`colors = 4` but reports the frame type as `SANE_FRAME_RGB` — the source comment reads
`/* was: SANE_FRAME_RGBI */`. `scanimage` hardcodes 3 channels for that frame type, so it
writes a header claiming 3 samples/pixel over a stream carrying 4. The result is sheared,
with the IR smeared through the visible channels. (The backend declares a
`write_tiff_rgbi_header` helper for exactly this problem and then never calls it.)

**2. The device name changes on every open.** The backend resets the scanner during
discovery, so it re-enumerates and its SANE name moves — `020:057`, `020:060`, `020:010`,
`020:021`, … A name captured from one `scanimage -L` is already stale by the next command,
which is why `scanimage -d pieusb:libusb:020:057` fails with "Invalid argument".
Enumeration and open have to happen in the same process.

**3. Prescan calibration lives on the open handle.** `sanei_pieusb_analyze_preview` stores
the per-channel bounds on the scanner handle, and `calibration="from preview"` reads them
back. Close the handle and the calibration is gone — so a shell loop cannot prescan and
then scan.

This package does not use SANE at all. It drives the scanner directly over USB, the way
the vendor software does, holds one session across prescan and scan, and writes the
4-channel data itself. The three problems above are why: a SANE frontend cannot get this
data out, and the backend cannot apply the shading correction either.

## Install

```sh
pip install -e .
```

numpy is the only hard dependency; the GUI adds none, because Tk ships with
Python and its `PhotoImage` reads the raw PPM bytes `rps7200.preview` produces. libusb is needed to talk to the scanner, and is loaded
the first time something actually does — so decoding a stored scan, merging a bracket or
writing a TIFF works on a machine with no scanner drivers at all. SANE is not required. `tifffile` is optional; it is used automatically when
present, and the built-in TIFF reader/writer is complete on its own. The two are held to
the same behaviour by `tests/test_tiff.py`, which runs every write/read pairing of them
against each other. `make test-all` runs the suite both ways:

```sh
make test-all
```

## Use

One calibration per power-on, then scan. **Load the film first**, wait for the lamp
(about 80 s), then calibrate and scan without unloading it -- CyberView does everything
with the film in the transport, and calibrating an empty one is a state the vendor
software never puts the scanner in. The calibration still measures the sensor rather
than the film: its frame is `(0, 3431, 10343, 6888)`, the lower part of the transport,
which the film does not cover.

```sh
# calibrate, scan, correct, and file the result with its raw bytes
python3 tools/scan.py --dpi 1800 --ir \
    --stock "Kodak Gold 200" --frame 3 --notes "test frame"

python3 tools/scan.py --dpi 600                  # faster, RGB only
python3 tools/scan.py --dpi 1800 --no-shading    # raw pixels, for comparison
python3 tools/scan.py --dpi 1800 --reuse         # reuse the cached reference
python3 tools/scan.py --film positive            # a slide: keeps its colour cast
python3 tools/scan.py --film bw                  # one channel out, infrared refused
```

### What `--film` changes

It is a metering and delivery decision, not a mode the scanner has. Every pass this
driver takes is RGB or RGBI; there is no black and white mode worth using, and CyberView
does not use one either.

| `--film` | metering | infrared | delivered |
|---|---|---|---|
| `negative` | per channel — takes the orange mask off before the ADC | allowed | 3 or 4 channels |
| `bw` | per channel — silver halide has no colour record to protect | **refused** | one channel |
| `positive` | locked — the cast *is* the picture | allowed | 3 or 4 channels |
| `kodachrome` | locked | **refused** | 3 or 4 channels |

**Infrared is refused on black and white and Kodachrome** rather than warned about.
Silver grain blocks infrared exactly as dust does, and Kodachrome's cyan layer absorbs
it — so the plane comes back holding the photograph instead of what is lying on top of
it, measured here at +0.97 correlation with green, and the pass still costs its ~212 s
floor. Chromogenic black and white (XP2, BW400CN, anything C-41) is the exception: it is
dye-based and cleans properly, so scan it as `--film negative`.

**Locking matters, and black and white is not locked.** A slide and a Kodachrome keep
their balance because the cast is the picture. Silver halide has no dye layers, so what
looks like a cast is the film base and the sensor's own response — holding the channels
together to preserve it cost green and blue about half a stop each, measured on repeat
pairs at 900 dpi. This diverges from `nkscan`, which locks monochrome; that is right for
a Coolscan, whose exposure register is 26 bits wide with no channel that runs out, and
wrong here, where red's ceiling is a rail the lock propagates to the other two.

A whole strip or roll, unattended:

```sh
python3 tools/scan_roll.py --dry-run --frames 6                 # prescan and advance only
python3 tools/scan_roll.py --dpi 1800 --ir --frames 6 \
    --roll 2026-08-28-gold200 --stock "Kodak Gold 200"
```

Every scan is filed in `library/` by default, with the raw bytes the scanner sent, the
session's shading reference and that pass's CCD mask. None of those can be recovered from
a TIFF, and without them a scan can never be re-decoded or re-corrected:

```sh
python3 tools/library.py list          # what is stored
python3 tools/library.py verify        # checksums and completeness
python3 tools/library.py reconstruct   # re-decode every scan with current code
python3 tools/make_comparison.py       # raw / corrected / inverted, for eyeballing
```

From Python:

```python
from rps7200.direct import DirectScanner
from rps7200.shading import apply_shading

with DirectScanner() as s:
    s.calibrate_shading()                        # once per power-on
    image, meta = s.scan(resolution=1800, infrared=True)
    rgb, ir = image[..., :3], image[..., 3]      # (H,W,3) and (H,W), uint16
```

### A window instead of a command line

```sh
make run                        # the scanner
make run-demo                   # no scanner: stored library entries drive the window
```

The demo decodes each entry's **raw bytes**, not the TIFF beside them -- so it runs the
same deinterleave and the same shading correction a real pass runs, and files entries
that `library.py reconstruct` reads back as identical. It picks its picture by film type,
answers at the resolution asked for, and refuses what the device refuses. Where it cannot
honour a request with the bytes in hand -- asking a four-channel entry for RGB -- it drops
the bytes and says so, rather than filing raw that decodes to a different photograph.

Prescan, scan, walk a roll, and look at what came off -- the filmstrip along the
bottom holds every pass of the session, prescans included, and clicking one puts
it back on the canvas. The channel selector switches between RGB and R, G, B or
**infrared alone**, which is the one plane no ordinary viewer will show you.

The preview is inverted by default so a negative can be judged by eye, and that
inversion is display only: what reaches `library/` is the raw negative with its
raw bytes, exactly as `tools/scan.py` files it. Inverting for real is NegPy's
job.

The film selector drives what is beside it. Set it to black and white and the
infrared box clears and greys out, "deliver one channel" ticks, and the view
switches to that channel -- which `preview.render` draws grey rather than
tinted, so the prescan and the scan both come up as photographs rather than as a
green separation. Set it back and the box returns, unchecked: silently re-arming
a 212-second pass is not something a settings change should do.

Opening the window claims the device and asks it who it is, and nothing else --
no calibration, no lamp, no transport until a button is pressed.

**Stopping.** A pass in flight cannot be interrupted safely: infrared holds the
device for its ~212 s floor however few lines were asked for, and an abandoned
read is what costs a power cycle. So *Stop* is cooperative -- it ends a roll
after the frame in flight, and a single scan after the pass finishes -- and says
which it will do. *Force abort* closes the transport out from under the read,
which is the only thing that actually unblocks it; the frame is lost and the
scanner will almost certainly need a power cycle at its own switch. It asks you
to type ABORT first.

#### Trying the contact sheet without a scanner

`make run-demo` walks a strip of real stored pictures, so the whole
survey-and-pick sequence can be exercised and checked with nothing plugged in:

1. **Roll** panel: set *frames* to 6 and tick **dry run**.
2. Press **Scan roll**. It walks six frames -- a different stored picture each
   time -- and the contact sheet opens by itself when it reaches the end.
3. Every cell shows one frame with its number, its contrast and its offset in
   millimetres. Those numbers come from the real `registration()` and
   `frame_contrast()` measuring the real pictures, so they differ frame to
   frame; if every caption reads the same, something is wrong.
4. Click pictures to untick them, or use **All** / **None**. The footer counts
   what is chosen and estimates what scanning it would cost.
5. **Scan chosen frames** asks to rewind five frames and scan the ticked ones.
   Accept it and watch the log: the frames nobody ticked say *"not chosen,
   advancing past it"* and are never prescanned.
6. Close the sheet and press **Contact sheet ...** to get it back.

What is worth checking, because these are the ways it could quietly be wrong:
the cells are six *different* pictures; the rewind in the dialog is one less
than the number walked; and only the ticked numbers appear as `frameNN.tif`
under `rolls/<name>/`, while `survey.json` there still lists all six.

**The contact sheet.** A dry run walks the strip prescanning and advancing only
-- about 20 seconds a frame -- and opens every picture it found in a grid, with
its number, its contrast and how far off centre it sits. Tick what is worth
having and only those frames are scanned: the film is rewound to where the walk
started, and a frame nobody ticked costs its ~7 s advance instead of the three
to six minutes a scan of it would. Seventeen frames at 3600 dpi RGBI is three
hours, and a strip with four keepers on it should not cost the same as one with
seventeen. The walk writes `survey.json` and a `prescanNN.tif` per frame beside
it, so a strip can be looked at again tomorrow instead of walked again.

The options are the ones the driver implements: resolution, infrared, film type,
exposure (metered or by hand), shading (measure or reuse), and for a roll the
frame count, a start-at for resuming, the metering mode and a dry run.
Bracketing is deliberately absent -- see `docs/multi-exposure-plan.md`, which
measured it and found it does not pay.

The resolutions on the menu are the ones this scanner has been driven at --
300, 600, 900, 1800, 3600 -- plus the 7200 dpi it reports as its optical
maximum. The box is editable and there is no client-side validation, so anything
else can be typed: it goes into MODE SELECT and the device refuses what it
dislikes with sense `0x26/0x82` before a byte of image data moves.

**It remembers the setup.** Resolution, infrared, film, exposure, metering,
where the files go, the window size and the pane widths all come back next
launch, from `gui-settings.json` beside the library (`RPS7200_SETTINGS` moves
it, `--settings` overrides it). Scan settings can be saved as named presets.
Losing that file costs a few seconds of resetting controls and nothing else: a
missing or corrupt one opens the window on its defaults rather than not opening
it.

What is deliberately *not* remembered is the roll name, frame, subject and
notes. Those describe one shot, and a stale value would file today's scan under
yesterday's name.

**Files are named by roll and frame**, because NegPy reads them next:

    2026-09-09-gold200_frame03_3600dpi_ir.tif

in whichever folder the window is pointed at, prescans in a `prescans/`
subdirectory beside them. A frame rescanned after a failure gets a suffix rather
than overwriting the first attempt -- the better of the two is not always the
second. The library entry keeps its own timestamped id, which is what makes it
findable years later.

## How scans are corrected

**The scanner returns raw pixels and never corrects them itself.** It measures its own
per-column sensor response during a calibration pass and hands that measurement back, but
applying it is the host's job. Run the calibration and discard the result — as this driver
did for a long time — and nothing changes in the image, which reads like a broken
calibration rather than a missing step.

The pass returns two phases per channel, unlit then lit: a dark reference averaging ~170
counts and a light reference averaging ~47,000. Correction is one division per column:

```
value = (raw - dark[c][j]) * (mean_light[c] - mean_dark[c]) / (light[c][j] - dark[c][j])
```

`j` is not the output column. The reference spans the whole CCD including pixels a given
pass never reads, so the **CCD mask** — read fresh on every pass — maps output columns to
reference columns. At 600 dpi it marks 860 of 5172 pixels used, starting at pixel 5; at
300 dpi, 428 starting at pixel 11. That per-pass mapping is what keeps the correction
aligned at any resolution.

Measured on a real frame at 1800 dpi, as how well the top half of the frame predicts the
bottom — which separates a reproducible sensor pattern from picture content:

| | raw | corrected |
|---|---|---|
| red | 0.897 | 0.265 |
| green | 0.782 | 0.242 |
| infrared | — | worst column defect 4.67% → 0.89% |

Blue does not improve, and should not: its raw figure is 0.153, so it has no fixed pattern
to remove. Blue carries the least signal on this scanner, so its column variation is noise.

The reference belongs to the power-on that measured it. `calibrate_shading()` is therefore
run once per session, exactly as the vendor software does at power-on.

## Exposure

`--auto-exposure` probes at 300 dpi and aims each channel's 99.5th percentile at
**0.80** of full scale, measured **inside the film** rather than across the whole
transport window. The probe is always RGB, in two rounds, plus one further round
only if a channel came back clipped — there the correction is a retreat rather than a
measurement, and everything else is settled in one proportional step because the sensor
is linear (r² = 0.9999 over a 4× range).

Three things about it are worth knowing before changing anything:

- **Exposure is a 16-bit timer that wraps.** Past 65535 a pass comes back *darker*, not
  brighter. `READ GAIN/OFFSET` hands back a fixed reference — `9604, 6506, 6506, 7745` —
  rather than what is in force, so every scan is `base × scale` and exposure cannot
  compound. Red's base is the highest, so its ceiling is ×6.82 where green and blue get
  ×10.07, and red is the channel that runs out first.
- **The band above the target is tighter than the band below it.** 0.08 under, 0.02 over.
  Landing under costs a little noise; landing over clips, and nothing downstream undoes
  that. When the target moved from 0.70 to 0.80 with a symmetric band, the top of the
  acceptance window went to 0.88 and a frame duly landed at 87% with samples at the rail.
- **Blue comes back several times brighter in an RGBI pass than in the RGB probe**, so
  its target is divided before an infrared scan. How much depends on the film —
  4.98–5.02 on colour negative, ~9.6 on black and white, each from a matched pair minutes
  apart with red and green confirming the mode was the only variable. One constant for
  every film put 34% of a B&W scan's blue channel at the rail.

- **Metering looks inside the film, and finds it once.** It keeps 90% of the width and
  90% of the height — **81% of the pass** — of whatever the film detector returned, which
  is the whole window when no aperture is in view. The empty aperture beside a
  strip is far brighter than any part of a negative -- 143/153/153 against the film's
  34/15/7 on a C-41 prescan -- so metering the whole window lets however much aperture is
  in view set the exposure. Measured on real prescans it read the percentile 5.9-11.1%
  high, and the scan came out that much short, silently and differently for each frame.
  The film is located on the first probe *while it is still dark*: the detector needs the
  aperture to be twice the median, and metering's job is to brighten the film until it is
  nearly as bright as the aperture, so by the round that settles the exposure that
  contrast is gone.

What the probe measured is filed with the scan, so the numbers above stay checkable from
ordinary work rather than needing a special run.

## Resolution

`--dpi` goes straight into MODE SELECT as a 16-bit field, so the device refuses what it
dislikes with sense `0x26/0x82` rather than the driver guessing. The default is **1800**.

Sizes are the geometry the device actually reports, not a calculation — it rounds its own
way, and 600 dpi returns 573 or 574 lines depending on the pass:

| `--dpi` | pixels | 3ch × 16-bit | 4ch × 16-bit | seen in a capture |
|---|---|---|---|---|
| 300 | 428 × 286 | ~0.7 MB | ~1 MB | yes |
| 600 | 860 × 573 | ~3 MB | ~4 MB | yes |
| 900 | 1292 × 860 | ~7 MB | ~9 MB | yes |
| 1200 | 1724 × 1148 | ~12 MB | ~16 MB | yes |
| 1800 | 2584 × 1721 | ~27 MB | ~36 MB | yes |
| 3600 | 5172 × 3443 | ~107 MB | ~142 MB | yes |
| 7200 | 10344 × 6888 | ~427 MB | ~570 MB | no — the INQUIRY maximum only |

Only those marked have been driven, by CyberView or by this driver. The window's ladder
offers exactly them; the box is not a limit, but a menu of guesses reads as a menu of
capabilities. The divisors of 7200 nobody has asked for are in
`docs/dpi-tradeoff-plan.md` as candidates, not as claims.

**Scan time is set by exposure, not only by line count.** Thirteen 3600 dpi RGB scans
fit `ms/line = 2.60 + 4.851e-4 × sum(exposure)` at r² = 1.0000, which is why a dense
negative can take four times as long as a slide at the same resolution. An infrared pass
has its own floor of ~212 s whatever the resolution, so it dominates below about 1800 dpi.

A prescan is a separate, cheap pass — 300 dpi, RGB, 8-bit, the whole transport — and takes
its own resolution rather than the scan's.

## Output

One TIFF per frame, uint16, plus a JSON sidecar recording resolution, geometry,
exposure/gain/offset, what the metering probe measured, and the settings used.

The shape depends on what was asked for:

| | shape | channels |
|---|---|---|
| `--ir` | `(H, W, 4)` | R, G, B, IR |
| plain | `(H, W, 3)` | R, G, B |
| `--film bw` | `(H, W)` | one, green by default |

The IR plane is tagged `ExtraSamples = 0 (unspecified)` — meaning "data, not alpha".
Some viewers (macOS Preview included) still report `hasAlpha: yes` and may composite it.
That is a viewer convention, not a problem with the file.

**A black and white scan is delivered flat, as `(H, W)` and not `(H, W, 1)`.** That
distinction decides whether a consumer recognises it: NegPy classifies by the minimum
correlation between channels, and measured across this library, black and white spans
0.926–0.988 while colour negative spans 0.008–0.976. They overlap, so no threshold
separates them — a three-channel B&W scan came back from NegPy's own classifier as
**Transparency**, processed as a slide. One channel makes it certain. The library still
files all three; only what leaves is reduced.

Nothing consumes or alters the IR plane on the way out.

### Filing every scan automatically

`tools/scan.py` and `tools/scan_roll.py` file each scan in the library with its raw
bytes. Anything calling `DirectScanner` directly used to file nothing — which is how a
week of diagnostic scans left no record at all, and why some evidence that was wanted
later no longer existed.

`DirectScanner` can now do it itself:

    RPS7200_DEBUG=1 python3 my_script.py          # or DirectScanner(debug=True)

Every `scan()` is then filed, tagged `debug`, with raw bytes, shading reference and
CCD mask — everything needed to re-decode it later.

**Off by default**, because an 1800 dpi RGBI entry is ~35 MB and routine use should not
pay for that. Turn it on for anything exploratory, where the scan you did not think
mattered is exactly the one you will want.

Each scan is spooled to a temporary file as it is taken, and the entries are assembled
and compressed after the device is closed — gzipping one with the device open and idle
has preceded a wedge. Spooled rather than kept in memory because a 7200 dpi RGBI frame
is 570 MB of pixels plus as much again of raw bytes, so a seventeen-frame roll would
otherwise want 19 GB of RAM; only paths stay resident, and the spool is deleted once
filing is done.

A filing failure is logged and swallowed — losing the record beats losing the session
that produced it.

Set `RPS7200_DEBUG_ROOT` to file somewhere other than `library/`.

`tools/scan.py` and `tools/scan_roll.py` pass `debug=False` explicitly, because they
file their own entries and letting the driver file as well writes every frame twice.

### What a roll costs on disk

Film grain barely compresses — a real `raw.bin.gz` is 96.7% of the raw size — so
plan for close to the uncompressed figures. A 38-frame roll:

| dpi | `rolls/` TIFFs | library | total |
|---|---|---|---|
| 1800 | 1.4 GB | 2.7 GB | **4.0 GB** |
| 3600 | 5.4 GB | 10.7 GB | **16.1 GB** |
| 7200 | 21.7 GB | 42.6 GB | **64.3 GB** |

With `RPS7200_DEBUG=1` on an ad-hoc script, add one frame of spool on top —
1.1 GB at 7200 dpi — not the whole roll, because each frame's spool is freed as soon
as its entry is written.

## Checking that the IR is real

A genuine IR plane sees through the dye layers, so it should **not** track the visible
channels: dust and scratches show as marks while the picture content is largely absent.
Correlate channel 3 against 0–2 — anything above ~0.9 means the IR is contaminated,
usually a channel mix-up:

```python
import numpy as np
from rps7200 import tiff

image = tiff.read("scan.tif").astype(float)
for i, name in enumerate("RGB"):
    print(name, np.corrcoef(image[..., i].ravel(), image[..., 3].ravel())[0, 1])
```

## Notes and limitations

- **A scan reports nothing until a batch of lines is ready.** Reads are paced against how
  far the scanner has physically scanned, so the driver spends most of a pass waiting.
  Run with `-v` to see the line counter move.
- **The lamp needs to warm up after a power cycle** — about 80 seconds. Until it does, the
  scanner answers `NOT READY` to every command, `READ STATE` included, so it cannot even
  be asked for its state. `DirectScanner.wait_warm()` polls through it and every scan
  calls it, up to 300 s.
- **The scanner can drop off the USB bus** after a failed scan and then needs a power
  cycle before it reappears. `DirectScanner().inquiry()` is the cheap way to check whether
  it is there at all.
- **`sane-find-scanner` reports "could not fetch string descriptor: Pipe error".** This
  device exposes no USB string descriptors (`iProduct = 0`), so the message is expected —
  but see below, because a flaky USB link produces similar symptoms.

### If scans fail during shading data

A scan that gets through warm-up and then dies here:

```
sanei_pieusb_get_shading_data()
sanei_pieusb_cmd_get_scanned_lines(): 4 lines (82752 bytes)
_pieusb_scsi_command read data failed for size 32768: 9
sanei_pieusb_usb_reset()
```

means the scanner accepted the SCSI READ but the **32 KB bulk transfer timed out** (30 s,
status 9 = `SANE_STATUS_IO_ERROR`). Small commands — INQUIRY, read state, gain/offset,
every option read — still work fine, so the driver and the device are talking; only bulk
data fails.

This is **not** a USB link problem, though it looks like one at first. The stall is
deterministic to the byte -- exactly 32768 every run -- whereas a marginal cable or hub
fails at varying offsets. Reproducing it needs neither: an independent implementation in
this repo, talking straight to the device over libusb, stalls at the same 32768.

The scanner also drives fine under CyberView and VueScan on the same cable and port, so
hardware, media and link are all good. This is solved — see "What the stock backend gets
wrong" below — and is kept here because the symptom is what you hit first.

Diagnose with:

```sh
SANE_DEBUG_PIEUSB=11 scanimage --mode Gray --preview=yes --format=tiff -o /tmp/control.tif
```

## Whole-roll scanning

```sh
python3 tools/scan_roll.py --dry-run --frames 6          # prescan and advance only
python3 tools/scan_roll.py --dpi 1800 --ir --frames 6 \
    --roll 2026-08-28-gold200 --stock "Kodak Gold 200"
```

The film is already at the first picture when this starts, so the first frame is scanned
before anything moves and the transport advances between frames. Shading is calibrated
**once** for the whole roll — which is what the vendor does, and why a 17-pass session in
the captures contains no calibration at all.

Every frame reaches disk the moment it exists: a library entry with the raw bytes, the
shading reference and the CCD mask beside the pixels, plus a `roll.json` manifest
rewritten after each one. A roll takes hours; a crash should cost the frame it was on and
not the roll. `--start-at N` resumes.

Start with `--dry-run`. It prescans and advances only, so it walks a six-frame strip in
about two and a half minutes and shows where each picture sits before three hours are
committed to scanning them. Its manifest is `survey.json`, so a walk and the roll that
follows it into the same directory do not overwrite each other.

The roll stops on whichever comes first: `--frames`, a prescan with no picture in it, an
advance that does not move the film, or three consecutive failures. A single failed frame
is recorded in the manifest and the roll goes on.

### What drives the transport

This does not go through SANE, so nothing here depends on `FLAG_SLIDE_TRANSPORT` in
`pieusb.conf` — the flag that is `0x00` for this model and stops the stock backend
advancing film at all. The commands come from `captures/600_ICE_FILM_STRIP_5.pcapng`,
CyberView walking a 5-frame strip end to end:

| | |
|---|---|
| advance | `SLIDE` (`d1 00 00 00 04 00`) with data **`04 01 00 01`** |
| confirmation | `READ_STATE` **byte 2** is the transport position |

Byte 2 stepped `0 → 1 → 2 → 3 → 4` across that session's four advances and stayed put
through a session that never advanced, 1.6–6.2 s after the command. The `READ_STATE`
issued immediately after the advance came back empty every time, so the poll has to
survive a failed read rather than read it as the end of the film. This driver previously
sent `04 16 00 00` — a zero where every observed advance carried a 1.

`State.media_loaded` is not usable for any of this: its bit is clear in every state seen
across six captures, including ones taken with film demonstrably loaded.

### Registration

The transport window is 36.5 mm and a 35 mm frame is 36 mm, so there is half a millimetre
of slack, and a frame that drifts is a frame with its edge outside the aperture that no
scan window can recover. It happens: CyberView's own detected windows over its 5-frame
strip started at `x=96` for four frames and then at `x=1727` for the fifth, losing 6 mm of
picture.

Each frame's prescan is therefore measured — `registration()` reports a signed offset and
how far short of a whole frame the film measures, both in millimetres, and both go into the
manifest and the frame's metadata. A drifted frame cannot be seen directly — the prescan
only covers the aperture — but film *narrower* than a whole frame can, and that is the same
thing.

The measurement keys on **level, not variance**, and that distinction is load-bearing.
Film attenuates and an empty aperture does not, so the film's edge is a step in brightness:
measured on a C-41 negative, the clear strip read 143/153/153 in R/G/B against the film's
34/15/7, and every threshold from 60% to 90% of the clear level returned the same edges.
Keying on *variance* cannot work here: a dark, low-contrast frame varies less than the
film's own slightly-skewed edge, so any threshold set as a fraction of the peak selects the
border and discards the photograph. `film_bounds()` is what registration uses. **Drift is reported, not corrected.** No capture contains a command that moves
the film by less than a whole frame, and `SET_SCAN_HEAD` (`0xD2`) is never sent by
anything. `tools/transport_probe.py` measures whether one exists; until it says otherwise,
a drifting strip is a thing to be told about, not something the driver quietly papers over.

### Time and space

Scan time barely depends on resolution — the carriage traverse dominates. Measured on the
vendor: 216 s at 600 dpi, 218 s at 900, 218 s at 1800, 217 s at 3600, all RGBI 16-bit.
Ours agrees (227 s at 900 and 1800, 334 s at 3600). A 300 dpi RGB prescan is ~16 s, which
is what makes per-frame metering affordable.

Per frame: ~16 s prescan + up to 48 s metering + 217–334 s scan + ~7 s advance, so **4.7 to
6.9 minutes**. A 36-frame roll is 3–4 hours, plus one 3–4 minute calibration. `--meter
once` saves about 30 minutes and keeps the frames comparable to each other; `--meter each`
is the default and is what CyberView does. At 3600 dpi a library entry is ~250 MB, so a
roll is about 9 GB.

## Scanner details

Read from the device's own INQUIRY response:

| | |
|---|---|
| vendor / product | `PIE` / `MF Scanner` |
| USB id | `0x05e3:0x0144` (Genesys Logic USB→SCSI bridge) |
| model | `0x0031` |
| firmware | `1.70` (2007) |
| optical resolution | 7200 dpi |
| filters | Infrared, Red, Green, Blue |
| colour depths | 16 / 12 / 8 / 1 bit |
| optional devices | ADF |
| fast preview | 300 dpi |
| scan area | 36.4913 × 24.2993 mm |

## Development

```sh
make install      # sync the dev tools with uv
make all          # fix + lint + type + tests -- run this before committing
```

Individual steps are `make lint`, `make type` and `make test`; everything runs through
`uv run`, so the pinned tools in `pyproject.toml` are what execute.

No test needs a scanner attached. The suite covers channel derivation and both TIFF
paths, the shading parse and two-point correction, metering and film types, the scan
library (including that a stored entry still decodes to the pixels it was saved with),
and the roll/registration logic. A test that genuinely needs the device is marked
`hardware` and is skipped by default.

`tifffile` is optional and the built-in TIFF path is complete, so both have to behave
identically. `make test-all` runs the suite twice, once with it installed and once with
the import blocked:

```sh
make test-all
```

After any change to how the scanner's bytes become pixels, re-check every stored scan:

```sh
make reconstruct          # python3 tools/library.py reconstruct
```

## Solved: what the stock backend gets wrong

A USB capture of CyberView scanning this exact scanner resolved this. The image
read now works from Python. The differences that mattered, CyberView vs pieusb:

| | CyberView | pieusb |
|---|---|---|
| `CMD_17` after the scan frame | `0a 00 00 00 06 00` + `17 00 02 00 01 00` | only sent when the config marks a slide transport, which is 0 for model 0x31 -- so never |
| SLIDE second byte | `0x16` | `0x01` |
| READ_STATE size | 13 | 12 |
| READ_GAIN_OFFSET size | 123 | 103 |
| MODE SELECT byte 12 | `0x02` | halftone pattern (0) |
| CCD mask (SCSI COPY) size | 5172 | 10344 (`shading_width`) |
| shading data read | **never performed** | performed, and stalls at 32768 bytes |

The chain: without `CMD_17` the scanner refuses to grant "skip shading analysis"
(sense `0x82` = "calibration disable not granted"), so it insists on a shading
pass -- and that shading read is the one that stalls and drops the device off the
USB bus. CyberView skips shading entirely and reads image data directly.

Also learned from the capture:

- **`available_lines` paces the read.** It rises as the scanner physically
  scans, and asking for more lines than are ready stalls the read until it times
  out -- which is unrecoverable and costs a power cycle. This is why the vendor
  software's reads come in uneven sizes (216, 3, 216, 216, 105, 105): it takes
  whatever is ready. (It reads a constant 0 or 7 when the command sequence is
  wrong, which is misleading; with the correct sequence it behaves properly.)
- **INDEX colour format delivers one colour plane per line**, each with a 2-byte
  index header, so a scan is `channels x height` lines of `2*width + 2` bytes.
  CyberView's reads were 216+3+216+216+105+105 = 861 lines = 3 x 287 rows.
- CyberView never sends INQUIRY; it opens with READ_STATE polling.

### Recovering a wedged scanner

A bulk read that times out mid-transfer leaves the scanner unresponsive to
control transfers. `libusb_clear_halt` sometimes clears it; re-plugging does not,
as it re-enumerates without recovering. **Power-cycle at the unit's own switch.**

What provokes it, all learned the hard way:

- **Abandoning a read mid-scan.** Infrared holds the device busy for its own
  ~212 s floor however few lines were asked for, so a low-resolution IR pass can
  outlast a short timeout. `read_lines` allows 300 s for this reason.
- **Holding the session open through heavy local work** — gzipping a 140 MB
  library entry with the device open and idle preceded one wedge.
- **`STOP SCAN`, and IEEE1284 RESET.** Earlier versions of this driver sent both
  on every exit path believing it prevented the fault; it causes it. The vendor
  sends neither, and neither does this driver now.
- Probing `READ(10)` (`0x28`).
- **`SET_SCAN_HEAD` (`0xD2`)** — accepted silently at any step count with no
  error and no state change, but 1000 steps turned the gears audibly and needed a
  power cycle. Never send it; see CLAUDE.md.

## Licence

GPL-3.0-or-later — see [LICENSE](LICENSE).

The shading correction in `rps7200/shading.py` follows the algorithm in SANE's
`pieusb` backend (`pieusb_calculate_shading`, `sanei_pieusb_correct_shading`).
That backend is GPL-2.0-**or-later**, and the "or later" is what makes GPL-3 an
option: this project takes it, for the patent grant and the clearer terms.

The rest was derived from the scanner's own behaviour and from USB captures of
the vendor software, for interoperability. The captures themselves are not
distributed: they record traffic from every device on the bus, keyboard HID
reports included.
