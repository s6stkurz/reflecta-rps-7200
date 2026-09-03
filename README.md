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

This package drives `libsane` directly through `ctypes`, holds one session across prescan
and scan, and writes the 4-channel data itself.

## Install

```sh
pip install -e .
```

numpy and libusb are all that is needed — the driver talks to the scanner directly over
USB and does **not** require SANE. `tifffile` is optional; it is used automatically when
present, and the built-in TIFF reader/writer is complete on its own. The two are held to
the same behaviour by `tests/test_tiff.py`, which runs every write/read pairing of them
against each other, and the suite is run both ways:

```sh
python3 -m pytest tests/ -q                        # tifffile installed
RPS7200_NO_TIFFFILE=1 python3 -m pytest tests/ -q  # as on a bare install
```

A second, older interface (`rps7200 …`, `rps7200.device`) drives the scanner through
SANE's `pieusb` backend. It still works and needs `brew install sane-backends`, but it
cannot apply the shading correction described below. Prefer the direct driver.

## Use

One calibration per power-on, then scan. Power the scanner on with **no film loaded**,
wait for the lamp (about 80 s), then:

```sh
# calibrate, scan, correct, and file the result with its raw bytes
python3 tools/scan.py --dpi 1800 --ir \
    --stock "Kodak Gold 200" --frame 3 --notes "test frame"

python3 tools/scan.py --dpi 600                  # faster, RGB only
python3 tools/scan.py --dpi 1800 --no-shading    # raw pixels, for comparison
python3 tools/scan.py --dpi 1800 --reuse         # reuse the cached reference
python3 tools/scan.py --film positive            # a slide: keeps its colour cast
```

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

## Resolution

`--dpi` accepts **25–7200**; the default is **600**. The backend's own default is 300 dpi,
so a resolution is always set explicitly.

| `--dpi` | approx pixels | size (4ch × 16-bit) |
|---|---|---|
| 600 | 862 × 574 | ~4 MB |
| 1200 | 1724 × 1148 | ~16 MB |
| 3600 | 5172 × 3444 | ~142 MB |
| 7200 | 10344 × 6888 | ~570 MB |

At 7200 dpi expect roughly double the listed figure in memory, because the backend buffers
the whole image itself as well.

**The prescan ignores `--dpi`.** With `preview=yes` the backend forces its fast-preview
resolution (300 dpi on this scanner). Geometry is always read back from
`sane_get_parameters` after the scan starts rather than computed from the request.

## Output

One TIFF per frame, `(H, W, 4)` uint16, channels in **R, G, B, IR** order, plus a JSON
sidecar recording resolution, geometry, exposure/gain/offset and the settings used.

The IR plane is tagged `ExtraSamples = 0 (unspecified)` — meaning "data, not alpha".
Some viewers (macOS Preview included) still report `hasAlpha: yes` and may composite it.
That is a viewer convention, not a problem with the file; use `--split` when you want
files that display normally.

On the **SANE** interface, everything that would consume or alter the IR is off by
default. (The direct driver does not use these; it sets the equivalent mode bytes itself
and applies shading on the host, as described above.)

| SANE option | Value | Why |
|---|---|---|
| `mode` | `RGBI` | the one-pass four-channel mode |
| `depth` | `16` | |
| `clean-image` | `no` | otherwise the backend spends the IR on its own dust removal |
| `correct-infrared` | `no` | no red-crosstalk correction |
| `fast-infrared` | `no` | repositions the head so IR stays aligned with RGB |
| `correct-shading` | `yes` | asks the backend to do the host-side division; the direct driver does its own |
| `crop` | `None` | the backend default (`Inside`) crops the frame |

## Checking that the IR is real

```sh
rps7200 inspect scan.tif
```

*(`inspect` belongs to the SANE interface; for a scan taken with the direct driver, load
the TIFF and correlate channel 3 against 0-2 yourself.)*

A genuine IR plane sees through the dye layers, so it should **not** track the visible
channels: dust and scratches show as marks while the picture content is largely absent.
`inspect` prints the correlation between IR and each of R, G and B — anything above ~0.9
means the IR is contaminated (usually `correct-infrared` left on, or a channel mix-up).

## Notes and limitations

- **Scans block with no progress.** *(SANE interface.)* The backend performs the entire capture and its
  post-processing inside `sane_start`; `sane_read` only drains a finished buffer. The
  process will sit silent for a long time, and cancellation is only honoured between
  scans. This is the backend's design, noted in its own man page.
- **The lamp needs to warm up after a power cycle** — about 80 seconds, measured. Until it
  does, the scanner reports `warmingUp` and the backend turns that into
  `SANE_STATUS_DEVICE_BUSY` from `sane_start` instead of waiting. This package retries
  automatically (`Scanner(warmup_timeout=...)`, 300 s by default); stock `scanimage` just
  fails with "Device busy", so retry it by hand.
- **The scanner can drop off the USB bus** after a failed scan and then needs a power
  cycle before it reappears. If `rps7200 list` finds nothing, power-cycle it and retry.
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
committed to scanning them.

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
`detect_frame()` looks for a step in *variance* instead and fails badly here — a dark,
low-contrast frame varies less than the film's own slightly-skewed edge (std 36–59 against
the picture's 1–10), so a threshold set at a fraction of the peak keeps four columns of 428
and discards the photograph. It reduced a frame filling the window edge to edge to a
0.26 mm sliver and called it 35 mm of drift. `film_bounds()` is what registration uses. **Drift is reported, not corrected.** No capture contains a command that moves
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
python3 -m pytest tests/ -q
```

103 tests, none of which need a scanner attached: channel derivation and the TIFF paths,
the shading parse and two-point correction, metering and film types, the scan library
(including that a stored entry still decodes to the pixels it was saved with), and the
roll/registration logic.

After any change to how the scanner's bytes become pixels, re-check every stored scan:

```sh
python3 tools/library.py reconstruct
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
