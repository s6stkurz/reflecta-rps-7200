"""Scanner control over the direct USB transport.

This drives the scanner the way the vendor's own software does, which differs
from SANE's ``pieusb`` backend in one decisive respect.

``pieusb`` leaves "shading analysis" enabled. The scanner then answers
``MUST_CALIBRATE`` when the scan starts, and the backend tries to read an
82752-byte shading block whose geometry it is openly unsure about (its own
comment reads *"although it's 45 lines, ccd_mask_size pixels, 16 bit depth in
all cases"*). This scanner delivers exactly 32768 bytes of that block and then
stops, the read times out after 30 s, and the device drops off the USB bus.

CyberView sets bit ``0x08`` -- skip shading analysis -- in the mode's quality
byte, documented in ``pieusb_scancmd.c``'s own reference dump of CyberView
traffic, and so never performs that read. This module does the same.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from .usb_transport import CheckCondition, NoDataYet, Transport, UsbError

# SCSI opcodes
SCSI_TEST_UNIT_READY = 0x00
SCSI_REQUEST_SENSE = 0x03
SCSI_READ = 0x08
SCSI_WRITE = 0x0A
SCSI_PARAM = 0x0F
SCSI_COPY = 0x18
SCSI_INQUIRY = 0x12
SCSI_MODE_SELECT = 0x15
SCSI_SCAN = 0x1B
SCSI_SLIDE = 0xD1
SCSI_SET_SCAN_HEAD = 0xD2
SCSI_READ_GAIN_OFFSET = 0xD7
SCSI_WRITE_GAIN_OFFSET = 0xDC
SCSI_READ_STATE = 0xDD

# Sub-commands carried in a WRITE payload
SUB_SCAN_FRAME = 0x12
SUB_EXPOSURE = 0x13
SUB_HIGHLIGHT_SHADOW = 0x14
SUB_CALIBRATION_INFO = 0x15
SUB_CAL_DATA = 0x16
SUB_CMD_17 = 0x17

# Mode: passes
ONE_PASS_COLOR = 0x80
ONE_PASS_RGBI = 0x90

# Mode: colour depth
DEPTH_16 = 0x20
DEPTH_8 = 0x04

# Mode: colour format
FORMAT_PIXEL = 0x01   # R,G,B[,I] interleaved per pixel
FORMAT_LINE = 0x02
FORMAT_INDEX = 0x04

# Mode: quality bitmask
QUALITY_SHARPEN = 0x02
QUALITY_SKIP_SHADING = 0x08   # what CyberView sets, and why it works
QUALITY_FAST_INFRARED = 0x80

BYTE_ORDER_INTEL = 0x01

#: Bytes prefixed to each line in INDEX colour format; the first is the ASCII
#: channel letter.
INDEX_HEADER = 2

#: Channel letters, in the order the scanner tags them.
CHANNEL_ORDER = "RGBI"

#: Read budget per READ command. Captures show the vendor software sizing its
#: batches to about this many bytes -- 208 lines x 2522 at 900 dpi, 104 x 5042
#: at 1800 -- subject to :data:`MAX_BATCH_LINES`.
READ_BUDGET_BYTES = 512 * 1024

#: Upper bound on lines per READ, which the vendor software never exceeds even
#: when the byte budget would allow far more (216 x 430 at 300 dpi).
MAX_BATCH_LINES = 216


def batch_for(bytes_per_line: int) -> int:
    """Lines to request per READ, mirroring the vendor software."""
    if bytes_per_line <= 0:
        return MAX_BATCH_LINES
    fits = -(-READ_BUDGET_BYTES // bytes_per_line)   # round up, as the vendor does
    return max(1, min(MAX_BATCH_LINES, fits))


#: Inset the vendor software keeps clear of the film edge -- every frame it
#: detected began at x=96, y=71. Not applied by default here: trimming loses
#: real image area, so detection returns the full picture it finds. Pass
#: ``inset=True`` to :meth:`DirectScanner.detect_frame` to trim like CyberView.
MIN_INSET_X = 96
MIN_INSET_Y = 71

#: Full scan extent, 0-based pixels at maximum resolution. Matches the vendor
#: software's frame and this scanner's 10344 x 6888 CCD. Used so that scanning
#: needs no INQUIRY: neither CyberView nor any run that scanned successfully
#: issues one, and doing so from inside the scan flow has broken reads.
FULL_FRAME = (0, 0, 10343, 6887)

#: CCD mask length the vendor software requests (pieusb uses shading_width).
CCD_MASK_SIZE = 5172

# Slide / autofeed transport actions
SLIDE_NEXT = 0x04
SLIDE_PREV = 0x05
SLIDE_INIT = 0x10
SLIDE_RELOAD = 0x40

#: Scanner coordinates are in units of 1/7200 inch.
COORD_PER_INCH = 7200
MM_PER_INCH = 25.4


def _cmd(opcode: int, size: int) -> bytes:
    """Build a 6-byte command; size goes big-endian into bytes 3-4."""
    return bytes([opcode, 0, 0, (size >> 8) & 0xFF, size & 0xFF, 0])


class CalibrationRequired(RuntimeError):
    """The scanner insists on calibrating and will not start the scan."""


class NoMediaLoaded(RuntimeError):
    """No film is loaded in the transport."""


class ScanReadError(RuntimeError):
    """The scanner refused a read of image data."""


class EndOfData(ScanReadError):
    """The scanner has no more scan lines to give.

    Reported as ILLEGAL REQUEST / ASC 0x20 once a scan is exhausted, which is
    indistinguishable by sense alone from a genuinely invalid command -- so it
    is only treated as end-of-data mid-read.
    """


_SENSE_KEYS = {
    0x00: "no sense",
    0x02: "not ready",
    0x03: "medium error",
    0x04: "hardware error",
    0x05: "illegal request",
    0x06: "unit attention",
    0x0B: "aborted command",
}

# Vendor-specific codes. pieusb_usb.c only decodes these under UNIT ATTENTION;
# every other sense key falls through to a generic message there, so they are
# keyed that way here rather than applied to all keys.
_UNIT_ATTENTION_CODES = {
    (0x1A, 0x00): "invalid field in parameter list",
    (0x20, 0x00): "invalid command operation code",
    (0x82, 0x00): "calibration disable not granted",
    (0x00, 0x06): "I/O process terminated",
    (0x26, 0x82): "MODE SELECT invalid: resolution too high",
    (0x26, 0x83): "MODE SELECT invalid: select only one colour",
}

# Standard SCSI additional sense codes, used for every other key.
_ASC = {
    (0x00, 0x00): "no additional sense",
    (0x1A, 0x00): "parameter list length error",
    (0x20, 0x00): "invalid command operation code",
    (0x24, 0x00): "invalid field in CDB",
    (0x25, 0x00): "logical unit not supported",
    (0x26, 0x00): "invalid field in parameter list",
    (0x29, 0x00): "power on or bus device reset occurred",
    (0x2C, 0x00): "command sequence error",
    (0x3D, 0x00): "invalid bits in identify message",
}


def describe_sense(key: int, code: int, qualifier: int) -> str:
    name = _SENSE_KEYS.get(key, f"key {key:#04x}")
    if key == 0x06:
        detail = _UNIT_ATTENTION_CODES.get((code, qualifier))
    else:
        detail = _ASC.get((code, qualifier))
    return f"{name}: {detail}" if detail else name


@dataclass
class Inquiry:
    vendor: str
    product: str
    model: int
    firmware: str
    max_resolution: int
    ccd_width: int
    ccd_length: int
    filters: int
    depths: int
    formats: int
    optional_devices: int
    frame: tuple[int, int, int, int]
    preview_resolution: int

    @property
    def has_infrared(self) -> bool:
        return bool(self.filters & 0x10)

    @property
    def has_adf(self) -> bool:
        return bool(self.optional_devices & 0x01)

    @property
    def supports_16bit(self) -> bool:
        return bool(self.depths & DEPTH_16)

    def describe(self) -> str:
        return (
            f"{self.vendor} {self.product} (model {self.model:#06x}, "
            f"fw {self.firmware})\n"
            f"  optical resolution : {self.max_resolution} dpi\n"
            f"  CCD                : {self.ccd_width} x {self.ccd_length} px\n"
            f"  infrared channel   : {'yes' if self.has_infrared else 'no'}\n"
            f"  16-bit             : {'yes' if self.supports_16bit else 'no'}\n"
            f"  ADF / autofeed     : {'yes' if self.has_adf else 'no'}\n"
            f"  preview resolution : {self.preview_resolution} dpi\n"
            f"  scan frame         : {self.frame}"
        )


@dataclass
class ScanParameters:
    width: int           # pixels per line
    lines: int           # total lines in the scan
    bytes_per_line: int
    filter_offset1: int
    filter_offset2: int
    available_lines: int  # lines ready to read right now


#: Bit seen set in the state byte when a strip holder was inserted (0x0D empty,
#: 0x4D loaded). Its exact meaning is unconfirmed -- it has since read 0x0D with
#: film demonstrably loaded -- so it is reported but never used to block a scan.
MEDIA_PRESENT = 0x40


@dataclass
class State:
    button: bool
    warming_up: bool
    scanning: int
    busy: int

    @property
    def media_loaded(self) -> bool:
        """Whether film is in the transport.

        The scanner ejects the strip at the end of every scan, so this is False
        again after each frame until the film is re-inserted.
        """
        return bool(self.scanning & MEDIA_PRESENT)


@dataclass
class Settings:
    """Per-channel exposure, gain and offset, in R, G, B, I order."""

    exposure: list[int]
    gain: list[int]
    offset: list[int]
    light: int = 4
    extra_entries: int = 0
    double_times: int = 0

    def describe(self) -> str:
        return (
            f"exposure={'-'.join(map(str, self.exposure))} "
            f"gain={'-'.join(map(str, self.gain))} "
            f"offset={'-'.join(map(str, self.offset))} light={self.light}"
        )


class DirectScanner:
    """Command-level control of the scanner."""

    def __init__(self, transport: Transport | None = None, verbose: bool = False):
        self.verbose = verbose
        self._own_transport = transport is None
        self.t = transport or Transport(verbose=verbose)
        self._scanning = False
        self._inquiry: Inquiry | None = None

    def _log(self, message: str) -> None:
        if self.verbose:
            print(f"[scan] {message}")

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> DirectScanner:
        if self._own_transport:
            self.t.open()
        return self

    def close(self) -> None:
        # Always attempt both, whatever state we think we are in: a scan left
        # running, or a command left half-issued, wedges the scanner until it is
        # power-cycled, and our idea of the state may be wrong.
        # No STOP SCAN and no bridge reset: the vendor software sends neither,
        # and resetting here is what left the next session unable to talk to the
        # scanner.
        self._scanning = False
        if self._own_transport:
            self.t.close()

    def __enter__(self) -> DirectScanner:
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- basic commands ----------------------------------------------------

    def inquiry(self, refresh: bool = False) -> Inquiry:
        if not refresh and self._inquiry is not None:
            return self._inquiry
        head = self.t.command(_cmd(SCSI_INQUIRY, 5), read_size=5)
        length = head[4] + 4
        d = self.t.command(_cmd(SCSI_INQUIRY, length), read_size=length)

        def text(start: int, size: int) -> str:
            return d[start : start + size].decode("ascii", "replace").rstrip("\x00 ")

        def short(offset: int) -> int:
            return int.from_bytes(d[offset : offset + 2], "little")

        # Offsets follow sanei_pieusb_cmd_inquiry in pieusb_scancmd.c.
        result = Inquiry(
            vendor=text(8, 8),
            product=text(16, 16),
            model=short(116),
            firmware=text(32, 4),
            max_resolution=short(36),
            ccd_width=short(40),
            ccd_length=short(42),
            filters=d[44],
            depths=d[45],
            formats=d[46],
            optional_devices=d[50],
            frame=(short(108), short(110), short(112), short(114)),
            preview_resolution=short(54),
        )
        self._inquiry = result
        return result

    def _query(
        self, command: bytes, read_size: int, label: str, retries: int = 3
    ) -> bytes:
        """Run a status/parameter command, absorbing one-shot CHECK CONDITIONs.

        The scanner queues a sense condition and reports it on whatever command
        comes next, whether or not that command is the one it relates to.
        Reading the sense clears it, so a retry generally succeeds.
        """
        last = ""
        for attempt in range(1, retries + 1):
            try:
                return self.t.command(command, read_size=read_size)
            except CheckCondition:
                try:
                    last = self.describe_sense_bytes(self.sense())
                except UsbError as exc:
                    last = f"(sense unavailable: {exc})"
                self._log(f"  {label}: {last}")
                time.sleep(0.3)
        raise ScanReadError(f"{label} failed after {retries} attempts: {last}")

    def read_state(self, retries: int = 3) -> State:
        d = self._query(
            _cmd(SCSI_READ_STATE, 13), 13, "read_state", retries=retries
        )
        return State(
            button=bool(d[0]), warming_up=bool(d[5]), scanning=d[6], busy=d[8]
        )

    def sense(self) -> bytes:
        return self.t.command(_cmd(SCSI_REQUEST_SENSE, 14), read_size=14)

    @staticmethod
    def describe_sense_bytes(info: bytes) -> str:
        key, code, qual = info[2] & 0x0F, info[12], info[13]
        return (
            f"key={key:#04x} code={code:#04x} qual={qual:#04x} "
            f"-- {describe_sense(key, code, qual)}"
        )

    def _unit_ready_sense(self) -> bytes | None:
        """TEST UNIT READY; returns None when good, else the sense bytes."""
        try:
            self.t.command(_cmd(SCSI_TEST_UNIT_READY, 0))
            return None
        except CheckCondition:
            try:
                return self.sense()
            except UsbError:
                return b"\x70" + b"\x00" * 13

    def wait_warm(self, timeout: float = 300.0, poll: float = 5.0) -> None:
        """Wait out the lamp warm-up (about 80 s from cold).

        Polls TEST UNIT READY rather than READ STATE: while the lamp warms, the
        scanner answers NOT READY (ASC 0x04) to *every* command, READ STATE
        included, so asking it for its state cannot work.
        """
        deadline = time.monotonic() + timeout
        announced = False
        while True:
            info = self._unit_ready_sense()
            if info is None:
                return
            key, code = info[2] & 0x0F, info[12]
            warming = key == 0x02 or code == 0x04
            if not warming:
                # Some other one-shot condition; reading the sense cleared it.
                self._log(f"  wait_warm: {self.describe_sense_bytes(info)}")
                if time.monotonic() > deadline:
                    return
                time.sleep(0.5)
                continue
            if not announced:
                print(
                    f"  lamp warming up (up to {timeout:.0f}s) ...", flush=True
                )
                announced = True
            if time.monotonic() > deadline:
                raise TimeoutError(f"lamp still warming after {timeout:.0f}s")
            time.sleep(poll)

    def test_unit_ready(self) -> bool:
        """Standard SCSI TEST UNIT READY.

        Also the conventional way to clear a pending sense condition, which is
        why the backend leans on it between phases -- the scanner refuses data
        reads while one is outstanding.
        """
        try:
            self.t.command(_cmd(SCSI_TEST_UNIT_READY, 0))
            return True
        except CheckCondition:
            try:
                self._log(f"  unit not ready: {self.describe_sense_bytes(self.sense())}")
            except UsbError:
                pass
            return False

    def wait_ready(self, timeout: float = 120.0, poll: float = 0.5) -> bool:
        """Poll TEST UNIT READY until the scanner reports good, as SANE does."""
        deadline = time.monotonic() + timeout
        while True:
            if self.test_unit_ready():
                return True
            if time.monotonic() > deadline:
                self._log("wait_ready timed out")
                return False
            time.sleep(poll)

    # -- configuration -----------------------------------------------------

    def _write_sub(self, sub: int, filter_mask: int, value: int) -> None:
        """Send an 8-byte sub-command payload via SCSI WRITE."""
        data = bytearray(8)
        data[0:2] = sub.to_bytes(2, "little")
        data[2:4] = (8 - 4).to_bytes(2, "little")
        data[4:6] = filter_mask.to_bytes(2, "little")
        data[6:8] = value.to_bytes(2, "little")
        self.t.command(_cmd(SCSI_WRITE, 8), data=bytes(data))

    def set_exposure_time(self, values: tuple[int, int, int] = (100, 100, 100)) -> None:
        """Set relative exposure time per channel (0-100), one write each."""
        self._log(f"exposure time {values}")
        for mask, value in zip((0x02, 0x04, 0x08), values):
            self._write_sub(SUB_EXPOSURE, mask, value)

    def set_highlight_shadow(
        self, values: tuple[int, int, int] = (100, 100, 100)
    ) -> None:
        self._log(f"highlight/shadow {values}")
        for mask, value in zip((0x02, 0x04, 0x08), values):
            self._write_sub(SUB_HIGHLIGHT_SHADOW, mask, value)

    def get_shading_parms(self) -> list[dict[str, int]]:
        """Read the shading/calibration descriptor (prepare-then-read)."""
        prep = bytearray(6)
        prep[0] = SUB_CALIBRATION_INFO | 0x80  # bit 7 = prepare read
        self.t.command(_cmd(SCSI_WRITE, 6), data=bytes(prep))
        d = self._query(_cmd(SCSI_READ, 32), 32, "get_shading_parms")

        entries, entry_size = d[4], d[5]
        out = []
        for k in range(entries):
            base = 8 + entry_size * k
            out.append(
                {
                    "type": d[base],
                    "send_bits": d[base + 1],
                    "receive_bits": d[base + 2],
                    "lines": d[base + 3],
                    "pixels_per_line": int.from_bytes(
                        d[base + 4 : base + 6], "little"
                    ),
                }
            )
        self._log(f"shading parms: {out}")
        return out

    def set_scan_frame(
        self, x0: int, y0: int, x1: int, y1: int, index: int = 0x80
    ) -> None:
        """Set the scan window.

        Coordinates are 0-based pixels at the scanner's maximum resolution, so
        a full frame is ``(0, 0, ccd_width - 1, ccd_length - 1)`` -- not the
        ``x0,y0,x1,y1`` reported by INQUIRY, which describe something else.
        ``index`` is 0x80, matching what the backend sends; 0 is not accepted.
        """
        data = bytearray(14)
        data[0:2] = SUB_SCAN_FRAME.to_bytes(2, "little")
        data[2:4] = (14 - 4).to_bytes(2, "little")
        data[4:6] = index.to_bytes(2, "little")
        data[6:8] = x0.to_bytes(2, "little")
        data[8:10] = y0.to_bytes(2, "little")
        data[10:12] = x1.to_bytes(2, "little")
        data[12:14] = y1.to_bytes(2, "little")
        self._log(f"scan frame {x0},{y0} -> {x1},{y1}")
        self.t.command(_cmd(SCSI_WRITE, 14), data=bytes(data))

    def set_mode(
        self,
        resolution: int,
        passes: int = ONE_PASS_RGBI,
        depth: int = DEPTH_16,
        color_format: int = FORMAT_PIXEL,
        skip_shading: bool = True,
        sharpen: bool = False,
        fast_infrared: bool = False,
        halftone_pattern: int = 0,
        line_threshold: int = 0x80,
    ) -> None:
        """Configure the scan.

        ``skip_shading`` defaults to True deliberately: leaving it off is what
        sends the backend into the shading read this scanner cannot complete.
        """
        quality = 0
        if sharpen:
            quality |= QUALITY_SHARPEN
        if skip_shading:
            quality |= QUALITY_SKIP_SHADING
        if fast_infrared:
            quality |= QUALITY_FAST_INFRARED

        data = bytearray(16)
        data[1] = 16 - 1
        data[2:4] = resolution.to_bytes(2, "little")
        data[4] = passes
        data[5] = depth
        data[6] = color_format
        data[8] = BYTE_ORDER_INTEL
        data[9] = quality
        data[12] = halftone_pattern if halftone_pattern else 0x02
        data[13] = line_threshold
        # Byte 14 is 0x21 for a four-channel RGBI pass and 0x10 for RGB,
        # from captures of the vendor software with and without infrared
        # cleaning enabled. pieusb hardcodes 0x10 (its comment reads "?"),
        # which is why it never yields an infrared plane.
        data[14] = 0x21 if passes == ONE_PASS_RGBI else 0x10

        self._log(
            f"mode res={resolution} passes={passes:#04x} depth={depth:#04x} "
            f"format={color_format:#04x} quality={quality:#04x}"
        )
        self.t.command(_cmd(SCSI_MODE_SELECT, 16), data=bytes(data))

    # -- scanning ----------------------------------------------------------

    def cmd_17(self, value: int = 1) -> None:
        """Vendor command 0x17, sent right after the scan frame.

        This is what makes the scanner *grant* "skip shading analysis". Without
        it, MODE SELECT with quality bit 0x08 is refused with sense 0x82
        ("calibration disable not granted"), the scanner insists on a shading
        pass, and the shading read then stalls at 32768 bytes.

        pieusb has this command but only issues it for models its config marks
        as having a slide transport -- which is 0 for model 0x31 -- so the stock
        backend never sends it here. CyberView always does.

        Captured bytes: cmd `0a 00 00 00 06 00`, data `17 00 02 00 01 00`.
        """
        data = bytearray(6)
        data[0:2] = SUB_CMD_17.to_bytes(2, "little")
        data[2:4] = (2).to_bytes(2, "little")
        data[4:6] = value.to_bytes(2, "little")
        self._log(f"cmd_17({value})")
        self.t.command(_cmd(SCSI_WRITE, 6), data=bytes(data))

    def slide(self, action: int = SLIDE_INIT) -> None:
        """Drive the film/slide transport.

        pieusb only issues this when its config marks the model as having a
        slide transport, and for model 0x31 that flag is 0, so the stock backend
        never initialises the transport at all -- even though INQUIRY reports an
        ADF. SLIDE_NEXT is also how a whole strip gets advanced frame by frame.
        """
        names = {
            SLIDE_NEXT: "next",
            SLIDE_PREV: "prev",
            SLIDE_INIT: "init",
            SLIDE_RELOAD: "reload",
        }
        self._log(f"slide transport: {names.get(action, hex(action))}")
        # Second byte is 0x16 in CyberView's traffic; pieusb sends 0x01.
        data = bytes([action, 0x16, 0x00, 0x00])
        self.t.command(_cmd(SCSI_SLIDE, 4), data=data)

    def start_scan(
        self,
        retries: int = 15,
        ready_timeout: float = 600.0,
        ready_poll: float = 1.0,
    ) -> None:
        """Begin scanning.

        Two distinct conditions have to be waited out, and they are counted
        separately:

        * NOT READY (ASC 0x04) -- the scanner is still preparing. This is not a
          failure and does not consume a retry; higher resolutions take longer,
          60+ seconds at 1800 dpi. Polled until ``ready_timeout``.
        * UNIT ATTENTION 0x82 ("calibration disable not granted") and friends --
          one-shot conditions that clear when read. These do consume a retry,
          but typically need two or three attempts before the scan starts.
        """
        deadline = time.monotonic() + ready_timeout
        attempts = 0
        last: tuple[int, int] | None = None

        while True:
            try:
                self.t.command(_cmd(SCSI_SCAN, 1))
                self._scanning = True
                return
            except CheckCondition:
                info = self.sense()
                key, code, qual = info[2] & 0x0F, info[12], info[13]
                last = (code, qual)

                if code == 0x04:          # still becoming ready
                    if time.monotonic() > deadline:
                        break
                    time.sleep(ready_poll)
                    continue

                attempts += 1
                self._log(
                    f"  start_scan: {describe_sense(key, code, qual)} "
                    f"(retry {attempts}/{retries})"
                )
                if attempts >= retries or time.monotonic() > deadline:
                    break
                time.sleep(0.5)

        # Leave the scanner usable; an abandoned start wedges it otherwise.
        self._scanning = False
        code, qual = last if last else (0, 0)
        raise CalibrationRequired(
            f"scanner refused to start: {describe_sense(6, code, qual)} "
            f"(sense code {code:#04x}/{qual:#04x})"
        )

    def finish_scan(self, polls: int = 3) -> None:
        """End a completed scan the way the vendor software does.

        CyberView never sends STOP SCAN. It reads all the data and then polls
        READ_STATE while the scanner settles. Sending STOP SCAN after a
        successful read appears to be what leaves this scanner unresponsive to
        the next session, so it is reserved for cancelling a scan that is still
        running.
        """
        self._scanning = False
        for _ in range(polls):
            try:
                self.read_state()
            except (CheckCondition, UsbError, ScanReadError):
                return
            time.sleep(0.2)

    def stop_scan(self) -> None:
        """Stop scanning. Never raises -- it runs on the cleanup path.

        Leaving a scan running is what wedges the scanner badly enough to need
        a power cycle, so this always makes the attempt.
        """
        self._log("stop scan")
        try:
            self.t.command(_cmd(SCSI_SCAN, 0))
        except CheckCondition:
            try:
                self._log(f"  stop_scan: {self.describe_sense_bytes(self.sense())}")
            except UsbError:
                pass
        except UsbError as exc:
            self._log(f"  stop_scan failed: {exc}")
        finally:
            self._scanning = False

    def get_gain_offset(self) -> Settings:
        """Read the scanner's current exposure/gain/offset."""
        d = self._query(
            _cmd(SCSI_READ_GAIN_OFFSET, 123), 123, "get_gain_offset"
        )

        def short(offset: int) -> int:
            return int.from_bytes(d[offset : offset + 2], "little")

        return Settings(
            exposure=[short(60), short(62), short(64), short(98)],
            offset=[d[66], d[67], d[68], d[100]],
            gain=[d[72], d[73], d[74], d[102]],
            light=d[75],
        )

    def set_gain_offset(self, s: Settings, infrared: bool = False) -> None:
        """Write exposure/gain/offset.

        The scanner will not accept a data READ until this has been sent -- it
        answers ILLEGAL REQUEST otherwise. This is the calibration step it means
        by "calibration disable not granted".
        """
        data = bytearray(29)
        for i in range(3):
            data[i * 2 : i * 2 + 2] = int(s.exposure[i]).to_bytes(2, "little")
            data[6 + i] = int(s.offset[i]) & 0xFF
            data[12 + i] = int(s.gain[i]) & 0xFF
        data[15] = s.light & 0xFF
        # With infrared enabled the vendor software sets byte 16 (extra
        # entries) and byte 27; both are 0 for a plain RGB pass.
        data[16] = 1 if infrared else (s.extra_entries & 0xFF)
        data[17] = s.double_times & 0xFF
        if infrared:
            data[27] = 1
        data[18:20] = int(s.exposure[3]).to_bytes(2, "little")
        data[20] = int(s.offset[3]) & 0xFF
        data[22] = int(s.gain[3]) & 0xFF

        self._log(f"gain/offset {s.describe()}")
        self.t.command(_cmd(SCSI_WRITE_GAIN_OFFSET, 29), data=bytes(data))

    def calibrate(self, infrared: bool = False) -> Settings:
        """Read the scanner's calibration values and write them back.

        Enough to satisfy the device's calibration requirement without the
        shading-data read that it cannot complete.
        """
        settings = self.get_gain_offset()
        self.set_gain_offset(settings, infrared=infrared)
        return settings

    def get_ccd_mask(self, size: int) -> bytes:
        """Read the CCD mask (SCSI COPY).

        ``sane_start`` performs this in "scan phase 3", immediately before
        reading scan parameters and image data. ``size`` is the shading width
        from :meth:`get_shading_parms`.
        """
        data = self._query(_cmd(SCSI_COPY, size), size, "get_ccd_mask")
        self._log(f"ccd mask: {len(data)} bytes")
        return data

    def get_parameters(self) -> ScanParameters:
        d = self._query(_cmd(SCSI_PARAM, 18), 18, "get_parameters")
        return ScanParameters(
            width=int.from_bytes(d[0:2], "little"),
            lines=int.from_bytes(d[2:4], "little"),
            bytes_per_line=int.from_bytes(d[4:6], "little"),
            filter_offset1=d[6],
            filter_offset2=d[7],
            available_lines=int.from_bytes(d[14:16], "little"),
        )

    def read_lines(
        self,
        lines: int,
        bytes_per_line: int,
        retries: int = 3,
        timeout_ms: int = 120_000,
    ) -> bytes:
        """Read ``lines`` scan lines.

        Retries like :meth:`_query` does: a queued one-shot sense condition is
        reported against whichever command arrives next, so the first attempt
        can be rejected for something that has nothing to do with this read.
        """
        last = ""
        for _ in range(retries):
            try:
                return self.t.command(
                    _cmd(SCSI_READ, lines),
                    read_size=lines * bytes_per_line,
                    timeout_ms=timeout_ms,
                )
            except CheckCondition:
                try:
                    last = self.describe_sense_bytes(self.sense())
                except UsbError as exc:
                    last = f"(sense unavailable: {exc})"
                self._log(f"  read_lines: {last}")
                time.sleep(0.3)
        if "asc=0x20" in last or "code=0x20" in last:
            raise EndOfData(
                f"scanner has no more lines (asked for {lines}): {last}"
            )
        raise ScanReadError(
            f"reading {lines} lines x {bytes_per_line} bytes was refused: {last}"
        )

    def read_planes(
        self,
        params: ScanParameters,
        channels: int,
        batch: int | None = None,
        timeout: float = 3600.0,
        poll: float = 0.02,
        idle_timeout: float = 120.0,
    ) -> np.ndarray:
        """Read a frame and deinterleave it into ``(H, W, channels)``.

        In INDEX colour format the scanner sends one colour plane per line,
        each prefixed with a 2-byte header whose first byte is the ASCII channel
        letter -- 'R', 'G', 'B' or 'I'. A frame is therefore ``channels x height``
        lines of ``bytes_per_line + 2``.

        Reads are paced against ``available_lines``, which rises as the scanner
        physically scans. Asking for more lines than it has ready makes the read
        stall until it times out, and a bulk timeout is unrecoverable -- the
        device then needs a power cycle. This is why the vendor software's reads
        come in uneven sizes (216, 3, 216, 216, 105, 105): it takes whatever is
        ready.
        """
        bpl = params.bytes_per_line + INDEX_HEADER
        total_lines = channels * params.lines
        if batch is None:
            batch = batch_for(bpl)
        deadline = time.monotonic() + timeout

        self._log(
            f"reading {total_lines} lines x {bpl} bytes, {batch} per request"
        )

        chunks: list[bytes] = []
        got = 0
        idle_since: float | None = None
        while got < total_lines:
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"read {got}/{total_lines} lines before timing out"
                )

            n = min(batch, total_lines - got)
            try:
                chunk = self.read_lines(n, bpl, retries=1)
            except NoDataYet:
                # The scanner has not scanned this far yet. This is its normal
                # way of saying "wait" -- the vendor software sees it on most
                # of its reads and simply asks again a moment later. Notably it
                # does not poll scan parameters to pace itself, and doing so
                # here was slow enough at high resolution to abort the scan.
                now = time.monotonic()
                idle_since = idle_since or now
                if now - idle_since > idle_timeout:
                    raise ScanReadError(
                        f"no data for {idle_timeout:.0f}s at "
                        f"{got}/{total_lines} lines"
                    ) from None
                time.sleep(poll)
                continue
            except EndOfData:
                self._log(f"end of data at {got}/{total_lines} lines")
                break

            idle_since = None
            chunks.append(chunk)
            got += n
            self._log(f"{got}/{total_lines} lines")

        return self._deinterleave(b"".join(chunks), params, channels)

    @staticmethod
    def _deinterleave(
        blob: bytes, params: ScanParameters, channels: int
    ) -> np.ndarray:
        bpl = params.bytes_per_line + INDEX_HEADER
        depth_bytes = params.bytes_per_line // params.width if params.width else 2
        dtype = np.dtype("<u2") if depth_bytes == 2 else np.dtype(np.uint8)

        planes: dict[str, list[np.ndarray]] = {}
        for i in range(len(blob) // bpl):
            line = blob[i * bpl : (i + 1) * bpl]
            tag = chr(line[0])
            planes.setdefault(tag, []).append(
                np.frombuffer(line[INDEX_HEADER:], dtype=dtype)
            )

        order = [c for c in CHANNEL_ORDER if c in planes]
        if not order:
            raise ScanReadError(
                "no recognisable channel tags in scan data; "
                f"saw {sorted(set(planes))!r}"
            )
        if len(order) != channels:
            raise ScanReadError(
                f"expected {channels} channels {list(CHANNEL_ORDER[:channels])}, "
                f"but the scanner produced {len(order)}: {order} "
                f"({ {c: len(v) for c, v in planes.items()} })"
            )

        height = min(len(planes[c]) for c in order)
        return np.stack([np.array(planes[c][:height]) for c in order], axis=-1)

    # -- prescan and framing -----------------------------------------------

    def prescan(
        self, resolution: int = 300, frame: tuple[int, int, int, int] | None = None
    ) -> tuple[np.ndarray, ScanParameters]:
        """Low-resolution RGB pass over the full transport.

        This is what the vendor software runs before every frame: 300 dpi,
        three channels, 8-bit, covering the whole scan area. It carries no
        infrared -- captures confirm the prescan is always ``passes=0x80`` --
        and exists to find where the picture actually sits.
        """
        image, meta = self.scan(
            resolution=resolution,
            infrared=False,
            depth=DEPTH_8,
            frame=frame or FULL_FRAME,
            prescan=False,
        )
        params = ScanParameters(
            width=meta["width"],
            lines=meta["height"],
            bytes_per_line=meta["bytes_per_line"],
            filter_offset1=0,
            filter_offset2=0,
            available_lines=0,
        )
        return image, params

    @staticmethod
    def detect_frame(
        image: np.ndarray,
        full_frame: tuple[int, int, int, int] = FULL_FRAME,
        threshold: float = 0.25,
        pad: int = 0,
        inset: bool = False,
    ) -> tuple[int, int, int, int]:
        """Locate the picture within a prescan image.

        Film base and the gaps between frames are close to uniform, while the
        picture varies; so the picture is the region whose per-row and
        per-column variation rises above a fraction of the maximum. Returns
        scanner coordinates (0-based pixels at maximum resolution), ready to
        pass to :meth:`set_scan_frame`.
        """
        grey = image.astype(np.float64)
        if grey.ndim == 3:
            grey = grey.mean(axis=2)

        col = grey.std(axis=0)
        row = grey.std(axis=1)

        def bounds(profile: np.ndarray) -> tuple[int, int]:
            peak = float(profile.max())
            if peak <= 0:
                return 0, len(profile) - 1
            active = np.flatnonzero(profile >= peak * threshold)
            if active.size == 0:
                return 0, len(profile) - 1
            return int(active[0]), int(active[-1])

        c0, c1 = bounds(col)
        r0, r1 = bounds(row)
        h, w = grey.shape

        fx0, fy0, fx1, fy1 = full_frame
        span_x, span_y = fx1 - fx0, fy1 - fy0

        x0 = fx0 + int(round((max(0, c0 - pad) / w) * span_x))
        x1 = fx0 + int(round((min(w - 1, c1 + pad) / w) * span_x))
        y0 = fy0 + int(round((max(0, r0 - pad) / h) * span_y))
        y1 = fy0 + int(round((min(h - 1, r1 + pad) / h) * span_y))

        if inset:
            # Opt-in only: trims the film edge the way CyberView does, at the
            # cost of a little real image area.
            x0 = max(x0, fx0 + MIN_INSET_X)
            y0 = max(y0, fy0 + MIN_INSET_Y)
            x1 = min(x1, fx1 - MIN_INSET_X)
            y1 = min(y1, fy1 - MIN_INSET_Y)

        # Never return a degenerate window.
        if x1 <= x0 or y1 <= y0:
            return full_frame
        return x0, y0, x1, y1

    # -- orchestration -----------------------------------------------------

    def scan(
        self,
        resolution: int = 300,
        infrared: bool = True,
        depth: int = DEPTH_16,
        frame: tuple[int, int, int, int] | None = None,
        advance: bool = False,
        require_media: bool = True,
        prescan: bool = False,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        """Run one scan and return ``(image, metadata)``.

        The command order here is the vendor software's, recovered from a USB
        capture. It is load-bearing: in particular :meth:`cmd_17` must follow
        the scan frame, or the scanner refuses to skip shading analysis and the
        scan cannot complete. See the README.
        """
        # Open with READ_STATE polling, as the vendor software does.
        for _ in range(4):
            try:
                if not self.read_state().warming_up:
                    break
            except (CheckCondition, ScanReadError):
                pass
            time.sleep(1)
        self.wait_warm()
        self.test_unit_ready()

        if require_media:
            state = self.read_state()
            if not state.media_loaded:
                # Reported, not enforced: this bit has read clear with film
                # definitely loaded, so trusting it would block valid scans.
                # Let the scanner itself refuse if there is really no film.
                self._log(
                    f"note: state {state.scanning:#04x} suggests no film, but "
                    "that bit is not reliable; continuing"
                )

        self.set_exposure_time()
        self.set_highlight_shadow()

        if frame is None:
            frame = FULL_FRAME
        self.set_scan_frame(*frame)

        # Must come after the scan frame. Without it the scanner will not grant
        # "skip shading analysis" and insists on a shading pass it cannot serve.
        try:
            self.cmd_17(1)
        except CheckCondition:
            self._log("  cmd_17 reported a condition; continuing")

        settings = self.get_gain_offset()
        self.set_gain_offset(settings, infrared=infrared)

        passes = ONE_PASS_RGBI if infrared else ONE_PASS_COLOR
        channels = 4 if infrared else 3
        self.set_mode(
            resolution=resolution,
            passes=passes,
            depth=depth,
            color_format=FORMAT_INDEX,
            skip_shading=True,
        )
        self.test_unit_ready()

        self.slide(SLIDE_INIT)
        self.wait_ready()

        started = time.monotonic()
        self.start_scan()
        try:
            self.wait_ready()
            self.get_ccd_mask(CCD_MASK_SIZE)
            params = self.get_parameters()
            self._log(
                f"params width={params.width} lines={params.lines} "
                f"bpl={params.bytes_per_line}"
            )
            image = self.read_planes(params, channels)
            if advance:
                self.slide(SLIDE_NEXT)
        except BaseException:
            # Deliberately no STOP SCAN. The vendor software never sends it,
            # and issuing it here reliably leaves the scanner unresponsive to
            # the next session, needing a power cycle. Settling the bridge is
            # enough to leave things usable.
            self._scanning = False
            raise
        else:
            self.finish_scan()

        meta = {
            "resolution_dpi": resolution,
            "channels": channels,
            "channel_order": [c for c in CHANNEL_ORDER][:channels]
            if not infrared
            else list(CHANNEL_ORDER),
            "depth": 16 if depth == DEPTH_16 else 8,
            "frame": list(frame),
            "width": int(params.width),
            "height": int(params.lines),
            "bytes_per_line": int(params.bytes_per_line),
            "exposure": settings.exposure,
            "gain": settings.gain,
            "offset": settings.offset,
            "duration_s": round(time.monotonic() - started, 1),
        }
        return image, meta
