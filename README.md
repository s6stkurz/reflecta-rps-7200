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

Requires SANE and numpy:

```sh
brew install sane-backends
pip install -e .
```

`tifffile` is optional — it is used automatically if present, but the built-in TIFF
reader/writer is complete on its own.

## Use

```sh
rps7200 list                      # confirm the scanner is visible
rps7200 list --options            # dump every backend option and its current value

rps7200 prescan -o preview.tif    # preview (the scanner picks the resolution)

rps7200 scan -o out/frame001.tif             # prescan + scan, 600 dpi
rps7200 scan -o out/frame001.tif --dpi 3600  # higher resolution
rps7200 scan -o scan.tif --split             # also write separate _rgb and _ir files
rps7200 scan -o scan.tif --no-prescan        # skip the calibration prescan

rps7200 inspect scan.tif          # verify channels, bit depth, and that IR is really IR
```

Override any backend option directly:

```sh
rps7200 scan -o scan.tif --set exposure-time-r=3200 --set gain-adjust='* 1.2'
```

From Python:

```python
from rps7200 import Scanner

with Scanner() as s:
    s.prescan()                       # also calibrates the scan below
    frame = s.scan(resolution=600)
    rgb, ir = frame.rgb, frame.ir     # (H,W,3) and (H,W), both uint16
```

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

Everything that would consume or alter the IR is off by default:

| Setting | Value | Why |
|---|---|---|
| `mode` | `RGBI` | the one-pass four-channel mode |
| `depth` | `16` | |
| `clean-image` | `no` | otherwise the backend spends the IR on its own dust removal |
| `correct-infrared` | `no` | no red-crosstalk correction |
| `fast-infrared` | `no` | repositions the head so IR stays aligned with RGB |
| `correct-shading` | `yes` | sensor calibration, not image editing |
| `crop` | `None` | the backend default (`Inside`) crops the frame |

## Checking that the IR is real

```sh
rps7200 inspect scan.tif
```

A genuine IR plane sees through the dye layers, so it should **not** track the visible
channels: dust and scratches show as marks while the picture content is largely absent.
`inspect` prints the correlation between IR and each of R, G and B — anything above ~0.9
means the IR is contaminated (usually `correct-infrared` left on, or a channel mix-up).

## Notes and limitations

- **Scans block with no progress.** The backend performs the entire capture and its
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

That split points at the USB link rather than the software. Worth trying, in order:
connect the scanner **directly to the machine** instead of through a hub or dock, swap the
USB cable, and use a rear/powered port. A hardware fault in the scanner (lamp or CCD not
producing data) presents the same way, so rule out the link first since it is cheaper.

Diagnose with:

```sh
SANE_DEBUG_PIEUSB=11 scanimage --mode Gray --preview=yes --format=tiff -o /tmp/control.tif
```

## Whole-roll autofeed (not enabled)

Film advance is gated on `FLAG_SLIDE_TRANSPORT`, which the backend reads from the fourth
field of the `pieusb.conf` line for this device. It is currently `0x00`:

```
usb 0x05e3 0x0144 0x31 0x00
```

With that at `0x00` the `advance` option is accepted but does nothing — so
`Scanner.scan_roll()` would rescan the same frame repeatedly. Setting it to `0x01` enables
`SLIDE_INIT` at scan start and `SLIDE_NEXT` after each non-preview scan.

Treat this as **experimental**. Those transport commands were written for the DigitDia
*slide magazine*, and whether the RPS 7200's motorised film transport answers the same SCSI
commands is untested. The scanner's INQUIRY does report an `ADF`, which is encouraging but
not proof.

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

The tests cover the channel-derivation and TIFF paths, and need no scanner attached.
