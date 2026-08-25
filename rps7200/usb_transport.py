"""Direct USB transport to the scanner, bypassing SANE.

The scanner is a SCSI device behind a Genesys Logic USB bridge. The bridge is
driven entirely through vendor control transfers to four "ports", with bulk IN
used only for payload data:

    port 0x88  write  IEEE1284 data line
    port 0x87  write  IEEE1284 control line
    port 0x85  write  SCSI command / data-out bytes, one at a time
    port 0x84  read   status byte
    port 0x82  write  expected bulk length (8 bytes, LE at offset 4)

Every command is preceded by an IEEE1284 "daisy" sequence that puts the bridge
into SCSI mode.

This exists because SANE's ``pieusb`` backend asks for the whole payload in one
length handshake, and this scanner stops delivering after 32 KB and then waits
forever -- the read times out and the device falls off the bus. Here the payload
is fetched in bounded windows, each with its own length handshake, which is what
:data:`MAX_WINDOW` controls.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import time
from enum import IntEnum

VENDOR_ID = 0x05E3
PRODUCT_ID = 0x0144

# Control transfer plumbing
_REQUEST_TYPE_IN = 0xC0   # vendor | device-to-host
_REQUEST_TYPE_OUT = 0x40  # vendor | host-to-device
_REQUEST_REGISTER = 0x0C
_REQUEST_BUFFER = 0x04
_ANY_INDEX = 0x00

PORT_SCSI_SIZE = 0x0082
PORT_SCSI_STATUS = 0x0084
PORT_SCSI_CMD = 0x0085
PORT_PAR_CTRL = 0x0087
PORT_PAR_DATA = 0x0088

# IEEE1284 line states
_C1284_NSTROBE = 0x01
_C1284_NINIT = 0x04

IEEE1284_ADDR = 0x00
IEEE1284_RESET = 0x30
IEEE1284_SCSI = 0xE0

_IEEE_PREAMBLE = (0xFF, 0xAA, 0x55, 0x00, 0xFF, 0x87, 0x78)

SCSI_COMMAND_LEN = 6

#: Bytes fetched per length handshake.
#:
#: The vendor software announces a whole read in one handshake, but windowing
#: at 32 KB is what the two verified full scans used and it works, so it stays.
#: What does matter is the batch size in read_planes: 216 lines per READ is the
#: vendor's value and works; 64 does not, and the device simply sends nothing.
#: Tunable via ``RPS7200_MAX_WINDOW`` for probing.
MAX_WINDOW = int(os.environ.get("RPS7200_MAX_WINDOW", 0x8000))

#: Bytes per individual bulk transfer inside a window.
BULK_CHUNK = 0x4000

#: Control transfers can be slow to answer while the scanner is busy: at 1800
#: dpi a status read took longer than a 5s timeout allowed, aborting the scan.
_CONTROL_TIMEOUT_MS = 30_000
_BULK_TIMEOUT_MS = 30_000


class UsbStatus(IntEnum):
    OK = 0x00      # command accepted; send data-out if any
    READ = 0x01    # device has data; send expected length then read
    CHECK = 0x02   # check condition, sense data available
    BUSY = 0x03    # wait, poll status again
    AGAIN = 0x08   # re-send the command
    FAIL = 0x88
    ERROR = 0xFF


class UsbError(RuntimeError):
    """A libusb call failed."""


class NoDataYet(UsbError):
    """The scanner returned no data because it has not scanned that far yet."""


class ScannerNotFound(RuntimeError):
    """The scanner is not on the USB bus."""


# ---------------------------------------------------------------------------
# libusb bindings
# ---------------------------------------------------------------------------

_LIBUSB_PATHS = (
    "/usr/local/lib/libusb-1.0.dylib",
    "/opt/homebrew/lib/libusb-1.0.dylib",
    "/usr/lib/libusb-1.0.so.0",
    "/usr/local/lib/libusb-1.0.so.0",
)


def _load_libusb() -> ctypes.CDLL:
    override = os.environ.get("LIBUSB_PATH")
    for path in ([override] if override else []) + list(_LIBUSB_PATHS):
        if path and os.path.exists(path):
            return ctypes.CDLL(path)
    found = ctypes.util.find_library("usb-1.0")
    if found:
        return ctypes.CDLL(found)
    raise OSError(
        "Could not locate libusb-1.0. Install it (brew install libusb) or set "
        "LIBUSB_PATH."
    )


_lib = _load_libusb()


class _EndpointDescriptor(ctypes.Structure):
    _fields_ = [
        ("bLength", ctypes.c_uint8),
        ("bDescriptorType", ctypes.c_uint8),
        ("bEndpointAddress", ctypes.c_uint8),
        ("bmAttributes", ctypes.c_uint8),
        ("wMaxPacketSize", ctypes.c_uint16),
        ("bInterval", ctypes.c_uint8),
        ("bRefresh", ctypes.c_uint8),
        ("bSynchAddress", ctypes.c_uint8),
        ("extra", ctypes.POINTER(ctypes.c_ubyte)),
        ("extra_length", ctypes.c_int),
    ]


class _InterfaceDescriptor(ctypes.Structure):
    _fields_ = [
        ("bLength", ctypes.c_uint8),
        ("bDescriptorType", ctypes.c_uint8),
        ("bInterfaceNumber", ctypes.c_uint8),
        ("bAlternateSetting", ctypes.c_uint8),
        ("bNumEndpoints", ctypes.c_uint8),
        ("bInterfaceClass", ctypes.c_uint8),
        ("bInterfaceSubClass", ctypes.c_uint8),
        ("bInterfaceProtocol", ctypes.c_uint8),
        ("iInterface", ctypes.c_uint8),
        ("endpoint", ctypes.POINTER(_EndpointDescriptor)),
        ("extra", ctypes.POINTER(ctypes.c_ubyte)),
        ("extra_length", ctypes.c_int),
    ]


class _Interface(ctypes.Structure):
    _fields_ = [
        ("altsetting", ctypes.POINTER(_InterfaceDescriptor)),
        ("num_altsetting", ctypes.c_int),
    ]


class _ConfigDescriptor(ctypes.Structure):
    _fields_ = [
        ("bLength", ctypes.c_uint8),
        ("bDescriptorType", ctypes.c_uint8),
        ("wTotalLength", ctypes.c_uint16),
        ("bNumInterfaces", ctypes.c_uint8),
        ("bConfigurationValue", ctypes.c_uint8),
        ("iConfiguration", ctypes.c_uint8),
        ("bmAttributes", ctypes.c_uint8),
        ("MaxPower", ctypes.c_uint8),
        ("interface", ctypes.POINTER(_Interface)),
        ("extra", ctypes.POINTER(ctypes.c_ubyte)),
        ("extra_length", ctypes.c_int),
    ]


_ctx_p = ctypes.c_void_p
_dev_p = ctypes.c_void_p
_handle_p = ctypes.c_void_p

_lib.libusb_init.argtypes = [ctypes.POINTER(_ctx_p)]
_lib.libusb_init.restype = ctypes.c_int
_lib.libusb_exit.argtypes = [_ctx_p]
_lib.libusb_exit.restype = None
_lib.libusb_open_device_with_vid_pid.argtypes = [
    _ctx_p, ctypes.c_uint16, ctypes.c_uint16
]
_lib.libusb_open_device_with_vid_pid.restype = _handle_p
_lib.libusb_close.argtypes = [_handle_p]
_lib.libusb_close.restype = None
_lib.libusb_claim_interface.argtypes = [_handle_p, ctypes.c_int]
_lib.libusb_claim_interface.restype = ctypes.c_int
_lib.libusb_release_interface.argtypes = [_handle_p, ctypes.c_int]
_lib.libusb_release_interface.restype = ctypes.c_int
_lib.libusb_reset_device.argtypes = [_handle_p]
_lib.libusb_reset_device.restype = ctypes.c_int
_lib.libusb_clear_halt.argtypes = [_handle_p, ctypes.c_ubyte]
_lib.libusb_clear_halt.restype = ctypes.c_int
_lib.libusb_get_device.argtypes = [_handle_p]
_lib.libusb_get_device.restype = _dev_p
_lib.libusb_get_active_config_descriptor.argtypes = [
    _dev_p, ctypes.POINTER(ctypes.POINTER(_ConfigDescriptor))
]
_lib.libusb_get_active_config_descriptor.restype = ctypes.c_int
_lib.libusb_free_config_descriptor.argtypes = [ctypes.POINTER(_ConfigDescriptor)]
_lib.libusb_free_config_descriptor.restype = None
_lib.libusb_control_transfer.argtypes = [
    _handle_p,
    ctypes.c_uint8,
    ctypes.c_uint8,
    ctypes.c_uint16,
    ctypes.c_uint16,
    ctypes.POINTER(ctypes.c_ubyte),
    ctypes.c_uint16,
    ctypes.c_uint,
]
_lib.libusb_control_transfer.restype = ctypes.c_int
_lib.libusb_bulk_transfer.argtypes = [
    _handle_p,
    ctypes.c_ubyte,
    ctypes.POINTER(ctypes.c_ubyte),
    ctypes.c_int,
    ctypes.POINTER(ctypes.c_int),
    ctypes.c_uint,
]
_lib.libusb_bulk_transfer.restype = ctypes.c_int
_lib.libusb_error_name.argtypes = [ctypes.c_int]
_lib.libusb_error_name.restype = ctypes.c_char_p


def _err(code: int) -> str:
    name = _lib.libusb_error_name(code)
    return name.decode() if name else f"error {code}"


LIBUSB_ERROR_TIMEOUT = -7
LIBUSB_ERROR_PIPE = -9
LIBUSB_ERROR_NO_DEVICE = -4
LIBUSB_ERROR_OVERFLOW = -8


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


class Transport:
    """Low-level command/data channel to the scanner."""

    def __init__(self, verbose: bool = False, max_window: int = MAX_WINDOW):
        self.verbose = verbose
        self.max_window = max_window
        self._ctx = _ctx_p()
        self._handle: _handle_p | None = None
        self._interface: int | None = None
        self.bulk_in_ep = 0x81
        self.max_packet_size = 512

        rc = _lib.libusb_init(ctypes.byref(self._ctx))
        if rc < 0:
            raise UsbError(f"libusb_init failed: {_err(rc)}")

    # -- lifecycle ---------------------------------------------------------

    def _log(self, message: str) -> None:
        if self.verbose:
            print(f"[usb] {message}")

    def _raw_open(self) -> None:
        handle = _lib.libusb_open_device_with_vid_pid(
            self._ctx, VENDOR_ID, PRODUCT_ID
        )
        if not handle:
            raise ScannerNotFound(
                f"no device {VENDOR_ID:#06x}:{PRODUCT_ID:#06x} on the USB bus "
                "(is the scanner powered on?)"
            )
        self._handle = handle
        self._discover_endpoints()
        rc = _lib.libusb_claim_interface(self._handle, 0)
        if rc < 0:
            _lib.libusb_close(self._handle)
            self._handle = None
            raise UsbError(f"could not claim interface 0: {_err(rc)}")
        self._interface = 0

    def open(self, reset: bool = False) -> Transport:
        """Open the scanner.

        No reset by default. Captures of the vendor software show it never
        sends IEEE1284 RESET (0x30) at all -- only 0x00 and 0xE0 -- and issuing
        one here left the scanner working for exactly one session and wedged for
        the next. Pass ``reset=True`` only to recover a device that is already
        unresponsive.

        Never calls ``libusb_reset_device`` either: on macOS a port reset makes
        this device drop off the bus for good until it is power-cycled.
        """
        self._raw_open()

        if reset:
            # Clear a stalled bulk endpoint first: while it is halted every
            # control transfer times out, so the bridge reset below cannot get
            # through until this succeeds.
            try:
                self.clear_halt()
            except Exception:
                pass
            try:
                self.reset()
            except UsbError as exc:
                self.close()
                raise UsbError(
                    "the scanner is not responding to control transfers "
                    f"({exc}). It is wedged from an earlier failed session; "
                    "power-cycle it and try again."
                ) from exc

        self._log(
            f"opened, bulk-in ep {self.bulk_in_ep:#04x}, "
            f"max packet {self.max_packet_size}"
        )
        return self

    def _discover_endpoints(self) -> None:
        device = _lib.libusb_get_device(self._handle)
        config = ctypes.POINTER(_ConfigDescriptor)()
        rc = _lib.libusb_get_active_config_descriptor(device, ctypes.byref(config))
        if rc < 0:
            self._log(f"config descriptor unavailable ({_err(rc)}); assuming 0x81")
            return
        try:
            cfg = config.contents
            for i in range(cfg.bNumInterfaces):
                iface = cfg.interface[i]
                for a in range(iface.num_altsetting):
                    alt = iface.altsetting[a]
                    for e in range(alt.bNumEndpoints):
                        ep = alt.endpoint[e]
                        is_bulk = (ep.bmAttributes & 0x03) == 0x02
                        is_in = (ep.bEndpointAddress & 0x80) != 0
                        if is_bulk and is_in:
                            self.bulk_in_ep = ep.bEndpointAddress
                            self.max_packet_size = ep.wMaxPacketSize or 512
                            return
        finally:
            _lib.libusb_free_config_descriptor(config)

    def close(self) -> None:
        if self._handle:
            if self._interface is not None:
                _lib.libusb_release_interface(self._handle, self._interface)
                self._interface = None
            _lib.libusb_close(self._handle)
            self._handle = None
        if self._ctx:
            _lib.libusb_exit(self._ctx)
            self._ctx = _ctx_p()

    def __enter__(self) -> Transport:
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _require(self) -> None:
        if not self._handle:
            raise UsbError("transport is not open")

    # -- primitives --------------------------------------------------------

    def _control_out(self, port: int, value: int) -> None:
        self._require()
        buf = (ctypes.c_ubyte * 1)(value & 0xFF)
        rc = _lib.libusb_control_transfer(
            self._handle,
            _REQUEST_TYPE_OUT,
            _REQUEST_REGISTER,
            port,
            _ANY_INDEX,
            buf,
            1,
            _CONTROL_TIMEOUT_MS,
        )
        if rc < 0:
            raise UsbError(f"control out to port {port:#06x} failed: {_err(rc)}")

    def _control_in(self) -> int:
        self._require()
        buf = (ctypes.c_ubyte * 1)()
        rc = _lib.libusb_control_transfer(
            self._handle,
            _REQUEST_TYPE_IN,
            _REQUEST_REGISTER,
            PORT_SCSI_STATUS,
            _ANY_INDEX,
            buf,
            1,
            _CONTROL_TIMEOUT_MS,
        )
        if rc < 0:
            raise UsbError(f"status read failed: {_err(rc)}")
        return buf[0]

    def _announce_length(self, size: int) -> None:
        """Tell the bridge how many bytes the next bulk read will fetch."""
        self._require()
        payload = bytearray(8)
        payload[4] = size & 0xFF
        payload[5] = (size >> 8) & 0xFF
        payload[6] = (size >> 16) & 0xFF
        payload[7] = (size >> 24) & 0xFF
        buf = (ctypes.c_ubyte * 8)(*payload)
        rc = _lib.libusb_control_transfer(
            self._handle,
            _REQUEST_TYPE_OUT,
            _REQUEST_BUFFER,
            PORT_SCSI_SIZE,
            _ANY_INDEX,
            buf,
            8,
            _CONTROL_TIMEOUT_MS,
        )
        if rc < 0:
            raise UsbError(f"length handshake for {size} bytes failed: {_err(rc)}")

    def _bulk_read_into(self, view: memoryview, timeout_ms: int) -> int:
        """Read up to ``len(view)`` bytes. Returns the count actually read."""
        self._require()
        buf = (ctypes.c_ubyte * len(view)).from_buffer(view)
        transferred = ctypes.c_int(0)
        rc = _lib.libusb_bulk_transfer(
            self._handle,
            self.bulk_in_ep,
            buf,
            len(view),
            ctypes.byref(transferred),
            timeout_ms,
        )
        if rc < 0 and not (
            rc == LIBUSB_ERROR_TIMEOUT and transferred.value > 0
        ):
            # Drain the stall before it poisons every later control transfer.
            try:
                self.clear_halt()
            except Exception:
                pass
            raise UsbError(
                f"bulk read of {len(view)} bytes failed after "
                f"{transferred.value} bytes: {_err(rc)}"
            )
        return transferred.value

    def ieee_command(self, command: int) -> None:
        """Put the bridge into a mode via the IEEE1284 daisy sequence."""
        for byte in _IEEE_PREAMBLE:
            self._control_out(PORT_PAR_DATA, byte)
        self._control_out(PORT_PAR_DATA, command)
        time.sleep(0.003)
        self._control_out(PORT_PAR_CTRL, _C1284_NINIT | _C1284_NSTROBE)
        self._control_out(PORT_PAR_CTRL, _C1284_NINIT)
        self._control_out(PORT_PAR_DATA, 0xFF)

    def clear_halt(self) -> None:
        """Clear a stalled bulk-in endpoint.

        A bulk read that times out mid-transfer leaves the endpoint stalled,
        and the bridge's IEEE1284 reset cannot clear that -- every later control
        transfer then times out and only a power cycle recovers it. Clearing the
        halt is the targeted fix and avoids the power cycle.
        """
        if not self._handle:
            return
        rc = _lib.libusb_clear_halt(self._handle, self.bulk_in_ep)
        self._log(f"clear_halt on ep {self.bulk_in_ep:#04x}: {_err(rc) if rc else 'ok'}")

    def reset(self) -> None:
        """Reset the bridge's IEEE1284 layer (not a USB port reset)."""
        self._log("bridge reset")
        self.ieee_command(IEEE1284_RESET)

    # -- SCSI ---------------------------------------------------------------

    def _send_command(self, command: bytes) -> int:
        if len(command) != SCSI_COMMAND_LEN:
            raise ValueError(
                f"SCSI command must be {SCSI_COMMAND_LEN} bytes, got {len(command)}"
            )
        self.ieee_command(IEEE1284_SCSI)
        for byte in command:
            self._control_out(PORT_SCSI_CMD, byte)
        return self._control_in()

    def _wait_not_busy(self, deadline: float, context: str) -> int:
        """Poll the status port until the device stops reporting BUSY.

        The device commonly returns BUSY right after a transfer completes and
        is not ready for the next command until it clears. Skipping this makes
        the following control transfer time out.
        """
        status = self._control_in()
        while status == UsbStatus.BUSY:
            if time.monotonic() > deadline:
                raise UsbError(f"{context}: device stayed busy")
            status = self._control_in()
        return status

    def _read_payload(self, size: int, timeout_ms: int) -> bytes:
        """Fetch ``size`` bytes, windowing the length handshake.

        This is the whole point of the module: the stock backend announces the
        full length once and then reads until it has everything. This scanner
        stops after 32 KB, so the announce/read cycle is repeated per window.
        """
        out = bytearray(size)
        view = memoryview(out)
        got = 0
        while got < size:
            window = min(self.max_window, size - got)
            self._announce_length(window)
            window_got = 0
            while window_got < window:
                chunk = min(BULK_CHUNK, window - window_got)
                n = self._bulk_read_into(
                    view[got + window_got : got + window_got + chunk], timeout_ms
                )
                if n == 0:
                    # Normal: the scanner answers a READ with zero bytes while
                    # it has nothing scanned yet. Captures of the vendor
                    # software show 82% of its reads returning empty at 1800
                    # dpi, retried every ~20ms. Report it so the caller can
                    # retry rather than treating it as an error.
                    raise NoDataYet(
                        f"scanner has no data ready ({got + window_got} of "
                        f"{size} bytes so far)"
                    )
                window_got += n
            got += window_got
            self._log(f"read {got}/{size} bytes")
        return bytes(out)

    def command(
        self,
        command: bytes,
        data: bytes | None = None,
        read_size: int = 0,
        timeout_ms: int = _BULK_TIMEOUT_MS,
        max_wait_s: float = 60.0,
    ) -> bytes:
        """Run one SCSI command, handling the bridge's retry protocol.

        Returns the payload for a read command, or ``b""`` otherwise.
        """
        self._require()
        try:
            return self._command(command, data, read_size, timeout_ms, max_wait_s)
        except CheckCondition:
            # A normal SCSI response, not a transport fault. Must NOT reset:
            # the caller's next move is REQUEST SENSE, and a bridge reset
            # discards the sense data that explains what went wrong.
            raise
        except UsbError:
            # No reset here. It was added to clear a command abandoned
            # mid-sequence, but captures show the vendor software never sends
            # IEEE1284 RESET, and doing so is what left the *next* session
            # unable to talk to the scanner at all.
            raise

    def _command(
        self,
        command: bytes,
        data: bytes | None,
        read_size: int,
        timeout_ms: int,
        max_wait_s: float,
    ) -> bytes:
        deadline = time.monotonic() + max_wait_s

        while True:
            status = self._send_command(command)

            if status == UsbStatus.AGAIN:
                if time.monotonic() > deadline:
                    raise UsbError(f"command {command[0]:#04x} kept asking for retry")
                time.sleep(1.0)
                continue

            if status == UsbStatus.BUSY:
                # Poll the status port rather than re-issuing the command.
                while status == UsbStatus.BUSY:
                    if time.monotonic() > deadline:
                        raise UsbError(f"command {command[0]:#04x} stayed busy")
                    status = self._control_in()
                if status == UsbStatus.AGAIN:
                    time.sleep(1.0)
                    continue

            if status == UsbStatus.OK:
                if data:
                    for byte in data:
                        self._control_out(PORT_SCSI_CMD, byte)
                    status = self._wait_not_busy(
                        deadline, f"command {command[0]:#04x} data-out"
                    )
                    if status == UsbStatus.CHECK:
                        raise CheckCondition(command[0])
                    if status not in (UsbStatus.OK, UsbStatus.READ):
                        raise UsbError(
                            f"command {command[0]:#04x} rejected data-out "
                            f"(status {status:#04x})"
                        )
                return b""

            if status == UsbStatus.READ:
                if read_size <= 0:
                    raise UsbError(
                        f"command {command[0]:#04x} has data but no read size given"
                    )
                payload = self._read_payload(read_size, timeout_ms)
                # The device reports BUSY here until it is ready for the next
                # command; not draining that stalls the following transfer.
                final = self._wait_not_busy(
                    deadline, f"command {command[0]:#04x} data-in"
                )
                if final == UsbStatus.CHECK:
                    raise CheckCondition(command[0])
                return payload

            if status == UsbStatus.CHECK:
                raise CheckCondition(command[0])

            raise UsbError(
                f"command {command[0]:#04x} returned unexpected status {status:#04x}"
            )


class CheckCondition(UsbError):
    """The device reported CHECK CONDITION; sense data is available."""

    def __init__(self, opcode: int):
        self.opcode = opcode
        super().__init__(
            f"command {opcode:#04x} returned CHECK CONDITION (sense available)"
        )
