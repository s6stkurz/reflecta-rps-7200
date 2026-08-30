"""Session handling for the Reflecta RPS 7200 via SANE's ``pieusb`` backend.

Three quirks of that backend shape this module:

1. RGBI mode returns four interleaved channels per pixel but reports the frame
   as ``SANE_FRAME_RGB``. The channel count is therefore derived from
   ``bytes_per_line`` rather than trusted from ``format``.
2. The scanner is reset during discovery and re-enumerates, so its SANE device
   name changes between processes. Enumeration and open must happen in the same
   session, with a retry when the name goes stale.
3. Preview calibration is stored on the open handle, so a prescan is only
   reusable by a scan performed through the same :class:`Scanner` instance.
"""

from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from . import sane_ffi as ffi
from .sane_ffi import (
    CAP_INACTIVE,
    Action,
    ConstraintType,
    SANE_Byte,
    SANE_Device,
    SANE_Handle,
    SANE_Int,
    SANE_Parameters,
    SaneError,
    Status,
    ValueType,
    check,
    fix,
    lib,
    unfix,
)

#: Resolution used when the caller does not ask for one. The backend's own
#: default is 300 dpi (it seeds ``resolution`` from the fast-preview value), so
#: this must always be set explicitly.
DEFAULT_RESOLUTION = 600

#: Settings that keep both the RGB and the infrared data untouched.
#:
#: ``correct-shading`` stays on because it is sensor calibration (lamp/CCD
#: non-uniformity), not image editing. Everything that would consume or alter
#: the IR channel is off.
RAW_SETTINGS: dict[str, Any] = {
    "mode": "RGBI",          # the one-pass four-channel mode
    "depth": 16,
    "clean-image": False,    # do not let the backend spend the IR on dust removal
    "correct-infrared": False,  # no red-crosstalk correction
    "fast-infrared": False,  # repositions the head, keeps IR aligned with RGB
    "correct-shading": True,
    "crop": "None",
    "smooth": 0,
}

_READ_CHUNK = 1 << 20  # 1 MiB


class DeviceNotFound(RuntimeError):
    """No matching scanner was present on the bus."""


# ---------------------------------------------------------------------------
# libsane init/exit refcounting
# ---------------------------------------------------------------------------

_init_depth = 0
_version_code = 0


def _sane_init() -> None:
    global _init_depth, _version_code
    if _init_depth == 0:
        version = SANE_Int()
        check(lib.sane_init(ctypes.byref(version), None), "sane_init")
        _version_code = version.value
    _init_depth += 1


def _sane_exit() -> None:
    global _init_depth
    _init_depth -= 1
    if _init_depth <= 0:
        _init_depth = 0
        lib.sane_exit()


def sane_version() -> str:
    code = _version_code
    return f"{(code >> 24) & 0xFF}.{(code >> 16) & 0xFF}.{code & 0xFFFF}"


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class Frame:
    """One captured image plus the geometry and settings that produced it."""

    data: np.ndarray            # (lines, pixels, channels), uint16 or uint8
    resolution: int
    depth: int
    channels: int
    preview: bool
    settings: dict[str, Any] = field(default_factory=dict)
    duration_s: float = 0.0

    @property
    def rgb(self) -> np.ndarray:
        return self.data[..., :3]

    @property
    def ir(self) -> np.ndarray:
        if self.channels < 4:
            raise ValueError(
                f"frame has {self.channels} channel(s); no infrared plane present"
            )
        return self.data[..., 3]

    @property
    def has_ir(self) -> bool:
        return self.channels >= 4

    def metadata(self) -> dict[str, Any]:
        h, w = self.data.shape[:2]
        return {
            "width": int(w),
            "height": int(h),
            "channels": int(self.channels),
            "depth": int(self.depth),
            "resolution_dpi": int(self.resolution),
            "preview": bool(self.preview),
            "duration_s": round(self.duration_s, 2),
            "channel_order": ["R", "G", "B", "I"][: self.channels],
            "settings": self.settings,
        }


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _device_list() -> list[dict[str, str]]:
    array = ctypes.POINTER(ctypes.POINTER(SANE_Device))()
    check(lib.sane_get_devices(ctypes.byref(array), 0), "sane_get_devices")
    devices = []
    i = 0
    while array[i]:
        d = array[i].contents

        def s(v: bytes | None) -> str:
            return v.decode("utf-8", "replace") if v else ""

        devices.append(
            {
                "name": s(d.name),
                "vendor": s(d.vendor),
                "model": s(d.model),
                "type": s(d.type),
            }
        )
        i += 1
    return devices


