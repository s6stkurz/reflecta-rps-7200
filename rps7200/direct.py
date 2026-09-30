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

import json
import os
import functools
import tempfile
import shutil
import threading
import time
import weakref
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Concatenate, ParamSpec, TypeVar

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
    MAX_CORRECTION_MM,
    StripWalk,
    frame_offset_mm,
    CALIBRATION_FRAME,
    CCD_MASK_SIZE,
    CLEAR_RATIO,
    FILM_LEVEL,
    FULL_FRAME,
    MIN_INSET_X,
    MIN_INSET_Y,
    NOMINAL_FRAME_WIDTH,
    film_bounds,
    metering_region,
    metering_slice,
    frame_contrast,
    gap_edges,
    registration,
    registration_error_mm,
    HOLD_TOLERANCE_MM,
    hold_plan,
    measure_shift_mm,
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
    MM_PER_COMMAND,
    MM_PER_INCH,
    MM_PER_UNIT,
    say_units,
    units,
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
    DeviceSuspect,
    EndOfData,
    Inquiry,
    NoMediaLoaded,
    ScanParameters,
    ScanReadError,
    ShadingUnavailable,
    Sense,
    StoppedBeforePass,
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
from .direction import ReadDirection, read_direction
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
    "SHADING_SKIPPED_EXPLICIT",
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
    "ShadingUnavailable",
    "Sense",
    "StoppedBeforePass",
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
    "metering_region",
    "metering_slice",
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
#: sooner. 0.80 takes the second: it is :data:`CLIP_START`, the knee above
#: which a sample is no longer trusted to be linear.
#:
#: It replaced 0.70, the most conservative figure of any driver read for
#: comparison -- pieusb 0.85, nkscan 0.97 -- which left about a fifth of a stop
#: unused for no measured reason.
EXPOSURE_TARGET = 0.80

#: A 16-bit sample's full scale, in DN.
FULL_SCALE = 65535.0
#: Where this sensor's response stops being trusted as linear: 0.80 of full
#: scale. A CCD compresses before it saturates -- departure from linear grows
#: to 1.5-1.9% in the 75-95% band on the nine-pass 3600 dpi ladder -- so a
#: sample above this is a measurement of the knee, not of the film. Metering
#: aims no higher (:data:`EXPOSURE_TARGET`). The archived bracket merge
#: (docs/multi-exposure/) stopped weighting samples here, which is where the
#: number came from.
CLIP_START = 0.80 * FULL_SCALE


#: What :meth:`DirectScanner.scan` records in ``meta["shading_skipped"]`` when
#: the caller asked for raw pixels outright, by passing ``shading=False``.
#:
#: **It is the only reason this driver still writes.** A pass that *wanted*
#: correction and could not get one raises :class:`ShadingUnavailable` rather
#: than returning raw pixels quietly, so on anything scanned today a skipped
#: correction is always a choice.
#:
#: Entries filed before that was true carry other reasons -- four in this
#: library, reading "no shading reference in this session" or "this pass is
#: 10344 columns but the reference covers 5172" -- and those were a shortfall,
#: not a choice. :func:`rps7200.library.corrected` compares against this
#: constant to tell the two apart, and `verify` already draws the same line.
#: They must not drift, which is why the string lives here rather than being
#: written out in both places.
SHADING_SKIPPED_EXPLICIT = "shading=False (explicit)"


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

    index: int                          # 0-based, its place on the strip
    position: int | None                # what READ_STATE said the transport held
    image: np.ndarray | None
    meta: dict[str, Any]
    prescan: np.ndarray | None
    registration: dict[str, Any]
    error: str | None = None
    # The same two pictures before flat-fielding, for the library, which stores
    # raw pixels and recomputes the correction on the way out. Carried on the
    # frame rather than read off the scanner afterwards: a frame's prescan is
    # taken several passes before its image, so anything like `last_raw` would
    # hand the prescan the image's pixels.
    raw_image: np.ndarray | None = None
    raw_prescan: np.ndarray | None = None
    # The prescan pass's own meta. `meta` above belongs to the frame scan, and
    # a dry run has no frame scan at all, so without this the survey's entries
    # had nothing to describe themselves with.
    prescan_meta: dict[str, Any] = field(default_factory=dict)

    #: The frame's prescan as it arrived, present only when a correction
    #: actually moved the film. Carried rather than re-read: by the time
    #: anybody asks, the corrector's own last pass has already rebound
    #: `last_pixels_raw`, so the scanner no longer holds the bytes this
    #: picture was made from.
    prescan_before: np.ndarray | None = None

    #: The prescan pass's own `capture_record` -- its bytes, its CCD mask and
    #: the reference in force -- taken the moment the pass was, for the same
    #: reason as `raw_prescan`. Read from the scanner when the frame is
    #: yielded, it is the frame scan's on a real roll, and on a walk whatever
    #: pass ran last: a hold's verification prescan that raised after its
    #: read left its bytes there, and the walk filed them beside the prescan
    #: it had not replaced -- the same shape, and a different photograph.
    prescan_capture: dict[str, Any] | None = None
    #: The same three for `prescan_before`, so the picture a hold or an aim
    #: replaced is filed raw as well rather than only drawn as a TIFF.
    raw_prescan_before: np.ndarray | None = None
    prescan_before_meta: dict[str, Any] = field(default_factory=dict)
    prescan_before_capture: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.image is not None


def raw_bytes_disagree(shape: tuple[int, ...], layout: dict[str, Any] | None,
                       meta: dict[str, Any] | None = None) -> dict[str, tuple]:
    """Where raw bytes laid out like this cannot be the pass with this shape.

    Empty when they can. Every writer that files bytes beside pixels asks this
    first: bytes of another pass decode to a different photograph, which is
    the one failure the library exists to make impossible. Here rather than in
    `session`, which re-exports it, so debug filing asks the same question.
    """
    layout = dict(layout or {})
    actual = {
        "lines": shape[0],
        "width": shape[1],
        "channels": shape[2] if len(shape) > 2 else 1,
    }
    # The rows the bytes can decode to: what arrived, not what GET PARAMETERS
    # declared, less what the 7200 dpi realignment trimmed. Judged against the
    # declared count, every pass that ended early -- the one whose bytes
    # matter most -- and every 7200 dpi pass looked like another pass's bytes,
    # and was filed without them.
    received = layout.get("lines_received")
    channels = layout.get("channels")
    if received is not None and channels:
        layout["lines"] = int(received) // int(channels)
    if layout.get("lines") is not None:
        layout["lines"] = (int(layout["lines"])
                           - int((meta or {}).get("stagger_realigned") or 0))
    # Only fields the layout actually declares are judged; an absent one says
    # nothing, and dropping good bytes over it would be its own bug.
    return {
        k: (layout[k], actual[k])
        for k in actual
        if layout.get(k) is not None and layout[k] != actual[k]
    }


def _prescans_kept(capture: dict[str, Any] | None,
                   before: dict[str, Any]) -> dict[str, Any]:
    """A frame's prescan records, as `RollFrame` carries them.

    A function rather than a method: the demo runs `DirectScanner.scan_roll`
    with itself as the scanner, and it is no subclass.
    """
    return {"prescan_capture": capture,
            "raw_prescan_before": before.get("raw"),
            "prescan_before_meta": dict(before.get("meta") or {}),
            "prescan_before_capture": before.get("capture")}


@dataclass(frozen=True)
class _Aim:
    """A target for :meth:`DirectScanner._hold_to_approved` that no operator set.

    That loop reads `.offset_mm` and `.reference` and nothing else, so the
    ensemble can hand it a measured target through the same door. Declared here
    rather than reusing `session.Approved`, because `session` imports `direct`
    and the arrow cannot go both ways.
    """

    offset_mm: float
    reference: Any


class _CommandLog:
    """The transport, with the commands of the pass in flight written down.

    Every command a pass sends -- the frame, MODE SELECT, gain and offset,
    SLIDE, START SCAN, the small READs of parameters and mask -- is what
    defines it, and none of it survived the pass: byte 14, the SLIDE INIT
    parameter, the GET PARAMETERS answer were all spent and forgotten, so an
    entry could not say how it was taken. Recorded only while `record` is a
    list (`scan` sets it for one pass). Image-data READs are counted, not
    listed: a 7200 dpi pass makes thousands, and the bytes they returned are
    `raw.bin.gz` already.
    """

    #: A response this long or longer is image data, counted rather than kept.
    BULK = 256

    def __init__(self, inner: Any):
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "record", None)
        object.__setattr__(self, "bulk", None)
        object.__setattr__(self, "_t0", 0.0)

    def start(self) -> None:
        object.__setattr__(self, "record", [])
        object.__setattr__(self, "bulk", {"reads": 0, "bytes": 0})
        object.__setattr__(self, "_t0", time.monotonic())

    def stop(self) -> dict[str, Any] | None:
        if self.record is None:
            return None
        out = {"sent": self.record, "image_reads": self.bulk}
        object.__setattr__(self, "record", None)
        object.__setattr__(self, "bulk", None)
        return out

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._inner, name, value)

    def command(self, command: bytes, *args: Any, **kwargs: Any) -> Any:
        if self.record is None:
            return self._inner.command(command, *args, **kwargs)
        data = kwargs.get("data", args[0] if args else None)
        entry: dict[str, Any] = {
            "t": round(time.monotonic() - self._t0, 3),
            "cdb": bytes(command).hex(),
        }
        if data:
            entry["out"] = bytes(data).hex()
        try:
            reply = self._inner.command(command, *args, **kwargs)
        except NoDataYet:
            if command[0] != SCSI_READ:
                entry["refused"] = NoDataYet.__name__
                self.record.append(entry)
                raise
            # The scanner has not scanned this far yet, and a read polls
            # every 20 ms until it has: counted, as the reads that returned
            # data are, rather than listed. Listed, a long pass buried the
            # refusals worth reading under thousands of identical waits.
            self.bulk["waits"] = self.bulk.get("waits", 0) + 1
            raise
        except Exception as exc:
            # The message too, not the class alone: "OSError" cannot tell the
            # known stall from any other, and the sense bytes or the libusb
            # code that could are in the message.
            entry["refused"] = f"{type(exc).__name__}: {exc}"
            self.record.append(entry)
            raise
        if reply and len(reply) >= self.BULK and command[0] == SCSI_READ:
            self.bulk["reads"] += 1
            self.bulk["bytes"] += len(reply)
            return reply
        if reply:
            entry["in"] = bytes(reply).hex()
        self.record.append(entry)
        return reply


def debug_from_env() -> bool:
    """Whether `RPS7200_DEBUG` turns debug filing on: 1, true, yes or on.

    The one reading of it, for `DirectScanner` and for every probe that
    refuses to run without it. The probes guarded on the variable being set
    at all, so `RPS7200_DEBUG=0` -- or 2, or y -- passed a guard whose whole
    purpose is to refuse a run that files nothing, and the run then filed
    nothing.
    """
    return (os.environ.get(DirectScanner.DEBUG_ENV, "").strip().lower()
            in {"1", "true", "yes", "on"})


