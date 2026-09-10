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

import os
import tempfile
import shutil
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .defects import (
    column_defect_sigma,
    destripe,
    dilate_defects,
    find_column_defects,
    flat_defect_sigma,
    resample_reference,
)
from .framing import (
    BLANK_CONTRAST,
    CALIBRATION_FRAME,
    CCD_MASK_SIZE,
    CLEAR_RATIO,
    FILM_LEVEL,
    FULL_FRAME,
    MIN_INSET_X,
    MIN_INSET_Y,
    NOMINAL_FRAME_WIDTH,
    film_bounds,
    frame_contrast,
    gap_edges,
    registration,
    registration_error_mm,
)
from .protocol import (
    ASC_END_OF_DATA,
    ASC_NOT_READY,
    BYTE_ORDER_INTEL,
    CHANNEL_ORDER,
    COORD_PER_INCH,
    DEPTH_8,
    DEPTH_16,
    FILM_BW,
    FILM_KODACHROME,
    FILM_NEGATIVE,
    FILM_POSITIVE,
    FILM_TYPES,
    FORMAT_INDEX,
    FORMAT_LINE,
    FORMAT_PIXEL,
    INDEX_HEADER,
    MAX_BATCH_LINES,
    MEDIA_PRESENT,
    METER_EACH,
    METER_MODES,
    METER_NONE,
    METER_ONCE,
    MM_PER_INCH,
    ONE_PASS_COLOR,
    ONE_PASS_RGBI,
    PROTOCOL_REVISION,
    QUALITY_CALIBRATE,
    QUALITY_FAST_INFRARED,
    QUALITY_SHARPEN,
    QUALITY_SKIP_SHADING,
    READ_BUDGET_BYTES,
    SCSI_COPY,
    SCSI_INQUIRY,
    SCSI_MODE_SELECT,
    SCSI_PARAM,
    SCSI_READ,
    SCSI_READ_GAIN_OFFSET,
    SCSI_READ_STATE,
    SCSI_REQUEST_SENSE,
    SCSI_SCAN,
    SCSI_SET_SCAN_HEAD,
    SCSI_SLIDE,
    SCSI_TEST_UNIT_READY,
    SCSI_VENDOR_E7,
    SCSI_WRITE,
    SCSI_WRITE_GAIN_OFFSET,
    SLIDE_INIT,
    SLIDE_NEXT,
    SLIDE_PREV,
    SLIDE_RELOAD,
    SUB_CAL_DATA,
    SUB_CALIBRATION_INFO,
    SUB_CMD_17,
    SUB_EXPOSURE,
    SUB_HIGHLIGHT_SHADOW,
    SUB_SCAN_FRAME,
    CalibrationRequired,
    EndOfData,
    Inquiry,
    NoMediaLoaded,
    ScanParameters,
    ScanReadError,
    Sense,
    Settings,
    State,
    _cmd,
    _is_unity,
    batch_for,
    describe_sense,
    INFRARED_IS_BLIND_TO,
    locks_white_balance,
    supports_infrared,
)
from .shading import ShadingReference, apply_shading, calculate_shading
from .usb_transport import CheckCondition, NoDataYet, Transport, UsbError

#: This module was one file; tools and tests import these names from here,
#: so the split re-exports every one of them.
__all__ = [
    "ASC_END_OF_DATA",
    "ASC_NOT_READY",
    "BLANK_CONTRAST",
    "BYTE_ORDER_INTEL",
    "CALIBRATION_FRAME",
    "CCD_MASK_SIZE",
    "CHANNEL_ORDER",
    "CLEAR_RATIO",
    "COORD_PER_INCH",
    "CalibrationRequired",
    "CheckCondition",
    "DEPTH_16",
    "DEPTH_8",
    "DirectScanner",
    "EndOfData",
    "FILM_BW",
    "FILM_KODACHROME",
    "FILM_LEVEL",
    "FILM_NEGATIVE",
    "FILM_POSITIVE",
    "FILM_TYPES",
    "FORMAT_INDEX",
    "FORMAT_LINE",
    "FORMAT_PIXEL",
    "FULL_FRAME",
    "INDEX_HEADER",
    "Inquiry",
    "MAX_BATCH_LINES",
    "MEDIA_PRESENT",
    "METER_EACH",
    "METER_MODES",
    "METER_NONE",
    "METER_ONCE",
    "MIN_INSET_X",
    "MIN_INSET_Y",
    "MM_PER_INCH",
    "NOMINAL_FRAME_WIDTH",
    "NoDataYet",
    "NoMediaLoaded",
    "ONE_PASS_COLOR",
    "ONE_PASS_RGBI",
    "PROTOCOL_REVISION",
    "QUALITY_CALIBRATE",
    "QUALITY_FAST_INFRARED",
    "QUALITY_SHARPEN",
    "QUALITY_SKIP_SHADING",
    "READ_BUDGET_BYTES",
    "RollFrame",
    "SCSI_COPY",
    "SCSI_INQUIRY",
    "SCSI_MODE_SELECT",
    "SCSI_PARAM",
    "SCSI_READ",
    "SCSI_READ_GAIN_OFFSET",
    "SCSI_READ_STATE",
    "SCSI_REQUEST_SENSE",
    "SCSI_SCAN",
    "SCSI_SET_SCAN_HEAD",
    "SCSI_SLIDE",
    "SCSI_TEST_UNIT_READY",
    "SCSI_VENDOR_E7",
    "SCSI_WRITE",
    "SCSI_WRITE_GAIN_OFFSET",
    "SLIDE_INIT",
    "SLIDE_NEXT",
    "SLIDE_PREV",
    "SLIDE_RELOAD",
    "SUB_CALIBRATION_INFO",
    "SUB_CAL_DATA",
    "SUB_CMD_17",
    "SUB_EXPOSURE",
    "SUB_HIGHLIGHT_SHADOW",
    "SUB_SCAN_FRAME",
    "BLUE_RGBI_HEADROOM",
    "BLUE_RGBI_HEADROOM_BY_FILM",
    "BLUE_RGBI_HEADROOM_UNMEASURED",
    "blue_rgbi_headroom",
    "EXPOSURE_TARGET",
    "OVER_TARGET_TOLERANCE",
    "ScanParameters",
    "ScanReadError",
    "Sense",
    "Settings",
    "ShadingReference",
    "State",
    "Transport",
    "UsbError",
    "_cmd",
    "_is_unity",
    "apply_shading",
    "batch_for",
    "calculate_shading",
    "column_defect_sigma",
    "describe_sense",
    "destripe",
    "dilate_defects",
    "film_bounds",
    "gap_edges",
    "find_column_defects",
    "flat_defect_sigma",
    "frame_contrast",
    "INFRARED_IS_BLIND_TO",
    "locks_white_balance",
    "supports_infrared",
    "registration",
    "registration_error_mm",
    "resample_reference",
]


#: Where metering puts each channel's 99.5th percentile, as a fraction of full
#: scale. See :meth:`DirectScanner.auto_exposure`.
#:
#: **0.80, chosen by measurement** with `tools/exposure_headroom.py`, which
#: simulates a higher exposure on stored raw bytes and runs the real shading
#: correction over it. Two things bound it, and they disagree:
#:
#: * *Clipping.* The correction's per-column gain exceeds 1 wherever the lamp
#:   falls off, so edge columns reach the ceiling first -- this is why pieusb
#:   holds its own target at 0.85. Measured here over six entries, four frames
#:   and three resolutions, it costs almost nothing: worst case 0.001% of blue
#:   at 0.80, 0.019% at 0.90 and 0.116% at 0.95.
#: * *Linearity.* A CCD compresses before it saturates. Measured on the 9-pass
#:   3600 dpi ladder, departure from linear is under 0.1% below a third of
#:   scale and grows to 1.5-1.9% in the 75-95% band.
#:
#: So clipping would allow well past 0.90 and linearity argues for stopping
#: sooner. 0.80 takes the second, which also keeps the driver consistent:
#: :data:`rps7200.bracket.CLIP_START` already declines to trust a sample above
#: 0.80, and metering should not aim where another module will not follow.
#:
#: It replaced 0.70, the most conservative figure of any driver read for
#: comparison -- pieusb 0.85, nkscan 0.97 -- which left about a fifth of a stop
#: unused for no measured reason.
EXPOSURE_TARGET = 0.80


#: How far *above* its target a channel may land and still be accepted.
#:
#: Deliberately much tighter than the tolerance below the target. Landing under
#: costs a little noise in the shadows; landing over clips, and a clipped
#: highlight cannot be recovered by anything downstream. 0.02 keeps the top of
#: the acceptance band at 0.82 for the shipped 0.80 target -- just past it, and
#: well short of the rail.
OVER_TARGET_TOLERANCE = 0.02


#: How much brighter blue comes back in an RGBI pass than in an RGB one at the
#: same exposure. :meth:`DirectScanner.auto_exposure` divides blue's target by
#: this when the scan that follows will be RGBI, because the probe is always
#: RGB.
#:
#: **It depends on the film, and by roughly a factor of two.** Two matched
#: measurements, each the same frame in both modes minutes apart, with red and
#: green confirming the mode is the only variable (they move by 0.97-1.00):
#:
#: * **colour negative: 4.98-5.02.** ``20260909T103542Z_unknown-film_600dpi``
#:   against ``20260909T104022Z_unknown-film_600dpi_ir``. Flat across density --
#:   5.02 in the densest decile against 4.95 in the brightest.
#: * **black and white: ~9.6** (8.3-10.5 across percentiles).
#:   ``20260910T120705Z_unknown-film_300dpi`` against
#:   ``20260910T121135Z_unknown-film_600dpi_ir``. *Not* flat across density:
#:   10.2 at the densest end against 8.3 at the brightest.
#:
#: An earlier reading of this file said one constant was the right model. That
#: was measured on one colour negative and over-generalised: the flat-across-
#: density test shows the effect is multiplicative *within* a film, not that it
#: is the same *between* films. Using the colour-negative 5.2 on black and white
#: put 34% of the blue channel at the rail, unrecoverable, on a scan that had
#: already cost its 212 s infrared floor.
#:
#: The density dependence on black and white points at an additive leak into the
#: blue record rather than a change of gain -- a leak is relatively largest where
#: the true blue signal is smallest, which is what the 10.2-to-8.3 slope says.
#: On a colour negative the mask suppresses blue so hard that the leak dominates
#: everywhere, which would make it *look* multiplicative. Not settled; the
#: numbers above are.
#:
#: Every value here sits above its measurement on purpose. The error is
#: asymmetric: too high only darkens a channel that is already noise-limited and
#: carries no fixed column pattern, while too low clips blue, which nothing
#: downstream can undo.
BLUE_RGBI_HEADROOM = 5.2