def discover(backend: str = "pieusb") -> list[dict[str, str]]:
    """Enumerate scanners, optionally filtered to one backend prefix."""
    _sane_init()
    try:
        devices = _device_list()
    finally:
        _sane_exit()
    if backend:
        devices = [d for d in devices if d["name"].startswith(f"{backend}:")]
    return devices


# ---------------------------------------------------------------------------
# Scanner session
# ---------------------------------------------------------------------------


class Scanner:
    """An open session against one scanner.

    Use as a context manager so prescan and scan share the handle::

        with Scanner() as s:
            s.prescan()
            frame = s.scan(resolution=600)
    """

    def __init__(
        self,
        device_name: str | None = None,
        backend: str = "pieusb",
        open_retries: int = 3,
        verbose: bool = False,
        warmup_timeout: float = 300.0,
        warmup_poll: float = 15.0,
    ):
        self.backend = backend
        self.verbose = verbose
        self.warmup_timeout = warmup_timeout
        self.warmup_poll = warmup_poll
        self._handle = SANE_Handle()
        self._opened = False
        self._index: dict[str, int] = {}
        self._prescan_done = False
        self.device: dict[str, str] = {}

        _sane_init()
        try:
            self._open(device_name, open_retries)
        except Exception:
            _sane_exit()
            raise

    # -- lifecycle ---------------------------------------------------------

    def _log(self, message: str) -> None:
        if self.verbose:
            print(f"[rps7200] {message}")

    def _open(self, device_name: str | None, retries: int) -> None:
        last: Exception | None = None
        for attempt in range(1, retries + 1):
            # Re-enumerate every attempt: the backend resets the device during
            # discovery, so a name from a previous pass may already be stale.
            devices = _device_list()
            if self.backend:
                devices = [
                    d for d in devices if d["name"].startswith(f"{self.backend}:")
                ]
            if device_name:
                devices = [d for d in devices if d["name"] == device_name]
            if not devices:
                last = DeviceNotFound(
                    f"no {self.backend or 'SANE'} device found"
                    + (f" matching {device_name!r}" if device_name else "")
                )
                self._log(f"attempt {attempt}: no device listed")
                time.sleep(0.5)
                continue

            target = devices[0]
            handle = SANE_Handle()
            status = lib.sane_open(target["name"].encode(), ctypes.byref(handle))
            if status == Status.GOOD:
                self._handle = handle
                self._opened = True
                self.device = target
                self._reload_options()
                self._log(f"opened {target['name']}")
                return
            last = SaneError(status, f"sane_open({target['name']})")
            self._log(f"attempt {attempt}: open failed ({last}); re-enumerating")
            time.sleep(0.5)
        assert last is not None
        raise last

    def close(self) -> None:
        if self._opened:
            lib.sane_close(self._handle)
            self._opened = False
            _sane_exit()

    def __enter__(self) -> Scanner:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _require_open(self) -> None:
        if not self._opened:
            raise RuntimeError("scanner session is closed")

    # -- options -----------------------------------------------------------

    def _descriptor(self, index: int):
        ptr = lib.sane_get_option_descriptor(self._handle, index)
        return ptr.contents if ptr else None

    def _reload_options(self) -> None:
        """Rebuild the name to index map.

        Indices are not stable across backends, and a set can invalidate them
        (``SANE_INFO_RELOAD_OPTIONS``), so this is re-run whenever that flag
        comes back.
        """
        self._index = {}
        count = SANE_Int()
        check(
            lib.sane_control_option(
                self._handle, 0, Action.GET_VALUE, ctypes.byref(count), None
            ),
            "read option count",
        )
        for i in range(1, count.value):
            desc = self._descriptor(i)
            if desc and desc.name:
                self._index[desc.name.decode()] = i

    def options(self) -> list[dict[str, Any]]:
        """Describe every option the backend exposes."""
        self._require_open()
        out = []
        for name, index in sorted(self._index.items(), key=lambda kv: kv[1]):
            desc = self._descriptor(index)
            if not desc:
                continue
            entry: dict[str, Any] = {
                "name": name,
                "title": desc.title.decode() if desc.title else "",
                "type": ValueType(desc.type).name,
                "unit": ffi.Unit(desc.unit).name,
                "active": (desc.cap & CAP_INACTIVE) == 0,
                "settable": bool(desc.cap & ffi.CAP_SOFT_SELECT),
            }
            ct = desc.constraint_type
            if ct == ConstraintType.RANGE and desc.constraint.range:
                r = desc.constraint.range.contents
                if desc.type == ValueType.FIXED:
                    entry["range"] = [unfix(r.min), unfix(r.max), unfix(r.quant)]
                else:
                    entry["range"] = [r.min, r.max, r.quant]
            elif ct == ConstraintType.STRING_LIST and desc.constraint.string_list:
                values, i = [], 0
                while desc.constraint.string_list[i]:
                    values.append(desc.constraint.string_list[i].decode())
                    i += 1
                entry["values"] = values
            elif ct == ConstraintType.WORD_LIST and desc.constraint.word_list:
                n = desc.constraint.word_list[0]
                words = [desc.constraint.word_list[i + 1] for i in range(n)]
                entry["values"] = (
                    [unfix(w) for w in words] if desc.type == ValueType.FIXED else words
                )
            if entry["active"]:
                try:
                    entry["value"] = self.get_option(name)
                except SaneError:
                    pass
            out.append(entry)
        return out

    def has_option(self, name: str) -> bool:
        return name in self._index

    def get_option(self, name: str) -> Any:
        self._require_open()
        if name not in self._index:
            raise KeyError(f"unknown option {name!r}")
        index = self._index[name]
        desc = self._descriptor(index)
        if desc is None:
            raise KeyError(f"unknown option {name!r}")

        if desc.type == ValueType.STRING:
            buf = ctypes.create_string_buffer(max(desc.size, 1))
            check(
                lib.sane_control_option(
                    self._handle, index, Action.GET_VALUE, buf, None
                ),
                f"get {name}",
            )
            return buf.value.decode("utf-8", "replace")

        word = SANE_Int()
        check(
            lib.sane_control_option(
                self._handle, index, Action.GET_VALUE, ctypes.byref(word), None
            ),
            f"get {name}",
        )
        if desc.type == ValueType.BOOL:
            return bool(word.value)
        if desc.type == ValueType.FIXED:
            return unfix(word.value)
        return word.value

    def set_option(self, name: str, value: Any) -> int:
        """Set one option. Returns the backend's info flags."""
        self._require_open()
        if name not in self._index:
            raise KeyError(f"unknown option {name!r}")
        index = self._index[name]
        desc = self._descriptor(index)
        if desc is None:
            raise KeyError(f"unknown option {name!r}")
        if desc.cap & CAP_INACTIVE:
            raise ValueError(f"option {name!r} is currently inactive")
        if not (desc.cap & ffi.CAP_SOFT_SELECT):
            raise ValueError(f"option {name!r} is not settable")

        info = SANE_Int()
        if desc.type == ValueType.STRING:
            payload = str(value).encode()
            buf = ctypes.create_string_buffer(payload, max(desc.size, len(payload) + 1))
            status = lib.sane_control_option(
                self._handle, index, Action.SET_VALUE, buf, ctypes.byref(info)
            )
        else:
            if desc.type == ValueType.BOOL:
                word = SANE_Int(1 if value else 0)
            elif desc.type == ValueType.FIXED:
                word = SANE_Int(fix(float(value)))
            else:
                word = SANE_Int(int(value))
            status = lib.sane_control_option(
                self._handle,
                index,
                Action.SET_VALUE,
                ctypes.byref(word),
                ctypes.byref(info),
            )
        check(status, f"set {name}={value!r}")

        if info.value & ffi.INFO_RELOAD_OPTIONS:
            self._reload_options()
        return info.value

    def apply(self, settings: dict[str, Any], strict: bool = False) -> dict[str, Any]:
        """Apply several options, reporting what actually took effect.

        ``mode`` and ``depth`` are applied first because they activate and
        deactivate other options.
        """
        applied: dict[str, Any] = {}
        order = sorted(
            settings.items(),
            key=lambda kv: {"mode": 0, "depth": 1}.get(kv[0], 2),
        )
        for name, value in order:
            try:
                self.set_option(name, value)
            except (KeyError, ValueError, SaneError) as exc:
                if strict:
                    raise
                self._log(f"skipped {name}={value!r}: {exc}")
                continue
            applied[name] = self.get_option(name)
        return applied

    # -- scanning ----------------------------------------------------------

    def parameters(self) -> SANE_Parameters:
        self._require_open()
        params = SANE_Parameters()
        check(
            lib.sane_get_parameters(self._handle, ctypes.byref(params)),
            "sane_get_parameters",
        )
        return params

    @staticmethod
    def _channels(params: SANE_Parameters) -> int:
        """Derive the channel count from the line stride.

        The backend reports RGBI as ``SANE_FRAME_RGB``, so ``format`` cannot be
        trusted to tell three channels from four.
        """
        bytes_per_sample = max(1, (params.depth + 7) // 8)
        if params.pixels_per_line <= 0 or params.depth < 8:
            return 1
        channels = params.bytes_per_line // (params.pixels_per_line * bytes_per_sample)
        return max(1, channels)

    def _start(self) -> None:
        """Begin a scan, waiting out the lamp warm-up.

        After a power cycle the scanner reports ``warmingUp``, and the backend
        turns that into ``SANE_STATUS_DEVICE_BUSY`` from ``sane_start`` rather
        than blocking. Retrying is the documented way through it.
        """
        deadline = time.monotonic() + self.warmup_timeout
        attempt = 0
        while True:
            status = lib.sane_start(self._handle)
            if status != Status.DEVICE_BUSY:
                check(status, "sane_start")
                return
            attempt += 1
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SaneError(
                    Status.DEVICE_BUSY,
                    f"sane_start (still warming up after "
                    f"{self.warmup_timeout:.0f}s)",
                )
            if attempt == 1:
                print(
                    "  lamp is warming up; waiting "
                    f"(up to {self.warmup_timeout:.0f}s) ...",
                    flush=True,
                )
            self._log(f"warming up, retry {attempt} ({remaining:.0f}s left)")
            time.sleep(min(self.warmup_poll, max(remaining, 0)))

    def _acquire(self, preview: bool, settings: dict[str, Any]) -> Frame:
        self._require_open()
        started = time.monotonic()

        self._start()
        try:
            params = self.parameters()
            channels = self._channels(params)
            if params.depth not in (8, 16):
                raise RuntimeError(
                    f"unsupported depth {params.depth}; expected 8 or 16"
                )
            if params.lines <= 0:
                raise RuntimeError(
                    "backend reported an unknown line count; cannot size the buffer"
                )

            total = params.bytes_per_line * params.lines
            expected = (
                params.pixels_per_line * channels * ((params.depth + 7) // 8)
            )
            if expected != params.bytes_per_line:
                raise RuntimeError(
                    f"line stride {params.bytes_per_line} is not "
                    f"{params.pixels_per_line} px x {channels} ch x "
                    f"{(params.depth + 7) // 8} B"
                )

            self._log(
                f"reading {params.pixels_per_line}x{params.lines} "
                f"x{channels}ch @{params.depth}bit ({total / 1e6:.1f} MB)"
            )

            buf = (SANE_Byte * total)()
            got = SANE_Int()
            offset = 0
            while offset < total:
                want = min(_READ_CHUNK, total - offset)
                ptr = ctypes.cast(
                    ctypes.byref(buf, offset), ctypes.POINTER(SANE_Byte)
                )
                status = lib.sane_read(self._handle, ptr, want, ctypes.byref(got))
                if status == Status.EOF:
                    break
                check(status, "sane_read")
                if got.value <= 0:
                    break
                offset += got.value

            if offset < total:
                raise RuntimeError(
                    f"short read: got {offset} of {total} bytes"
                )
        finally:
            lib.sane_cancel(self._handle)

        dtype = np.dtype("<u2") if params.depth == 16 else np.dtype(np.uint8)
        # Wrap the ctypes buffer rather than copying it; at 7200 dpi a copy
        # would briefly double a ~570 MB allocation. numpy keeps `buf` alive
        # through the array's .base reference.
        image = np.frombuffer(buf, dtype=dtype).reshape(
            params.lines, params.pixels_per_line, channels
        )

        resolution = int(round(self.get_option("resolution")))
        return Frame(
            data=image,
            resolution=resolution,
            depth=params.depth,
            channels=channels,
            preview=preview,
            settings=settings,
            duration_s=time.monotonic() - started,
        )

    def prescan(self, settings: dict[str, Any] | None = None) -> Frame:
        """Run a preview scan.

        Besides producing a low-resolution image, this populates the per-channel
        calibration bounds the backend stores on this handle, which
        :meth:`scan` then reuses via ``calibration="from preview"``.

        The resolution is chosen by the backend (its fast-preview value, 300 dpi
        on this scanner) and cannot be overridden.
        """
        self._require_open()
        applied = self.apply({**RAW_SETTINGS, **(settings or {})})
        applied.update(self.apply({"preview": True}))
        frame = self._acquire(preview=True, settings=applied)
        self._prescan_done = True
        return frame

    def scan(
        self,
        resolution: int = DEFAULT_RESOLUTION,
        settings: dict[str, Any] | None = None,
        use_prescan: bool | None = None,
        advance: bool = False,
    ) -> Frame:
        """Capture one full frame as RGB + infrared.

        ``resolution`` is in dpi and is validated against the backend's range.
        ``use_prescan`` defaults to whether a prescan has run on this handle.
        """
        self._require_open()
        if use_prescan is None:
            use_prescan = self._prescan_done

        wanted = dict(RAW_SETTINGS)
        wanted["preview"] = False
        wanted["resolution"] = self._validate_resolution(resolution)
        wanted["calibration"] = (
            "from preview" if use_prescan else "from internal test"
        )
        if self.has_option("advance"):
            wanted["advance"] = advance
        wanted.update(settings or {})

        applied = self.apply(wanted)
        return self._acquire(preview=False, settings=applied)

    def _validate_resolution(self, dpi: int) -> int:
        """Clamp-check a dpi value against the descriptor's own range."""
        if not self.has_option("resolution"):
            raise RuntimeError("backend exposes no resolution option")
        desc = self._descriptor(self._index["resolution"])
        if (
            desc
            and desc.constraint_type == ConstraintType.RANGE
            and desc.constraint.range
        ):
            r = desc.constraint.range.contents
            lo, hi = (
                (unfix(r.min), unfix(r.max))
                if desc.type == ValueType.FIXED
                else (r.min, r.max)
            )
            if not (lo <= dpi <= hi):
                raise ValueError(
                    f"resolution {dpi} dpi is outside the scanner's "
                    f"{int(lo)}-{int(hi)} dpi range"
                )
        return int(dpi)

    def scan_roll(
        self,
        frames: int,
        resolution: int = DEFAULT_RESOLUTION,
        prescan_each: bool = True,
        settings: dict[str, Any] | None = None,
    ):
        """Scan several frames in one session, advancing the film between them.

        Not usable yet. Film advance is gated on ``FLAG_SLIDE_TRANSPORT``, which
        the backend takes from the fourth field of the ``pieusb.conf`` line for
        this device. That field is currently ``0x00``::

            usb 0x05e3 0x0144 0x31 0x00

        With it at ``0x00`` the ``advance`` option is accepted but does nothing,
        so this would rescan the same frame N times.

        Use :meth:`rps7200.direct.DirectScanner.scan_roll` instead. It drives
        the transport over USB, so the config flag does not apply, and it sends
        the advance the vendor software actually sends -- recovered from a
        capture of CyberView walking a 5-frame strip. See "Whole-roll scanning"
        in the README.
        """
        for _ in range(frames):
            if prescan_each:
                self.prescan(settings)
            yield self.scan(
                resolution=resolution, settings=settings, advance=True
            )