def file_spool(folder: str | Path, root: str | Path, *,
               claimed: bool = False,
               say: Callable[[str], None] = print) -> list[Path]:
    """File a debug spool left behind, from the record beside each pass.

    One is left when a filing failed -- a full disk, a root that could not be
    made -- or when a process died before close(). Its comment always said it
    "can be filed later by hand", and nothing could. Run this with no window
    or tool holding the scanner: a session files its own spool as it closes,
    and this would file the same passes again.

    A pass a caller claimed (`DirectScanner.debug_claim`) was probably filed
    by that caller; its sidecar says so, and it is left unless ``claimed``.
    A spool written before sidecars named their files is read by its numbers:
    each pass takes the latest `NNN-shading.npz` at or before its own. Each
    pass filed is removed from the spool, and the spool once only references
    are left in it. Returns the entries written.
    """
    folder = Path(folder)
    written: list[Path] = []
    references: dict[str, ShadingReference | None] = {}

    def reference(path: Path | None) -> ShadingReference | None:
        if path is None or not path.exists():
            return None
        if path.name not in references:
            references[path.name] = ShadingReference.load(path)
        return references[path.name]

    def latest_reference(number: int) -> Path | None:
        found = [p for p in folder.glob("*-shading.npz")
                 if p.name.split("-")[0].isdigit()
                 and int(p.name.split("-")[0]) <= number]
        if not found:
            return None
        return max(found, key=lambda p: int(p.name.split("-")[0]))

    for side in sorted(folder.glob("*-meta.json")):
        prefix = side.name[: -len("-meta.json")]
        try:
            record = json.loads(side.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            say(f"{side.name}: cannot be read ({exc}); left as it is")
            continue
        if record.get("claimed") and not claimed:
            say(f"{prefix}: claimed by the caller that took it, which files "
                "its own; left as it is (--claimed files it too)")
            continue
        files = record.get("files")
        if files is None:                                 # written before
            files = {"image": f"{prefix}-image.npy",
                     "raw": f"{prefix}-raw.bin",
                     "ccd_mask": f"{prefix}-ccd_mask.bin"}
            shading = (latest_reference(int(prefix)) if prefix.isdigit()
                       else None)
        else:
            shading = folder / files["shading"] if files.get("shading") else None
        paths = {key: folder / name for key, name in files.items()
                 if key != "shading" and name}
        image = paths.get("image")
        if image is None or not image.exists():
            say(f"{prefix}: its pixels are not here; left as it is")
            continue
        raw = paths.get("raw")
        mask = paths.get("ccd_mask")
        item = {
            "image_path": image,
            "meta": record.get("meta") or {},
            "raw_path": raw if raw is not None and raw.exists() else None,
            "raw_layout": record.get("raw_layout"),
            "reference": reference(shading),
            "ccd_mask": (mask.read_bytes()
                         if mask is not None and mask.exists() else None),
            "tags": list(record.get("tags") or ["debug"]) + ["from-spool"],
            "notes": record.get("notes") or "filed from a spool left behind",
            "captured": record.get("captured"),
        }
        try:
            entry = DirectScanner._file_spooled(item, root)
        except Exception as exc:                          # noqa: BLE001
            say(f"{prefix}: could not be filed ({exc}); left as it is")
            continue
        written.append(entry)
        say(f"{prefix} -> {entry}")
        for path in (image, raw, mask, side):
            if path is not None:
                path.unlink(missing_ok=True)
    if folder.exists() and not [p for p in folder.iterdir()
                                if not p.name.endswith("-shading.npz")]:
        shutil.rmtree(folder, ignore_errors=True)
    return written


_P = ParamSpec("_P")
_R = TypeVar("_R")


def _keeps_what_a_failed_pass_left(
    method: Callable[Concatenate[DirectScanner, _P], _R],
) -> Callable[Concatenate[DirectScanner, _P], _R]:
    """A pass, with what it leaves behind when it raises kept rather than lost.

    Its command log, stopped: it went on recording every later command into a
    list the next pass then threw away, so the commands that led to a failure
    were the ones never kept. And, where every line of it was read before the
    failure -- a decode that refused the bytes, a realignment, a correction --
    the pass itself, filed tagged ``failed`` (`_after_a_failed_pass`). Those
    bytes were dropped, debug filing included, which is exactly the case they
    are kept for.

    A wrapper, so every way out of the pass is covered without the pass
    itself being re-indented around a handler.
    """
    @functools.wraps(method)
    def run(self: DirectScanner, *args: _P.args, **kwargs: _P.kwargs) -> _R:
        try:
            return method(self, *args, **kwargs)
        except BaseException as exc:
            self._after_a_failed_pass(exc)
            raise

    return run


class DirectScanner:
    """Command-level control of the scanner."""

    #: The last pass's pixels before flat-fielding, and that pass's meta.
    #: Class attributes as well as instance ones, because stand-ins subclass
    #: this without running its `__init__` -- a stand-in that never scans
    #: should read "nothing yet", not raise AttributeError.
    last_pixels_raw: np.ndarray | None = None
    last_scan_meta: dict[str, Any] | None = None
    #: And what `capture_record` hands over, for the same reason: a roll
    #: takes each prescan's record as it is taken, stand-in or not.
    last_raw: bytes | None = None
    last_raw_layout: dict[str, Any] | None = None
    _shading: ShadingReference | None = None
    _ccd_mask: bytes | None = None
    #: What the pass in flight was asked for and, once read, its bytes and
    #: pixels: see `_keeps_what_a_failed_pass_left`. None between passes.
    _in_flight: dict[str, Any] | None = None
    #: The command log of the last pass that raised, kept for a caller that
    #: wants to know what it sent. A pass read in full files its own.
    last_failed_commands: dict[str, Any] | None = None
    #: The lines of the last calibration that raised, and what it was, for
    #: `ensure_shading` to archive tagged failed. None once taken.
    last_failed_calibration: dict[str, Any] | None = None
    #: What the next pass is being taken for, set by the loop that takes it
    #: -- a metering probe, a hold's verification prescan -- and recorded in
    #: its meta as ``pass_role``. Consumed by `scan`, so it describes one pass.
    _pass_role: dict[str, Any] | None = None
    #: The library tag a debug-filed pass gets for its ``pass_role``. The
    #: hold loop takes an aim's verification passes too.
    _ROLE_TAGS = {"metering probe": "probe", "verification prescan": "hold"}
    #: The sub-frame moves sent since the last pass, in the transport's own
    #: terms (`move_record`), for the next pass's meta as ``moves_before``.
    #: Consumed by `scan`, so each move is recorded with the one pass that
    #: first saw where it left the film.
    _moves_since_pass: list[dict[str, Any]] | None = None
    #: The infrared floor: an **untied** pass with infrared on holds the device
    #: this long however few lines were asked for. Measured at 212-227 s across
    #: resolutions; this is the conservative end, which the estimates use.
    #: It guards no read: `UNTIED_INFRARED_IDLE_S` below does, from the top
    #: of the range. Changing this moves no timeout.
    INFRARED_FLOOR_S = 212.0
    #: How long a read waits for data that has not come yet before it gives up.
    READ_IDLE_S = 120.0
    #: The top of the measured range, which the read is held to.
    INFRARED_FLOOR_MAX_S = 227.0
    #: What an untied infrared pass may sit silent for: the 227 s top of its
    #: measured range, and a minute on top. Short of the floor, the read gave
    #: up after 120 s -- the combination that wedged the device once, as a
    #: 60 s timeout. Waiting longer only delays noticing a stall; giving up
    #: early *is* the stall.
    UNTIED_INFRARED_IDLE_S = INFRARED_FLOOR_MAX_S + 60.0

    #: Why this device may still be mid-scan, once a pass or a calibration
    #: stopped part way through its read; None while it is not. Set, it makes
    #: every command that would drive the device raise `DeviceSuspect`. Never
    #: cleared on this object: the recovery is a power cycle and a new session.
    suspect: str | None = None
    #: Where the reference in force came from (`load_shading`,
    #: `calibrate_shading`); recorded with every pass it corrects.
    _shading_origin: dict[str, Any] | None = None

    #: Environment variable that turns automatic filing on without touching
    #: code, so a probe script inherits it rather than having to remember.
    DEBUG_ENV = "RPS7200_DEBUG"
    #: Where debug entries go. Overridable so a test never writes into the real
    #: library -- which it did, once, before this existed.
    DEBUG_ROOT_ENV = "RPS7200_DEBUG_ROOT"
    #: The library the caller files into -- the window's, a tool's
    #: `--library` -- which debug filing then files into too. It went to
    #: RPS7200_DEBUG_ROOT or `./library` whatever the caller used, so with
    #: `--library D:/lib` the frames landed there and the probes and prescans
    #: that explain them in whatever directory the window was started from.
    #: None, and the variable above decides, and then `library.DEFAULT_ROOT`.
    debug_root: str | Path | None = None
    #: The spool beside that library, not in the system's temporary
    #: directory: that is RAM on many Linux machines and the system disk on
    #: Windows, and a spool there ran out long before the library's disk did.
    #: A dot-directory, so no listing of entries and no `verify` takes it
    #: for one.
    DEBUG_SPOOL_DIR = ".spool"
    #: The spool's bookkeeping is touched from two threads: the scanning one
    #: spools and flushes, a caller's writer answers for the passes it
    #: claimed (`debug_claim`). On the class so a stand-in built without
    #: `__init__` has one.
    _debug_lock = threading.Lock()

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
        self.debug = debug_from_env() if debug is None else bool(debug)
        #: Scans waiting to be filed. Only paths and metadata live here; the
        #: pixels are spooled to disk, because a 7200 dpi roll would otherwise
        #: want 19 GB of RAM.
        self._debug_pending: list[dict[str, Any]] = []
        self._debug_spool: Path | None = None
        self._debug_lock = threading.Lock()
        self.verbose = verbose
        # Where a display listens. Both are host-side and optional: nothing the
        # device is sent changes, which is why PROTOCOL_REVISION stays put.
        self.log_hook = log_hook
        self.progress_hook = progress_hook
        self._own_transport = transport is None
        self.t = _CommandLog(transport or Transport(verbose=verbose))
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
        # The last pass's pixels before flat-fielding. Unlike `last_raw` this
        # is set on every pass, so it cannot be a leftover from an earlier one;
        # it is what a caller files in the library, which stores raw.
        self.last_pixels_raw = None
        self.last_scan_meta = None
        # Which way the last pass was read, from its own line tags. Same
        # contract as `last_pixels_raw`: this pass, read it now.
        self.last_read_direction: ReadDirection | None = None
        # The last READ STATE, and whether a calibration has moved the
        # carriage since it was read. Recorded with each pass as evidence of
        # where the carriage was; nothing decides on it (see `scan`).
        self.last_state: State | None = None
        self._calibrated_since_state = False
        # What the last auto_exposure() probe actually measured, filed with the
        # scan by :meth:`scan`. See :meth:`auto_exposure`.
        self.last_metering: dict[str, Any] | None = None

    def _mark_suspect(self, why: str) -> None:
        if self.suspect is None:
            self.suspect = why
            self._log(f"the scanner may still be mid-scan ({why}); nothing more "
                      "will be sent to it that could drive it. Power-cycle it "
                      "and open a new session.")

    @classmethod
    def correctable_at(cls, resolution: int,
                       frame: tuple[int, int, int, int] | None = None) -> bool:
        """Whether a pass at this resolution can be shading-corrected at all.

        The same test `scan` makes before a pass, for a caller that wants to
        refuse before opening the device -- a 7200 dpi request refused after
        a calibration and metering has spent minutes to learn this.
        """
        needed = cls._shading_columns_needed(frame or FULL_FRAME, resolution)
        return needed <= cls.MAX_SHADING_COLUMNS

    @classmethod
    def uncorrectable(cls, resolution: int,
                      frame: tuple[int, int, int, int] | None = None
                      ) -> ShadingUnavailable:
        """The refusal a corrected pass gets where `correctable_at` says no.

        A class method for the reason `uncalibrated` is a static one: the
        demo refuses a 7200 dpi pass with these words rather than a retyped
        copy of them. It used to accept one, resample a stored picture and
        file a roll the scanner would refuse frame by frame.
        """
        needed = cls._shading_columns_needed(frame or FULL_FRAME, resolution)
        return ShadingUnavailable(
            f"a {resolution} dpi pass over this frame is {needed} "
            f"columns, and this scanner's calibration will not produce "
            f"a reference wider than {cls.MAX_SHADING_COLUMNS} at any "
            f"resolution -- so it cannot be corrected at all, and no "
            f"calibration will change that. Scan at 3600 dpi or below, "
            f"or pass shading=False to accept raw pixels deliberately."
        )

    @classmethod
    def read_idle_s(cls, infrared: bool, fast_infrared: bool) -> float:
        """How long this pass's read may go without data before giving up."""
        if infrared and not fast_infrared:
            return max(cls.READ_IDLE_S, cls.UNTIED_INFRARED_IDLE_S)
        return cls.READ_IDLE_S

    @staticmethod
    def infrared_blind(film: str, roll: bool = False) -> ValueError:
        """The refusal an infrared pass gets on film its plane cannot see.

        Static for the reason `uncalibrated` is: the demo refuses with these
        words. It carried a retyped copy that had already lost the C-41
        sentence and compared against a literal "bw".
        """
        cost = ("every frame of this roll would spend its ~212 s floor and "
                "hand back" if roll else
                "the pass would spend its ~212 s floor and hand back")
        return ValueError(
            f"infrared is blind to {film}: its "
            + ("grain" if film == FILM_BW else "cyan layer")
            + f" absorbs infrared, so {cost} the picture rather than the dust. "
            "Scan it RGB. (Chromogenic C-41 black and white does clean "
            "properly -- scan that as a negative.)"
        )

    @staticmethod
    def uncalibrated(reason: str = "no shading reference in this session"
                     ) -> ShadingUnavailable:
        """The refusal a corrected pass gets when no calibration covers it.

        A static method so a stand-in refuses with the same words rather than
        a retyped copy of them.
        """
        return ShadingUnavailable(
            f"{reason}. Calibrate first (the window's Calibrate, "
            f"`ensure_shading`, or the tool without --no-shading), or pass "
            f"shading=False to accept raw pixels deliberately."
        )

    def _refuse_if_suspect(self, what: str) -> None:
        """Refuse to drive a device a pass was abandoned in.

        Status queries -- READ STATE, REQUEST SENSE, TEST UNIT READY, INQUIRY --
        are not refused: they are how anybody finds out what state it is in.
        """
        if self.suspect is not None:
            raise DeviceSuspect(
                f"not starting {what}: {self.suspect}. The scanner may still "
                "be mid-scan; power-cycle it and open a new session.")

    def _log(self, message: str) -> None:
        if self.verbose:
            try:
                print(f"[scan] {message}")
            except (OSError, ValueError):
                # A terminal that has closed answers every write with EIO,
                # and read_planes logs every chunk: raised, a line of progress
                # abandoned the read it was reporting on.
                pass
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
        # Said in every entry this reference corrects: a cached reference
        # belongs to the power-on that measured it, and an entry corrected by
        # one from another day looked exactly like one measured that morning.
        self._shading_origin = {
            "action": "loaded", "path": str(path),
            "file_modified_utc": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(Path(path).stat().st_mtime)),
            "loaded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        # And the calibration it was reduced from, where `save_shading` left
        # word of one: the next calibration overwrites the cache, so a path to
        # it names nothing a month later, and `verify` checks only the
        # archives an entry names. Trusted only while it describes these
        # very bytes -- a sidecar left beside some other file names the
        # wrong calibration, which is worse than naming none.
        link = self._shading_link(Path(path))
        if link is not None:
            self._shading_origin.update(link)
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
        # Beside, then over: a cache cut short by a kill or a full disk made
        # "reuse" fail to load and "Use the cached one" offer a broken file.
        temp = path.with_name(f".{path.stem}.part.npz")
        # Uncompressed: `ensure_shading` writes it with the device open.
        self._shading.save(temp, compress=False)
        from .library import _replace, _write_atomic

        # The old word on where the cache came from goes before the cache it
        # describes does: stopped between the two, the cache names no
        # calibration rather than the one it has just stopped being.
        sidecar = self._shading_sidecar(path)
        sidecar.unlink(missing_ok=True)
        _replace(temp, path)
        origin = self._shading_origin or {}
        if origin.get("archive"):
            import hashlib
            try:
                _write_atomic(sidecar, json.dumps({
                    "archive": origin["archive"],
                    "archive_sha256": origin.get("archive_sha256"),
                    "reference_sha256": hashlib.sha256(
                        path.read_bytes()).hexdigest(),
                }, indent=2))
            except OSError as exc:
                # Costs the link, not the cache: `library.calibration_of`
                # still finds the archive by content.
                self._log(f"could not say beside {path} which calibration it "
                          f"came from ({exc})")
        return path

    @staticmethod
    def _shading_sidecar(path: Path) -> Path:
        """Where `save_shading` says which archived calibration a cache is."""
        return path.with_name(path.name + ".json")

    def _shading_link(self, path: Path) -> dict[str, Any] | None:
        """The archive a cached reference names, if it names one for these
        bytes; None otherwise, and never raises -- a reference that loaded is
        in force whatever its sidecar says."""
        import hashlib
        try:
            link = json.loads(
                self._shading_sidecar(path).read_text(encoding="utf-8"))
            if (not isinstance(link, dict) or not link.get("archive")
                    or link.get("reference_sha256")
                    != hashlib.sha256(path.read_bytes()).hexdigest()):
                return None
        except (OSError, ValueError):
            return None
        return {"archive": str(link["archive"]),
                "archive_sha256": link.get("archive_sha256")}

    def archive_calibration(self, result: dict[str, Any],
                            root: str | Path) -> Path | None:
        """Keep one calibration's own bytes, beside the cache, for good.

        ``root/<UTC time>/`` holds ``data.bin`` -- every calibration line
        exactly as read -- with ``calibration.json`` (width, line stride,
        resolution, when, every command sent and what came back, checksum),
        the reference reduced from it and the calibration's CCD mask.

        Written uncompressed: the device is still open, and compressing with
        it open and idle is what preceded a wedge. It is 1.7 MB.

        Written as a library entry is: ``INCOMPLETE`` first and removed last,
        the record beside and renamed over. A folder a kill or a full disk
        cut short passed for a whole calibration.

        A calibration that raised is kept too, with what it said
        (``failed``) and no reference: see `ensure_shading`.
        """
        from .library import INCOMPLETE, _write_atomic

        data = result.get("data")
        if not data:
            return None
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        root = Path(root)
        # The parents first and on their own. Asked of the folder itself,
        # `mkdir(parents=True)` on Windows answers FileExistsError when a
        # *parent* is a file -- for a folder that does not exist -- and a loop
        # that takes that as "name taken, try the next" never ends, with the
        # device open. Here a parent in the way raises, once.
        root.mkdir(parents=True, exist_ok=True)
        folder, n = root / stamp, 2
        while True:
            try:
                folder.mkdir()
                break
            except FileExistsError:
                if not folder.exists():
                    raise
                folder, n = root / f"{stamp}-{n}", n + 1
        (folder / INCOMPLETE).write_text(
            "this calibration was being written and did not finish\n",
            encoding="utf-8")
        (folder / "data.bin").write_bytes(data)
        mask = result.get("ccd_mask")
        if mask is not None:
            (folder / "ccd_mask.bin").write_bytes(bytes(mask))
        if result.get("reference") is not None:
            result["reference"].save(folder / "shading.npz", compress=False)
        import hashlib
        record = {
            # Its own time where it carries one: a calibration that failed,
            # or was not taken, never became the session's, and the origin of
            # the reference in force is another day's.
            "measured_utc": (result.get("measured_utc")
                             or (self._shading_origin or {}).get("measured_utc")),
            "media_loaded": result.get("media_loaded"),
            "resolution": result.get("resolution"),
            "pixels_per_line": result.get("pixels_per_line"),
            "bytes_per_line": result.get("bytes_per_line"),
            "index_header": INDEX_HEADER,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "duration_s": result.get("duration_s"),
            "commands": result.get("commands"),
            "reference": "shading.npz" if result.get("reference") is not None else None,
            "ccd_mask": "ccd_mask.bin" if mask is not None else None,
            "protocol_revision": PROTOCOL_REVISION,
            # Why there is no reference beside lines that reduce to one: the
            # device sent fewer than it declared, or a channel came back as
            # one phase (`calibration_shortfall`). The one reason, under both
            # names a reader may look for it by.
            "lines_declared": result.get("lines_declared"),
            "lines_arrived": result.get("lines_arrived"),
            "incomplete": result.get("incomplete"),
            "refused": result.get("refused"),
            # Where it stopped and what it said, for one that raised.
            "failed": result.get("failed"),
        }
        _write_atomic(folder / "calibration.json",
                      json.dumps(record, indent=2, default=str))
        (folder / INCOMPLETE).unlink(missing_ok=True)
        return folder

    def _archive_failed_calibration(self, root: Path) -> None:
        """Archive the lines of a calibration that raised, tagged failed.

        They went with it: the lines of one refused part way, or read in
        full and then lost to whatever raised after the read, are the only
        evidence of what the device sent. (A refused mask no longer raises:
        it costs the mask, not the calibration.) Never raises: the failure
        being reported is the calibration's.
        """
        failed, self.last_failed_calibration = self.last_failed_calibration, None
        if not failed or not failed.get("data"):
            return
        try:
            folder = self.archive_calibration(
                dict(failed, commands=self.last_failed_commands), root)
        except Exception as exc:                      # noqa: BLE001
            self._log(f"could not keep the failed calibration's bytes ({exc})")
            return
        self._log(f"the calibration failed; the {len(failed['data'])} bytes it "
                  f"read are kept in {folder}")

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

        ``skip`` leaves the session with no reference at all. A pass that then
        asks for correction is refused rather than calibrated for (see `scan`);
        one taken with ``shading=False`` comes back raw, and striped: the
        scanner never corrects its own output.

        A calibration that comes back incomplete (`calibration_shortfall`)
        raises `ShadingUnavailable` once its bytes are archived, leaving the
        reference in force and the cache as they were.
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
        try:
            result = self.calibrate_shading(keep_data=True)
        except BaseException:
            self._archive_failed_calibration(path.parent)
            raise
        duration = round(time.monotonic() - started, 1)
        # The calibration's own bytes, kept: the reference is a reduction of
        # them, and a reduction cannot be redone with better code once its
        # input is gone. Without this no correction in the library could ever
        # be recomputed from scratch.
        archive = None
        try:
            archive = self.archive_calibration(result, path.parent)
        except Exception as exc:                      # noqa: BLE001
            # Anything, not only a disk: a record that would not serialise
            # raised past this and threw away a successful 3-4 minute
            # calibration, neither cached nor put in force. The bytes are the
            # loss; the reference is still good.
            self._log(f"could not keep the calibration's bytes ({exc})")
        if result["reference"] is None:
            # Kept, and said, but neither cached nor put in force: see
            # `calibration_shortfall`. Raised, so a window job fails and a
            # tool stops, rather than a summary line going by while the
            # reference in force is still the one from before.
            raise ShadingUnavailable(
                "the calibration came back incomplete ("
                + (result.get("incomplete") or "no usable lines")
                + "), so no reference was taken from it and the cache was left "
                "as it was. "
                + ("The reference in force before it still is"
                   if self._shading is not None else
                   "A corrected scan will be refused until a calibration "
                   "succeeds")
                + (f"; its bytes are in {archive}." if archive is not None
                   else "."))
        if archive is not None and self._shading_origin is not None:
            import hashlib
            self._shading_origin["archive"] = str(archive)
            # Which bytes, not only where: `save_shading` passes both on, so
            # a pass corrected by this reference reloaded another day still
            # names the lines it was reduced from.
            self._shading_origin["archive_sha256"] = hashlib.sha256(
                result["data"]).hexdigest()
        # A cache that cannot be written costs the cache, not the calibration:
        # the reference is in hand, and discarding a successful 3-4 minute
        # calibration over a full disk left every scan refused.
        saved = None
        try:
            saved = self.save_shading(path)
        except OSError as exc:
            self._log(f"could not cache the reference at {path} ({exc}); "
                      "it is still in force for this session")
        drained = result["bytes_drained"] / 1e6
        summary = f"  {drained:.2f} MB in {duration:.0f}s"
        summary += f", saved {saved}" if saved else ", not cached"
        if archive is not None:
            summary += f"; its bytes are in {archive}"
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

        Only the most recent pass is described. A caller taking several passes
        has to call this once per pass, as each is captured, because `last_raw`
        is overwritten.
        """
        return {
            "reference": self._shading,
            "ccd_mask": self._ccd_mask,
            "raw": self.last_raw,
            "raw_layout": self.last_raw_layout,
        }

    # -- automatic filing (debug mode) -------------------------------------

    def _debug_capture(self, image: np.ndarray, meta: dict[str, Any],
                       capture: dict[str, Any] | None = None,
                       failed: bool = False) -> None:
        """Spool a scan to disk for filing after the session.

        ``capture`` is the pass's own record where the caller has it, and
        then trusted as this pass's; otherwise the scanner's is read. A
        ``failed`` pass is spooled whether or not debug filing is on, and
        filed tagged so (`_after_a_failed_pass`).

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
        if not (getattr(self, "debug", False) or failed):
            return
        try:
            spool = self._debug_spool_dir()
            # Counted, never taken from the queue's length: a pass its caller
            # has filed leaves the queue (`debug_claim`), and the length then
            # named a number a pass still waiting had -- whose files the next
            # one would have written over.
            n = self._debug_count = getattr(self, "_debug_count", 0) + 1
            # The pass's meta itself, not a copy: its caller goes on to add
            # what only it knows -- a bracket's index and ratio, a roll
            # frame's index, position and registration -- and a copy taken
            # here filed the pass without them, so a bracket debug filing
            # kept could not be told apart or merged again. The sidecar
            # below is the copy, as the pass stood when it was spooled.
            item: dict[str, Any] = {"meta": meta, "captured": time.time()}
            item["tags"] = (["failed"] if failed else []) + (
                ["debug"] if getattr(self, "debug", False) else [])
            # And what it was for, where the loop that took it said
            # (`pass_role`), so the library can be asked for a roll's probes
            # or a hold's verification passes: every one was tagged "debug"
            # and nothing else, and could be found only by reading records.
            role = self._ROLE_TAGS.get(
                str((meta.get("pass_role") or {}).get("kind")))
            if role is not None:
                item["tags"].append(role)
            item["notes"] = ("a pass read in full that then failed: "
                             f"{(meta.get('failed') or {}).get('error')}"
                             if failed else "captured with RPS7200_DEBUG on")
            record = self.capture_record() if capture is None else capture
            # So a caller that files this very pass itself can say so, and the
            # flush does not file it twice (`debug_claim`). Weak, because the
            # pixels of a 7200 dpi roll must not be held alive by the spool.
            try:
                item["pixels"] = weakref.ref(image)
            except TypeError:
                item["pixels"] = None

            image_path = spool / f"{n:03d}-image.npy"
            np.save(image_path, image)
            item["image_path"] = image_path

            raw = record.get("raw")
            layout = record.get("raw_layout") or {}
            # The bytes must be this pass's. `read_planes` no longer leaves an
            # earlier pass's behind, and this is the second guard: bytes laid
            # out for another width, channel count or height are another
            # photograph. The session's own question, which judges the rows
            # the bytes can decode to -- what arrived, less what the 7200 dpi
            # realignment trimmed -- so it asks about height too, where this
            # used to leave height out rather than get those two wrong.
            if capture is None and raw is not None and raw_bytes_disagree(
                    image.shape, layout, meta):
                self._log("debug: the raw bytes held do not describe this "
                          "pass; spooling it without them")
                raw, layout = None, {}
            if raw is not None:
                raw_path = spool / f"{n:03d}-raw.bin"
                raw_path.write_bytes(raw)
                item["raw_path"] = raw_path
            item["raw_layout"] = layout or None
            # Small enough to keep: a shading reference is a few hundred kB and
            # the CCD mask is 5172 bytes.
            item["reference"] = record.get("reference")
            item["ccd_mask"] = record.get("ccd_mask")
            if item["reference"] is not None:
                # Once per reference, not once per pass: every pass of a
                # session shares it. Uncompressed, as everything else here:
                # the device is open. Removed with the spool, never per pass,
                # because the passes after this one still point at it.
                saved = getattr(self, "_debug_reference_saved", None)
                if saved is None or saved[0] is not item["reference"] \
                        or not saved[1].exists():
                    ref_path = spool / f"{n:03d}-shading.npz"
                    item["reference"].save(ref_path, compress=False)
                    saved = (item["reference"], ref_path)
                    self._debug_reference_saved = saved
                item["reference_path"] = saved[1]
            if item["ccd_mask"] is not None:
                mask_path = spool / f"{n:03d}-ccd_mask.bin"
                mask_path.write_bytes(bytes(item["ccd_mask"]))
                item["mask_path"] = mask_path

            # And on disk beside the pixels, last, so a spool left behind --
            # by a failed filing, a force abort, a process that died before
            # close() -- says what each pass was and which of the files
            # beside it are its own, and `file_spool` can file it.
            item["meta_path"] = spool / f"{n:03d}-meta.json"
            self._debug_note(item)

            with self._debug_lock:
                self._debug_pending.append(item)
        except Exception as exc:                      # never break a scan
            self._log(f"debug: could not spool this scan ({exc})")

    @staticmethod
    def _debug_note(item: dict[str, Any]) -> None:
        """Write a spooled pass's sidecar: its record, and whose files are
        whose. Rewritten when the pass is claimed, or its claim given back,
        so a spool left behind says which passes a caller may have filed.

        The reference is named per pass because it is written once per
        calibration, beside the first pass that used it: without the name, a
        spool filed by hand had to guess which of its `NNN-shading.npz`
        applied to which pass.
        """
        def name(key: str) -> str | None:
            path = item.get(key)
            return Path(path).name if path is not None else None

        Path(item["meta_path"]).write_text(json.dumps(
            {"meta": item["meta"], "raw_layout": item.get("raw_layout"),
             "captured": item.get("captured"),
             "files": {"image": name("image_path"), "raw": name("raw_path"),
                       "shading": name("reference_path"),
                       "ccd_mask": name("mask_path")},
             "tags": item.get("tags"), "notes": item.get("notes"),
             "claimed": bool(item.get("claimed"))},
            indent=2, default=str), encoding="utf-8")

    def _after_a_failed_pass(self, exc: BaseException) -> None:
        """Keep what a pass that raised left: see `_keeps_what_a_failed_pass_left`.

        Never raises: the failure being reported is the pass's, and this must
        not replace it with its own.
        """
        flight, self._in_flight = getattr(self, "_in_flight", None), None
        try:
            stopper = getattr(getattr(self, "t", None), "stop", None)
            leftover = stopper() if callable(stopper) else None
            commands = (flight or {}).get("commands") or leftover
            if commands is not None:
                self.last_failed_commands = commands
            if flight and not flight.get("raw") and flight.get("chunks"):
                # Given up part way: the lines that did arrive.
                blob = b"".join(flight.pop("chunks"))
                flight.update(raw=blob, cut_short=True,
                              raw_layout=self._raw_layout(
                                  flight["params"], flight["channels"], blob))
            if not flight or not flight.get("raw"):
                return
            self._keep_failed_pass(flight, exc, commands)
        except Exception as problem:                      # noqa: BLE001
            self._log(f"could not keep what the failed pass left ({problem})")

    def _keep_failed_pass(self, flight: dict[str, Any], exc: BaseException,
                          commands: dict[str, Any] | None) -> None:
        """Spool a pass that failed after its lines were read, to be filed:
        all of them, or those that arrived before the read was given up.

        Its decoded pixels where the decode got that far. Where it did not,
        the lines exactly as they arrived, one row each with their tags --
        not a picture, and the record says so, but what scan.tif can hold of
        bytes nothing could decode; `raw.bin.gz` beside it is the pass.
        """
        why = f"{type(exc).__name__}: {exc}"
        layout = dict(flight.get("raw_layout") or {})
        pixels = flight.get("pixels")
        stage = "after the decode" if pixels is not None else "in the decode"
        if flight.get("cut_short"):
            # The lines of a read given up part way decode as any short read
            # does -- to the rows every plane reached -- where they decode.
            stage = "during the read"
            try:
                pixels = self.decode_index(flight["raw"], flight["params"],
                                           flight["channels"])[0]
            except Exception:                             # noqa: BLE001
                pixels = None
        decoded = pixels is not None
        if pixels is None:
            stride = int(layout.get("line_stride") or 0) or 1
            blob = flight["raw"]
            lines = len(blob) // stride
            pixels = np.frombuffer(blob, np.uint8, count=lines * stride
                                   ).reshape(lines, stride, 1)
        meta = dict(flight.get("meta") or {})
        meta.update({
            "width": layout.get("width"),
            "height": int(pixels.shape[0]),
            "bytes_per_line": layout.get("bytes_per_line"),
            "read_direction": (self.last_read_direction.as_record()
                               if self.last_read_direction is not None
                               else None),
            "commands": commands,
            "shading": None,
            # Not corrected, and not to be as though nothing had happened:
            # `library.corrected` hands it over as it came, saying why. Except
            # where it was never to be corrected: `shading=False` stays the
            # sentinel that says so, and the failure is `failed` and the tag.
            # Written over with this, a raw-on-purpose pass with no reference
            # -- calibration off -- read "correction was asked for" in
            # `verify` for good, and in `library.corrected`, both false.
            "shading_skipped": (f"the pass failed {stage}: {why}"
                                if flight.get("shading", True)
                                else SHADING_SKIPPED_EXPLICIT),
            "failed": {
                "stage": stage, "error": why,
                "pixels": ("decoded" if decoded else
                           "the lines as they arrived, one row each, tags "
                           "included -- not a picture"),
            },
        })
        self._debug_capture(pixels, meta, failed=True, capture={
            "reference": self._shading, "ccd_mask": self._ccd_mask,
            "raw": flight["raw"], "raw_layout": layout})
        self._log(f"this pass was read in full and then failed ({why}); its "
                  "bytes are kept, and filed tagged failed once the device "
                  "has closed")

    def _debug_root(self) -> Path:
        """Where debug filing files: see `debug_root`."""
        from . import library
        return Path(self.debug_root or os.environ.get(self.DEBUG_ROOT_ENV)
                    or library.DEFAULT_ROOT)

    def _debug_spool_dir(self) -> Path:
        """This session's spool, made on first use beside the library."""
        if self._debug_spool is not None and not self._debug_spool.is_dir():
            # Gone under a session that is still open -- a temporary-file
            # cleaner, where the library could not be written, or a hand.
            # Every pass after was refused at np.save, one log line each, and
            # the session's unfiled passes went with it. Made again, and said.
            self._log(f"debug: the spool {self._debug_spool} has gone; "
                      "passes spooled there before are lost, and a new one "
                      "is made for the rest of this session")
            self._debug_spool = None
            self._debug_reference_saved = None
        if self._debug_spool is None:
            parent = self._debug_root() / self.DEBUG_SPOOL_DIR
            try:
                parent.mkdir(parents=True, exist_ok=True)
                self._debug_spool = Path(
                    tempfile.mkdtemp(prefix="rps7200-debug-", dir=parent))
            except OSError:
                # A library that cannot be written to is no reason to spool
                # nothing: the pass then waits in the system's temporary
                # directory, and the flush says where when it cannot file it.
                self._debug_spool = Path(
                    tempfile.mkdtemp(prefix="rps7200-debug-"))
        return self._debug_spool

    def debug_claim(self, pixels: np.ndarray | None
                    ) -> Callable[[Any], None] | None:
        """Say that the caller files the pass these raw pixels came from.

        Debug filing then leaves it out, so a tool that files its own entries
        can keep debug on and still not file each of its passes twice -- which
        at 7200 dpi was 43 GB of duplicate on a roll, and the reason the window
        and the tools used to switch debug off outright. With it off they filed
        nothing of the passes they do not keep themselves: metering probes,
        hold and aim prescans. Pass the very array `last_pixels_raw` held.

        **A claim is a promise, and the spooled copy is kept until it is
        kept.** Returns the caller's receipt, or None when nothing spooled is
        this pass: call it once the caller's own filing is over, with the
        entry it wrote -- the spooled copy is deleted then, not later, so a
        roll holds one or two frames in the spool rather than every frame
        until the window closes -- or with None when it could not file the
        pass, and debug filing files it after all. It used to delete a claimed
        pass at close() on the claim alone, and every claimant files *after*
        close(): a full library disk lost the pass from both places, which is
        the one case the spool is there for.

        Claim only a pass filed *with its bytes*. A caller that files one
        without them -- the session drops bytes that describe another pass --
        leaves the spooled copy, which has them, to be filed as well.
        """
        if pixels is None:
            return None
        # `getattr`: stand-ins subclass this without running `__init__`.
        with self._debug_lock:
            for item in getattr(self, "_debug_pending", ()):
                ref = item.get("pixels")
                if ref is not None and ref() is pixels:
                    item["claimed"] = True
                    self._debug_renote(item)
                    return functools.partial(self._debug_receipt, item)
        return None

    def _debug_renote(self, item: dict[str, Any]) -> None:
        """`_debug_note`, never costing the caller its pass for a sidecar."""
        try:
            self._debug_note(item)
        except Exception as exc:                          # noqa: BLE001
            self._log(f"debug: could not update {item.get('meta_path')} ({exc})")

    def _debug_receipt(self, item: dict[str, Any], entry: Any) -> None:
        """The claimant's answer for one pass: see `debug_claim`."""
        with self._debug_lock:
            if entry is None:
                # Not filed after all, so it goes back to being ours: the
                # flush files it, and until then its spool stays put.
                item["claimed"] = False
                self._debug_renote(item)
                self._log("debug: a pass its caller could not file is kept, "
                          "and filed with the rest")
                return
            self._debug_pending = [i for i in self._debug_pending
                                   if i is not item]
        if self._debug_unlink(item):
            self._log(f"debug: a filed pass left files behind in "
                      f"{self._debug_spool}")

    def debug_settle(self) -> None:
        """File what the spool still holds, now that every claimant is done.

        For a caller that claims passes (`debug_claim`) and files them after
        close(), as the window and the tools do: call this after that
        filing, with the device closed. close() files what nobody claimed and
        leaves a claimed pass still waiting for its caller's answer to this,
        with the spool it sits in. Here a claim that was never answered is
        taken as a pass nobody filed, and filed.
        """
        self._debug_flush(settle=True)

    @staticmethod
    def _debug_unlink(item: dict[str, Any]) -> int:
        """Remove one spooled pass's files. Returns how many would not go."""
        stuck = 0
        for key in ("image_path", "raw_path", "meta_path", "mask_path"):
            path = item.get(key)
            if path is not None:
                try:
                    Path(path).unlink(missing_ok=True)
                except Exception:                    # noqa: BLE001
                    stuck += 1
        return stuck

    @staticmethod
    def _file_spooled(item: dict[str, Any], root: str | Path,
                      inquiry: Any = None) -> Path:
        """File one spooled pass in the library at ``root``. Returns the entry.

        Its pixels are mapped, not loaded -- tifffile's writer walks them a
        strip at a time, so a 570 MB frame need not be resident; the built-in
        writer, without tifffile, copies the whole of it into one `bytes` and
        so does not keep this promise -- and the mapping is let go before
        this returns. POSIX lets a file be unlinked while it is mapped and
        keeps the inode until the mapping goes; Windows refuses outright, with
        WinError 32. That refusal was swallowed, so on Windows nothing was
        ever freed -- a 38-frame roll at 7200 dpi left 43 GB in the temporary
        directory, for ever. Letting go of the array is enough:
        `library.save` keeps no reference to it.
        """
        from datetime import datetime, timezone

        from . import library
        from .library import FilmNotes

        # When the pass was taken, which is what its id and `created` say:
        # filed after close(), or days later from a spool left behind, they
        # said when it was filed.
        captured = item.get("captured")
        created = (datetime.fromtimestamp(float(captured), timezone.utc)
                   if captured else None)
        image = np.load(item["image_path"], mmap_mode="r")
        try:
            return library.save(
                image, item["meta"],
                root=root,
                film=FilmNotes(notes=item.get(
                    "notes", "captured with RPS7200_DEBUG on")),
                tags=item.get("tags") or ["debug"],
                reference=item.get("reference"),
                ccd_mask=item.get("ccd_mask"),
                raw_path=item.get("raw_path"),
                raw_layout=item.get("raw_layout"),
                inquiry=inquiry,
                created=created,
            )
        finally:
            del image

    def _debug_flush(self, settle: bool = False) -> None:
        """Write the queued scans. Called after the transport is closed.

        Failures are logged and swallowed. Filing is a record-keeping duty, and
        losing the record is better than losing the session that produced it.

        A pass that is claimed, its caller not yet having answered for it, is
        left for `debug_settle` (``settle``), which the caller runs once its
        own filing is done; everything else is filed here. See `debug_claim`.
        """
        with self._debug_lock:
            # `getattr`: stand-ins subclass this without running `__init__`.
            queued: list[dict[str, Any]] = list(
                getattr(self, "_debug_pending", None) or ())
            # Only the claimed ones wait. close() used to file nothing at all
            # while any claim was open, and the tools file and settle after
            # their `with` block: a second Ctrl-C, or anything else that
            # escaped before that, left every metering probe and hold prescan
            # nobody had claimed unfiled in the spool, where before the claim
            # existed close() had filed them. Filing beside a claimant still
            # writing is safe -- `library._reserve` hands each writer a
            # directory of its own -- and it is what close() always did.
            waiting: list[dict[str, Any]] = (
                [] if settle else [i for i in queued if i.get("claimed")])
            pending = (queued if settle else
                       [i for i in queued if not i.get("claimed")])
            if queued:
                self._debug_pending = waiting
        if waiting:
            self._log(f"debug: {len(waiting)} claimed scan(s) still to be filed "
                      "by their caller; they stay in the spool until it has "
                      f"answered ({self._debug_spool})")
        if not pending:
            # Nothing to file, and perhaps still a spool: every pass in it
            # claimed and answered for leaves the reference they shared,
            # which is removed with the spool and never per pass. Returning
            # here left that directory behind in the library's `.spool`, one
            # per session, on any session whose every pass was claimed.
            if not waiting:
                self._debug_remove_spool()
            return
        self._log(f"debug: filing {len(pending)} scan(s) in the library ...")
        try:
            from . import library  # noqa: F401  (fails here, not per pass)
        except Exception as exc:
            self._log(f"debug: library unavailable ({exc}); {len(pending)} "
                      f"scan(s) left unfiled in {self._debug_spool}")
            # Forgotten, so a later pass spools elsewhere; see `failed` below.
            self._debug_spool = None
            self._debug_reference_saved = None
            return

        root = self._debug_root()
        stuck = 0
        failed = 0
        lost = 0
        for n, item in enumerate(pending, 1):
            filed = False
            if item.get("claimed"):
                # Claimed and never answered for, with the claimant done: it
                # was not filed, as far as anybody can tell, and a pass filed
                # twice is a nuisance where one filed nowhere is a loss.
                self._log(f"debug: scan {n}/{len(pending)} was claimed and "
                          "never filed by its caller; filing it here")
            # Its files, before the library is asked: a spool whose files
            # were removed under it was reported "kept in" a folder that did
            # not hold them, and a pass whose raw bytes alone had gone left
            # an INCOMPLETE entry around an empty raw.bin.gz.
            spooled = [Path(p) for p in (item.get("image_path"),
                                         item.get("raw_path"))
                       if p is not None]
            gone = [str(p) for p in spooled if not p.exists()]
            if gone:
                left = [str(p) for p in spooled if p.exists()]
                self._log(f"debug: scan {n}/{len(pending)} cannot be filed: "
                          f"its spooled {', '.join(gone)} was deleted before "
                          "it could be"
                          + (f"; {', '.join(left)} is kept" if left else ""))
                # What is left of it stays, as a failure's does. With nothing
                # left it is lost, not failed: counted as failed, a spool
                # made again after the first was deleted was reported as
                # holding a pass it never saw, and kept on disk, empty, for
                # the sake of it.
                if left:
                    failed += 1
                else:
                    lost += 1
                continue
            try:
                entry = self._file_spooled(item, root, self._inquiry)
                self._log(f"debug: filed {n}/{len(pending)} -> {entry}")
                filed = True
            except Exception as exc:
                failed += 1
                self._log(f"debug: could not file scan {n} ({exc}); its spooled "
                          f"pixels, bytes and record are kept in {self._debug_spool}")
            finally:
                # Free each frame's spool as soon as it is filed, not at the
                # end. At 7200 dpi a frame spools 1.1 GB, so holding all 38 of
                # a roll through the flush would want 43 GB of disk on top of
                # the library being written -- the peak is what runs a machine
                # out of space, not the total.
                #
                # Only once it is filed. A spool unlinked after a failed save
                # was the only copy of that pass -- a full disk or a mistyped
                # RPS7200_DEBUG_ROOT deleted every scan it could not file.
                if filed:
                    stuck += self._debug_unlink(item)
        if stuck:
            # Said out loud rather than swallowed. The silence is what let the
            # leak above run for a whole platform without anyone noticing.
            self._log(f"debug: {stuck} spooled file(s) could not be removed; "
                      f"{self._debug_spool} is still on disk")
        if lost:
            self._log(f"debug: {lost} scan(s) were deleted from the spool "
                      "before they could be filed, and are lost")
        if failed:
            # Kept, all of it: the directory is the only copy of what failed.
            self._log(f"debug: {failed} scan(s) could not be filed and remain "
                      f"in {self._debug_spool}")
            # Forgotten rather than reused, so a later pass spools into a fresh
            # directory and cannot overwrite what is left here.
            self._debug_spool = None
            self._debug_reference_saved = None
            return
        if not waiting:
            # Not while a claimed pass is still in it: its files are the only
            # copy until its caller answers.
            self._debug_remove_spool()

    def _debug_remove_spool(self) -> None:
        """Remove this session's spool, once nothing in it is waiting."""
        spool = getattr(self, "_debug_spool", None)
        try:
            if spool is not None:
                shutil.rmtree(spool, ignore_errors=True)
                # Only forget it once it is actually gone: this is the one
                # handle to the directory, and dropping it on a failed clean
                # loses the chance to say where the leftovers are.
                if not spool.exists():
                    parent = spool.parent
                    self._debug_spool = None
                    self._debug_reference_saved = None
                    # The library's `.spool` too, once nothing is in it --
                    # another session's spool, or one left by a failed filing,
                    # keeps it, which is what an rmdir refuses to remove.
                    if parent.name == self.DEBUG_SPOOL_DIR:
                        try:
                            parent.rmdir()
                        except OSError:
                            pass
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
            raw_hex=bytes(d).hex(),
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

        A response shorter than asked for is refused here rather than handed
        back, because every caller decodes by fixed offset -- `get_gain_offset`
        reads d[102], `read_state` reads d[8] -- and a short buffer therefore
        raises IndexError from somewhere far from the cause. That is not
        theoretical: it wedged the scanner on 2026-09-13. `get_gain_offset`
        came back short inside `calibrate_shading`'s read loop, which catches
        ScanReadError and would have shrugged it off, but an IndexError is not
        a ScanReadError -- it escaped the loop, abandoned the calibration
        mid-read, and cost a power cycle.
        """
        last: Sense | str = ""
        for attempt in range(1, retries + 1):
            try:
                data = self.t.command(command, read_size=read_size)
            except CheckCondition:
                last = self.read_sense()
                self._log(f"  {label}: {last}")
                time.sleep(0.3)
                continue
            if len(data) < read_size:
                raise ScanReadError(
                    f"{label} returned {len(data)} bytes, expected {read_size}"
                )
            return data
        raise ScanReadError(f"{label} failed after {retries} attempts: {last}")

    def read_state(self, retries: int = 3) -> State:
        d = self._query(
            _cmd(SCSI_READ_STATE, 13), 13, "read_state", retries=retries
        )
        state = State(
            button=bool(d[0]),
            warming_up=bool(d[5]),
            scanning=d[6],
            busy=d[8],
            position=d[2],
            raw=bytes(d),
        )
        self.last_state = state
        self._calibrated_since_state = False
        return state

    def carriage_record(self) -> dict[str, Any] | None:
        """The last READ STATE before a pass, as evidence of where the carriage was.

        Recorded, not acted on: the bit is tied to the carriage only by the
        vendor captures. ``stale`` when a calibration ran after the read --
        calibration moves the carriage, which is exactly why the captures'
        three misses were each session's first pass.
        """
        state = self.last_state
        if state is None or not state.raw:
            return None
        return {"read_state": state.raw.hex(), "far_end": state.carriage_far,
                "stale": bool(self._calibrated_since_state)}

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

    @staticmethod
    def byte14_for(passes: int) -> int:
        """MODE SELECT byte 14 as this driver sends it, when not overridden.

        Its bit 0 leaves the carriage at the far end after the pass, so the
        pass after it may come back bottom-up. A static method so the demo's
        carriage takes the decision from here rather than a copy.
        """
        return 0x21 if passes == ONE_PASS_RGBI else 0x10

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
        # Bit 0 is now known to leave the carriage at the far end after the
        # pass, so the next pass may be read bottom-up; `rps7200.direction`
        # reads that from each pass's own lines rather than predicting it.
        # The full set of seven refutes that: RGB passes carry 0x21 twenty-six
        # times. What holds across all 71 MODE SELECTs is that *bit 0* tracks
        # the scan frame's y0 shifting by one line, which is bidirectional
        # scanning -- the carriage images going down, then coming back. The
        # upper nibble is not explained. See docs/protocol.md section 4.
        #
        # The default is left alone until the hardware says what it should be;
        # `byte14` is how that gets asked.
        data[14] = byte14 if byte14 is not None else self.byte14_for(passes)

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
        self._refuse_if_suspect(f"a film move ({names.get(action, hex(action))})")
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

        One frame per command, ``steps`` times. The value byte does not count
        frames -- ``04 01 00 02`` moved the film one, as ``04 01 00 01`` does
        (`docs/protocol.md` section 5) -- and a move of several frames sent
        as one command with ``value=steps`` moved one and reported success on
        the first change, where the demo moved them all.
        """
        if steps != 1:
            if steps < 1:
                raise ValueError(f"a move of {steps} frames")
            position = None
            for _ in range(steps):
                position = self._whole_frames(action, 1, timeout, poll, verb)
                if position is None:
                    return None
            return position
        before = self.position()
        # Where the film was has to be known before the move, or the first
        # poll to answer counts as the move whatever it says: the counter
        # lags the command by 1.6-6.2 s, so an unknown `before` took the old
        # position, read a second later, for the new one. Asked again, and
        # failing that the last reading there was.
        for _ in range(3):
            if before is not None:
                break
            time.sleep(poll)
            before = self.position()
        if before is None and self.last_state is not None:
            before = self.last_state.position
        self._moves_left_behind()
        self.slide(action, param=0x01, value=0x01)

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
        self._refuse_if_suspect("a scan")
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
            except BaseException as exc:
                # Not a refusal: the SCAN went out and what the device made of
                # it is not known -- a status read that timed out, BUSY past
                # its deadline, Ctrl-C. It may be scanning. This used to escape
                # before any guard, so a roll counted the frame an ordinary
                # failure, advanced the film and started the next pass into it.
                self._scanning = False
                self._mark_suspect(f"{type(exc).__name__} as a scan was "
                                   f"started: {exc}")
                raise

        # Leave the scanner usable; an abandoned start wedges it otherwise.
        self._scanning = False
        raise CalibrationRequired(f"scanner refused to start: {last}")

    def finish_scan(self, polls: int = 3) -> None:
        """End a completed scan the way the vendor software does.

        CyberView never sends STOP SCAN. It reads all the data and then polls
        READ_STATE while the scanner settles. Sending STOP SCAN after a
        successful read appears to be what leaves this scanner unresponsive to
        the next session, and CLAUDE.md lists it among the commands never to
        send -- not to cancel a scan still running either: the recovery from
        an abandoned pass is a power cycle (`suspect`).
        """
        self._scanning = False
        for _ in range(polls):
            try:
                self.read_state()
            except (CheckCondition, UsbError, ScanReadError):
                return
            time.sleep(0.2)

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
        # A byte each, refused rather than masked, as the exposure's two bytes
        # already are by `to_bytes`: masked, a gain of 256 went to the device
        # as 0 while the pass's record said 256 -- a record that disagrees
        # with what was sent, which is what re-evaluating a pass cannot have.
        for name, values in (("gain", s.gain), ("offset", s.offset)):
            wrong = [int(v) for v in values if not 0 <= int(v) <= 0xFF]
            if wrong:
                raise ValueError(f"{name} {wrong} does not fit the one byte "
                                 "it is sent in (0-255)")
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
        # Not a status query: a write, which probes and the roll also make
        # outside a pass -- a probe's restore after a read it abandoned.
        self._refuse_if_suspect("a gain and offset write")
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
                data = self.t.command(
                    _cmd(SCSI_READ, lines),
                    read_size=lines * bytes_per_line,
                    timeout_ms=timeout_ms,
                    max_wait_s=max_wait_s,
                )
                # Every caller counts `lines` as read on return. A READ
                # answered OK with no data phase came back as b"" and was
                # counted anyway: a pass could reach its declared line count
                # early and be taken as complete while the device still held
                # lines. Refused, so the pass is not taken for whole.
                if len(data) != lines * bytes_per_line:
                    raise ScanReadError(
                        f"reading {lines} lines x {bytes_per_line} bytes "
                        f"returned {len(data)} bytes")
                return data
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
        idle_timeout: float | None = None,
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
        if idle_timeout is None:
            idle_timeout = self.READ_IDLE_S
        deadline = time.monotonic() + timeout

        self._log(
            f"reading {total_lines} lines x {bpl} bytes, {batch} per request"
        )

        chunks: list[bytes] = []
        # The pass in flight holds its lines as they arrive, not only once
        # they are all in: a read given up part way -- a timeout, a refused
        # read, Ctrl-C -- is the pass whose bytes are most worth keeping, and
        # they went with this list (`_keeps_what_a_failed_pass_left`).
        flight = getattr(self, "_in_flight", None)
        if flight is not None and keep_raw:
            flight.update(chunks=chunks, params=params, channels=channels)
        got = 0
        idle_since: float | None = None
        while got < total_lines:
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"read {got}/{total_lines} lines before timing out"
                )

            n = min(batch, total_lines - got)
            try:
                # The bulk transfer, and a pause part way through its payload,
                # get the same patience as a READ answered "not yet": a device
                # that stays silent rather than saying so used to be given up
                # at 120 s whatever the pass, short of an untied infrared
                # pass's ~220 s floor (`read_idle_s`).
                chunk = self.read_lines(n, bpl, retries=1,
                                        timeout_ms=int(idle_timeout * 1000))
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
        # Every line is in: whatever goes wrong from here on is the host's, and
        # leaves nothing outstanding on the device.
        self._read_complete = True
        # Whether the device ended the read before the lines GET PARAMETERS
        # declared, which `scan` records with the pass: see `short_read`.
        self.last_read_short = len(blob) // bpl < total_lines
        layout = self._raw_layout(params, channels, blob)
        # And for the pass in flight, so one whose decode below -- or whose
        # realignment or correction after it -- then fails is still filed
        # with them (`_keeps_what_a_failed_pass_left`). Only where the bytes
        # are kept at all: a caller that asked for none files nothing. The
        # lines themselves are let go: the joined bytes are all of them.
        if flight is not None and keep_raw:
            flight.pop("chunks", None)
            flight.update(raw=blob, raw_layout=layout)
        if keep_raw:
            self.last_raw = blob
            self.last_raw_layout = layout
        else:
            # Cleared, never left over. A pass that did not keep its bytes used
            # to leave the previous pass's here, and `capture_record` handed
            # them on: debug filing put them beside this pass's pixels, which
            # is an entry whose bytes decode to a different photograph.
            self.last_raw = None
            self.last_raw_layout = None
        image, direction = self.decode_index(blob, params, channels)
        self.last_read_direction = direction
        if flight is not None and keep_raw:
            flight["pixels"] = image
        if direction.reversed:
            self._log("this pass was read bottom-up (its first line is "
                      f"{direction.lead}, its last {direction.trail or 'cut short'}); "
                      "turned upright")
        elif not direction.known:
            self._log(f"which way this pass was read is unknown: {direction.why}; "
                      "left as it came")
        return image

    @staticmethod
    def _raw_layout(params: ScanParameters, channels: int,
                    blob: bytes) -> dict[str, Any]:
        """Everything a decoder needs, so the bytes stay meaningful without
        this object. Line stride includes the 2-byte channel tag."""
        return {
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

    @staticmethod
    def _deinterleave(
        blob: bytes, params: ScanParameters, channels: int
    ) -> np.ndarray:
        """The pass's pixels, upright. See :meth:`decode_index`."""
        return DirectScanner.decode_index(blob, params, channels)[0]

    @staticmethod
    def decode_index(
        blob: bytes, params: ScanParameters, channels: int
    ) -> tuple[np.ndarray, ReadDirection]:
        """Deinterleave index-format lines into ``(H, W, channels)``, upright.

        Returns the image and the direction its own line tags say the pass was
        read in (`rps7200.direction`). A pass read bottom-up comes back with
        its rows in reverse order; they are turned here, in the decode, so
        every path from bytes to pixels -- a scan, the library's re-decode,
        the demo -- delivers the same upright picture and nothing downstream
        has to know. The raw bytes are not touched.

        Truncated to the planes' common height *before* turning: the lines
        are aligned by their index from the start of the read, and a short
        read loses its last lines -- the top of the picture, on a pass read
        bottom-up. Turning each plane first would misalign the planes by
        however many lines each one lost.
        """
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
        direction = read_direction(blob, bpl, lines=params.lines, channels=channels)
        step = -1 if direction.reversed else 1
        image = np.stack([np.array(planes[c][:height][::step]) for c in order],
                         axis=-1)
        return image, direction

    #: The widest shading reference this device will produce, in columns.
    #:
    #: **Measured on the hardware 2026-09-13, and it is a ceiling, not a
    #: default.** `calibrate_shading(resolution=7200)` was run on the device:
    #: the shading descriptor came back declaring `pixels_per_line=10344` --
    #: a *byte* count, so 5172 columns -- which is the identical descriptor it
    #: declares at 3600 dpi. The calibration does not widen with the MODE
    #: SELECT resolution field, so no resolution argument can produce a
    #: reference for a 7200 dpi pass's 10344 columns. Asking again costs two
    #: minutes of scanner time and returns 5172 again.
    #:
    #: Numerically the same as :data:`CCD_MASK_SIZE`, and for the same reason:
    #: the mask carries one byte per calibration column.
    MAX_SHADING_COLUMNS = CCD_MASK_SIZE

    #: The only resolution wide enough that adjacent output columns are
    #: adjacent CCD elements rather than a subsampling of them. Below this,
    #: one of the two staggered element rows described below is simply never
    #: read, which is why the stagger has no visible effect at 3600 dpi and
    #: under -- see :meth:`_realign_native_column_stagger`.
    NATIVE_COLUMN_STAGGER_DPI = 7200

    #: Measured 2026-09-13 by cross-correlating a 7200 dpi frame's even and
    #: odd columns against each other, per channel, after removing each
    #: column's own fixed offset (its mean) so the correlation is about
    #: picture content and not the ordinary shading pattern. On two unrelated
    #: entries (different frames, `20260911T103600Z` and `20260911T104244Z`)
    #: and three crops each, correlation peaks cleanly at lag 4 -- 0.995-0.997
    #: against 0.95-0.96 at lag 0 -- for every one of R, G and B. This is the
    #: signature of a staggered linear CCD: two rows of elements, offset from
    #: each other along the scan direction to pack more columns than one row's
    #: pitch allows, so odd columns are physically reading the frame this many
    #: lines later than even ones.
    NATIVE_COLUMN_STAGGER_LINES = 4

    @staticmethod
    def _realign_native_column_stagger(
        image: np.ndarray, lines: int = NATIVE_COLUMN_STAGGER_LINES
    ) -> np.ndarray:
        """Undo the even/odd column stagger a native 7200 dpi read comes with.

        Without this, the two interleaved element rows are decoded as if they
        shared a scan line, which draws every other column shifted up or down
        against its neighbours -- a zigzag, column by column, that widens with
        distance from wherever the two happen to agree.

        Realigning costs the last ``lines`` rows of whichever parity leads:
        there is no data to shift the other parity *into*, so the frame is
        trimmed rather than padded. At 4 lines out of several thousand this is
        not worth trading for a seam.
        """
        if lines <= 0:
            return image
        h = image.shape[0]
        if h <= lines:
            raise ValueError(
                f"frame is only {h} lines; cannot realign a {lines}-line "
                "column stagger"
            )
        aligned = np.empty((h - lines, *image.shape[1:]), dtype=image.dtype)
        aligned[:, 0::2, ...] = image[: h - lines, 0::2, ...]
        aligned[:, 1::2, ...] = image[lines:, 1::2, ...]
        return aligned

    @staticmethod
    def _shading_columns_needed(
        frame: tuple[int, int, int, int], resolution: int
    ) -> int:
        """Predicted column count for a pass at this frame and resolution.

        Matches :meth:`calibrate_shading`'s own width formula, so ``scan()``
        can decide *before* physically taking a pass whether the reference
        already in hand will cover it, rather than discovering the mismatch
        only after the device has been read.
        """
        x0, _, x1, _ = frame
        return round((x1 - x0 + 1) * resolution / COORD_PER_INCH)

    # -- prescan and framing -----------------------------------------------

    def prescan(
        self,
        resolution: int = 300,
        frame: tuple[int, int, int, int] | None = None,
        keep_raw: bool = False,
        film: str = FILM_NEGATIVE,
        shading: bool = True,
    ) -> tuple[np.ndarray, ScanParameters]:
        """Low-resolution RGB pass over the full transport.

        This is what the vendor software runs before every frame: 300 dpi,
        three channels, 8-bit, covering the whole scan area. It carries no
        infrared -- captures confirm the prescan is always ``passes=0x80`` --
        and exists to find where the picture actually sits.

        Shading is on, like every other pass: the reference is measured in
        16-bit units and this pass is 8-bit, so applying it unscaled used to
        drive every pixel to zero -- ``apply_shading`` now scales the whole
        reference down to the pass's own depth before subtracting the dark
        half, which is the fix, not skipping the correction. A raw prescan is
        still a picture shown before calibration is applied, which is exactly
        what this driver no longer does anywhere.
        """
        image, meta = self.scan(
            resolution=resolution,
            infrared=False,
            depth=DEPTH_8,
            frame=frame or FULL_FRAME,
            # True unless the caller chose raw on purpose (`--no-shading`).
            shading=shading,
            keep_raw=keep_raw,
            # Does not change the pass -- a framing pass runs at the device's
            # own settings and meters nothing. It is carried so the entry says
            # what was in the transport.
            film=film,
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
        """Open a session nearly the way the vendor software does after power-on.

        INQUIRY, then the vendor command 0xE7, then REQUEST SENSE and a SLIDE.
        0xE7 is refused on this model as an invalid opcode, and the vendor is
        refused too -- its REQUEST SENSE is the answer to that; measured, it is
        not what lets a calibration run (`docs/protocol.md` section 11).

        The SLIDE is not the vendor's. CyberView sends `00 01 00 04`; this
        sends `00 01 00 00`, `slide`'s value left at 0 -- and a value-0
        sub-frame command does move the film. So this moves it forward from
        where the operator put it, by `param 1`'s 2.84 units if value 0 moves
        as value 4 does, and no caller -- the probes, all of them -- records
        the move. Said here rather than changed: which of the two to send is
        a change to what the device is sent.
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

    @_keeps_what_a_failed_pass_left
    def calibrate_shading(
        self,
        resolution: int = 3600,
        timeout: float = 300.0,
        keep_data: bool = False,
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
        0x0800 here, "calibrate now"). The vendor always calibrates at 3600 dpi,
        whatever resolution scans then follow, which is why that stayed the
        default here for a long time.

        ``resolution`` widens the pass so a reference can cover a scan the
        3600 dpi calibration cannot -- 7200 dpi's 10344 columns against 3600's
        5172. **No capture and no prior session has ever calibrated at
        anything but 3600 dpi.** Everything below this line is reconstructed
        from that one resolution and only verified there; a non-default value
        is exercising an untested combination and should be watched the first
        time it runs for real, not trusted on the strength of this docstring.

        Details that matter, all of which differ from an ordinary scan:

        * frame ``(0, 3431, 10343, 6888)`` -- the lower part of the transport,
          unrelated to ``resolution``: it is a physical span in the device's
          own 1/7200" coordinates, and a higher resolution only samples it
          more finely, the same way an ordinary scan frame does
        * three channels, mode depth 8-bit
        * ``SLIDE INIT`` carries ``10 01 00 00`` here, not the 0x15/0x16 second
          byte seen elsewhere
        * calibration lines come back **16-bit regardless of the mode depth**:
          2 * width + 2 bytes, so 10346 at 3600 dpi. Sizing the read from the
          mode depth reads nothing at all, which is why an earlier attempt
          drained zero bytes.
        * the vendor alternates 4-line and 72-line reads, re-reading and
          re-writing gain/offset between them
        """
        self._refuse_if_suspect("a calibration")
        self.last_failed_calibration = None
        # Every command of the calibration, with what came back -- the
        # 128-byte calibration info block, the shading descriptor, the gain
        # read-back -- kept with its bytes (`archive_calibration`).
        logger = getattr(self.t, "start", None)
        if callable(logger):
            logger()
        opened_on: State | None = None
        for _ in range(4):
            try:
                opened_on = self.read_state()
                if not opened_on.warming_up:
                    break
            # Empty is "not yet" here too, as in `scan`'s opening polls.
            except (CheckCondition, NoDataYet, ScanReadError):
                pass
            time.sleep(1)
        # What byte 8 said as the calibration began, said and kept with its
        # bytes. Not acted on: it was measured against the film once, with one
        # variable changed, and nothing corroborates it -- whoever started
        # this was asked instead (the window's box, the tools' --film-loaded).
        # It lay only in the READ STATE reply among the calibration's commands,
        # where nothing read it.
        media_loaded = None if opened_on is None else opened_on.media_loaded
        if media_loaded is False:
            self._log("note: READ STATE byte 8 says the transport is empty. "
                      "A calibration runs with the film in; if it is not, "
                      "stop and load it")
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
        # Read and write it back, as the vendor does immediately before this
        # pass. The values are not ours to choose: the device meters the
        # calibration pass itself and returns the same ~48000 light level
        # whatever is written here -- measured 2026-09-10, writing
        # 7540/5108/5108 still produced 9604/6506/6506 on all 40 blocks. A
        # `exposure_scale` parameter existed to make that testable and is gone
        # now that the answer is in: it looked effective and was not.
        #
        # Which is not the same as exposure being irrelevant to the reference.
        # It is not: a channel calibrated 3x below its scan exposure corrected
        # 13.0% -> 1.4%, one 6x below 8.2% -> 2.0%, and one 10x below got
        # WORSE, 10.0% -> 11.2%. The device simply does not let the host pick.
        self.set_gain_offset(self.get_gain_offset())

        self.set_mode(
            resolution=resolution,
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
        #
        # +1: the frame is inclusive of both endpoints, so its span is
        # x1 - x0 + 1 units. At 3600 dpi this only shows up as which way a
        # .5 rounds (5171.5 -> 5172, either way); at 7200 dpi there is no
        # rounding to hide behind and the off-by-one is exact -- 10343
        # against the 10344 every 7200 dpi pass has actually reported.
        x0, _, x1, _ = CALIBRATION_FRAME
        width = round((x1 - x0 + 1) * resolution / COORD_PER_INCH)
        #: The lines the descriptor declares, all entries together, where it
        #: could be read: see the check after the read.
        lines_declared: int | None = None
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
            lines_declared = sum(int(e.get("lines", 0) or 0) for e in parms)
            self._log(
                f"shading descriptor: {len(parms)} entries, "
                f"{lines_declared} lines declared, "
                f"{width} columns"
            )
        bpl = 2 * width + INDEX_HEADER

        self.start_scan()
        drained = 0
        collected: list[bytes] = []
        # Whether the scanner said it had finished. Anything that ends the
        # read before then leaves the device mid-scan.
        ended = False
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
                except EndOfData:
                    # "No more lines", ASC 0x20: the only refusal that says
                    # the pass is over. Any other -- NOT READY, a one-shot
                    # UNIT ATTENTION, a sense that could not be read -- is not
                    # the scanner finishing, and used to be taken for it: the
                    # device was left mid-calibration and driven on, and the
                    # blocks read so far became the reference. It now leaves
                    # this loop as it came, as a lost read does, and the
                    # device suspect (below).
                    self._log(f"  scanner finished after {blocks} blocks")
                    ended = True
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
                # From the last block, not from the first read: the pass is
                # given up only once it has gone silent, as `read_planes`
                # gives up a pass. Counted from the start, a calibration still
                # sending at 300 s was abandoned mid-read -- the wedge -- for
                # being slow rather than for having stopped.
                deadline = time.monotonic() + timeout
            if not ended:
                # The scanner went silent without saying it had finished.
                # Building a reference from what arrived would be a partial
                # calibration passed off as a whole one, and the read it
                # leaves is an abandoned one.
                self._mark_suspect(f"the calibration sent nothing for "
                                   f"{timeout:.0f} s after {blocks} blocks")
                raise ScanReadError(
                    f"calibration went {timeout:.0f} s without a block after "
                    f"{blocks} and never said it had finished; no reference "
                    "was built from it")
            # This calibration's own width, not the module constant: the two
            # only coincide because every calibration before this one ran at
            # 3600 dpi. A wider pass needs a wider mask read to match.
            try:
                mask: bytes | None = self.get_ccd_mask(width)
            except ScanReadError as exc:
                # Refused after the scanner said it had finished, so nothing
                # is outstanding -- and every pass reads its own mask, which
                # is the one a correction uses. This one is only kept with the
                # calibration's bytes, and losing it cost the whole
                # calibration: 3-4 minutes, and the bytes with it.
                self._log(f"the calibration's CCD mask was refused ({exc}); "
                          "its lines are kept without it")
                mask = None
        except BaseException as exc:
            if not ended:
                self._mark_suspect(f"{type(exc).__name__} during the calibration "
                                   f"read: {exc}")
            # The lines that did arrive, for `ensure_shading` to archive
            # tagged failed. A calibration that raised -- part way through
            # its read, or after it -- dropped them, and they are the only
            # evidence of what the device sent.
            self.last_failed_calibration = {
                "data": b"".join(collected), "bytes_drained": drained,
                "pixels_per_line": width, "bytes_per_line": bpl,
                "resolution": int(resolution),
                "measured_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                              time.gmtime()),
                "failed": {"stage": ("after the read" if ended
                                     else "during the read"),
                           "error": f"{type(exc).__name__}: {exc}"},
            }
            raise
        finally:
            self.finish_scan()

        stopper = getattr(self.t, "stop", None)
        commands = stopper() if callable(stopper) else None
        data = b"".join(collected)
        measured_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        # The point of the pass. The scanner measured its per-column response
        # and handed it back; it does not apply it, so a calibration whose
        # result is discarded genuinely changes nothing in the image.
        reference = calculate_shading(data, width)
        # Held to the device's own count and to its two phases, channel by
        # channel (`calibration_shortfall`): end of data early looks, to the
        # loop above, exactly like the real end. The lines are kept all the
        # same (`archive_calibration`).
        arrived = len(data) // bpl
        shortfall = self.calibration_shortfall(reference, arrived, lines_declared)
        if shortfall is None and reference is not None:
            self._shading = reference
            self._shading_origin = {
                "action": "calibrated", "resolution": int(resolution),
                "width": int(width), "measured_utc": measured_utc,
            }
            if mask is not None:
                self._ccd_mask = mask
            self._log(
                f"shading reference: {width} columns, channels "
                f"{reference.channels}, means "
                f"{[round(reference.mean[c], 1) for c in reference.channels]}"
            )
        else:
            # Not taken, and nothing it would have replaced is lost: a
            # calibration that ended after its dark lines used to become the
            # session's reference -- the dark level averaged as a one-point
            # "light" one, cached over the good file for every later --reuse
            # -- and one that ended with no lines at all left the session
            # none, whatever it had before.
            reference = None
            self._log(f"calibration incomplete after {blocks} blocks: "
                      f"{shortfall}; no reference was taken from it")

        # The calibration pass moved the carriage, so a READ STATE taken before
        # it no longer says where the carriage is. See `carriage_record`.
        self._calibrated_since_state = True
        return {
            "shading_calibration": True,
            "data": data if keep_data else None,
            "reference": reference,
            # Why `reference` is None, when it is; None for a whole one. The
            # one reason, under both names a reader may look for it by.
            "incomplete": shortfall,
            "measured_utc": measured_utc,
            # Byte 8 of the READ STATE it began with, as `media_loaded` reads
            # it; None when no READ STATE was answered.
            "media_loaded": media_loaded,
            "ccd_mask": mask,
            "bytes_per_line": bpl,
            "pixels_per_line": width,
            "bytes_drained": drained,
            "duration_s": round(time.monotonic() - started, 1),
            "resolution": int(resolution),
            "commands": commands,
            "lines_declared": lines_declared,
            "lines_arrived": arrived,
            "refused": shortfall,
        }

    @staticmethod
    def calibration_shortfall(
        reference: ShadingReference | None,
        arrived: int | None = None,
        declared: int | None = None,
    ) -> str | None:
        """Why a calibration's lines cannot stand as a reference, or None.

        The pass is two phases, unlit and then lit, in every channel:
        measured here at ~170-200 counts against ~47,000, 160 lines in 40
        blocks (`docs/shading-calibration-plan.md`), and a pass that stopped
        short stopped in the dark phase, the one that comes first. Its
        reference is the dark floor, divided into every scan after it and
        each filed as corrected. Two things are known well enough to hold it
        to, and it is held to both:

        * the device's own count, ``declared`` against the ``arrived`` lines.
          It declares one phase's lines (4 x 20) and sends both, about 160,
          so fewer than it declares is a calibration that ended in its dark
          phase.
        * its two phases, channel by channel, because the count alone cannot
          see an end of data exactly at the phase boundary: as many lines as
          declared, every one of them dark, and no level gap to split. Per
          channel, not ``reference.two_point``, which one channel splitting
          already makes true: cut off after red's first lit line, green and
          blue are still the dark floor alone. Red, green and blue must be
          there, and every channel that came back must split -- infrared
          too, which every calibration on record returned with a dark phase
          of its own (`docs/vignette-plan.md`). A channel that did not is
          one whose lit lines never came -- or, never seen, a device that
          sent only lit ones, where refusing costs a recalibration and not
          a roll divided by 170 counts.

        Every reason that applies is given, not only the first.
        """
        if reference is None:
            return "no calibration lines it could read"
        why = []
        if declared and arrived is not None and arrived < declared:
            why.append(f"{arrived} lines arrived where the descriptor declared "
                       f"{declared}; a reference from them would be the dark "
                       "phase, or part of it")
        missing = [name for channel, name in enumerate(("red", "green", "blue"))
                   if channel not in reference.ref]
        if missing:
            why.append(f"no {' or '.join(missing)} lines came back")
        unlit = [c for c in reference.channels if c not in reference.dark]
        if unlit:
            names = ", ".join("RGBI"[c] if c < 4 else str(c) for c in unlit)
            why.append(f"channel{'s' if len(unlit) > 1 else ''} {names} did "
                       "not split into a dark and a lit phase; a reference "
                       "from them would be one phase alone, most likely the "
                       "dark")
        return " -- and ".join(why) or None

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
        shading: bool = True,
        role: dict[str, Any] | None = None,
    ) -> list[float]:
        """Find per-channel exposure scales by probing at low resolution.

        ``role`` is added to each probe's ``pass_role``: the roll and frame
        it metered for, where a roll does the metering.

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
        for the infrared scan.

        Worth knowing how far that goes, from the three single-stock captures
        (`docs/protocol.md`): CyberView meters a colour negative's blue *below*
        the device's own base -- 5834 against 6506 -- meters black and white
        high on all three channels, and **does not meter a slide at all**,
        scanning it at `9604, 6506, 6506` on every pass. This driver meters
        everything. That is a deliberate divergence, not an oversight: metering
        a negative per channel is what takes the mask off before the ADC
        instead of quantising the blue record through it. Blue on this scanner carries no fixed column
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
        # Before the gain read and write below, which went to a suspect device
        # ahead of the first probe's refusal.
        self._refuse_if_suspect("metering")
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

        # The exposure every scale is relative to, read once and written back
        # before each round. On this device READ GAIN/OFFSET returns a fixed
        # reference rather than what was last written -- the exposure fields
        # hold 9604/6506/6506/7745 however different the value just written,
        # see `scan_roll` -- so the scales cannot compound either way; reading
        # it once and writing it back is what keeps that true if the device
        # ever did echo, rather than a premise the arithmetic leans on.
        base = self.get_gain_offset()
        self._log(
            f"auto-exposure: film={film}, "
            f"{'locked (one factor for R/G/B)' if locked else 'per channel'}"
        )

        # One further round is allowed, and spent only on a clipped channel --
        # see the ``rounds``/``max_rounds`` note in the docstring.
        budget = max(rounds, rounds + 1 if max_rounds is None else max_rounds)
        probes: list[dict[str, Any]] = []
        #: Where the film is, found on the first probe while it is still dark.
        region: tuple[slice, slice] | None = None
        #: The same, as a record: rows and columns of the probe, and the
        #: probe's size, so a pass at another resolution can be read over it.
        region_record: dict[str, list[int]] | None = None

        for round_no in range(1, budget + 1):
            self.set_gain_offset(base)
            asked = list(scales)
            self._pass_role = dict(role or {}, kind="metering probe",
                                   round=round_no)
            image, _ = self.scan(
                resolution=resolution,
                infrared=False,
                exposure_scale=scales,
                # The film in the transport, so a probe's entry says what it
                # measured. It changes nothing sent: the probe is RGB, and it
                # meters nothing itself. Every probe was filed "negative".
                film=film,
                # As the pass being metered: a scan taken raw on purpose is
                # metered raw, rather than its probe asking for a correction
                # the session has no reference for.
                shading=shading,
                # CLAUDE.md's rule is "file every scan, with its raw bytes",
                # without an exception for throwaway passes -- and a metering
                # probe is only throwaway until someone asks what it saw. At
                # 300 dpi it costs about 1.3 MB.
                keep_raw=True,
            )
            # Inside the film, not the whole window. The empty aperture is far
            # brighter than any part of the picture, so metering the whole frame
            # lets however much of it is in view decide the exposure -- measured
            # at 5.6-10.0% short on real prescans.
            #
            # Measured once, on this first pass, and reused. The detector needs
            # the aperture to be twice the median, and metering's whole job is
            # to brighten the film until it is nearly as bright as the aperture
            # -- so by the round that settles the exposure the contrast it
            # depends on is gone. Detecting each round would quietly stop
            # working exactly when it mattered.
            if region is None:
                region = metering_slice(image)
                rows, cols = image.shape[:2]
                region_record = {
                    "rows": list(region[0].indices(rows)[:2]),
                    "cols": list(region[1].indices(cols)[:2]),
                    "of": [int(rows), int(cols)],
                }
            crop = image[region]
            levels = [
                float(np.percentile(crop[..., c], percentile)) / full
                for c in range(crop.shape[2])
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
            # 0.80 moved the top of that band to 0.88 -- past the knee at
            # CLIP_START -- and a B&W frame duly landed at 87%
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
            # Where every round was read. Detected once, on the first probe
            # (see above), and otherwise lost: a caller that reads the passes
            # it meters had to detect it again on each, and could judge two
            # rungs of one ladder over different pixels.
            "region": region_record,
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

    @_keeps_what_a_failed_pass_left
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
        fast_infrared: bool = True,
        slide_init_param: int = 0x16,
        metering: dict[str, Any] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        """Run one scan and return ``(image, metadata)``.

        The command order follows the vendor software's, recovered from a USB
        capture, where it is known to be load-bearing: :meth:`cmd_17` must
        follow the scan frame, or the scanner refuses to skip shading analysis
        and the scan cannot complete. See the README. It is not the vendor's
        order throughout -- gain and offset come after the frame, COPY and
        PARAM between SCAN and the first READ -- and `docs/protocol.md`
        section 8 lists where it differs.

        ``film`` reaches auto-exposure, and only auto-exposure: it decides
        whether the visible channels are metered together or apart. Getting it
        wrong on a slide takes the cast off the picture -- see
        :func:`locks_white_balance`.

        ``shading`` applies this session's shading reference, which is what
        removes the vertical striping. The scanner measures its per-column
        response but returns raw pixels, so the session calibrates once, up
        front, as the vendor does at power-on (`ensure_shading`). This method
        never returns a pass it was asked to correct uncorrected, and never
        calibrates inside one either -- that path stalled the device. Where the
        reference is missing or too narrow for this pass it raises
        :class:`~rps7200.protocol.ShadingUnavailable` (`uncalibrated`), and it
        raises *before* sending anything -- metering included -- so a refusal
        costs no scanner time. Pass ``shading=False`` to accept raw pixels on
        purpose.

        **A 7200 dpi pass cannot be corrected at all on this hardware** and is
        refused outright: the device's calibration will not produce a
        reference past :data:`MAX_SHADING_COLUMNS` columns whatever resolution
        it is asked for, and a 7200 dpi frame is twice that. See
        `docs/7200dpi-plan.md`.

        ``fast_infrared`` **ties the infrared plane's cost to the resolution
        asked for, and is on by default.** Without it an infrared pass costs
        ~220 s at every resolution -- a floor, indifferent to the line count.
        With it the floor is not spent at all and the pass costs what its lines
        cost: `7.5 s + 59.9 ms/line`, measured across five resolutions and
        fitting each to within 0.16 s.

        So the saving is whatever the floor was worth there -- 89% at 300 dpi,
        50% at 1800, and nothing above about 3700 dpi, where the line count has
        already overtaken the floor. Quality was measured at 1800 dpi on colour
        negative and 3600 on slide, eleven passes: the picture, the infrared
        plane and the dust it carries are all unchanged.

        Pass ``False`` for the fixed-cost pass. Worth knowing either way: the
        bit shifts where the carriage starts by a line or two, so passes taken
        with and without it are not pixel-aligned with each other.

        **CyberView sends it in none of 3,955 captured commands**, so this is
        the one field here with no capture behind it -- `tests/test_fast_infrared.py`
        holds the payload byte by byte instead. See `docs/fast-infrared-plan.md`.

        ``metering`` is the `last_metering` of a caller that metered this pass
        itself and hands over its ``exposure_scale`` -- a roll meters each
        frame before scanning it. The pass is then recorded as metered, with
        that record, exactly as one that set ``auto_exposure``.

        ``should_stop`` is asked once, after metering and before the pass's
        first command, and a yes raises `StoppedBeforePass`: a Ctrl-C through
        metering's probes was otherwise followed by the whole pass it was
        pressed to prevent.
        """
        # What the caller took this pass for (`_pass_role`), taken now so that
        # a pass refused below cannot leave it to describe the next one.
        role, self._pass_role = self._pass_role, None
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
            raise self.infrared_blind(film)

        if frame is None:
            frame = FULL_FRAME

        if shading:
            # Never fall through to raw pixels for lack of a reference wide
            # enough, and never find out after the pass: both checks are made
            # before anything is sent -- before metering too, whose probes
            # would otherwise be spent on a pass that is then refused. Finding
            # out afterwards spends 5.5 minutes at 7200 dpi to learn what is
            # knowable here.
            needed = self._shading_columns_needed(frame, resolution)
            if not self.correctable_at(resolution, frame):
                # No resolution argument can widen the reference past this --
                # measured on the device, see MAX_SHADING_COLUMNS. Calibrating
                # would cost two minutes and return the same 5172 columns.
                raise self.uncorrectable(resolution, frame)
            if self._shading is None or needed > self._shading.pixels_per_line:
                reason = (
                    "no shading reference in this session" if self._shading is None
                    else f"the reference covers {self._shading.pixels_per_line} "
                         f"columns and this pass needs {needed}"
                )
                # Refused, not calibrated here. A calibration started inside a
                # pass used to be the fallback, and it is the one path recorded
                # as stalling the device: measured twice, `bulk read of 16384
                # bytes failed after 0 bytes: LIBUSB_ERROR_PIPE` right after the
                # shading descriptor, and the scanner stopped answering. The
                # same calibration asked for up front -- `ensure_shading`, the
                # window's Calibrate, the tools -- works. It was also reached
                # without anyone choosing it: `--no-shading` on a roll, a scan
                # queued behind a calibration that failed, a metering probe.
                raise self.uncalibrated(reason)

        # Here, before the first command, and not left to SLIDE INIT: a pass
        # on a device an earlier one was abandoned in used to send READ STATE,
        # the lamp wait, both sub-command ladders, the frame, gain and offset
        # and MODE SELECT -- metering's probes too -- before that refused it.
        self._refuse_if_suspect("a scan")

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
                target=exposure_target, infrared=infrared, film=film,
                shading=shading,
            )
            self._log(
                f"auto-exposure: {[round(v, 3) for v in exposure_scale]}"
            )

        # Between the probes and the pass, where stopping abandons nothing:
        # the probes are read to their last line, and nothing of this pass
        # has been sent.
        self._stop_before_pass(should_stop, metered=auto_exposure)

        # From here to the last line read is this pass: recorded, so the entry
        # can say exactly what it was sent. After metering on purpose -- the
        # probes are passes of their own, each with its own record.
        started_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        logger = getattr(self.t, "start", None)
        if callable(logger):
            logger()
        # Nothing a pass before this one left may describe it -- a metering
        # probe's included. `read_planes` cleared `last_raw` only once a read
        # completed, so a pass that raised before its last line, or before
        # its read at all, left the previous pass's bytes for the next
        # `capture_record` to hand over as this one's.
        self.last_raw = None
        self.last_raw_layout = None
        self.last_pixels_raw = None
        self.last_scan_meta = None
        self._in_flight = None

        # Open with READ_STATE polling, as the vendor software does.
        for _ in range(4):
            try:
                if not self.read_state().warming_up:
                    break
            # An empty answer too: `position()` already takes it as "not yet",
            # and the READ STATE after a film move comes back empty every
            # time, so a hold's prescan 0.4 s after its nudge failed the frame
            # here over a status query the next poll would have answered.
            except (CheckCondition, NoDataYet, ScanReadError):
                pass
            time.sleep(1)
        self.wait_warm()
        self.test_unit_ready()

        if require_media:
            try:
                state: State | None = self.read_state()
            except (CheckCondition, NoDataYet, ScanReadError):
                state = None             # a note, not worth the pass
            if state is not None and not state.media_loaded:
                # Reported, not enforced: byte 8 was measured against the film
                # once, with one variable changed, and no capture can
                # corroborate it -- every one was taken with film in. This
                # printed byte 6 and called it unreliable, which it is; the
                # byte the driver reads is 8, and it is what said empty.
                self._log(
                    f"note: READ STATE byte 8 reads {state.busy:#04x}, which "
                    "has meant an empty transport; not enforced, since "
                    "nothing corroborates it -- continuing"
                )

        self.set_exposure_time()
        self.set_highlight_shadow()

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
        # Gated on `infrared` here rather than left to each caller. The bit
        # governs how the infrared plane is acquired, so on an RGB pass there
        # is nothing for it to govern -- and its behaviour there is simply
        # unmeasured: `docs/fast-infrared-plan.md` characterises it on RGBI
        # passes and says nothing about RGB. `tools/scan.py` and
        # `tools/scan_roll.py` each cleared it locally; the window did not, and
        # `gui-settings.json` persists the box ticked, so an ordinary RGB scan
        # from it was sending an untested combination. One gate here closes all
        # three callers, which is why the tools' own guards are now redundant
        # rather than load-bearing.
        fast_infrared = bool(fast_infrared and infrared)
        self.set_mode(
            resolution=resolution,
            passes=passes,
            depth=depth,
            color_format=FORMAT_INDEX,
            skip_shading=skip_shading,
            byte14=byte14,
            fast_infrared=fast_infrared,
        )
        self.test_unit_ready()

        self.slide(SLIDE_INIT, param=slide_init_param)
        self.wait_ready()

        # Taken at the last moment the carriage cannot have moved since: the
        # READ STATE polled above, marked stale if a calibration ran after it.
        carriage = self.carriage_record()
        self.last_read_direction = None
        mode = {"byte14_override": byte14, "skip_shading": bool(skip_shading),
                "slide_init_param": int(slide_init_param),
                "depth": int(depth), "passes": int(passes)}
        # What this pass is, for the record of one that is read in full and
        # then fails (`_keeps_what_a_failed_pass_left`). The meta below says
        # it again, and more, for one that does not.
        flight: dict[str, Any] = {"meta": {
            "resolution_dpi": resolution, "channels": channels, "film": film,
            "protocol_revision": PROTOCOL_REVISION,
            "channel_order": list(CHANNEL_ORDER[:channels]),
            "depth": 16 if depth == DEPTH_16 else 8, "frame": list(frame),
            "exposure": settings.exposure, "gain": settings.gain,
            "offset": settings.offset, "fast_infrared": bool(fast_infrared),
            "started_utc": started_utc, "mode": mode,
            "carriage_state": carriage,
        }}
        if role is not None:
            flight["meta"]["pass_role"] = role
        # Whether it asked to be corrected: a pass taken raw on purpose is
        # still that when it fails (`_keep_failed_pass`).
        flight["shading"] = bool(shading)
        self._in_flight = flight
        started = time.monotonic()
        try:
            image, params, ccd_mask = self._read_pass(
                channels, keep_raw, resolution,
                idle_timeout=self.read_idle_s(infrared, fast_infrared))
        finally:
            stopper = getattr(self.t, "stop", None)
            commands = stopper() if callable(stopper) else None
            flight["commands"] = commands

        # After the scan has settled, never inside it: the vendor polls
        # READ_STATE for several seconds once the last line is read and only
        # then moves the film.
        if advance:
            self.advance()

        # Recorded when applied: `scan.tif` then holds the decode *and* this,
        # and `library.decode_raw`/`reconstruct` replay it from the record.
        # Unrecorded, every 7200 dpi entry read "decode CHANGED" for ever, and
        # the session's shape guard took the 4 missing rows for another
        # pass's bytes and dropped them.
        stagger_realigned = 0
        if resolution == self.NATIVE_COLUMN_STAGGER_DPI:
            before = image.shape[0]
            image = self._realign_native_column_stagger(image)
            stagger_realigned = self.NATIVE_COLUMN_STAGGER_LINES
            if "pixels" in flight:
                flight["pixels"] = image
                flight["meta"]["stagger_realigned"] = stagger_realigned
            self._log(
                f"realigned {self.NATIVE_COLUMN_STAGGER_LINES}-line native "
                f"column stagger: {before} -> {image.shape[0]} lines"
            )

        # What the scanner sent, before flat-fielding. The library stores this
        # and not the corrected pixels: the reference is stored beside it, so
        # the corrected image is always recomputable, while a corrected file
        # cannot be un-corrected if the correction later turns out to be wrong.
        # Callers still get the corrected image -- see the return below.
        raw_pixels = image
        shading_report = None
        # Set only for a deliberate `shading=False` pass -- library.save reads
        # it as "raw on purpose", distinct from `shading_report`, which means a
        # correction happened. `shading=True` no longer has a soft-failure
        # path: see the raise below. It used to, and that silence was the bug
        # -- six 3600 dpi RGBI frames were filed uncorrectable on 2026-09-09
        # because a restarted session had no reference and every scan simply
        # logged one line and carried on. Half an hour of scanning, and nothing
        # in the entries said the correction had been wanted.
        shading_skipped = None
        if shading and self._shading is not None and params.width > self._shading.pixels_per_line:
            # Belt and braces. The width was checked against the frame before
            # the pass ran, so reaching here means the device produced a wider
            # pass than its own frame and resolution implied. Refuse anyway:
            # correcting half a frame is worse than correcting none, and
            # returning either silently is the bug this replaced.
            raise ShadingUnavailable(
                f"this pass came back {params.width} columns, wider than the "
                f"{self._shading_columns_needed(frame, resolution)} its frame "
                f"and resolution implied, and past the reference's "
                f"{self._shading.pixels_per_line}. Pass shading=False to "
                "accept raw pixels deliberately."
            )
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
            # The check before the pass makes this unreachable; kept so a
            # correction that was asked for can never quietly not happen.
            raise ShadingUnavailable(
                "no shading reference for this pass. Pass shading=False to "
                "accept raw pixels deliberately."
            )
        else:
            shading_skipped = SHADING_SKIPPED_EXPLICIT

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
            "height": int(image.shape[0]),
            # The lines GET PARAMETERS declared, a plane each, and whether
            # the device ended the read on "end of data" before they were
            # in. Such a pass decodes to fewer rows and was filed as an
            # ordinary one: without its raw bytes, whose layout alone held
            # the declared count, nothing in the entry said it was short.
            "lines_declared": int(params.lines),
            "short_read": bool(getattr(self, "last_read_short", False)),
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
            "exposure_metered": bool(auto_exposure) or metering is not None,
            # Recorded on every pass, not only the ones that set it. A ladder
            # is six passes of one frame differing in nothing else, so an
            # entry that does not say which side it came from is not evidence.
            "fast_infrared": bool(fast_infrared),
            "duration_s": round(time.monotonic() - started, 1),
            # Which way the carriage read this pass, from its own line tags,
            # and whether the rows were turned upright -- `decode_index`.
            "read_direction": (self.last_read_direction.as_record()
                               if self.last_read_direction is not None else None),
            # Rows the native column-stagger realignment trimmed, 0 when none
            # ran. The one host transform `scan.tif` carries beyond the decode.
            "stagger_realigned": stagger_realigned,
            # When the pass began, in UTC. An entry's id and `created` are when
            # it was filed, which for a spooled or queued pass is later.
            "started_utc": started_utc,
            # What defined the pass and was otherwise spent and forgotten: the
            # mode choices, and every command sent with what came back.
            "mode": mode,
            "commands": commands,
            # Where this pass's shading reference came from, and when --
            # whenever there is one in force, applied or not: a pass taken raw
            # on purpose is filed with it all the same (`capture_record`), and
            # its entry said nothing of whether that was this session's
            # reference or a cache from weeks before.
            "shading_origin": (dict(origin)
                               if self._shading is not None
                               and (origin := self._shading_origin)
                               else None),
            # The READ STATE taken before the pass, kept as evidence of where
            # the carriage was. `carriage_record` says why nothing acts on it.
            "carriage_state": carriage,
            # The sub-frame moves since the pass before (`nudge`): what put
            # the film where this pass saw it.
            "moves_before": self._take_moves(),
        }
        # Only for a scan that did its own metering. The probe passes inside
        # auto_exposure() are scans too, and attaching this to them would file
        # the *previous* frame's metering against them.
        if auto_exposure and self.last_metering is not None:
            meta["metering"] = self.last_metering
        elif metering is not None:
            # Metered by the caller, and so every roll frame: they were filed
            # `exposure_metered: false` with no block at all, which is what
            # a commanded exposure looks like -- and `signature` then took
            # an outcome of metering for the request.
            meta["metering"] = metering
        # A pass only debug filing keeps -- a metering probe, a verification
        # prescan -- says what it was for, where nothing else would: filed
        # tagged `debug` and nothing more, it could not be told from any other
        # pass, nor matched to the frame it served.
        if role is not None:
            meta["pass_role"] = role
        # The raw pixels, not the corrected ones: see `raw_pixels` above.
        self._debug_capture(raw_pixels, meta)
        # Done: the pass did not fail, and its bytes are not held twice.
        self._in_flight = None
        # For a caller that files this pass itself rather than through debug
        # filing. Set on every pass, so it is never a stale leftover the way
        # `last_raw` once was -- but it describes the pass that *just* ran, so
        # read it immediately. `scan_roll` copies it onto the frame instead of
        # reading it later, because a frame's prescan runs several passes
        # before its image.
        self.last_pixels_raw = raw_pixels
        # Same contract as `last_pixels_raw`: this pass, read it now. It exists
        # for `prescan()`, which returns ScanParameters rather than meta, and
        # whose callers were hand-building a meta instead -- which is how every
        # prescan came to be filed describing itself wrongly.
        self.last_scan_meta = dict(meta)
        return image, meta

    def _read_pass(
        self, channels: int, keep_raw: bool, resolution: int,
        idle_timeout: float | None = None,
    ) -> tuple[np.ndarray, ScanParameters, bytes]:
        """START SCAN, read every line of the pass, and settle it.

        Returns the decoded pixels, the pass's parameters and its CCD mask. A
        pass that fails before its last line is in leaves the device mid-scan,
        and marks it so (`suspect`): nothing may drive it again until it has
        been power-cycled.
        """
        self.start_scan()
        # Set by `read_planes` once every byte of the pass is in; an exception
        # before that leaves the device mid-scan, one after it does not.
        self._read_complete = False
        try:
            self.wait_ready()
            # Read per pass, not once: the mask marks which CCD pixels *this*
            # pass samples, which is what keeps the shading columns aligned at
            # reduced resolutions. Sized from the active calibration's own
            # width -- they only coincide with CCD_MASK_SIZE because every
            # calibration before this one ran at 3600 dpi.
            mask_size = (
                self._shading.pixels_per_line
                if self._shading is not None else CCD_MASK_SIZE
            )
            ccd_mask = self.get_ccd_mask(mask_size)
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
            # Debug filing keeps every pass's own bytes, whatever the caller
            # asked for: a pass it files has to carry the bytes it came from,
            # and the spool is on disk, so the cost is one pass in memory.
            image = self.read_planes(
                params, channels, idle_timeout=idle_timeout,
                keep_raw=keep_raw or bool(getattr(self, "debug", False)))
        except BaseException as exc:
            # Deliberately no STOP SCAN. The vendor software never sends it,
            # and issuing it here reliably leaves the scanner unresponsive to
            # the next session, needing a power cycle. Settling the bridge is
            # enough to leave things usable.
            self._scanning = False
            if not self._read_complete:
                # A timeout, a refused read, a Ctrl-C: the pass was abandoned
                # with lines still to come. Nothing may drive the device now.
                self._mark_suspect(f"{type(exc).__name__} during a {resolution} "
                                   f"dpi pass: {exc}")
            raise
        else:
            self.finish_scan()
        return image, params, ccd_mask

    #: Consecutive frames that fail to reach their approved position before
    #: the loop stops trying for the rest of the roll. The same shape as
    #: `max_failures`: a fault that repeats is a fault with the setup.
    HOLD_GIVE_UP_FRAMES = 3

    #: How long to let the transport settle after a sub-frame move before
    #: looking again. A class attribute so a stand-in can run this same loop
    #: without waiting out real seconds it is only pretending to need.
    HOLD_SETTLE_S = 0.4

    def _hold_to_approved(
        self,
        index: int,
        image: np.ndarray,
        prescan_resolution: int,
        approved: Any,
        keep_raw: bool = False,
        should_stop: Callable[[], bool] | None = None,
        rejudge: Callable[[np.ndarray], tuple[bool, str]] | None = None,
        source: str = "operator",
        shading: bool = True,
        film: str = FILM_NEGATIVE,
        roll: str | None = None,
    ) -> dict[str, Any]:
        """Move the film until this frame sits where it was decided to go.

        Usually the reference is not a measurement -- it is the picture the
        operator looked at in the contact sheet and accepted -- and then this
        never decides where the frame *should* be; it only asks whether it is
        still where he left it, and closes the gap if not. `_aim_frame` passes
        a target the ensemble measured instead, which is the one case where the
        reference did come from a detector; `source` says which, so that a log
        line never claims the operator asked for something he did not.

        ``rejudge`` is looked at after each verification pass and may only
        **stop**. It never re-aims, because `target` staying a constant of the
        frame is exactly what makes `hold_plan`'s no-limit-cycle argument hold:
        a loop that re-aims on its own noise can chase itself.

        It iterates, because a backward offset spends its first command on
        backlash: the transport advances
        forward between frames, so it enters each one loaded forward, and a
        move the other way loses two to three commands before anything happens.
        One shot would report that as a failure. Two converge.

        It never reverses within a frame, never exceeds a travel budget, and
        caps at :data:`~rps7200.framing.MAX_HOLD_MOVES` moves. Whatever
        happens, the frame is scanned: the outcome is recorded, not enforced.

        The target is never negated. It is a distance in the *picture*,
        measured against the reference by `measure_shift_mm`, so the loop is
        closed where the operator looked and the transport's physical sense
        does not enter it. It used to take the window's "reverse the direction"
        tick, which is for hand moves, and negate the target with it: on a
        transport that goes the way the code assumes, every frame of the roll
        was driven to the mirror of where he put it and reported `held`, and on
        one that did not, the first move tripped `wrong_way`. An inverted
        transport is what the direction check below exists to catch.
        """
        target = approved.offset_mm
        out: dict[str, Any] = {
            "target_mm": round(target, 4), "outcome": "held", "moves": 0,
            "spent_mm": 0.0,
            "history": [], "prescan": None, "roll_abort": None,
            "source": source, "clamped": False,
            # Every SLIDE this hold sent, in units of `param`
            # (`move_record`): `history` holds only what was measured, and
            # the moves were a log line.
            "moves_sent": [],
            # What every measurement in `history` was taken against. An
            # operator's reference is a walk's prescan, filed as its own
            # entry; named nowhere, the confidences could not be recomputed.
            "reference_entry": (str(entry) if (entry := getattr(
                approved, "reference_entry", None)) else None),
            "reference_shape": (list(shape) if (shape := getattr(
                approved.reference, "shape", None)) is not None else None),
        }
        try:
            return self._hold_loop(index, image, prescan_resolution, approved,
                                   out, keep_raw=keep_raw,
                                   should_stop=should_stop, rejudge=rejudge,
                                   source=source, shading=shading, film=film,
                                   roll=roll)
        except BaseException as exc:
            # A verification pass that raised took with it the only record
            # of the moves already sent: the film has moved, and the frame's
            # marks said nothing about how. Carried on the exception for the
            # roll to put in the failed frame's marks.
            setattr(exc, "hold", {k: v for k, v in out.items()
                                  if k != "prescan"})
            raise

    def _hold_loop(
        self, index: int, image: np.ndarray, prescan_resolution: int,
        approved: Any, out: dict[str, Any], *, keep_raw: bool,
        should_stop: Callable[[], bool] | None,
        rejudge: Callable[[np.ndarray], tuple[bool, str]] | None,
        source: str, shading: bool, film: str, roll: str | None = None,
    ) -> dict[str, Any]:
        """`_hold_to_approved`'s loop, filling ``out`` as it goes."""
        target = approved.offset_mm
        measured, detail = measure_shift_mm(approved.reference, image)
        out["history"].append(detail)
        spent = 0.0
        direction = 0

        while True:
            want, outcome = hold_plan(target, measured, spent, direction,
                                      out["moves"])
            out["outcome"] = outcome
            if want is None:
                break
            if should_stop is not None and should_stop():
                out["outcome"] = "stopped"
                break

            before = measured
            asked = self.nudge(want)
            out["moves_sent"].append(self.move_record(asked))
            out["clamped"] = out["clamped"] or bool(asked.get("clamped"))
            delivered_mm = asked["asked_mm"] * (1 if want > 0 else -1)
            spent += abs(delivered_mm)
            direction = 1 if want > 0 else -1
            out["moves"] += 1
            out["spent_mm"] = round(spent, 4)

            time.sleep(self.HOLD_SETTLE_S)
            # What this pass is and which frame it served, and the roll's
            # film. This pass replaces the frame's prescan, and a walk files
            # it as that prescan's entry: without the film every frame a hold
            # or an aim moved was recorded as a colour negative, and filed by
            # debug filing alone it said "negative" and nothing else.
            self._pass_role = {"kind": "verification prescan", "for": source,
                               "roll": roll, "roll_index": index,
                               "move": out["moves"]}
            image, _ = self.prescan(resolution=prescan_resolution,
                                    keep_raw=keep_raw, shading=shading,
                                    film=film)
            out["prescan"] = image
            measured, detail = measure_shift_mm(approved.reference, image)
            out["history"].append(detail)

            if out["moves"] == 1 and measured is not None and before is not None:
                # The one free check on direction. If the film went the other
                # way, the transport's sense is not what this roll assumed and
                # every frame after this would be driven wrong -- so this stops
                # the roll correcting, not just this frame. It is a measurement
                # of direction, not a second opinion about his number.
                went = measured - before
                if abs(went) > HOLD_TOLERANCE_MM and (went > 0) != (want > 0):
                    out["outcome"] = "wrong_way"
                    out["roll_abort"] = (
                        f"frame {index + 1}: asked for {say_units(want)} and "
                        f"the film went {say_units(went)}. The direction is "
                        "inverted, so "
                        "every frame would be driven the wrong way -- holding "
                        "is off for the rest of this roll."
                    )
                    self._log(out["roll_abort"])
                    break

            if rejudge is not None:
                keep, why = rejudge(image)
                if not keep:
                    # Not a disagreement about the distance -- the loop does
                    # not take a second opinion on that. This is the frame no
                    # longer looking like the one that was judged at all.
                    out["outcome"] = "abandoned"
                    out["abandoned"] = why
                    self._log(f"frame {index + 1}: {why}")
                    break

        final = measured
        out["final_mm"] = None if final is None else round(final, 4)
        out["residual_mm"] = (None if final is None
                              else round(target - final, 4))
        out["confidence"] = out["history"][-1].get("confidence")
        out["dy"] = out["history"][-1].get("dy")
        # Which way up the prescans read. It matters downstream and nothing
        # else can tell: `reversal_against` compares a scan against its own
        # prescan and cannot say which of the two reversed, so it blames the
        # scan. Here there is a third picture -- the approved reference -- and
        # it settles the question for free.
        out["row_reversed"] = any(h.get("row_reversed")
                                  for h in out["history"])
        self._log(
            f"frame {index + 1}: {source} {say_units(target)} -> "
            f"{out['outcome']}"
            + (f", now {say_units(final)} after {out['moves']} move(s)"
               if final is not None else ", not verified")
        )
        return out

    def _aim_frame(
        self, index: int, image: np.ndarray, prescan_resolution: int,
        walk: Any, *, dry_run: bool = False, keep_raw: bool = False,
        should_stop: Callable[[], bool] | None = None,
        shading: bool = True, film: str = FILM_NEGATIVE,
        roll: str | None = None,
    ) -> dict[str, Any]:
        """Judge where this frame sits, put it there, and check the work.

        Replaces `_correct_registration`, now deleted, which was one shot on
        one detector: `registration_error_mm` -> `gap_edges`, which anchors its
        runs at column 0 and so answers "0.0 mm, registered" for any frame
        whose gap has a sliver of the neighbour beside it. Four of nine such
        calls on a real sixteen-frame walk were provably false, and a positive
        assertion of correctness is worse than an abstention because nothing
        downstream can tell them apart.

        The judgment is made **once**, from the pass the caller already took,
        and then delivered by the same loop that holds a frame to an operator's
        position -- which already iterates through backlash, refuses to reverse
        inside a frame, watches the direction on its first move and caps at
        `MAX_HOLD_MOVES`. What is new here is only the number and where it came
        from.
        """
        decision, detail = walk.judge(index, image)
        out: dict[str, Any] = {
            "decision_mm": None if decision is None else round(decision, 4),
            "ensemble": detail, "moved": False, "prescan": None,
            "outcome": "abstained", "roll_abort": None,
        }

        if decision is None:
            out["reason"] = detail.get("reason", "")
            self._log(f"frame {index + 1}: left as it came -- {out['reason']}")
            return out

        # The edge reader's note says `source`, not the legacy ensemble's
        # `agreed` and `chose`, and these lines read "by " and "from ?".
        agreed = ("+".join(detail.get("agreed", []))
                  or str(detail.get("source") or "the detector"))
        if abs(decision) < HOLD_TOLERANCE_MM:
            # Inside the smallest move the hardware can make, so there is
            # nothing to ask for. Not "close enough" -- unaskable.
            out["outcome"] = "in_place"
            self._log(f"frame {index + 1}: {say_units(decision)} by {agreed}, "
                      f"inside the "
                      f"{say_units(HOLD_TOLERANCE_MM, signed=False)} the "
                      "transport can move")
            return out

        if not walk.affordable(decision):
            walk.record(index, None, 0.0)
            walk.stop(
                f"frame {index + 1} wants {say_units(decision)} on top of the "
                f"{say_units(walk.travel_mm, signed=False)} this roll has "
                "already nudged")
            out["outcome"] = "budget"
            out["reason"] = walk.off_reason
            self._log(f"frame {index + 1}: {walk.off_reason}")
            return out

        if dry_run:
            param = self.param_for_mm(decision)
            out["would_send"] = {
                "action": 0x00 if decision >= 0 else 0x01, "param": param,
                "asked_mm": round(
                    (self.STEP_MM * param + self.OVERHEAD_MM)
                    * (1 if decision >= 0 else -1), 3),
            }
            out["outcome"] = "dry_run"
            self._log(
                f"frame {index + 1}: {say_units(decision)} by {agreed}; "
                f"would send "
                f"{out['would_send']['action']:#04x} {param:#04x} 00 04 "
                f"({say_units(out['would_send']['asked_mm'])}) -- dry run")
            return out

        self._log(f"frame {index + 1}: {say_units(decision)} by {agreed} "
                  f"(from {detail.get('chose') or detail.get('reason', '?')})")
        try:
            fix = self._hold_to_approved(
                index, image, prescan_resolution,
                _Aim(offset_mm=decision, reference=image),
                keep_raw=keep_raw, should_stop=should_stop, source="ensemble",
                rejudge=self._rejudge_for(index, walk, decision),
                shading=shading, film=film, roll=roll,
            )
        except BaseException as exc:
            # The decision with the moves it had sent (`_hold_to_approved`),
            # for the failed frame's marks, as a finished aim leaves them.
            setattr(exc, "aim", dict(out, **(getattr(exc, "hold", None) or {}),
                                     moved=bool((getattr(exc, "hold", None)
                                                 or {}).get("moves"))))
            raise
        out.update({k: v for k, v in fix.items() if k != "prescan"})
        out["prescan"] = fix.get("prescan")
        out["moved"] = fix["moves"] > 0

        # Where the frame was actually left, and whether anybody can say so. An
        # unverified move is the dangerous one: the film has moved and nothing
        # knows how far, so the arrival this frame contributed is now a stale
        # number. `record` drops it rather than let the prior build on it.
        walk.record(index, fix.get("residual_mm"), fix.get("spent_mm", 0.0),
                    verified=fix.get("final_mm") is not None)
        if fix["outcome"] == "held":
            walk.landed()
        else:
            walk.missed(fix["outcome"])
        if fix.get("roll_abort"):
            walk.stop(fix["roll_abort"])
        return out

    def _rejudge_for(self, index: int, walk: Any, target_mm: float):
        """The second look, which may stop this frame and nothing else.

        The operator asked for a judgment call after each pass, and this is it
        -- but deliberately only in the direction that can refuse. Letting it
        re-aim would put the detector's own noise inside the feedback loop, and
        a constant target is what makes `hold_plan` provably unable to chatter.

        So it fires on the gross case only: the frame is no longer anywhere
        this decision predicted. Reasons that happens are real -- the film
        slipping, a band that was picture all along -- and none of them are
        improved by nudging further.
        """
        def look(image: np.ndarray) -> tuple[bool, str]:
            if getattr(walk, "reader", None) is not None:
                mm = walk.reader.reread(index, image)
            elif walk.base is None:
                return True, ""
            else:
                mm = frame_offset_mm(image, walk.base).mm
            if mm is None:
                return True, ""          # cannot see; not evidence of trouble
            if abs(mm) > abs(target_mm) + MAX_CORRECTION_MM:
                return False, (
                    f"frame {index + 1}: after moving, the gap reads "
                    f"{say_units(mm)} -- further out than the "
                    f"{say_units(target_mm)} this started from. The frame is "
                    "not "
                    "where any of this predicted, so it is left alone")
            return True, ""
        return look

    #: The calibrated law for SLIDE actions 0x00 / 0x01: distance =
    #: STEP_MM x param + OVERHEAD_MM, which is `param + COMMAND_UNITS` (1.84)
    #: units -- `rps7200/protocol.py`, docs/protocol.md section 5. The fit of
    #: ten points with a 0.0185 mm residual that stood here was the first
    #: one, whose 1.57-unit ramp is ruled out.
    STEP_MM = MM_PER_UNIT
    OVERHEAD_MM = MM_PER_COMMAND

    #: The largest `param` a single correction may use, and therefore the
    #: largest correction there is: past it, `plan_nudges` chains commands and
    #: pays the ramp and the scatter again for each.
    #:
    #: It was 8, on the reasoning that "the aperture allows 0.49 mm of
    #: registration error, so anything beyond about a millimetre means the
    #: measurement is wrong rather than the film being far out". That premise
    #: is gone. `MAX_REGISTRATION_MM` is how precisely a frame can be *placed*
    #: in the aperture, never how badly the transport can *leave* one: the walk
    #: of 2026-09-22 proposed corrections out to 13.78 units and every one of
    #: them placed its frame, and walk A sat 1.4 to 2.2 mm out. The old cap
    #: turned those into three chained commands each.
    #:
    #: 87 rather than higher because it is the largest move two prescans can
    #: still confirm. Measured the same day, `verify_protocol.py` stage 15:
    #: param 87 moved 109 px at confidence 129, param 160 moved 196 px at
    #: confidence **27** against a floor of 55 -- it goes somewhere and cannot
    #: say where, and a correction that cannot be checked is worse than a
    #: smaller one that can. 87 is also the largest the vendor itself sends.
    MAX_CORRECTION_PARAM = 87

    @staticmethod
    def param_for_mm(millimetres: float) -> int:
        """The `param` byte that moves the film this far. See :meth:`nudge`.

        Static so the move planner in :mod:`rps7200.session` can ask the same
        question without a device: the snapping has to have one home, or the
        planner and the mover drift apart and a plan stops predicting what the
        hardware does.
        """
        n = round((abs(millimetres) - DirectScanner.OVERHEAD_MM)
                  / DirectScanner.STEP_MM)
        return max(1, min(DirectScanner.MAX_CORRECTION_PARAM, n))

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
        # `param_for_mm` clamps at MAX_CORRECTION_PARAM, and its own comment
        # calls that a refusal -- but it returns a smaller move instead, which
        # looks exactly like success. An iterating caller absorbs the shortfall
        # on its next pass; one that does not deserves to be told.
        #
        # Only at the cap. Below it `param_for_mm` rounds to the nearest
        # step, and a request that rounds down is snapped, not clamped: it
        # was logged "the largest single command is 4.8 units" for a 5.2-unit
        # error sent as param 3, and recorded as clamped, eighty params
        # short of the real largest.
        short = abs(millimetres) - asked
        clamped = param >= self.MAX_CORRECTION_PARAM and short > 1e-9
        self._log(
            f"nudge {'+' if forward else '-'}"
            f"{say_units(asked, signed=False)} (param {param}) for a "
            f"{say_units(millimetres)} error"
            + (f" -- the largest single command is "
               f"{say_units(asked, signed=False)}, so "
               f"{say_units(short, signed=False)} remains" if clamped else "")
        )
        self.slide(0x00 if forward else 0x01, param=param, value=0x04)
        answer = {"param": param, "forward": forward,
                  "asked_mm": round(asked if forward else -asked, 3),
                  "requested_mm": round(millimetres, 3), "clamped": clamped,
                  "short_mm": round(short, 4) if clamped else 0.0}
        # Kept for the next pass's record. The window's Move button, a hold
        # and an aim all come through here, and none of them left the SLIDE
        # bytes anywhere a library entry could show: which command placed a
        # frame was a log line.
        if self._moves_since_pass is None:
            self._moves_since_pass = []
        self._moves_since_pass.append(self.move_record(answer))
        return answer

    @staticmethod
    def move_record(answer: dict[str, Any]) -> dict[str, Any]:
        """One `nudge` as a record: the bytes it sent and what they were
        for, in units of `param`, never millimetres."""
        return {
            "action": 0x00 if answer["forward"] else 0x01,
            "param": int(answer["param"]),
            "asked_units": round(units(answer["asked_mm"]), 2),
            "requested_units": round(units(answer["requested_mm"]), 2),
            "clamped": bool(answer["clamped"]),
            "short_units": round(units(answer["short_mm"]), 2),
        }

    def _take_moves(self) -> list[dict[str, Any]] | None:
        """The moves since the last pass, handed to this one and forgotten."""
        moves, self._moves_since_pass = self._moves_since_pass, None
        return moves or None

    @staticmethod
    def _stop_before_pass(should_stop: Callable[[], bool] | None,
                          metered: bool) -> None:
        """Raise `StoppedBeforePass` if a stop has been asked for.

        Asked between metering and a pass's first command, where stopping
        abandons nothing. Here rather than in `scan` so the demo's `scan`
        stops where this one does, with the same words.
        """
        if should_stop is not None and should_stop():
            raise StoppedBeforePass(
                "stopped before the pass" + (", after metering"
                                             if metered else ""))

    def _moves_left_behind(self) -> None:
        """Forget the sub-frame moves no pass saw, before a whole-frame move.

        They placed the frame being left, not the one the film is going to:
        kept, a Move on frame 3 and then Next filed frame 4's pass with frame
        3's nudges as how it got there -- and so did the next frame's prescan
        after a hold whose verification pass raised, whose moves the roll has
        already filed in the failed frame's marks. A wrong record is worse
        than none, so they go, with a line in the log saying so.
        """
        moves, self._moves_since_pass = self._moves_since_pass, None
        if moves:
            self._log(f"{len(moves)} sub-frame move(s) seen by no pass, "
                      f"left behind with the frame")

    # -- rolls -------------------------------------------------------------

    #: The highest transport position taken at its word. READ_STATE byte 2
    #: counts whole frames from where the strip went in, and a 36-exposure roll
    #: is about 38 of them -- the longest this driver is written for. The one
    #: reading ever seen past that was a stale 72, left over with no strip in
    #: and reset to 0 when one went in (`docs/protocol.md` section 9). A number
    #: no strip can have is not a place to count frames from, so above this the
    #: position is unknown.
    #:
    #: Here rather than in :mod:`rps7200.session`, which re-exports it, because
    #: both roll loops -- this one and the demo's -- decide with it, and a
    #: decision the stand-in needs is taken from this class, never retyped.
    LAST_PLAUSIBLE_POSITION = 39

    @staticmethod
    def plausible_position(position: int | None) -> bool:
        """Whether a counter reading is a place on a strip at all."""
        return (position is not None
                and 0 <= position <= DirectScanner.LAST_PLAUSIBLE_POSITION)

    @staticmethod
    def roll_ends(
        first_index: int,
        skip: int,
        frames: int | None,
        only: Sequence[int] | frozenset[int] | None,
    ) -> Callable[[int], bool]:
        """When a roll is over, as a test on the index it has reached.

        Over at ``frames`` places on from where it started (``skip`` of them
        advanced past first), or once it is past the last frame in ``only`` --
        there is nothing left to do there, so it ends rather than advancing
        through the rest of the strip looking for pictures the operator has
        already said no to. Neither given: never, and the roll runs until the
        strip does.

        Static, and asked by :meth:`scan_roll` and the demo's roll alike, for
        the reason :meth:`param_for_mm` is. The demo carried its own copy of
        this and it drifted three ways in one day -- a six-frame default,
        ignoring the end of ``only``, walking past its own strip -- each of
        which made the demo report something the scanner would not do.
        """
        end = None if frames is None else first_index + skip + frames
        last_wanted = max(only) if only else None

        def finished(index: int) -> bool:
            if end is not None and index >= end:
                return True
            return last_wanted is not None and index > last_wanted

        return finished

    @staticmethod
    def place_on_strip(
        index: int,
        position: int | None,
        wanted: frozenset[int] | None = None,
        finished: Callable[[int], bool] | None = None,
    ) -> tuple[int | None, str | None]:
        """The index a frame is filed under, and what to log if it moved.

        A roll counts one place per advance, and that count is what every file
        and record is named by -- which is right only while the film moves one
        place per advance. An advance that took two, or a key pressed on the
        scanner mid-roll, left the count naming a picture it was not: the
        counter said so, the log said so, and the frame was filed under the
        other picture's number anyway, where a sheet's tick or an approved
        position then reached the wrong photograph.

        So the counter's word is taken whenever it has one that a strip can
        have: the index becomes ``position``, and the count carries on from
        there. Returns ``(index, None)`` when there is nothing to say.

        ``wanted`` is the roll's chosen indexes, so a jump that passed one of
        them says which -- that frame was not scanned, and nothing else would
        say so. A reading no strip can have is logged and the count kept.

        ``(None, why)`` says the roll ends here, and it ends on either side of
        the count. Past its end, by the roll's own ``finished``
        (:meth:`roll_ends`), where the frame is not in the roll at all. And
        **behind its count**, naming the frames the film went back over:
        followed backwards, the count went over them again -- re-scanning
        places this roll had already taken, filing them under numbers it had
        already used, overwriting their prescans, and yielding more than
        ``frames`` pictures, because where a roll ends is fixed from where it
        started. Neither has been seen on this transport, and the second
        takes a counter going back mid-roll or a caller that did not seek: the
        seek puts the film on ``first_index`` before a roll begins.

        Static and asked by both roll loops, for the reason :meth:`roll_ends`
        is: a decision the demo needs is taken from here, never retyped.
        """
        if position is None or position == index:
            return index, None
        if not DirectScanner.plausible_position(position):
            return index, (f"frame {index + 1}: the transport's counter reads "
                           f"{position}, which no strip has, so the frame "
                           "keeps the number the roll counted")
        said = (f"frame {index + 1}: the transport says the film is on frame "
                f"{position + 1}")

        def listed(numbers: list[int]) -> str:
            return (("frame " if len(numbers) == 1 else "frames ")
                    + ", ".join(str(n) for n in numbers))

        if position < index:
            return None, (f"{said}, behind the roll -- it went back over "
                          f"{listed(list(range(position + 1, index + 1)))}, "
                          "so the roll ends here rather than take them again")
        passed = [n + 1 for n in range(index, position)
                  if wanted is None or n in wanted]
        unscanned = (f" -- the film went past {listed(passed)} without it "
                     "being scanned") if passed else ""
        if finished is not None and finished(position):
            return None, (f"{said}, past the end of this roll, so it ends "
                          f"here{unscanned}")
        return position, (f"{said}, so it is filed as frame {position + 1}"
                          f"{unscanned}")

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
        approved: dict[int, Any] | None = None,
        fast_infrared: bool = True,
        first_index: int = 0,
        edge_reader: Callable[[str], Any] | None = None,
        shading: bool = True,
        roll: str | None = None,
    ) -> Iterator[RollFrame]:
        """Walk a roll or strip, yielding one :class:`RollFrame` per picture.

        A generator, not a list. At 3600 dpi a frame is ~142 MB of pixels plus
        its raw bytes, so the caller has to write each one out and let it go;
        collecting a roll in memory is not possible. It also means the caller
        can stop mid-roll, and that a frame reaches disk the moment it exists
        rather than at the end of a three-hour run.

        The first picture is scanned **before** any advance -- the film is
        already positioned at it when the roll starts. ``skip`` advances that
        many times first.

        ``first_index`` is where the film is when the roll starts, as the
        transport counts it, and so the index the first frame is given. The
        caller puts the film there first -- `session.seek` does, from wherever
        it was -- so that a frame's index is its place on the strip and the
        same picture has the same number in every roll and walk. Counting from
        0 wherever the film happened to be is what numbered frame 11 as 1.
        ``only``, ``approved`` and ``frames`` all count from it, and where the
        counter disagrees with the count part-way, the counter wins: see
        :meth:`place_on_strip`.

        Every frame is scanned at the full transport window. Cropping is a
        host-side decision that can be revisited; a window detected wrongly
        during an unattended run cannot.

        ``drift_warning`` is how much narrower than a whole frame a picture may
        measure before the log says the film has drifted -- 240 units is 0.85 mm,
        comfortably past detection jitter and well short of losing anything.

        Stops on whichever comes first: ``frames`` places on from where it
        started -- pictures, while the counter agrees with the count -- a
        prescan with no picture in it (:func:`frame_contrast` below
        ``blank_contrast``), an advance that does not move the film, a counter
        that reads past the end of the roll or behind its count, or
        ``max_failures`` consecutive failures. A single failed frame does not
        end the roll -- it is yielded with ``error`` set and the roll goes on.

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

        ``only`` is the frame indexes worth scanning, in the same numbering the
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
            raise self.infrared_blind(film, roll=True)

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
        #: Holding stays on until something says it should not: the direction
        #: came back inverted, or several frames running would not reach the
        #: position asked for. Scanning continues either way.
        holding = True
        # The only thing in a roll that remembers anything across frames. Built
        # once so the base level and the advance it learns carry forward; None
        # when nothing asked for aiming, so an ordinary roll is untouched.
        # ``edge_reader(film)`` makes a fresh reader for this roll (the
        # window passes `tools/frame_edges.walk_reader`); None, or a film it
        # does not read, leaves the strip-level detector in charge.
        walk = (StripWalk(reader=edge_reader(film) if edge_reader else None)
                if (correct or correct_dry_run) else None)
        misses = 0
        index = first_index

        wanted = frozenset(only) if only is not None else None
        if wanted is not None and not wanted:
            self._log("no frames were chosen, so there is nothing to scan")
            return
        finished = self.roll_ends(first_index, skip, frames, wanted)

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

        while not finished(index):
            # The frame has not begun here, so this is where stopping is
            # cheapest -- and it covers the advance, which takes 2-7 seconds
            # during which a stop would otherwise not be looked at again until
            # the frame after it had been prescanned. At 3600 dpi RGBI that is
            # six minutes and 250 MB spent after the operator said stop.
            if should_stop is not None and should_stop():
                self._log("stopping before the next frame, as asked")
                return

            # Where the film is, before anything is decided about this frame:
            # whether it was chosen, which approved position it is held to and
            # what it is filed as all follow from its place on the strip, and
            # the counter is what knows that. Read for a frame that is only
            # being passed as well, so a jump is caught where it happened
            # rather than at the next chosen frame.
            position = self.position()
            placed, moved = self.place_on_strip(index, position, wanted,
                                                finished)
            if moved is not None:
                self._log(moved)
            if placed is None:
                return                # past the end, or behind the count
            index = placed

            if wanted is not None and index not in wanted:
                # Surveyed and not chosen. The prescan that would decide this
                # has already been taken and looked at, so taking another one
                # here would only spend the operator's time re-asking.
                self._log(f"frame {index + 1}: not chosen, advancing past it")
                index += 1
                if finished(index) or not keep_going():
                    return
                continue

            started = time.monotonic()
            prescan_before = None
            prescan_image = None
            raw_prescan = None
            prescan_meta: dict[str, Any] = {}
            prescan_capture: dict[str, Any] | None = None
            #: The raw pixels, meta and record of the picture a hold or an
            #: aim replaced, beside `prescan_before`.
            before: dict[str, Any] = {}
            marks: dict[str, Any] = {}

            try:
                prescan_image, _ = self.prescan(
                    resolution=prescan_resolution, keep_raw=keep_raw,
                    shading=shading,
                    # The roll's film, so the prescan's entry says what was in
                    # the transport; it was recorded as "negative" whatever it was.
                    film=film,
                )
                raw_prescan = self.last_pixels_raw
                prescan_meta = dict(self.last_scan_meta or {})
                # Now, while the last pass is this one: see `prescan_capture`.
                prescan_capture = self.capture_record()
                contrast = frame_contrast(prescan_image)
                marks = dict(registration(prescan_image, window))
                marks["contrast"] = round(contrast, 4)
                self._log(
                    f"frame {index + 1}: contrast {contrast:.3f}, "
                    f"picture x{marks['x0']}..{marks['x1']}, "
                    f"offset {say_units(marks['offset_mm'])}, "
                    f"short by {say_units(marks['shortfall_mm'], signed=False)}"
                )

                if contrast < blank_contrast:
                    self._log(
                        f"frame {index + 1}: nothing in the window "
                        f"(contrast {contrast:.3f} < {blank_contrast}); "
                        "end of film"
                    )
                    return

                if walk is not None:
                    marks["base"] = walk.observe(index, prescan_image)

                # An approved position beats the automatic detector outright.
                # That is the whole point of it: the operator looked at this
                # frame and said where it goes, and a gap measurement that has
                # been wrong before must not overrule him. A frame he did not
                # adjust still gets the old behaviour, so a mixed roll is
                # coherent and a roll with no approvals is unchanged.
                held = approved.get(index) if approved else None
                if held is not None and holding:
                    fix = self._hold_to_approved(
                        index, prescan_image, prescan_resolution, held,
                        keep_raw=keep_raw,
                        should_stop=should_stop,
                        # Without this every machine proposal logged as
                        # `operator` -- the one thing `source`'s own docstring
                        # says the field exists to prevent.
                        source=getattr(held, "source", None) or "operator",
                        shading=shading, film=film, roll=roll,
                    )
                    if fix.get("roll_abort"):
                        holding = False
                    if fix.get("outcome") != "held":
                        misses += 1
                        if misses >= self.HOLD_GIVE_UP_FRAMES:
                            holding = False
                            self._log(
                                f"{misses} frames in a row did not reach the "
                                "approved position; holding is off for the "
                                "rest of this roll. Frames are still scanned."
                            )
                    else:
                        misses = 0
                    marks["approved"] = {k: v for k, v in fix.items()
                                         if k != "prescan"}
                    if fix.get("prescan") is not None:
                        # The picture as it arrived, kept as the aim below
                        # keeps it: the hold moved the film away from it, and
                        # it is what the hold was judged from.
                        prescan_before = prescan_image
                        before = {"raw": raw_prescan, "meta": prescan_meta,
                                  "capture": prescan_capture}
                        prescan_image = fix["prescan"]
                        # The replacement prescan was the helper's last pass,
                        # so its raw pixels are the ones on hand now.
                        raw_prescan = self.last_pixels_raw
                        prescan_meta = dict(self.last_scan_meta or {})
                        prescan_capture = self.capture_record()
                        marks.update(
                            {k: v for k, v in registration(
                                prescan_image, window).items()}
                        )
                        marks["contrast"] = round(
                            frame_contrast(prescan_image), 4)
                elif held is not None:
                    marks["approved"] = {
                        "target_mm": round(held.offset_mm, 4),
                        "outcome": "off",
                        "reason": "holding was switched off earlier in this roll",
                    }
                elif correct or correct_dry_run:
                    fix = self._aim_frame(
                        index, prescan_image, prescan_resolution, walk,
                        dry_run=correct_dry_run, keep_raw=keep_raw,
                        should_stop=should_stop, shading=shading, film=film,
                        roll=roll,
                    )
                    marks["correction"] = {k: v for k, v in fix.items()
                                           if k != "prescan"}
                    if fix.get("prescan") is not None:
                        # The picture as it arrived, kept because the pass that
                        # replaced it is otherwise the only one anybody sees --
                        # and a correction that moved the frame somewhere worse
                        # would look exactly like one that worked.
                        prescan_before = prescan_image
                        before = {"raw": raw_prescan, "meta": prescan_meta,
                                  "capture": prescan_capture}
                        prescan_image = fix.pop("prescan")
                        # The replacement prescan was the helper's last pass,
                        # so its raw pixels are the ones on hand now.
                        raw_prescan = self.last_pixels_raw
                        prescan_meta = dict(self.last_scan_meta or {})
                        prescan_capture = self.capture_record()
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
                        f"frame {index + 1}: picture is "
                        f"{say_units(marks['shortfall_mm'], signed=False)} "
                        "narrower than a whole frame -- the film has drifted and "
                        "part of it is outside the aperture"
                    )

                if dry_run:
                    yield RollFrame(index, position, None, {}, prescan_image,
                                    marks, raw_prescan=raw_prescan,
                                    prescan_meta=prescan_meta,
                                    prescan_before=prescan_before,
                                    **_prescans_kept(prescan_capture,
                                                     before))
                else:
                    if meter != METER_NONE and not (meter == METER_ONCE and metered):
                        # `infrared` here says the scan that follows is RGBI;
                        # it does not make the probe infrared. auto_exposure
                        # always probes in RGB. What the flag buys is blue's
                        # headroom: blue comes back several times brighter in
                        # RGBI than in RGB at the same exposure -- measured
                        # 4.98-5.02 on colour negative and ~9.6 on black and
                        # white, which is why `BLUE_RGBI_HEADROOM` is per film --
                        # so a blue metered to fill the range on an RGB probe
                        # clips in the scan. Passing False here cost exactly
                        # that -- one roll metered blue to 10.07x, which on a
                        # 6506 base pins the 16-bit timer at its 65535 ceiling
                        # before the RGBI gain is applied.
                        self.set_gain_offset(baseline, infrared=infrared)
                        scales = self.auto_exposure(
                            target=exposure_target, infrared=infrared, film=film,
                            shading=shading,
                            # Which roll and frame the probes served: kept
                            # only by debug filing, a probe named neither.
                            role={"roll": roll, "roll_index": index},
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
                    # What decided `scales`: this frame's metering or, metering
                    # once, the roll's first.
                    last = getattr(self, "last_metering", None)
                    decided_by = dict(last) if metered and last else None
                    image, meta = self.scan(
                        resolution=resolution,
                        infrared=infrared,
                        frame=window,
                        exposure_scale=scales,
                        film=film,
                        keep_raw=keep_raw,
                        fast_infrared=fast_infrared,
                        shading=shading,
                        metering=decided_by,
                    )
                    meta["roll_index"] = index
                    meta["roll_position"] = position
                    meta["registration"] = marks
                    yield RollFrame(index, position, image, meta, prescan_image,
                                    marks, raw_image=self.last_pixels_raw,
                                    raw_prescan=raw_prescan,
                                    prescan_meta=prescan_meta,
                                    prescan_before=prescan_before,
                                    **_prescans_kept(prescan_capture,
                                                     before))
                failures = 0
            # UsbError covers CheckCondition and NoDataYet. ValueError is in
            # here because a roll runs for hours unattended: one frame that
            # decodes to an unexpected shape should cost that frame, not the
            # thirty after it.
            except (UsbError, ScanReadError, CalibrationRequired,
                    ShadingUnavailable, TimeoutError, ValueError) as exc:
                failures += 1
                self._log(f"frame {index + 1} failed "
                          f"({failures}/{max_failures}): {exc}")
                # A hold or an aim that raised part way had already moved the
                # film: what it sent and measured comes with the exception
                # (`_hold_to_approved`), and belongs in this frame's marks as
                # it would have been had the frame finished.
                aimed, held_part = (getattr(exc, "aim", None),
                                    getattr(exc, "hold", None))
                if aimed is not None:
                    marks["correction"] = {k: v for k, v in aimed.items()
                                           if k != "prescan"}
                elif held_part is not None:
                    marks["approved"] = held_part
                # With the prescans it did take, and their records: a frame
                # that failed is the one whose prescan is the only account
                # of it, and it was dropped by every caller.
                yield RollFrame(
                    index, position, None, {}, prescan_image, marks,
                    error=str(exc), raw_prescan=raw_prescan,
                    prescan_meta=prescan_meta,
                    prescan_before=prescan_before,
                    **_prescans_kept(prescan_capture, before),
                )
                if self.suspect is not None:
                    # Not a frame that failed but a device that may still be
                    # mid-scan. Advancing and scanning the next frame into it
                    # is how one lost frame became a lost roll.
                    self._log("ending the roll: the scanner may still be mid-scan")
                    raise DeviceSuspect(self.suspect)
                if failures >= max_failures:
                    self._log(f"giving up after {failures} consecutive failures")
                    return

            self._log(f"frame {index + 1} took "
                      f"{time.monotonic() - started:.0f}s")
            index += 1
            if finished(index):
                break
            if not keep_going():
                return
