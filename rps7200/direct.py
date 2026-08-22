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

from .usb_transport import CheckCondition, Transport, UsbError

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
SCSI_READ_GAIN_OFFSET = 0xD7
SCSI_WRITE_GAIN_OFFSET = 0xDC
SCSI_READ_STATE = 0xDD

# Sub-commands carried in a WRITE payload
SUB_SCAN_FRAME = 0x12
SUB_EXPOSURE = 0x13
SUB_HIGHLIGHT_SHADOW = 0x14
SUB_CALIBRATION_INFO = 0x15

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

#: Scanner coordinates are in units of 1/7200 inch.
COORD_PER_INCH = 7200
MM_PER_INCH = 25.4


def _cmd(opcode: int, size: int) -> bytes:
    """Build a 6-byte command; size goes big-endian into bytes 3-4."""
    return bytes([opcode, 0, 0, (size >> 8) & 0xFF, size & 0xFF, 0])


class CalibrationRequired(RuntimeError):
    """The scanner insists on calibrating and will not start the scan."""


class ScanReadError(RuntimeError):
    """The scanner refused a read of image data."""


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


@dataclass
class State:
    button: bool
    warming_up: bool
    scanning: int
    busy: int


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

    def _log(self, message: str) -> None:
        if self.verbose:
            print(f"[scan] {message}")

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> DirectScanner:
        if self._own_transport:
            self.t.open()
        return self

    def close(self) -> None:
        if self._scanning:
            try:
                self.stop_scan()
            except UsbError:
                pass
        if self._own_transport:
            self.t.close()

    def __enter__(self) -> DirectScanner:
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- basic commands ----------------------------------------------------

    def inquiry(self) -> Inquiry:
        head = self.t.command(_cmd(SCSI_INQUIRY, 5), read_size=5)
        length = head[4] + 4
        d = self.t.command(_cmd(SCSI_INQUIRY, length), read_size=length)

        def text(start: int, size: int) -> str:
            return d[start : start + size].decode("ascii", "replace").rstrip("\x00 ")

        def short(offset: int) -> int:
            return int.from_bytes(d[offset : offset + 2], "little")

        # Offsets follow sanei_pieusb_cmd_inquiry in pieusb_scancmd.c.
        return Inquiry(
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
            _cmd(SCSI_READ_STATE, 12), 12, "read_state", retries=retries
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
        data[12] = halftone_pattern
        data[13] = line_threshold
        data[14] = 0x10

        self._log(
            f"mode res={resolution} passes={passes:#04x} depth={depth:#04x} "
            f"format={color_format:#04x} quality={quality:#04x}"
        )
        self.t.command(_cmd(SCSI_MODE_SELECT, 16), data=bytes(data))

    # -- scanning ----------------------------------------------------------

    def start_scan(self, retries: int = 3) -> None:
        """Begin scanning.

        The scanner answers CHECK CONDITION / UNIT ATTENTION with sense code
        0x82 ("calibration disable not granted") when asked to skip shading
        analysis. UNIT ATTENTION is a one-shot notification in SCSI -- reading
        the sense clears the condition -- so the command is retried.
        """
        last: tuple[int, int] | None = None
        for attempt in range(1, retries + 1):
            self._log(f"start scan (attempt {attempt})")
            try:
                self.t.command(_cmd(SCSI_SCAN, 1))
                self._scanning = True
                return
            except CheckCondition:
                info = self.sense()
                key, code, qual = info[2] & 0x0F, info[12], info[13]
                last = (code, qual)
                self._log(
                    f"  sense key={key:#04x} code={code:#04x} qual={qual:#04x}"
                    f" -- {describe_sense(key, code, qual)}"
                )
                time.sleep(0.5)

        code, qual = last if last else (0, 0)
        raise CalibrationRequired(
            f"scanner refused to start: {describe_sense(6, code, qual)} "
            f"(sense code {code:#04x}/{qual:#04x})"
        )

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
            _cmd(SCSI_READ_GAIN_OFFSET, 103), 103, "get_gain_offset"
        )

        def short(offset: int) -> int:
            return int.from_bytes(d[offset : offset + 2], "little")

        return Settings(
            exposure=[short(60), short(62), short(64), short(98)],
            offset=[d[66], d[67], d[68], d[100]],
            gain=[d[72], d[73], d[74], d[102]],
            light=d[75],
        )

    def set_gain_offset(self, s: Settings) -> None:
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
        data[16] = s.extra_entries & 0xFF
        data[17] = s.double_times & 0xFF
        data[18:20] = int(s.exposure[3]).to_bytes(2, "little")
        data[20] = int(s.offset[3]) & 0xFF
        data[22] = int(s.gain[3]) & 0xFF

        self._log(f"gain/offset {s.describe()}")
        self.t.command(_cmd(SCSI_WRITE_GAIN_OFFSET, 29), data=bytes(data))

    def calibrate(self) -> Settings:
        """Read the scanner's calibration values and write them back.

        Enough to satisfy the device's calibration requirement without the
        shading-data read that it cannot complete.
        """
        settings = self.get_gain_offset()
        self.set_gain_offset(settings)
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
        self, lines: int, bytes_per_line: int, retries: int = 3
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
                    _cmd(SCSI_READ, lines), read_size=lines * bytes_per_line
                )
            except CheckCondition:
                try:
                    last = self.describe_sense_bytes(self.sense())
                except UsbError as exc:
                    last = f"(sense unavailable: {exc})"
                self._log(f"  read_lines: {last}")
                time.sleep(0.3)
        raise ScanReadError(
            f"reading {lines} lines x {bytes_per_line} bytes was refused: {last}"
        )

    def read_image(
        self,
        params: ScanParameters,
        channels: int,
        max_batch: int = 32,
        timeout: float = 600.0,
    ) -> np.ndarray:
        """Read a whole frame, pacing against the scanner's available lines.

        The scanner exposes ``available_lines``; asking for more than it has
        ready is what makes a read block until it times out. Batches are capped
        so a single transfer stays modest.
        """
        bpl = params.bytes_per_line
        total = params.lines
        out = bytearray(total * bpl)
        got_lines = 0
        deadline = time.monotonic() + timeout

        while got_lines < total:
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"read {got_lines}/{total} lines before timing out"
                )
            current = self.get_parameters()
            ready = min(current.available_lines, total - got_lines, max_batch)
            if ready <= 0:
                time.sleep(0.2)
                continue
            # Clear any pending condition first; the scanner rejects READ with
            # ILLEGAL REQUEST while one is outstanding.
            self.test_unit_ready()
            chunk = self.read_lines(ready, bpl)
            offset = got_lines * bpl
            out[offset : offset + len(chunk)] = chunk
            got_lines += ready
            self._log(f"{got_lines}/{total} lines")

        depth_bytes = bpl // (params.width * channels) if params.width else 2
        dtype = np.dtype("<u2") if depth_bytes == 2 else np.dtype(np.uint8)
        return np.frombuffer(bytes(out), dtype=dtype).reshape(
            total, params.width, channels
        )

    # -- orchestration -----------------------------------------------------

    def scan(
        self,
        resolution: int = 600,
        infrared: bool = True,
        depth: int = DEPTH_16,
        frame: tuple[int, int, int, int] | None = None,
        skip_shading: bool = True,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        """Run one full scan and return ``(image, metadata)``."""
        info = self.inquiry()
        self.wait_warm()

        # Setup order follows sane_start in pieusb.c. The scanner rejects a
        # data READ with ILLEGAL REQUEST unless it has been taken through this
        # sequence, so the ordering is load-bearing rather than incidental.
        self.set_exposure_time()
        self.set_highlight_shadow()
        self.get_shading_parms()

        if frame is None:
            frame = (0, 0, info.ccd_width - 1, info.ccd_length - 1)
        self.set_scan_frame(*frame)
        self.calibrate()
        self.wait_ready()

        passes = ONE_PASS_RGBI if infrared else ONE_PASS_COLOR
        channels = 4 if infrared else 3
        color_format = FORMAT_INDEX if infrared else FORMAT_PIXEL
        self.set_mode(
            resolution=resolution,
            passes=passes,
            depth=depth,
            color_format=color_format,
            skip_shading=skip_shading,
        )
        self.wait_ready()

        self.start_scan()
        try:
            self.wait_ready()
            # The scanner rejects data reads with ILLEGAL REQUEST until
            # exposure/gain/offset have been written.
            self.calibrate()
            params = self.get_parameters()
            self._log(
                f"params width={params.width} lines={params.lines} "
                f"bpl={params.bytes_per_line} avail={params.available_lines}"
            )
            image = self.read_image(params, channels)
        finally:
            self.stop_scan()

        meta = {
            "resolution_dpi": resolution,
            "channels": channels,
            "channel_order": ["R", "G", "B", "I"][:channels],
            "depth": 16 if depth == DEPTH_16 else 8,
            "frame": list(frame),
            "width": int(params.width),
            "lines": int(params.lines),
            "bytes_per_line": int(params.bytes_per_line),
            "skip_shading": skip_shading,
        }
        return image, meta