#: For film whose ratio has not been measured. The larger of the two known
#: values plus margin, because an unmeasured film is exactly where a guess
#: should fail safe -- and where the old single constant did not.
BLUE_RGBI_HEADROOM_UNMEASURED = 11.0

#: Per film type. Only ``negative`` has its own measurement at the low end; the
#: rest take the safe value until someone has a matched pair for them.
#: :attr:`DirectScanner.last_metering` records blue's achieved level on every
#: metered scan, so these become checkable from ordinary work.
BLUE_RGBI_HEADROOM_BY_FILM = {
    FILM_NEGATIVE: BLUE_RGBI_HEADROOM,
    FILM_BW: BLUE_RGBI_HEADROOM_UNMEASURED,
    FILM_POSITIVE: BLUE_RGBI_HEADROOM_UNMEASURED,
    FILM_KODACHROME: BLUE_RGBI_HEADROOM_UNMEASURED,
}


def blue_rgbi_headroom(film: str) -> float:
    """How much to hold blue back when an RGBI pass follows an RGB probe."""
    return BLUE_RGBI_HEADROOM_BY_FILM.get(film, BLUE_RGBI_HEADROOM_UNMEASURED)


@dataclass
class RollFrame:
    """One picture from a roll, as :meth:`DirectScanner.scan_roll` yields it.

    ``image`` and ``meta`` are None and empty on a frame that failed, or on a
    dry run; ``error`` says which. A failed frame is still yielded, because a
    roll takes hours and the caller needs to know what it lost without losing
    the rest.
    """

    index: int                          # 0-based, from the start of the roll
    position: int | None                # what READ_STATE said the transport held
    image: np.ndarray | None
    meta: dict[str, Any]
    prescan: np.ndarray | None
    registration: dict[str, Any]
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.image is not None


