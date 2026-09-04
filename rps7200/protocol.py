"""What the scanner is told and what it says back.

Opcodes, payload layouts, and the values the device reports -- INQUIRY, READ
STATE, READ GAIN/OFFSET, the scan parameters and the sense response. No I/O:
`DirectScanner` in :mod:`rps7200.direct` is what sends these.

`PROTOCOL_REVISION` lives here because this is the module that defines what a
revision *is*: the command sequence, its order, and the payloads.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

PROTOCOL_REVISION = 1

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
SCSI_VENDOR_E7 = 0xE7   # sent once at session start; purpose unknown

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

# Mode: quality field. Bytes 9 and 10 of the MODE payload behave as one
# 16-bit little-endian value. Across 33 scan cycles in six captures of the
# vendor software, 32 send 0x0008 and exactly one sends 0x0800 -- so 0x0008
# means "reuse the calibration already held" and 0x0800 means "calibrate now".
# Sending neither (both bytes zero) does nothing at all.
QUALITY_SHARPEN = 0x02
QUALITY_SKIP_SHADING = 0x08     # reuse existing calibration
QUALITY_CALIBRATE = 0x0800      # run a shading calibration pass
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


# Film types, for metering. Only the white-balance rule depends on these.
FILM_NEGATIVE = "negative"
FILM_POSITIVE = "positive"
FILM_KODACHROME = "kodachrome"
FILM_BW = "bw"
FILM_TYPES = (FILM_NEGATIVE, FILM_POSITIVE, FILM_KODACHROME, FILM_BW)


# How a roll is metered. See DirectScanner.scan_roll.
METER_EACH = "each"
METER_ONCE = "once"
METER_NONE = "none"
METER_MODES = (METER_EACH, METER_ONCE, METER_NONE)


def locks_white_balance(film: str) -> bool:
    """Whether metering must move the visible channels together.

    A colour negative's orange mask sits over the whole frame. Metering the
    channels as one group leaves it there to be quantised through, and the blue
    record ends up in a fraction of the range it could have had -- so a negative
    is metered per channel, which takes the mask off before the ADC instead of
    after it.

    Everything else keeps its balance. A slide, a Kodachrome and a black and
    white negative all carry their cast because that cast *is* the picture;
    stretching each channel to the same target on its own takes it off.

    Note what this scanner can actually deliver on the negative side. Blue sits
    near the top of the 16-bit exposure timer before any film is loaded -- the
    lamp is weak there and the blue filter passes little -- so there is only
    about x1.2 of exposure left to give it. The mask can be taken off red and
    green; on blue the hardware has almost nothing left. See
    :meth:`DirectScanner.auto_exposure`, which reports when it hits that.
    """
    if film not in FILM_TYPES:
        raise ValueError(f"unknown film type {film!r}; expected one of {FILM_TYPES}")
    return film != FILM_NEGATIVE


# Slide / autofeed transport actions
SLIDE_NEXT = 0x04
SLIDE_PREV = 0x05
SLIDE_INIT = 0x10
SLIDE_RELOAD = 0x40

#: Scanner coordinates are in units of 1/7200 inch.
COORD_PER_INCH = 7200
MM_PER_INCH = 25.4


def _is_unity(scale: float | Sequence[float]) -> bool:
    """Whether an exposure scale asks for no change at all.

    A per-channel scale is a list, and ``[1.0, 1.0, 1.0] != 1.0`` is always
    True, so comparing against the number alone reported every metered scan as
    rescaled.
    """
    if isinstance(scale, (int, float)):
        return float(scale) == 1.0
    return all(float(v) == 1.0 for v in scale)


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


#: ASC reported once a scan is exhausted. Indistinguishable by sense alone from
#: a genuinely invalid command, so it only means end-of-data mid-read.
ASC_END_OF_DATA = 0x20

#: ASC the scanner reports while it is still becoming ready.
ASC_NOT_READY = 0x04


@dataclass(frozen=True)
class Sense:
    """A parsed REQUEST SENSE response.

    Exists so a caller can ask what the scanner said instead of matching
    substrings in a log line. A read that ran out of scan lines and a read
    refused for any other reason both arrive as CHECK CONDITION, and only the
    ASC separates them.
    """

    key: int
    code: int
    qualifier: int
    #: Why the sense could not be read, when it could not be.
    problem: str | None = None

    @classmethod
    def parse(cls, info: bytes) -> "Sense":
        if len(info) < 14:
            return cls.unreadable(f"{len(info)} bytes, expected 14")
        return cls(key=info[2] & 0x0F, code=info[12], qualifier=info[13])

    @classmethod
    def unreadable(cls, problem: str) -> "Sense":
        return cls(key=0, code=0, qualifier=0, problem=problem)

    @property
    def readable(self) -> bool:
        return self.problem is None

    @property
    def end_of_data(self) -> bool:
        """The scan is exhausted -- only meaningful mid-read."""
        return self.readable and self.code == ASC_END_OF_DATA

    @property
    def not_ready(self) -> bool:
        return self.readable and self.code == ASC_NOT_READY

    def __str__(self) -> str:
        if not self.readable:
            return f"(sense unavailable: {self.problem})"
        return (
            f"key={self.key:#04x} code={self.code:#04x} "
            f"qual={self.qualifier:#04x} "
            f"-- {describe_sense(self.key, self.code, self.qualifier)}"
        )


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


#: "Media present" in the state byte. On this scanner it does track the film:
#: measured 2026-09-04, one variable changed, 0x0d with the transport empty and
#: 0x4d with a strip loaded.
#:
#: It is still never used to block a scan, because it is not dependable. The bit
#: is clear in all 155 READ_STATE responses of the vendor power-on capture, whose
#: state byte reads 0x1d idle and 0x9d scanning, and it has previously read clear
#: with film demonstrably loaded. A set bit is evidence; a clear one is not.
#: Only the person at the scanner can actually see the transport -- ask them.
MEDIA_PRESENT = 0x40


@dataclass
class State:
    button: bool
    warming_up: bool
    scanning: int
    #: Byte 8. Named for what it was first taken to be; measured, it is the
    #: media flag -- 1 with an empty transport, 0 with film. See
    #: :attr:`media_loaded`.
    busy: int
    #: Where the transport has the film, counting from 0. This is the one
    #: trustworthy signal that an advance has happened -- see
    #: :meth:`DirectScanner.advance`.
    position: int = 0

    @property
    def media_loaded(self) -> bool:
        """Whether film is in the transport. Byte 8, and it is inverted.

        Measured with one variable changed -- a strip going in -- byte 8 read 1
        empty and 0 loaded. The captures cannot corroborate that, and that is the
        point: all 737 of their READ STATE responses hold 0, because every
        capture was taken with film loaded. An empty transport is the condition
        they never contained.

        This used to read ``scanning & MEDIA_PRESENT``, byte 6, and could not be
        believed: in the same reading that byte said 0x1d, i.e. no film, with a
        strip demonstrably in the transport. Byte 6 is left alone as
        :attr:`scanning` because other bits in it are meaningful.
        """
        return not self.no_media

    @property
    def no_media(self) -> bool:
        """Byte 8 raised: the transport is empty."""
        return bool(self.busy)


@dataclass
class Settings:
    """Per-channel exposure, gain and offset, in R, G, B, I order."""

    exposure: list[int]
    gain: list[int]
    offset: list[int]
    light: int = 4
    extra_entries: int = 0
    double_times: int = 0

    def scaled(self, factor: float | Sequence[float]) -> Settings:
        """Copy with exposure multiplied, clamped to the 16-bit field.

        ``factor`` may be one number for every channel, or one per channel in
        R, G, B, I order -- the channels need very different exposures, most
        obviously blue, which saturates far sooner than the rest with no film
        in the transport.

        Always a new object, including at a factor of 1.0. Returning self there
        aliases the caller's settings to the device's, so a later edit to one
        silently moves the other.
        """
        if isinstance(factor, (int, float)):
            factors = [float(factor)] * len(self.exposure)
        else:
            factors = list(factor)
            factors += [1.0] * (len(self.exposure) - len(factors))
        return Settings(
            exposure=[
                int(max(100, min(65535, round(e * f))))
                for e, f in zip(self.exposure, factors)
            ],
            gain=list(self.gain),
            offset=list(self.offset),
            light=self.light,
            extra_entries=self.extra_entries,
            double_times=self.double_times,
        )

    def describe(self) -> str:
        return (
            f"exposure={'-'.join(map(str, self.exposure))} "
            f"gain={'-'.join(map(str, self.gain))} "
            f"offset={'-'.join(map(str, self.offset))} light={self.light}"
        )