class DirectScanner:
    """Command-level control of the scanner."""

    #: Environment variable that turns automatic filing on without touching
    #: code, so a probe script inherits it rather than having to remember.
    DEBUG_ENV = "RPS7200_DEBUG"
    #: Where debug entries go. Overridable so a test never writes into the real
    #: library -- which it did, once, before this existed.
    DEBUG_ROOT_ENV = "RPS7200_DEBUG_ROOT"

    #: Where a display listens in. Declared on the class as well as set in
    #: __init__, because the test doubles stand in for a scanner without
    #: running its constructor and would otherwise not have them at all.
    log_hook: Callable[[str], None] | None = None
    progress_hook: Callable[[int, int], None] | None = None

    def __init__(
        self,
        transport: Transport | None = None,
        verbose: bool = False,
        debug: bool | None = None,
        log_hook: Callable[[str], None] | None = None,
        progress_hook: Callable[[int, int], None] | None = None,
    ):
        # `debug` files every scan in the library automatically. Off by default
        # so ordinary use is not burdened; see the class docstring and
        # CLAUDE.md for who has to turn it on and why.
        self.debug = (
            os.environ.get(self.DEBUG_ENV, "").strip().lower()
            in {"1", "true", "yes", "on"}
            if debug is None
            else bool(debug)
        )
        #: Scans waiting to be filed. Only paths and metadata live here; the
        #: pixels are spooled to disk, because a 7200 dpi roll would otherwise
        #: want 19 GB of RAM.
        self._debug_pending: list[dict[str, Any]] = []
        self._debug_spool: Path | None = None
        self.verbose = verbose
        # Where a display listens. Both are host-side and optional: nothing the
        # device is sent changes, which is why PROTOCOL_REVISION stays put.
        self.log_hook = log_hook
        self.progress_hook = progress_hook
        self._own_transport = transport is None
        self.t = transport or Transport(verbose=verbose)
        self._scanning = False
        self._inquiry: Inquiry | None = None
        # The shading reference this session has acquired. The scanner returns
        # raw pixels and never applies it itself -- see rps7200.shading.
        self._shading: ShadingReference | None = None
        self._ccd_mask: bytes | None = None
        # The last pass's bytes exactly as the scanner sent them, kept only
        # when asked: enough to rebuild the image if the decode ever changes.
        self.last_raw: bytes | None = None
        self.last_raw_layout: dict[str, Any] | None = None
        # What the last auto_exposure() probe actually measured, filed with the
        # scan by :meth:`scan`. See :meth:`auto_exposure`.
        self.last_metering: dict[str, Any] | None = None

    def _log(self, message: str) -> None:
        if self.verbose:
            print(f"[scan] {message}")
        # A UI wants these lines without capturing stdout. Swallowing the hook's
        # own failures is deliberate: a broken display must not take down the
        # scan it is displaying, least of all mid-read.
        if self.log_hook is not None:
            try:
                self.log_hook(message)
            except Exception:                            # noqa: BLE001
                pass

    # -- the session's calibration -----------------------------------------
    #
    # The scanner hands back its per-column response and never applies it, so
    # correcting a scan needs the reference, the CCD mask for that pass, and --
    # to correct it again later, differently -- the raw bytes. All three belong
    # to the session and vanish with it, which is why they are reachable rather
    # than private: every tool needs them to file a scan, and reaching into
    # `_shading` from outside is how that was done before.

    @property
    def shading(self) -> ShadingReference | None:
        """The reference this session will correct with, or None."""
        return self._shading

    @shading.setter
    def shading(self, reference: ShadingReference | None) -> None:
        self._shading = reference

    @property
    def ccd_mask(self) -> bytes | None:
        """The column mapping for the most recent pass.

        Read per pass, not per session: the mask says which CCD pixels *this*
        resolution sampled, so a scan saved with the calibration pass's mask
        cannot be corrected afterwards.
        """
        return self._ccd_mask

    def load_shading(self, path: str | Path) -> ShadingReference:
        """Use a reference saved earlier instead of calibrating.

        Saves the 3-4 minutes a calibration costs, at the price of a reference
        that describes the sensor at the exposure and gain of the pass that
        measured it -- prefer a fresh one when the exposure has moved.
        """
        self._shading = ShadingReference.load(Path(path))
        self._log(
            f"loaded shading from {path}: {self._shading.pixels_per_line} columns, "
            f"channels {self._shading.channels}"
        )
        return self._shading

    def save_shading(self, path: str | Path) -> Path | None:
        """Write this session's reference. Returns None when there is none."""
        if self._shading is None:
            return None
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._shading.save(path)
        return path

    def ensure_shading(
        self, path: str | Path, reuse: bool = False, skip: bool = False
    ) -> dict[str, Any]:
        """Have a shading reference for this session: load one, or measure one.

        Once per session, as the vendor does once per power-on -- every later
        scan reuses the result. Returns what happened, so a caller can report it
        without repeating the decision:

        ``action``     one of ``"skipped"``, ``"loaded"``, ``"calibrated"``
        ``reference``  the reference now in force, or None
        ``path``       where it was read from or written to, or None
        ``duration_s`` what the calibration cost, when one ran
        ``summary``    one line saying which of those happened, to print

        ``skip`` leaves the session with no reference at all, which returns raw
        pixels: the scanner never corrects its own output, so scans then come
        back striped.
        """
        path = Path(path)
        if skip:
            return {
                "action": "skipped",
                "reference": None,
                "path": None,
                "summary": "shading correction disabled: expect vertical striping",
            }

        if reuse and path.exists():
            reference = self.load_shading(path)
            return {
                "action": "loaded",
                "reference": reference,
                "path": path,
                "summary": (
                    f"reusing {path} ({reference.pixels_per_line} columns, "
                    f"channels {reference.channels})"
                ),
            }

        started = time.monotonic()
        result = self.calibrate_shading()
        duration = round(time.monotonic() - started, 1)
        saved = self.save_shading(path)
        drained = result["bytes_drained"] / 1e6
        summary = f"  {drained:.2f} MB in {duration:.0f}s"
        summary += (
            f", saved {saved}" if result["reference"] is not None
            else " -- no usable shading reference; scans will be raw"
        )
        return {
            "action": "calibrated",
            "reference": result["reference"],
            "path": saved,
            "duration_s": duration,
            "bytes_drained": result["bytes_drained"],
            "summary": summary,
        }

    def capture_record(self) -> dict[str, Any]:
        """Everything `rps7200.library.save` needs beyond the pixels and meta.

        Gathered while the session is open and written after it closes: filing
        an entry gzips well over a hundred megabytes, and holding the device
        open and idle through that has preceded it going unresponsive.

        Only the most recent pass is described. A bracket has to call this once
        per pass, as each is captured, because `last_raw` is overwritten.
        """
        return {
            "reference": self._shading,
            "ccd_mask": self._ccd_mask,
            "raw": self.last_raw,
            "raw_layout": self.last_raw_layout,
        }

    # -- automatic filing (debug mode) -------------------------------------

    def _debug_capture(self, image: np.ndarray, meta: dict[str, Any]) -> None:
        """Spool a scan to disk for filing after the session.

        **Held on disk, not in memory.** A 7200 dpi RGBI frame is 570 MB of
        pixels and about as much again of raw bytes, so queueing seventeen of
        them would want 19 GB of RAM. Only paths and metadata stay resident.

        Written uncompressed and sequentially, which is a memcpy and a disk
        write. The thing to keep away from an open device is *compression* --
        gzipping an entry with the scanner open and idle preceded a wedge -- and
        that is deferred to the flush, after close(). A plain write of a few
        hundred megabytes costs a second or two against a scan measured in
        minutes.
        """
        if not self.debug:
            return
        try:
            if self._debug_spool is None:
                self._debug_spool = Path(
                    tempfile.mkdtemp(prefix="rps7200-debug-")
                )
            n = len(self._debug_pending)
            item: dict[str, Any] = {"meta": dict(meta), "captured": time.time()}
            record = self.capture_record()

            image_path = self._debug_spool / f"{n:03d}-image.npy"
            np.save(image_path, image)
            item["image_path"] = image_path

            raw = record.get("raw")
            if raw is not None:
                raw_path = self._debug_spool / f"{n:03d}-raw.bin"
                raw_path.write_bytes(raw)
                item["raw_path"] = raw_path
            item["raw_layout"] = record.get("raw_layout")
            # Small enough to keep: a shading reference is a few hundred kB and
            # the CCD mask is 5172 bytes.
            item["reference"] = record.get("reference")
            item["ccd_mask"] = record.get("ccd_mask")

            self._debug_pending.append(item)
        except Exception as exc:                      # never break a scan
            self._log(f"debug: could not spool this scan ({exc})")

    def _debug_flush(self) -> None:
        """Write the queued scans. Called after the transport is closed.

        Failures are logged and swallowed. Filing is a record-keeping duty, and
        losing the record is better than losing the session that produced it.
        """
        if not self._debug_pending:
            return
        pending, self._debug_pending = self._debug_pending, []
        self._log(f"debug: filing {len(pending)} scan(s) in the library ...")
        try:
            from . import library
            from .library import FilmNotes
        except Exception as exc:
            self._log(f"debug: library unavailable ({exc}); {len(pending)} lost")
            return

        root = os.environ.get(self.DEBUG_ROOT_ENV) or library.DEFAULT_ROOT
        for n, item in enumerate(pending, 1):
            try:
                # mmap the image rather than loading it: tiff.write walks it
                # once, so a 570 MB frame need not be resident.
                image = np.load(item["image_path"], mmap_mode="r")
                entry = library.save(
                    image, item["meta"],
                    root=root,
                    film=FilmNotes(notes="captured with RPS7200_DEBUG on"),
                    tags=["debug"],
                    reference=item.get("reference"),
                    ccd_mask=item.get("ccd_mask"),
                    raw_path=item.get("raw_path"),
                    raw_layout=item.get("raw_layout"),
                    inquiry=self._inquiry,
                )
                self._log(f"debug: filed {n}/{len(pending)} -> {entry}")
            except Exception as exc:
                self._log(f"debug: could not file scan {n} ({exc})")
            finally:
                # Free each frame's spool as soon as it is filed, not at the
                # end. At 7200 dpi a frame spools 1.1 GB, so holding all 38 of
                # a roll through the flush would want 43 GB of disk on top of
                # the library being written -- the peak is what runs a machine
                # out of space, not the total.
                for key in ("image_path", "raw_path"):
                    path = item.get(key)
                    if path is not None:
                        try:
                            Path(path).unlink(missing_ok=True)
                        except Exception:
                            pass
        try:
            if self._debug_spool is not None:
                shutil.rmtree(self._debug_spool, ignore_errors=True)
                self._debug_spool = None
        except Exception:
            pass

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
        # Only now, with the device closed, is it safe to spend time writing.
        self._debug_flush()

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
        last: Sense | str = ""
        for attempt in range(1, retries + 1):
            try:
                return self.t.command(command, read_size=read_size)
            except CheckCondition:
                last = self.read_sense()
                self._log(f"  {label}: {last}")
                time.sleep(0.3)
        raise ScanReadError(f"{label} failed after {retries} attempts: {last}")

    def read_state(self, retries: int = 3) -> State:
        d = self._query(
            _cmd(SCSI_READ_STATE, 13), 13, "read_state", retries=retries
        )
        return State(
            button=bool(d[0]),
            warming_up=bool(d[5]),
            scanning=d[6],
            busy=d[8],
            position=d[2],
        )

    def sense(self) -> bytes:
        return self.t.command(_cmd(SCSI_REQUEST_SENSE, 14), read_size=14)

    def read_sense(self) -> Sense:
        """REQUEST SENSE, parsed. Never raises.

        A sense read can itself fail -- the condition it was going to explain is
        often the reason -- and a caller deciding what a refusal meant still has
        to decide something. `Sense.unreadable` is that case, and it answers no
        to every question rather than pretending to be a code.
        """
        try:
            return Sense.parse(self.sense())
        except (UsbError, IndexError) as exc:
            return Sense.unreadable(str(exc))

    def _unit_ready_sense(self) -> Sense | None:
        """TEST UNIT READY; returns None when good, else what it complained of."""
        try:
            self.t.command(_cmd(SCSI_TEST_UNIT_READY, 0))
            return None
        except CheckCondition:
            return self.read_sense()

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
            warming = info.key == 0x02 or info.not_ready
            if not warming:
                # Some other one-shot condition; reading the sense cleared it.
                self._log(f"  wait_warm: {info}")
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
                self._log(f"  unit not ready: {self.read_sense()}")
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
        calibrate: bool = False,
        sharpen: bool = False,
        fast_infrared: bool = False,
        halftone_pattern: int = 0,
        line_threshold: int = 0x80,
        byte14: int | None = None,
    ) -> None:
        """Configure the scan.

        ``skip_shading`` defaults to True deliberately: leaving it off is what
        sends the backend into the shading read this scanner cannot complete.

        ``byte14`` overrides the last meaningful byte, whose default below is
        known to be wrong -- see the comment there. It exists so the byte can be
        driven directly and measured; nothing in normal operation passes it.
        """
        quality = 0
        if sharpen:
            quality |= QUALITY_SHARPEN
        if calibrate:
            quality |= QUALITY_CALIBRATE      # byte 10; excludes skip-shading
        elif skip_shading:
            quality |= QUALITY_SKIP_SHADING   # byte 9
        if fast_infrared:
            quality |= QUALITY_FAST_INFRARED

        data = bytearray(16)
        data[1] = 16 - 1
        data[2:4] = resolution.to_bytes(2, "little")
        data[4] = passes
        data[5] = depth
        data[6] = color_format
        data[8] = BYTE_ORDER_INTEL
        data[9:11] = int(quality).to_bytes(2, "little")
        data[12] = halftone_pattern if halftone_pattern else 0x02
        data[13] = line_threshold
        # Byte 14 was read as "0x21 for RGBI, 0x10 for RGB" from two captures.
        # The full set of seven refutes that: RGB passes carry 0x21 twenty-six
        # times. What holds across all 71 MODE SELECTs is that *bit 0* tracks
        # the scan frame's y0 shifting by one line, which is bidirectional
        # scanning -- the carriage images going down, then coming back. The
        # upper nibble is not explained. See docs/protocol.md section 4.
        #
        # The default is left alone until the hardware says what it should be;
        # `byte14` is how that gets asked.
        data[14] = byte14 if byte14 is not None else (
            0x21 if passes == ONE_PASS_RGBI else 0x10
        )

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

    def slide(
        self, action: int = SLIDE_INIT, param: int = 0x16, value: int = 0
    ) -> None:
        """Drive the film/slide transport.

        pieusb only issues this when its config marks the model as having a
        slide transport, and for model 0x31 that flag is 0, so the stock backend
        never initialises the transport at all -- even though INQUIRY reports an
        ADF. SLIDE_NEXT is also how a whole strip gets advanced frame by frame.

        The payload is four bytes, ``action param 00 value``. ``param`` is 0x16
        in the capture this driver was reconstructed from; it takes 0x01, 0x13,
        0x14, 0x15 and 0x16 across the six captures with no visible difference,
        so it is left where it is. ``value`` matters: every film advance the
        vendor performs carries 1 there (once 2), never 0, which is what this
        driver used to send.
        """
        names = {
            SLIDE_NEXT: "next",
            SLIDE_PREV: "prev",
            SLIDE_INIT: "init",
            SLIDE_RELOAD: "reload",
        }
        self._log(f"slide transport: {names.get(action, hex(action))}")
        data = bytes([action, param, 0x00, value])
        self.t.command(_cmd(SCSI_SLIDE, 4), data=data)

    def _whole_frames(
        self, action: int, steps: int, timeout: float, poll: float, verb: str
    ) -> int | None:
        """Move whole frames and wait until `READ_STATE` says it happened.

        Waiting is the point, and it is the same waiting in both directions.
        `READ_STATE` byte 2 is the transport position, and it is the only signal
        in any capture that says the film has actually moved: it stepped
        0 -> 1 -> 2 -> 3 -> 4 across the strip session's four advances, and
        stayed put through a session that never advanced. The new value showed
        up 1.6 s to 6.2 s later, and the READ_STATE issued immediately after the
        command came back empty every time -- so the poll has to survive a
        failed read rather than treat it as the end.
        """
        before = self.position()
        self.slide(action, param=0x01, value=steps)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(poll)
            now = self.position()
            if now is not None and now != before:
                self._log(f"{verb} to position {now}")
                return now
        self._log(
            f"no movement: position still {before} after {timeout:.0f}s"
        )
        return None

    def advance(
        self, steps: int = 1, timeout: float = 30.0, poll: float = 0.5
    ) -> int | None:
        """Move the film on by one frame, and wait until it has.

        The payload is the vendor's: ``04 01 00 01``, seen three times in
        ``600_ICE_FILM_STRIP_5.pcapng`` (with ``04 01 00 02`` once, for reasons
        the capture does not explain -- the position still moved by one).

        Returns the new position, or None if it never moved -- which is how a
        roll ends.
        """
        position = self._whole_frames(
            SLIDE_NEXT, steps, timeout, poll, "advanced"
        )
        if position is None:
            self._log("treating that as the end of the film")
        return position

    def retreat(
        self, steps: int = 1, timeout: float = 30.0, poll: float = 0.5
    ) -> int | None:
        """Move the film back by one frame, and wait until it has.

        ``05 01 00 01``, the mirror of :meth:`advance`. This is what the vendor
        sends to rewind a finished roll, one frame per step, and it has been
        driven here five times in a row with the position stepping down by
        exactly one each time.

        Unlike an advance, a refusal is not the end of anything -- it usually
        means the film is already at the first frame. Returns the new position,
        or None if it did not move.
        """
        return self._whole_frames(SLIDE_PREV, steps, timeout, poll, "went back")

    def position(self) -> int | None:
        """Where the transport has the film, or None if it would not say.

        The one trustworthy signal that an advance happened -- see
        :meth:`advance`. Never raises: a READ_STATE issued right after a
        transport command comes back empty every time, so a caller polling this
        has to be able to tell "not yet" from "failed".
        """
        try:
            return self.read_state(retries=1).position
        except (CheckCondition, UsbError, ScanReadError, IndexError):
            return None

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
        last = Sense.unreadable("the scanner never refused with a sense")

        while True:
            try:
                self.t.command(_cmd(SCSI_SCAN, 1))
                self._scanning = True
                return
            except CheckCondition:
                # read_sense rather than sense(): a REQUEST SENSE that itself
                # fails used to propagate straight out of this handler, leaving
                # the start abandoned mid-sequence, which is what wedges the
                # scanner. An unreadable sense is a reason to retry, not to
                # walk away.
                last = self.read_sense()

                if last.not_ready:        # still becoming ready
                    if time.monotonic() > deadline:
                        break
                    time.sleep(ready_poll)
                    continue

                attempts += 1
                self._log(f"  start_scan: {last} (retry {attempts}/{retries})")
                if attempts >= retries or time.monotonic() > deadline:
                    break
                time.sleep(0.5)

        # Leave the scanner usable; an abandoned start wedges it otherwise.
        self._scanning = False
        raise CalibrationRequired(f"scanner refused to start: {last}")

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
                self._log(f"  stop_scan: {self.read_sense()}")
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
        max_wait_s: float = 300.0,
    ) -> bytes:
        """Read ``lines`` scan lines.

        Retries like :meth:`_query` does: a queued one-shot sense condition is
        reported against whichever command arrives next, so the first attempt
        can be rejected for something that has nothing to do with this read.

        ``max_wait_s`` is well above the transport's 60 s default because a
        read here waits on the scanner physically scanning, not on a bus
        round trip. Infrared holds the device busy for its own ~212 s floor
        however few lines were asked for, so at low resolution with IR the
        60 s default expired first -- and abandoning a read mid-scan is what
        leaves this device needing a power cycle.
        """
        last = Sense.unreadable("no attempt was made")
        for _ in range(retries):
            try:
                return self.t.command(
                    _cmd(SCSI_READ, lines),
                    read_size=lines * bytes_per_line,
                    timeout_ms=timeout_ms,
                    max_wait_s=max_wait_s,
                )
            except CheckCondition:
                last = self.read_sense()
                self._log(f"  read_lines: {last}")
                time.sleep(0.3)
        if last.end_of_data:
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
        keep_raw: bool = False,
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
            # Structured, so a progress bar does not have to parse the line
            # above and then go quietly dead the day it is reworded.
            if self.progress_hook is not None:
                try:
                    self.progress_hook(got, total_lines)
                except Exception:                        # noqa: BLE001
                    pass

        blob = b"".join(chunks)
        if keep_raw:
            # Everything a decoder needs, so the bytes stay meaningful without
            # this object. Line stride includes the 2-byte channel tag.
            self.last_raw = blob
            self.last_raw_layout = {
                "format": "index",
                "bytes_per_line": int(params.bytes_per_line),
                "line_stride": int(params.bytes_per_line) + INDEX_HEADER,
                "index_header": INDEX_HEADER,
                "width": int(params.width),
                "lines": int(params.lines),
                "channels": int(channels),
                "byte_order": "little",
                "lines_received": len(blob) // (int(params.bytes_per_line) + INDEX_HEADER),
            }
        return self._deinterleave(blob, params, channels)

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
        self,
        resolution: int = 300,
        frame: tuple[int, int, int, int] | None = None,
        keep_raw: bool = False,
    ) -> tuple[np.ndarray, ScanParameters]:
        """Low-resolution RGB pass over the full transport.

        This is what the vendor software runs before every frame: 300 dpi,
        three channels, 8-bit, covering the whole scan area. It carries no
        infrared -- captures confirm the prescan is always ``passes=0x80`` --
        and exists to find where the picture actually sits.

        Shading correction is off, and has to be: the reference is measured in
        16-bit units and this pass is 8-bit, so subtracting its dark half would
        drive every pixel to zero. A framing pass does not need the correction
        anyway -- it is looking for where the picture stops, not at its
        colour.
        """
        image, meta = self.scan(
            resolution=resolution,
            infrared=False,
            depth=DEPTH_8,
            frame=frame or FULL_FRAME,
            shading=False,
            keep_raw=keep_raw,
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

    def session_start(self) -> None:
        """Open a session the way the vendor software does after power-on.

        INQUIRY, then the vendor command 0xE7, then REQUEST SENSE and a SLIDE
        with `00 01 00 04`. 0xE7 takes no data and its meaning is unknown, but
        it appears at the start of every captured session and only in the two
        captures that contain a successful calibration -- so it may be what
        puts the scanner into a state where calibration is accepted.
        """
        self.inquiry(refresh=True)
        try:
            self.t.command(_cmd(SCSI_VENDOR_E7, 4))
            self._log("0xE7 accepted")
        except CheckCondition:
            try:
                self._log(f"0xE7: {self.read_sense()}")
            except UsbError:
                pass
        except UsbError as exc:
            self._log(f"0xE7 failed: {exc}")
        try:
            self.sense()
        except UsbError:
            pass
        try:
            self.slide(0x00, param=0x01)
        except (CheckCondition, UsbError) as exc:
            self._log(f"opening slide failed: {exc}")

    def calibrate_shading(
        self,
        timeout: float = 300.0,
        keep_data: bool = False,
        exposure_scale: float | Sequence[float] = 1.0,
    ) -> dict[str, Any]:
        """Run the scanner's shading calibration, as the vendor does at startup.

        This runs and returns data: 40 blocks, 1.66 MB, the scanner ending the
        pass itself. Whether it actually improves striping is UNVERIFIED -- the
        scan that would have measured it stalled before completing.

        What blocked this for four attempts was a PARAM call of my own: the
        vendor issues none during calibration, and mine was refused and
        evidently disturbed the pass. The width comes from the frame instead.
        The 0xe7 vendor command is not involved -- it is refused with ASC 0x20,
        and the vendor follows it immediately with REQUEST SENSE, so it is
        refused there too.

        Reconstructed from a capture taken from scanner power-on: this is the
        very first thing the vendor software does once the device is ready, and
        every later scan reuses the result (mode quality 0x0008, "reuse", versus
        0x0800 here, "calibrate now").

        Details that matter, all of which differ from an ordinary scan:

        * frame ``(0, 3431, 10343, 6888)`` -- the lower part of the transport
        * 3600 dpi, three channels, mode depth 8-bit
        * ``SLIDE INIT`` carries ``10 01 00 00`` here, not the 0x15/0x16 second
          byte seen elsewhere
        * calibration lines come back **16-bit regardless of the mode depth**:
          2 * width + 2 bytes, so 10346 at 3600 dpi. Sizing the read from the
          mode depth reads nothing at all, which is why an earlier attempt
          drained zero bytes.
        * the vendor alternates 4-line and 72-line reads, re-reading and
          re-writing gain/offset between them
        """
        for _ in range(4):
            try:
                if not self.read_state().warming_up:
                    break
            except (CheckCondition, ScanReadError):
                pass
            time.sleep(1)
        self.wait_warm()
        self.test_unit_ready()

        self.set_exposure_time()
        self.set_highlight_shadow()

        prep = bytearray(6)
        prep[0:2] = (SUB_CALIBRATION_INFO | 0x80).to_bytes(2, "little")
        prep[2:4] = (2).to_bytes(2, "little")
        try:
            self.t.command(_cmd(SCSI_WRITE, 6), data=bytes(prep))
            info = self._query(_cmd(SCSI_READ, 128), 128, "calibration_info")
            self._log(f"calibration info: {info[:12].hex(' ')}")
        except (CheckCondition, ScanReadError) as exc:
            self._log(f"calibration info read failed ({exc}); continuing")

        self.set_scan_frame(*CALIBRATION_FRAME)
        try:
            self.cmd_17(1)
        except CheckCondition:
            pass
        # Calibrate at the exposure the scans will use. The reference
        # describes the sensor at one integration time and does not carry
        # across a large change in it: measured on a real frame, a channel
        # calibrated 3x below its scan exposure corrected 13.0% -> 1.4%, one
        # 6x below 8.2% -> 2.0%, and one 10x below got WORSE, 10.0% -> 11.2%.
        # The vendor writes 8277/28645/53160 immediately before this pass --
        # scanning exposures, not the power-on defaults.
        settings = self.get_gain_offset().scaled(exposure_scale)
        self.set_gain_offset(settings)
        if not _is_unity(exposure_scale):
            self._log(f"calibrating at {settings.describe()}")

        self.set_mode(
            resolution=3600,
            passes=ONE_PASS_COLOR,
            depth=DEPTH_8,
            color_format=FORMAT_INDEX,
            calibrate=True,
        )
        self.slide(SLIDE_INIT, param=0x01)
        self.wait_ready()

        started = time.monotonic()
        # Width comes from the frame, not from PARAM: the vendor issues no
        # PARAM at all during calibration, and calling it here is rejected and
        # may disturb the scan. Lines are 16-bit whatever the mode depth says.
        # Column count from the device's own descriptor. `pixels_per_line`
        # there is a BYTE count -- 10344 = 5172 columns x 16 bits -- which is
        # easy to read as columns and be exactly twice wrong. The frame-derived
        # value is the fallback, and the two are compared so a mismatch is
        # visible rather than silent.
        x0, _, x1, _ = CALIBRATION_FRAME
        width = round((x1 - x0) * 3600 / COORD_PER_INCH)
        try:
            parms = self.get_shading_parms()
        except (CheckCondition, ScanReadError) as exc:
            self._log(f"shading descriptor unreadable ({exc}); sizing from the frame")
        else:
            declared = [e["pixels_per_line"] // 2 for e in parms if e.get("pixels_per_line")]
            if declared:
                if declared[0] != width:
                    self._log(
                        f"note: descriptor says {declared[0]} columns, the frame "
                        f"implies {width}; using the descriptor"
                    )
                width = declared[0]
            self._log(
                f"shading descriptor: {len(parms)} entries, "
                f"{sum(e.get('lines', 0) for e in parms)} lines declared, "
                f"{width} columns"
            )
        bpl = 2 * width + INDEX_HEADER

        self.start_scan()
        drained = 0
        collected: list[bytes] = []
        try:
            self._log(f"calibration reads: {bpl} bytes/line (width {width})")

            # The vendor issues nothing at all for nine seconds after START
            # SCAN -- not a poll, literal silence -- then TEST UNIT READY and
            # its first read. Polling here instead appears to disturb the
            # calibration: doing so left every read refused with ASC 0x20.
            self._log("waiting 10s in silence, as the vendor does")
            time.sleep(10.0)
            self.test_unit_ready()

            # The vendor alternates 4-line and 72-line reads, but conditionally
            # -- after a 4-line read it sometimes takes 72 and sometimes not,
            # judging by the data. Since that condition is unknown, read in
            # small fixed blocks until the scanner refuses. A refusal (ASC 0x20)
            # is answered before any transfer and is harmless; asking for lines
            # that do not exist stalls the bulk transfer instead, and a bulk
            # timeout leaves the device needing a power cycle.
            deadline = time.monotonic() + timeout
            blocks = 0
            while time.monotonic() < deadline:
                try:
                    self.set_gain_offset(self.get_gain_offset())
                except (CheckCondition, ScanReadError):
                    pass
                try:
                    chunk = self.read_lines(4, bpl, retries=1)
                except NoDataYet:
                    time.sleep(0.05)
                    continue
                except (EndOfData, ScanReadError):
                    self._log(f"  scanner finished after {blocks} blocks")
                    break
                drained += len(chunk)
                # Always kept: the reference is built from these bytes, so
                # collecting only on request meant the default path parsed an
                # empty buffer and quietly produced no reference at all.
                # `keep_data` decides whether the caller also gets them back.
                collected.append(chunk)
                blocks += 1
                if blocks % 10 == 0:
                    self._log(f"  {blocks} blocks, {drained/1e6:.2f} MB")
            mask = self.get_ccd_mask(CCD_MASK_SIZE)
        finally:
            self.finish_scan()

        data = b"".join(collected)
        # The point of the pass. The scanner measured its per-column response
        # and handed it back; it does not apply it, so a calibration whose
        # result is discarded genuinely changes nothing in the image.
        self._shading = calculate_shading(data, width)
        if self._shading is None:
            self._log("calibration returned no usable shading lines")
        else:
            self._ccd_mask = mask
            self._log(
                f"shading reference: {width} columns, channels "
                f"{self._shading.channels}, means "
                f"{[round(self._shading.mean[c], 1) for c in self._shading.channels]}"
            )

        return {
            "shading_calibration": True,
            "data": data if keep_data else None,
            "reference": self._shading,
            "ccd_mask": mask,
            "bytes_per_line": bpl,
            "pixels_per_line": width,
            "bytes_drained": drained,
            "duration_s": round(time.monotonic() - started, 1),
        }

    # -- exposure ----------------------------------------------------------

    def auto_exposure(
        self,
        target: float = EXPOSURE_TARGET,
        percentile: float = 99.5,
        resolution: int = 300,
        infrared: bool = False,
        rounds: int = 2,
        tolerance: float = 0.08,
        start: Sequence[float] | None = None,
        film: str = FILM_NEGATIVE,
        infrared_blue_headroom: float | None = None,
        max_rounds: int | None = None,
    ) -> list[float]:
        """Find per-channel exposure scales by probing at low resolution.

        Aims to put ``percentile`` of each channel at ``target`` of full scale
        -- high enough to use the range, with headroom so highlights do not
        clip. Returns scales to hand to :meth:`scan` as ``exposure_scale``.

        **Always probes in RGB, never in infrared**, and in at most two rounds:
        this is what the vendor software does. An infrared pass costs its own
        ~212 s floor however few lines are asked for, so metering in infrared
        would spend ten minutes to learn what a three-second pass can tell us.

        ``infrared`` therefore does not change how the probe is taken. It says
        the scan that follows will be RGBI, which matters only for blue: blue
        comes back several times brighter in an RGBI pass than in an RGB one at
        the *same* exposure, so a blue metered to fill the range in RGB clips in
        RGBI. Blue's target is divided by ``infrared_blue_headroom`` to leave
        room for that; passing None takes the value this ``film`` needs, which
        is not the same for all of them -- see :data:`BLUE_RGBI_HEADROOM`.

        Costing blue some exposure is the right trade here, and the vendor
        makes it too: its own captures meter blue to 1475-5906 where green sits
        near 40000, an order of magnitude down, and reuse those values verbatim
        for the infrared scan. Blue on this scanner carries no fixed column
        pattern -- it is noise-limited, not detail-limited -- so a darker blue
        costs little, while a clipped blue is unrecoverable.

        ``rounds`` is the normal number of probes and ``max_rounds`` the most
        that may be spent. They differ only for a clipped channel: a level at
        full scale says the channel is somewhere *above* it, so the retreat
        applied there is a guess rather than a measurement and is worth
        confirming. Everything else is settled in one proportional step,
        because the sensor is linear in exposure -- measured r^2 = 0.9999 over
        a 4x range on the 3600 dpi ladder in the library.

        What each round measured is left on :attr:`last_metering`, and
        :meth:`scan` files it with the entry. That is what makes the headroom
        above checkable from ordinary scans instead of a special experiment.

        ``film`` decides whether the visible channels are metered together or
        apart -- see :func:`locks_white_balance`. This matters: metering a slide
        per channel stretches each one to the same target and takes the cast
        off the picture. Infrared is always metered on its own, being no part
        of the colour balance.

        The channels differ enormously -- with no film in the transport blue
        saturates while red sits near a fifth of scale -- so a negative is
        metered per channel rather than with one global factor.
        """
        locked = locks_white_balance(film)
        # None means "whatever this film needs" -- the ratio differs by roughly
        # a factor of two between colour negative and black and white, and a
        # single number blew a third of the blue channel on the latter. An
        # explicit value still wins, so a probe can pin it.
        if infrared_blue_headroom is None:
            infrared_blue_headroom = blue_rgbi_headroom(film)
        # Cleared up front so a run that raises leaves no stale telemetry for
        # the next scan to file as its own.
        self.last_metering = None
        # The probe is always three-channel; `infrared` describes the scan
        # that follows, not this pass.
        channels = 3
        scales = list(start) if start else [1.0] * channels
        limited = [False] * channels
        full = 65535.0

        # The exposure every scale is relative to, read once. :meth:`scan`
        # multiplies whatever the device currently holds, and SET GAIN OFFSET
        # persists, so re-reading it each round would compound the scales.
        base = self.get_gain_offset()
        self._log(
            f"auto-exposure: film={film}, "
            f"{'locked (one factor for R/G/B)' if locked else 'per channel'}"
        )

        # One further round is allowed, and spent only on a clipped channel --
        # see the ``rounds``/``max_rounds`` note in the docstring.
        budget = max(rounds, rounds + 1 if max_rounds is None else max_rounds)
        probes: list[dict[str, Any]] = []

        for round_no in range(1, budget + 1):
            self.set_gain_offset(base)
            asked = list(scales)
            image, _ = self.scan(
                resolution=resolution,
                infrared=False,
                exposure_scale=scales,
                # CLAUDE.md's rule is "file every scan, with its raw bytes",
                # without an exception for throwaway passes -- and a metering
                # probe is only throwaway until someone asks what it saw. At
                # 300 dpi it costs about 1.3 MB.
                keep_raw=True,
            )
            levels = [
                float(np.percentile(image[..., c], percentile)) / full
                for c in range(image.shape[2])
            ]
            self._log(
                f"auto-exposure round {round_no}: "
                + " ".join(
                    f"{'RGBI'[c]}={levels[c]:.0%}" for c in range(len(levels))
                )
            )
            # Blue's target is the one that moves: it comes back about 5x
            # brighter in an RGBI pass than in the RGB probe at the same
            # exposure, so a blue metered to fill the range here clips there.
            targets = [target] * len(levels)
            if infrared and len(targets) > 2:
                targets[2] = target / max(1.0, infrared_blue_headroom)

            clipped = [level >= 0.999 for level in levels]
            probes.append({
                "round": round_no,
                "scales": [round(v, 4) for v in asked],
                "levels": [round(v, 4) for v in levels],
                "targets": [round(v, 4) for v in targets],
                "clipped": clipped,
            })

            # Asymmetric, and it has to be. `tolerance` is how far *under* a
            # target is close enough to stop; going over is not symmetric with
            # going under, because under costs a little noise and over clips,
            # which nothing downstream can undo.
            #
            # This was `abs(v - t) <= tolerance`, which at the old target of
            # 0.70 accepted up to 0.78 and was harmless. Raising the target to
            # 0.80 moved the top of that band to 0.88 -- past the knee
            # bracket.py stops trusting -- and a B&W frame duly landed at 87%
            # with samples at the rail. Raising a target is not safe unless the
            # band above it is looked at too.
            over = OVER_TARGET_TOLERANCE
            if all(-tolerance <= v - t <= over for v, t in zip(levels, targets)):
                break
            # Past the normal budget only to re-measure a retreat. A level at
            # full scale says the channel is somewhere above it, so the 0.25
            # below is a guess; every other correction is one proportional step
            # on a linear sensor and needs no confirming.
            if round_no >= rounds and not any(clipped):
                break

            visible = levels[:3]
            ceilings = [
                65535 / base.exposure[c] if base.exposure[c] else 8.0
                for c in range(len(scales))
            ]
            growth = []
            for c, level in enumerate(levels):
                if c >= len(scales):
                    break
                # Locked: every visible channel moves by the one factor the
                # brightest of them needs, so none clips and the proportions --
                # the film's own cast -- survive. Infrared is metered alone.
                measured = max(visible) if (locked and c < 3) else level
                if measured <= 0.001:
                    growth.append(4.0)          # far too dark to measure
                elif measured >= 0.999:
                    growth.append(0.25)         # clipped; back well off
                else:
                    growth.append(targets[c] / measured)

            # A locked film has to stay *locked* against the ceiling too. The
            # per-channel clamp below cannot do that: the ceilings are not
            # equal -- red's is x6.82 against x10.07 for green and blue,
            # because its base exposure is higher -- so clamping each channel
            # on its own silently pulls red 45% below the other two and puts a
            # cast on exactly the films the lock exists to keep the cast of.
            #
            # So hold the whole group back by one factor instead, the tightest
            # any of them needs. That under-exposes the scan rather than
            # colouring it, and `limited` below says it happened.
            held = 1.0
            if locked:
                for c in range(min(3, len(growth))):
                    want = scales[c] * growth[c]
                    if want > ceilings[c] > 0:
                        held = min(held, ceilings[c] / want)

            for c in range(len(growth)):
                scales[c] *= growth[c] * (held if (locked and c < 3) else 1.0)
                # Bound by what the timer can actually hold, not by a
                # guess. A fixed cap of 8x used to stop blue short: with film
                # loaded the device's own blue exposure sits low (6506 in one
                # scan), leaving room for 10x, and the cap -- not the hardware
                # -- was what kept the blue record dark.
                if scales[c] > ceilings[c]:
                    limited[c] = True
                scales[c] = max(0.01, min(ceilings[c], scales[c]))
            if held < 1.0:
                for c in range(min(3, len(growth))):
                    if abs(scales[c] - ceilings[c]) < 1e-6:
                        limited[c] = True
                self._log(
                    f"  locked metering held to x{held:.3f} of what it wanted, "
                    f"so R/G/B keep their proportions; the scan lands "
                    f"{100 * (1 - held):.0f}% under target"
                )

        self._log(f"auto-exposure result: {[round(v, 3) for v in scales]}")

        # Left for scan() to file with the entry. Metering is the one step whose
        # inputs are otherwise unrecoverable -- the probe is thrown away and only
        # its conclusion survives -- so a scan that came out wrong could not be
        # told from one metered against a frame that asked for something odd.
        # With this, blue's RGBI headroom is measurable from ordinary scans.
        self.last_metering = {
            "target": target,
            "percentile": percentile,
            "film": film,
            "locked": locked,
            "infrared": infrared,
            "blue_headroom": infrared_blue_headroom if infrared else None,
            "resolution_dpi": resolution,
            "base_exposure": list(base.exposure),
            "rounds": probes,
            "scales": [round(v, 4) for v in scales],
            "limited": list(limited),
        }

        # Exposure is a 16-bit timer count, and past full scale the firmware
        # wraps -- the pass comes out darker, not brighter. A channel that
        # wanted more than the timer holds was held at the ceiling and did not
        # reach the target; say so, because silently returning a scale that
        # could not be applied reads as a metering failure later.
        for c, was_limited in enumerate(limited):
            if was_limited and c < len(base.exposure):
                # The same guard the ceiling above carries. Without it this
                # message -- which exists only to explain the ceiling -- was the
                # one thing that could not survive the case it describes: the
                # scanner reported a zero exposure just after re-enumerating and
                # metering died here, inside the branch that reports the
                # problem, rather than in the arithmetic that handles it.
                room = (
                    f"{65535 / base.exposure[c]:.2f}x of exposure "
                    f"{base.exposure[c]}"
                    if base.exposure[c]
                    else "the device reported an exposure of 0, so the fallback "
                    "ceiling of 8x"
                )
                self._log(
                    f"  note: {'RGBI'[c]} is held at the timer ceiling "
                    f"({room} is all there is); this channel could "
                    f"not reach the target"
                )
        return scales

    # -- flat field --------------------------------------------------------

    # -- orchestration -----------------------------------------------------

    # -- exposure brackets --------------------------------------------------

    MIN_BRACKET_PASSES = 2
    MAX_BRACKET_PASSES = 9

    def bracket_ladder(
        self,
        scales: Sequence[float],
        passes: int,
        stops: float = 2.0,
        base: Settings | None = None,
    ) -> list[float]:
        """Geometric multipliers for a bracket, topping out at the timer ceiling.

        ``scales`` is the metered per-channel exposure scale. Every pass
        multiplies all three channels by the *same* number, so the ratio between
        two passes is the same in every channel -- which is what lets the merge
        weight a pass with one exposure value instead of three.

        The top of the ladder is the largest multiplier that keeps every channel
        inside the 16-bit exposure timer. Past 65535 the timer wraps and the pass
        comes back darker, so a bracket that ignored this would not merely
        saturate, it would fold. The rest of the ladder steps down from there by
        ``stops``.

        Which channel binds depends on the film: green metered highest in every
        negative measured here, so green usually sets the ceiling.
        """
        if not self.MIN_BRACKET_PASSES <= passes <= self.MAX_BRACKET_PASSES:
            raise ValueError(
                f"a bracket is {self.MIN_BRACKET_PASSES} to "
                f"{self.MAX_BRACKET_PASSES} passes, got {passes}"
            )
        if stops <= 0:
            raise ValueError(f"stops must be positive, got {stops}")

        base = base or self.get_gain_offset()
        metered = [
            base.exposure[c] * (scales[c] if c < len(scales) else 1.0)
            for c in range(3)
        ]
        headroom = min(
            (65535.0 / e for e in metered if e > 0), default=1.0
        )
        if headroom < 1.0:
            # The metered exposure is already at the rail; the bracket can only
            # go down from here.
            headroom = 1.0
        top = headroom
        bottom = top / (2.0 ** stops)
        ladder = list(np.geomspace(bottom, top, passes))

        binding = min(range(3), key=lambda c: 65535.0 / metered[c] if metered[c] else 1e9)
        self._log(
            f"bracket ladder: {passes} passes over {stops:g} stops, "
            f"x{bottom:.3f} to x{top:.3f} of the metered exposure "
            f"({'RGB'[binding]} binds the ceiling)"
        )
        return ladder

    def scan_bracket(
        self,
        passes: int = 3,
        stops: float = 2.0,
        resolution: int = 300,
        infrared: bool = False,
        film: str = FILM_NEGATIVE,
        frame: tuple[int, int, int, int] | None = None,
        auto_exposure: bool = True,
        exposure_scale: Sequence[float] | None = None,
        keep_raw: bool = True,
        shading: bool = True,
        retain: bool = True,
        on_pass: Callable[[int, np.ndarray, dict[str, Any], dict[str, Any]], None]
        | None = None,
    ) -> tuple[list[np.ndarray], list[float], list[dict[str, Any]]]:
        """Scan one frame several times at different exposures.

        Returns ``(frames, ratios, metas)`` in ascending exposure order, ready
        for :func:`rps7200.bracket.merge_bracket`. The film is not advanced and
        the shading reference is acquired once for the whole bracket, so every
        pass describes the same frame through the same sensor state.

        Infrared is deliberately *not* bracketed. It costs its own ~212 s floor
        per pass however few lines are asked for, and its exposure is a device
        constant the vendor never meters, so bracketing it would multiply the
        scan time for nothing. With ``infrared`` set, one pass -- the brightest,
        which carries the most signal -- is taken as RGBI and the rest as RGB.

        ``on_pass(index, image, meta, capture)`` is called as each pass lands,
        with that pass's :meth:`capture_record`. It exists because only one
        pass's raw bytes survive on the scanner: ``last_raw`` is overwritten by
        the pass after it, so a caller that waits for the return value can file
        the last pass and no other. Do no heavy work in it -- the session is
        open and the next pass is about to start.
        """
        if not self.MIN_BRACKET_PASSES <= passes <= self.MAX_BRACKET_PASSES:
            raise ValueError(
                f"a bracket is {self.MIN_BRACKET_PASSES} to "
                f"{self.MAX_BRACKET_PASSES} passes, got {passes}"
            )

        if exposure_scale is not None:
            scales = list(exposure_scale)
        elif auto_exposure:
            scales = self.auto_exposure(film=film, infrared=infrared)
        else:
            scales = [1.0, 1.0, 1.0]

        ladder = self.bracket_ladder(scales, passes, stops)

        frames: list[np.ndarray] = []
        ratios: list[float] = []
        metas: list[dict[str, Any]] = []
        for i, k in enumerate(ladder):
            last = i == len(ladder) - 1
            pass_scale = [s * k for s in scales[:3]]
            self._log(
                f"bracket pass {i + 1}/{passes}: x{k:.3f} "
                f"({'RGBI' if (infrared and last) else 'RGB'})"
            )
            image, meta = self.scan(
                resolution=resolution,
                infrared=infrared and last,
                frame=frame,
                exposure_scale=pass_scale,
                keep_raw=keep_raw,
                shading=shading,
                film=film,
            )
            meta["bracket_index"] = i
            meta["bracket_ratio"] = float(k)
            meta["bracket_passes"] = passes
            meta["bracket_stops"] = float(stops)
            ratios.append(float(k))
            metas.append(meta)
            if on_pass is not None:
                on_pass(i, image, meta, self.capture_record())
            # `retain` off keeps only one pass alive at a time. Nine passes at
            # 3600 dpi are 960 MB of pixels and as much again in raw bytes, and
            # a caller that has already written each one to disk in `on_pass`
            # has no use for the list. Writing there is safe as long as it stays
            # quick -- an uncompressed dump is well under a second, where
            # gzipping a whole library entry with the device open and idle is
            # what preceded a wedge.
            if retain:
                frames.append(image)
            else:
                del image
        return frames, ratios, metas

    def scan(
        self,
        resolution: int = 300,
        infrared: bool = True,
        depth: int = DEPTH_16,
        frame: tuple[int, int, int, int] | None = None,
        advance: bool = False,
        require_media: bool = True,
        exposure_scale: float | Sequence[float] = 1.0,
        auto_exposure: bool = False,
        exposure_target: float = EXPOSURE_TARGET,
        skip_shading: bool = True,
        shading: bool = True,
        film: str = FILM_NEGATIVE,
        keep_raw: bool = False,
        byte14: int | None = None,
        slide_init_param: int = 0x16,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        """Run one scan and return ``(image, metadata)``.

        The command order here is the vendor software's, recovered from a USB
        capture. It is load-bearing: in particular :meth:`cmd_17` must follow
        the scan frame, or the scanner refuses to skip shading analysis and the
        scan cannot complete. See the README.

        ``film`` reaches auto-exposure, and only auto-exposure: it decides
        whether the visible channels are metered together or apart. Getting it
        wrong on a slide takes the cast off the picture -- see
        :func:`locks_white_balance`.

        ``shading`` applies this session's shading reference, which is what
        removes the vertical striping. The scanner measures its per-column
        response but returns raw pixels, so without a reference -- acquired by
        :meth:`calibrate_shading`, once per session, as the vendor does at
        power-on -- the stripes are simply left in.
        """
        if infrared and not supports_infrared(film):
            # Refused rather than warned. This costs the ~212 s infrared floor
            # and returns a plane holding the photograph instead of the dust --
            # measured on a B&W frame here at +0.97 correlation with green --
            # and it drags blue's metering along with it, which is what put 34%
            # of that scan's blue channel at the rail.
            #
            # Chromogenic black and white (XP2, BW400CN, anything C-41) is
            # dye-based and does clean properly. It is not FILM_BW: it is a
            # colour negative that looks grey, and belongs under
            # FILM_NEGATIVE, which is where the exception lives.
            raise ValueError(
                f"infrared is blind to {film}: its "
                + ("grain" if film == FILM_BW else "cyan layer")
                + " absorbs infrared, so the pass would spend its ~212 s floor "
                "and hand back the picture rather than the dust. Scan it RGB. "
                "(Chromogenic C-41 black and white does clean properly -- scan "
                "that as a negative.)"
            )

        if auto_exposure:
            # Probe in RGB whatever the scan will be, in at most two rounds --
            # the vendor's own sequence. Scans otherwise run at the scanner's
            # defaults, which land around 3-10% of full scale, most of the
            # 16-bit range unused.
            #
            # Blue does behave differently with infrared enabled, coming back
            # about 5x brighter at the same exposure (BLUE_RGBI_HEADROOM). That
            # is handled by metering blue lower when an RGBI scan follows, not
            # by probing in RGBI: an infrared probe costs its own ~212 s floor
            # per round.
            self._log(f"auto-exposure: probing in RGB (scan is "
                      f"{'RGBI' if infrared else 'RGB'})")
            exposure_scale = self.auto_exposure(
                target=exposure_target, infrared=infrared, film=film
            )
            self._log(
                f"auto-exposure: {[round(v, 3) for v in exposure_scale]}"
            )

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

        settings = self.get_gain_offset().scaled(exposure_scale)
        self.set_gain_offset(settings, infrared=infrared)
        if not _is_unity(exposure_scale):
            shown = (
                f"x{exposure_scale:g}"
                if isinstance(exposure_scale, (int, float))
                else "x" + "/".join(f"{v:g}" for v in exposure_scale)
            )
            self._log(f"exposure scaled {shown}: {settings.describe()}")

        passes = ONE_PASS_RGBI if infrared else ONE_PASS_COLOR
        channels = 4 if infrared else 3
        self.set_mode(
            resolution=resolution,
            passes=passes,
            depth=depth,
            color_format=FORMAT_INDEX,
            skip_shading=skip_shading,
            byte14=byte14,
        )
        self.test_unit_ready()

        self.slide(SLIDE_INIT, param=slide_init_param)
        self.wait_ready()

        started = time.monotonic()
        self.start_scan()
        try:
            self.wait_ready()
            # Read per pass, not once: the mask marks which CCD pixels *this*
            # pass samples, which is what keeps the shading columns aligned at
            # reduced resolutions.
            ccd_mask = self.get_ccd_mask(CCD_MASK_SIZE)
            # Kept for the caller: this pass's mask, not the calibration
            # pass's. They differ -- the mask says which CCD pixels *this*
            # resolution sampled -- so correcting a saved scan later needs
            # this one.
            self._ccd_mask = ccd_mask
            params = self.get_parameters()
            self._log(
                f"params width={params.width} lines={params.lines} "
                f"bpl={params.bytes_per_line}"
            )
            image = self.read_planes(params, channels, keep_raw=keep_raw)
        except BaseException:
            # Deliberately no STOP SCAN. The vendor software never sends it,
            # and issuing it here reliably leaves the scanner unresponsive to
            # the next session, needing a power cycle. Settling the bridge is
            # enough to leave things usable.
            self._scanning = False
            raise
        else:
            self.finish_scan()

        # After the scan has settled, never inside it: the vendor polls
        # READ_STATE for several seconds once the last line is read and only
        # then moves the film.
        if advance:
            self.advance()

        shading_report = None
        # Why a pass came back raw, when it was asked to be corrected. Kept
        # separately from `shading_report`, which library.save reads as "a
        # correction happened" -- and separately from `shading=False`, which is
        # a request for raw pixels and not a shortfall at all.
        #
        # Six 3600 dpi RGBI frames were filed uncorrectable on 2026-09-09
        # because a restarted session had no reference and every scan simply
        # logged one line and carried on. Half an hour of scanning, and nothing
        # in the entries said the correction had been wanted.
        shading_skipped = None
        if (
            shading
            and self._shading is not None
            and params.width > self._shading.pixels_per_line
        ):
            # The mask holds one byte per calibration column, so a pass wider
            # than the calibration cannot be mapped -- at 7200 dpi the image is
            # 10344 columns against 5172 in the reference. Correcting half the
            # frame is worse than correcting none.
            shading_skipped = (
                f"this pass is {params.width} columns but the reference covers "
                f"{self._shading.pixels_per_line}"
            )
            self._log(f"shading skipped: {shading_skipped}; returning raw pixels")
        elif shading and self._shading is not None:
            image, shading_report = apply_shading(image, self._shading, ccd_mask)
            # Name the channel, not just the count. Clipping here is almost
            # always one channel -- blue -- and a total says nothing about
            # which exposure to lower.
            per = shading_report.get("clipped_per_channel") or []
            worst = ""
            if shading_report["clipped"] and per:
                c = max(range(len(per)), key=lambda i: per[i])
                share = per[c] / shading_report["clipped"]
                worst = f", worst {CHANNEL_ORDER[c]} at {share:.0%} of them"
            self._log(
                f"shading corrected: {shading_report['columns']}/"
                f"{shading_report['width']} columns"
                + (f", {shading_report['clipped']} samples clipped{worst}"
                   if shading_report["clipped"] else "")
            )
        elif shading:
            shading_skipped = "no shading reference in this session"
            self._log(
                "*** NO SHADING REFERENCE: these pixels are RAW and can never "
                "be corrected. Run calibrate_shading() once per session -- the "
                "scanner does not correct its own output ***"
            )

        meta = {
            "resolution_dpi": resolution,
            "channels": channels,
            "film": film,
            "protocol_revision": PROTOCOL_REVISION,
            "shading": shading_report,
            "shading_skipped": shading_skipped,
            "channel_order": list(CHANNEL_ORDER[:channels]),
            "depth": 16 if depth == DEPTH_16 else 8,
            "frame": list(frame),
            "width": int(params.width),
            "height": int(params.lines),
            "bytes_per_line": int(params.bytes_per_line),
            # Read by get_parameters() and otherwise discarded. Recorded
            # because two passes at an identical frame and dpi have correlated
            # at lag -16 columns rather than lag 0, and these are the prime
            # suspect -- a suspicion that cannot be tested without the numbers.
            "filter_offsets": [int(params.filter_offset1), int(params.filter_offset2)],
            "exposure": settings.exposure,
            "gain": settings.gain,
            "offset": settings.offset,
            "exposure_scale": list(exposure_scale)
            if not isinstance(exposure_scale, (int, float))
            else exposure_scale,
            # Whether the exposure above was *metered* or *asked for*. The
            # library's signature() excludes a metered exposure deliberately --
            # it is an outcome that lands slightly differently every run without
            # changing what was requested. A commanded exposure is the opposite:
            # it is the request, and it is the only thing distinguishing the
            # members of a bracket, which are otherwise the same frame at the
            # same dpi, depth and channel count.
            "exposure_metered": bool(auto_exposure),
            "duration_s": round(time.monotonic() - started, 1),
        }
        # Only for a scan that did its own metering. The probe passes inside
        # auto_exposure() are scans too, and attaching this to them would file
        # the *previous* frame's metering against them.
        if auto_exposure and self.last_metering is not None:
            meta["metering"] = self.last_metering
        self._debug_capture(image, meta)
        return image, meta

    def _correct_registration(
        self, index: int, image: np.ndarray, prescan_resolution: int, dry_run: bool
    ) -> dict[str, Any]:
        """Measure the frame's registration and nudge it back, once.

        Returns what it did. A re-prescan follows any real move, because that is
        the only way to tell a correction that landed from one that backlash
        swallowed -- the failure that would otherwise look identical to success.
        It is also what the vendor does: in setup CyberView moves, scans, moves,
        scans, each scan checking the last move.
        """
        # The caller has already looked at this frame; measuring its prescan
        # again would cost 12 s to learn nothing.
        before, why = registration_error_mm(image)
        out: dict[str, Any] = {"before_mm": before, "reason": why,
                               "moved": False, "prescan": None}

        if before is None:
            self._log(f"frame {index}: not correcting -- {why}")
            return out
        if abs(before) < self.CORRECTION_DEADBAND_MM:
            self._log(
                f"frame {index}: registration {before:+.3f} mm, inside the "
                f"{self.CORRECTION_DEADBAND_MM} mm deadband -- leaving it"
            )
            return out

        # A gap on the left means the frame sits too far towards +x, so it has
        # to come back: the opposite sign to the error.
        want = -before
        if dry_run:
            param = self.param_for_mm(want)
            out["would_send"] = {
                "action": 0x00 if want >= 0 else 0x01, "param": param,
                "asked_mm": round(
                    (self.STEP_MM * param + self.OVERHEAD_MM)
                    * (1 if want >= 0 else -1), 3),
            }
            self._log(
                f"frame {index}: registration {before:+.3f} mm; would send "
                f"{out['would_send']['action']:#04x} {param:#04x} 00 04 "
                f"({out['would_send']['asked_mm']:+.3f} mm) -- dry run"
            )
            return out

        out.update(self.nudge(want))
        out["moved"] = True
        time.sleep(0.4)

        image, _ = self.prescan(resolution=prescan_resolution)
        after, why_after = registration_error_mm(image)
        out["after_mm"] = after
        out["after_reason"] = why_after
        out["prescan"] = image
        if after is None:
            self._log(f"frame {index}: after nudging, {why_after}")
        else:
            improved = abs(after) < abs(before)
            out["improved"] = bool(improved)
            self._log(
                f"frame {index}: registration {before:+.3f} -> {after:+.3f} mm "
                f"({'better' if improved else 'NO BETTER -- backlash?'})"
            )
        return out

    # -- sub-frame positioning ---------------------------------------------

    #: The calibrated law for SLIDE actions 0x00 / 0x01, fitted over both
    #: directions: distance = STEP_MM x param + OVERHEAD_MM. Worst residual
    #: 0.0185 mm across ten points; see docs/protocol.md section 11.
    STEP_MM = 0.1057
    OVERHEAD_MM = 0.1662

    #: Below this the loop leaves the frame alone. Roughly half the smallest
    #: move the hardware can make (param 1 = 0.27 mm), so it never asks for a
    #: correction it cannot deliver, and never chatters at measurement noise.
    CORRECTION_DEADBAND_MM = 0.15

    #: A correction larger than this is refused. The aperture allows 0.49 mm of
    #: registration error, so anything beyond about a millimetre means the
    #: measurement is wrong rather than the film being far out.
    MAX_CORRECTION_PARAM = 8

    def param_for_mm(self, millimetres: float) -> int:
        """The `param` byte that moves the film this far. See :meth:`nudge`."""
        n = round((abs(millimetres) - self.OVERHEAD_MM) / self.STEP_MM)
        return max(1, min(self.MAX_CORRECTION_PARAM, n))

    def nudge(self, millimetres: float) -> dict[str, Any]:
        """Move the film a sub-frame distance, without touching the frame count.

        `SLIDE 00 <param> 00 04` forward, `01 <param> 00 04` back. Calibrated in
        docs/protocol.md section 11; `value` has no measurable effect and is left
        at the 0x04 the vendor pairs with small params.

        **Backlash matters.** Two to three steps are swallowed after a direction
        change, so a small correction that reverses direction may not move the
        film at all. The caller is expected to re-measure rather than assume.
        """
        param = self.param_for_mm(millimetres)
        forward = millimetres >= 0
        asked = self.STEP_MM * param + self.OVERHEAD_MM
        self._log(
            f"nudge {'+' if forward else '-'}{asked:.3f} mm "
            f"(param {param}) for a {millimetres:+.3f} mm error"
        )
        self.slide(0x00 if forward else 0x01, param=param, value=0x04)
        return {"param": param, "forward": forward,
                "asked_mm": round(asked if forward else -asked, 3)}

    # -- rolls -------------------------------------------------------------

    def scan_roll(
        self,
        frames: int | None = None,
        resolution: int = 1800,
        infrared: bool = True,
        film: str = FILM_NEGATIVE,
        meter: str = METER_EACH,
        exposure_target: float = EXPOSURE_TARGET,
        prescan_resolution: int = 300,
        blank_contrast: float = BLANK_CONTRAST,
        drift_warning: int = 240,
        skip: int = 0,
        only: tuple[int, ...] | None = None,
        keep_raw: bool = True,
        max_failures: int = 3,
        should_stop: Callable[[], bool] | None = None,
        scan_frame: tuple[int, int, int, int] | None = None,
        dry_run: bool = False,
        correct: bool = False,
        correct_dry_run: bool = False,
    ) -> Iterator[RollFrame]:
        """Walk a roll or strip, yielding one :class:`RollFrame` per picture.

        A generator, not a list. At 3600 dpi a frame is ~142 MB of pixels plus
        its raw bytes, so the caller has to write each one out and let it go;
        collecting a roll in memory is not possible. It also means the caller
        can stop mid-roll, and that a frame reaches disk the moment it exists
        rather than at the end of a three-hour run.

        The first picture is scanned **before** any advance -- the film is
        already positioned at it when the roll starts. ``skip`` advances that
        many times first, which is how a part-scanned roll is resumed.

        Every frame is scanned at the full transport window. Cropping is a
        host-side decision that can be revisited; a window detected wrongly
        during an unattended run cannot.

        ``drift_warning`` is how much narrower than a whole frame a picture may
        measure before the log says the film has drifted -- 240 units is 0.85 mm,
        comfortably past detection jitter and well short of losing anything.

        Stops on whichever comes first: ``frames`` pictures, a prescan with no
        picture in it (:func:`frame_contrast` below ``blank_contrast``), an
        advance that does not move the film, or ``max_failures`` consecutive
        failures. A single failed frame does not end the roll -- it is yielded
        with ``error`` set and the roll goes on.

        ``meter`` is one of:

        ``"each"``
            re-meter before every frame, which is what CyberView does -- its
            gain/offset writes differ frame to frame.
        ``"once"``
            meter on the first picture and hold those scales for the roll. The
            frames stay comparable to each other, which matters when the whole
            roll is inverted with one set of parameters, and it saves ~45 s a
            frame.
        ``"none"``
            scan at whatever the device holds.

        ``only`` is the frame numbers worth scanning, in the same numbering the
        yielded :class:`RollFrame` carries. Everything else is advanced past
        without being prescanned or scanned, so a frame nobody chose costs its
        ~7 s advance rather than 13 s surveyed or six minutes scanned, and the
        roll ends after the last chosen frame instead of walking out the strip.
        It is meant to follow a ``dry_run`` survey, which is where the numbers
        come from; ``None`` scans every frame, and an empty selection scans
        none. Metering, registration, failure counting and the manifest are
        untouched by it.
        """
        if meter not in METER_MODES:
            raise ValueError(
                f"unknown meter mode {meter!r}; expected one of {METER_MODES}"
            )
        # Up front, not on the first frame: a bad film type raises from inside
        # metering, and a roll would otherwise spend three failures discovering
        # a typo it could have refused in the first second.
        locks_white_balance(film)
        # Same reasoning, and it costs far more here: a roll calibrates for
        # three or four minutes before the first frame, so an infrared setting
        # the film is blind to would be discovered after the expensive part.
        if infrared and not supports_infrared(film):
            raise ValueError(
                f"infrared is blind to {film}: its "
                + ("grain" if film == FILM_BW else "cyan layer")
                + " absorbs infrared, so every frame of this roll would spend "
                "its ~212 s floor and hand back the picture rather than the "
                "dust. Scan it RGB."
            )

        window = scan_frame or FULL_FRAME

        # The reference every frame is metered from. On this device READ
        # GAIN/OFFSET returns a fixed reference rather than a readback, so this
        # is the same value every frame anyway -- but reading it once and
        # writing it back explicitly is what makes that assumption checkable
        # instead of load-bearing and invisible.
        baseline = self.get_gain_offset()
        self._log(f"roll baseline exposure: {baseline.describe()}")

        scales: float | Sequence[float] = 1.0
        metered = False
        failures = 0
        index = 0

        # Past the last chosen frame there is nothing left to do, so the roll
        # ends there rather than advancing through the rest of the strip
        # looking for pictures the operator has already said no to.
        wanted = frozenset(only) if only is not None else None
        if wanted is not None and not wanted:
            self._log("no frames were chosen, so there is nothing to scan")
            return
        last_wanted = max(wanted) if wanted else None

        def finished(index: int) -> bool:
            if frames is not None and index >= skip + frames:
                return True
            return last_wanted is not None and index > last_wanted

        def keep_going() -> bool:
            """Advance to the next frame, unless stop was asked for first."""
            # Checked here, immediately before the film moves, because this is
            # the last instant at which stopping is free. A caller that only
            # checks after consuming a frame has already let this advance and
            # the prescan after it happen.
            if should_stop is not None and should_stop():
                self._log("stopping before the next advance, as asked")
                return False
            return self.advance() is not None

        for _ in range(skip):
            position = self.advance()
            if position is None:
                self._log("nothing to skip to: the transport did not move")
                return
            index += 1

        while frames is None or index < skip + frames:
            # The frame has not begun here, so this is where stopping is
            # cheapest -- and it covers the advance, which takes 2-7 seconds
            # during which a stop would otherwise not be looked at again until
            # the frame after it had been prescanned. At 3600 dpi RGBI that is
            # six minutes and 250 MB spent after the operator said stop.
            if should_stop is not None and should_stop():
                self._log("stopping before the next frame, as asked")
                return

            if wanted is not None and index not in wanted:
                # Surveyed and not chosen. The prescan that would decide this
                # has already been taken and looked at, so taking another one
                # here would only spend the operator's time re-asking.
                self._log(f"frame {index}: not chosen, advancing past it")
                index += 1
                if finished(index) or not keep_going():
                    return
                continue

            started = time.monotonic()
            prescan_image = None
            marks: dict[str, Any] = {}
            position = self.position()

            try:
                prescan_image, _ = self.prescan(
                    resolution=prescan_resolution, keep_raw=keep_raw
                )
                contrast = frame_contrast(prescan_image)
                marks = dict(registration(prescan_image, window))
                marks["contrast"] = round(contrast, 4)
                self._log(
                    f"frame {index}: contrast {contrast:.3f}, "
                    f"picture x{marks['x0']}..{marks['x1']}, "
                    f"offset {marks['offset_mm']:+.2f} mm, "
                    f"short by {marks['shortfall_mm']:.2f} mm"
                )

                if contrast < blank_contrast:
                    self._log(
                        f"frame {index}: nothing in the window "
                        f"(contrast {contrast:.3f} < {blank_contrast}); "
                        "end of film"
                    )
                    return

                if correct or correct_dry_run:
                    fix = self._correct_registration(
                        index, prescan_image, prescan_resolution, correct_dry_run
                    )
                    marks["correction"] = fix
                    if fix.get("prescan") is not None:
                        prescan_image = fix.pop("prescan")
                        marks.update(
                            {k: v for k, v in registration(
                                prescan_image, window).items()}
                        )
                        marks["contrast"] = round(
                            frame_contrast(prescan_image), 4)

                if marks["shortfall"] > drift_warning:
                    # Reported, not corrected by this branch. Sub-frame movement
                    # exists and is calibrated -- see `correct` above and
                    # docs/protocol.md section 11 -- but a shortfall this large
                    # is picture hanging outside the aperture, which no amount
                    # of nudging brings back.
                    self._log(
                        f"frame {index}: picture is {marks['shortfall_mm']:.2f} mm "
                        "narrower than a whole frame -- the film has drifted and "
                        "part of it is outside the aperture"
                    )

                if dry_run:
                    yield RollFrame(index, position, None, {}, prescan_image, marks)
                else:
                    if meter != METER_NONE and not (meter == METER_ONCE and metered):
                        # `infrared` here says the scan that follows is RGBI;
                        # it does not make the probe infrared. auto_exposure
                        # always probes in RGB. What the flag buys is blue's
                        # headroom: blue comes back 2-3.7x brighter in RGBI than
                        # in RGB at the same exposure, so a blue metered to fill
                        # the range on an RGB probe clips in the scan. Passing
                        # False here cost exactly that -- one roll metered blue
                        # to 10.07x, which on a 6506 base pins the 16-bit timer
                        # at its 65535 ceiling before the RGBI gain is applied.
                        self.set_gain_offset(baseline, infrared=infrared)
                        scales = self.auto_exposure(
                            target=exposure_target, infrared=infrared, film=film
                        )
                        metered = True

                    # Correct whether or not the device echoes back what was
                    # written. It does not: across 17 READ GAIN/OFFSET
                    # responses in the strip capture only bytes 66-68 -- the
                    # live R/G/B offsets -- ever change, and the exposure fields
                    # hold 9604/6506/6506/7745 however different the value just
                    # written. So the read is a fixed reference, `scaled()`
                    # always yields base x scale, and exposure cannot compound
                    # frame to frame. One write costs nothing and keeps the roll
                    # right if that ever stops being true.
                    self.set_gain_offset(baseline, infrared=infrared)
                    image, meta = self.scan(
                        resolution=resolution,
                        infrared=infrared,
                        frame=window,
                        exposure_scale=scales,
                        film=film,
                        keep_raw=keep_raw,
                    )
                    meta["roll_index"] = index
                    meta["roll_position"] = position
                    meta["registration"] = marks
                    yield RollFrame(index, position, image, meta, prescan_image, marks)
                failures = 0
            # UsbError covers CheckCondition and NoDataYet. ValueError is in
            # here because a roll runs for hours unattended: one frame that
            # decodes to an unexpected shape should cost that frame, not the
            # thirty after it.
            except (UsbError, ScanReadError, CalibrationRequired,
                    TimeoutError, ValueError) as exc:
                failures += 1
                self._log(f"frame {index} failed ({failures}/{max_failures}): {exc}")
                yield RollFrame(
                    index, position, None, {}, prescan_image, marks, error=str(exc)
                )
                if failures >= max_failures:
                    self._log(f"giving up after {failures} consecutive failures")
                    return

            self._log(f"frame {index} took {time.monotonic() - started:.0f}s")
            index += 1
            if finished(index):
                break
            if not keep_going():
                return
