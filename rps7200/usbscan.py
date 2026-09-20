"""The same protocol, carried by Windows' own scanner driver.

On Windows this scanner arrives with `usbscan.sys` already bound to it, by an
INF from Pacific Image whose manufacturer string is "Film Scanner WIA Vendor".
libusb cannot open a device another driver holds, which is why every comparable
project -- SANE, NegPy, the `pieusb` Python backend, nkscan -- tells people to
replace that driver with WinUSB using Zadig. That works, and it stops CyberView
and VueScan seeing the scanner until it is put back.

It is not necessary here, because `usbscan.sys` already exposes exactly the two
things this driver needs, and the vendor's own software proves it. Reading
`C:\\Windows\\System32\\MF5000_x64.dll`, CyberView's scan engine, for the
usbscan IOCTL codes::

    IOCTL_WRITE_REGISTERS   0x80002010   x59
    IOCTL_READ_REGISTERS    0x8000200C   x27
    IOCTL_SEND_USB_REQUEST  0x80002024   x0        <- never used
    libusb / WinUSB / UsbDk              absent

Those two IOCTLs build a vendor control transfer: request type 0xC0 for in and
0x40 for out, `bRequest = 0x04` when the length is more than one byte and
`0x0C` when it is one, `wValue` the port, `wIndex` as given. That is
character for character what `usb_transport` already sends, and this driver
makes only three shapes of control transfer:

    _control_out      OUT  0x0C  1 byte    -> WRITE_REGISTERS
    _control_in       IN   0x0C  1 byte    -> READ_REGISTERS
    _announce_length  OUT  0x04  8 bytes   -> WRITE_REGISTERS

All three are covered with nothing left over, which is also why the protocol is
byte-at-a-time in the first place: it is not a quirk of the scanner, it is the
Windows register model its firmware was designed around.

**What is proven and what is not.** The handle opens unelevated, the driver
answers `IOCTL_GET_VERSION`, and `IOCTL_GET_PIPE_CONFIGURATION` returns the
same endpoints libusb discovers -- bulk IN 0x81 at 512 bytes, bulk OUT 0x02,
interrupt IN 0x83. No *write* has been sent through it. That `WRITE_REGISTERS`
reaches the wire as 0x40/0x0C is documented, not measured, and the same goes
for `ReadFile` landing on 0x81 rather than some other pipe.

**The timeout is the sharp edge.** `IOCTL_SET_TIMEOUT` counts in whole seconds
and is documented to a maximum of 214. The infrared pass has a floor of about
212 s. Two seconds of margin, on precisely the failure CLAUDE.md says costs a
power cycle -- and whether an expired `ReadFile` abandons the read or merely
returns short is not known. The reads here are windowed at 32 KB and the status
byte is polled before each, so the clock should only start once the device has
data; that is a reason to expect it to be fine, not a measurement.
"""
from __future__ import annotations

import ctypes
import sys

if sys.platform == "win32":
    from ctypes import wintypes
else:
    # `ctypes.wintypes` refuses to import anywhere else, and importing this
    # module must not depend on the platform: the rule mapping a transfer
    # length to a bRequest, and the pipe-table decode, are ordinary logic and
    # are tested on whatever machine runs the suite. Nothing that actually
    # touches Windows is reachable off it -- see `_api`.
    class wintypes:                                  # noqa: N801
        DWORD = ctypes.c_ulong
        HANDLE = ctypes.c_void_p
        BOOL = ctypes.c_int
        HWND = ctypes.c_void_p
        LPCWSTR = ctypes.c_wchar_p

from .usb_transport import (
    PORT_SCSI_SIZE,
    PORT_SCSI_STATUS,
    Transport,
    UsbError,
    _ANY_INDEX,
    _REQUEST_BUFFER,
    _REQUEST_REGISTER,
    PRODUCT_ID,
    VENDOR_ID,
)

#: The device interface class usbscan.sys registers under: still image.
_IMAGE_CLASS = "{6bdd1fc6-810f-11d0-bec7-08002be2092f}"


def _ioctl(index: int) -> int:
    """CTL_CODE(FILE_DEVICE_USB_SCAN, 0x800 + index, METHOD_BUFFERED, ANY)."""
    return (0x8000 << 16) | ((0x800 + index) << 2)


IOCTL_GET_VERSION = _ioctl(0)
IOCTL_READ_REGISTERS = _ioctl(3)
IOCTL_WRITE_REGISTERS = _ioctl(4)
IOCTL_GET_DEVICE_DESCRIPTOR = _ioctl(6)
IOCTL_RESET_PIPE = _ioctl(7)
IOCTL_GET_PIPE_CONFIGURATION = _ioctl(10)
IOCTL_SET_TIMEOUT = _ioctl(11)

#: What `IOCTL_RESET_PIPE` wants: a pipe *kind*, not an endpoint address.
RESET_READ_DATA_PIPE = 1

#: Documented ceiling for IOCTL_SET_TIMEOUT, in seconds. The infrared pass's
#: floor is ~212 s, so this is very nearly not enough; see the module docstring.
MAX_TIMEOUT_S = 214

#: Bulk pipe types as IOCTL_GET_PIPE_CONFIGURATION reports them.
_PIPE_BULK = 2


class _IO_BLOCK(ctypes.Structure):
    """What READ_REGISTERS and WRITE_REGISTERS take.

    `uOffset` is the wValue -- for this bridge, the port. `uIndex` is the
    wIndex. `bRequest` is not here at all: the driver derives it from
    `uLength`, which is the whole reason this maps onto our three transfers
    without anything left over.
    """

    _fields_ = [("uOffset", ctypes.c_uint),
                ("uLength", ctypes.c_uint),
                ("pbyData", ctypes.POINTER(ctypes.c_ubyte)),
                ("uIndex", ctypes.c_uint)]


class _USBSCAN_TIMEOUT(ctypes.Structure):
    """Whole seconds, not milliseconds. See MAX_TIMEOUT_S."""

    _fields_ = [("TimeoutRead", ctypes.c_ushort),
                ("TimeoutWrite", ctypes.c_ushort),
                ("TimeoutEvent", ctypes.c_ushort)]


class _GUID(ctypes.Structure):
    _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort),
                ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]


class _SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", _GUID),
                ("Flags", wintypes.DWORD),
                ("Reserved", ctypes.POINTER(ctypes.c_ulong))]


_IMAGE_GUID = _GUID(0x6BDD1FC6, 0x810F, 0x11D0,
                    (ctypes.c_ubyte * 8)(0xBE, 0xC7, 0x08, 0x00,
                                         0x2B, 0xE2, 0x09, 0x2F))


def derived_request(length: int) -> int:
    """The bRequest usbscan.sys will send for a transfer of `length` bytes.

    Not a choice this code makes -- it is the driver's rule, written down here
    so that a transfer it cannot express is caught before it is sent rather
    than going out as the wrong request.
    """
    return _REQUEST_BUFFER if length > 1 else _REQUEST_REGISTER


def pipes(blob: bytes) -> list[dict]:
    """Decode what IOCTL_GET_PIPE_CONFIGURATION returns.

    ULONG NumberOfPipes, then eight fixed 8-byte records: max packet size,
    endpoint address, interval, and a 4-byte pipe type.
    """
    if len(blob) < 4:
        return []
    count = int.from_bytes(blob[0:4], "little")
    out = []
    for i in range(min(count, (len(blob) - 4) // 8)):
        record = blob[4 + i * 8: 12 + i * 8]
        out.append({
            "max_packet_size": int.from_bytes(record[0:2], "little"),
            "endpoint": record[2],
            "interval": record[3],
            "type": int.from_bytes(record[4:8], "little"),
        })
    return out


def _api():
    """The two DLLs, loaded on use so this module imports anywhere."""
    if sys.platform != "win32":
        raise UsbError("the usbscan transport is Windows only")
    setup = ctypes.WinDLL("setupapi", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    setup.SetupDiGetClassDevsW.restype = wintypes.HANDLE
    setup.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(_GUID), wintypes.LPCWSTR,
                                           wintypes.HWND, wintypes.DWORD]
    setup.SetupDiEnumDeviceInterfaces.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(_GUID), wintypes.DWORD,
        ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA)]
    setup.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
    setup.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA),
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p]
    setup.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
    setup.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                wintypes.HANDLE]
    k32.DeviceIoControl.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p]
    k32.DeviceIoControl.restype = wintypes.BOOL
    k32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                             ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k32.ReadFile.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    return setup, k32


def interfaces() -> list[str]:
    """Every still-image device interface path on this machine."""
    setup, _ = _api()
    DIGCF_PRESENT_INTERFACE = 0x02 | 0x10
    info = setup.SetupDiGetClassDevsW(ctypes.byref(_IMAGE_GUID), None, None,
                                      DIGCF_PRESENT_INTERFACE)
    found, iface, i = [], _SP_DEVICE_INTERFACE_DATA(), 0
    iface.cbSize = ctypes.sizeof(iface)
    try:
        while setup.SetupDiEnumDeviceInterfaces(info, None,
                                                ctypes.byref(_IMAGE_GUID), i,
                                                ctypes.byref(iface)):
            need = wintypes.DWORD()
            setup.SetupDiGetDeviceInterfaceDetailW(
                info, ctypes.byref(iface), None, 0, ctypes.byref(need), None)
            buf = ctypes.create_string_buffer(need.value)
            # cbSize of the *detail* struct, which is 8 on x64 and 6 on x86 --
            # the alignment differs from the buffer it introduces.
            ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0] = (
                8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6)
            if setup.SetupDiGetDeviceInterfaceDetailW(
                    info, ctypes.byref(iface), buf, need.value,
                    ctypes.byref(need), None):
                found.append(ctypes.wstring_at(
                    ctypes.addressof(buf) + ctypes.sizeof(wintypes.DWORD)))
            i += 1
    finally:
        setup.SetupDiDestroyDeviceInfoList(info)
    return found


def scanner_interface() -> str | None:
    """This scanner's interface path, or None if it is not one of them.

    Matched on the ids in the path rather than by opening each candidate: a
    machine may have other imaging devices, and opening one to ask what it is
    would be rude to whatever is using it.
    """
    wanted = f"vid_{VENDOR_ID:04x}&pid_{PRODUCT_ID:04x}"
    for path in interfaces():
        if wanted in path.lower():
            return path
    return None


def available() -> bool:
    """Whether this machine can use this transport at all."""
    if sys.platform != "win32":
        return False
    try:
        return scanner_interface() is not None
    except Exception:
        return False


class UsbscanTransport(Transport):
    """`Transport`, with its device primitives carried by usbscan.sys.

    Subclassed rather than composed because `Transport` already separates the
    two: everything above `_control_out`/`_control_in`/`_announce_length`/
    `_bulk_read_into` -- the IEEE1284 daisy, the SCSI framing, the windowed
    payload read -- is written purely in terms of them and does not change.
    `tests/test_transport_reads.py` replaces the same four methods for the
    same reason.
    """

    #: Read timeout asked of the driver, in seconds. Deliberately just under
    #: the documented ceiling; see MAX_TIMEOUT_S and the module docstring.
    READ_TIMEOUT_S = MAX_TIMEOUT_S

    def __init__(self, verbose: bool = False, max_window: int | None = None):
        # Deliberately not Transport.__init__: there is no libusb context here,
        # and calling it would load a library this path exists to avoid.
        from .usb_transport import MAX_WINDOW
        self.verbose = verbose
        self.max_window = MAX_WINDOW if max_window is None else max_window
        self.bulk_in_ep = 0x81
        self.max_packet_size = 512
        self._handle = None
        self._interface = None
        self._path: str | None = None
        self._setup = self._k32 = None

    # -- lifecycle ---------------------------------------------------------

    def _raw_open(self) -> None:
        from .usb_transport import ScannerNotFound

        self._setup, self._k32 = _api()
        path = scanner_interface()
        if path is None:
            raise ScannerNotFound(
                f"no still-image device {VENDOR_ID:#06x}:{PRODUCT_ID:#06x} on "
                "this machine (is the scanner powered on, and the cable in?)")
        GENERIC_RW = 0x80000000 | 0x40000000
        SHARE_RW = 0x00000001 | 0x00000002
        OPEN_EXISTING = 3
        handle = self._k32.CreateFileW(path, GENERIC_RW, SHARE_RW, None,
                                       OPEN_EXISTING, 0, None)
        if handle == ctypes.c_void_p(-1).value or not handle:
            raise ScannerNotFound(
                f"the scanner is on the bus but {path} would not open "
                f"(WinError {ctypes.get_last_error()}). Something else may "
                "have it -- close CyberView, VueScan or Windows Fax and Scan.")
        self._handle, self._path, self._interface = handle, path, 0
        self._discover_endpoints()
        self._set_timeout(self.READ_TIMEOUT_S)
        self._log(f"opened via usbscan.sys, {path}")

    def _discover_endpoints(self) -> None:
        """Take the bulk IN endpoint from the driver's own pipe table."""
        blob = self._ioctl_out(IOCTL_GET_PIPE_CONFIGURATION, b"", 512)
        for pipe in pipes(blob):
            if pipe["type"] == _PIPE_BULK and pipe["endpoint"] & 0x80:
                self.bulk_in_ep = pipe["endpoint"]
                self.max_packet_size = pipe["max_packet_size"] or 512
                return
        self._log("no bulk IN pipe reported; assuming 0x81")

    def close(self) -> None:
        if self._handle and self._k32:
            self._k32.CloseHandle(self._handle)
        self._handle = self._path = None
        self._interface = None

    # -- the IOCTL plumbing ------------------------------------------------

    def _ioctl_out(self, code: int, payload: bytes, size: int) -> bytes:
        """An IOCTL that takes `payload` in and hands `size` bytes back."""
        out = (ctypes.c_ubyte * max(size, 1))()
        got = wintypes.DWORD()
        ok = self._k32.DeviceIoControl(
            self._handle, code,
            (ctypes.c_char_p(payload) if payload else None), len(payload),
            ctypes.byref(out), size, ctypes.byref(got), None)
        if not ok:
            raise UsbError(f"ioctl {code:#010x} failed: "
                           f"WinError {ctypes.get_last_error()}")
        return bytes(out)[:got.value]

    def _registers(self, code: int, port: int, data: bytes | None,
                   length: int) -> bytes:
        """READ_REGISTERS or WRITE_REGISTERS, which is a control transfer.

        The data buffer is passed as the *output* buffer as well as through
        `pbyData`, which is what the driver's documentation asks for in both
        directions.
        """
        if self._handle is None:
            raise UsbError("transport is not open")
        buf = (ctypes.c_ubyte * max(length, 1))()
        if data:
            buf[:len(data)] = data
        block = _IO_BLOCK()
        block.uOffset = port
        block.uLength = length
        block.uIndex = _ANY_INDEX
        block.pbyData = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))
        got = wintypes.DWORD()
        ok = self._k32.DeviceIoControl(
            self._handle, code, ctypes.byref(block), ctypes.sizeof(block),
            ctypes.byref(buf), length, ctypes.byref(got), None)
        if not ok:
            which = "read" if code == IOCTL_READ_REGISTERS else "write"
            raise UsbError(
                f"register {which} on port {port:#06x} failed: "
                f"WinError {ctypes.get_last_error()}")
        return bytes(buf)[:length]

    def _set_timeout(self, seconds: int) -> None:
        """Whole seconds, and the driver's own ceiling is 214 of them."""
        if seconds > MAX_TIMEOUT_S:
            self._log(f"read timeout clamped from {seconds}s to {MAX_TIMEOUT_S}s, "
                      "which is this driver's documented maximum")
            seconds = MAX_TIMEOUT_S
        block = _USBSCAN_TIMEOUT(TimeoutRead=seconds, TimeoutWrite=seconds,
                                 TimeoutEvent=seconds)
        got = wintypes.DWORD()
        ok = self._k32.DeviceIoControl(
            self._handle, IOCTL_SET_TIMEOUT, ctypes.byref(block),
            ctypes.sizeof(block), ctypes.byref(block), ctypes.sizeof(block),
            ctypes.byref(got), None)
        if not ok:
            self._log("IOCTL_SET_TIMEOUT refused: "
                      f"WinError {ctypes.get_last_error()}; the driver's "
                      "default of 120 s applies")

    # -- the four primitives Transport is written in terms of ---------------

    def _control_out(self, port: int, value: int) -> None:
        self._expect(_REQUEST_REGISTER, 1)
        self._registers(IOCTL_WRITE_REGISTERS, port, bytes([value & 0xFF]), 1)

    def _control_in(self) -> int:
        self._expect(_REQUEST_REGISTER, 1)
        return self._registers(IOCTL_READ_REGISTERS, PORT_SCSI_STATUS, None, 1)[0]

    def _announce_length(self, size: int) -> None:
        payload = bytes([0, 0, 0, 0,
                         size & 0xFF, (size >> 8) & 0xFF,
                         (size >> 16) & 0xFF, (size >> 24) & 0xFF])
        self._expect(_REQUEST_BUFFER, len(payload))
        self._registers(IOCTL_WRITE_REGISTERS, PORT_SCSI_SIZE, payload,
                        len(payload))

    def _bulk_read_into(self, view: memoryview, timeout_ms: int) -> int:
        """Read up to ``len(view)`` bytes. Returns the count actually read.

        The timeout is not per call here: usbscan.sys holds one, in seconds,
        set when the handle opened. A caller asking for something far shorter
        is asking for something this driver cannot give, so it is logged rather
        than silently ignored.
        """
        if self._handle is None:
            raise UsbError("transport is not open")
        wanted = len(view)
        buf = (ctypes.c_ubyte * wanted).from_buffer(view)
        read = wintypes.DWORD()
        ok = self._k32.ReadFile(self._handle, ctypes.byref(buf), wanted,
                                ctypes.byref(read), None)
        if not ok:
            error = ctypes.get_last_error()
            try:
                self.clear_halt()
            except Exception:
                pass
            raise UsbError(f"bulk read of {wanted} bytes failed after "
                           f"{read.value} bytes: WinError {error}")
        return read.value

    def clear_halt(self) -> None:
        """Reset the read pipe. Takes a pipe *kind*, not an endpoint address."""
        if self._handle is None:
            return
        kind = ctypes.c_uint(RESET_READ_DATA_PIPE)
        got = wintypes.DWORD()
        ok = self._k32.DeviceIoControl(
            self._handle, IOCTL_RESET_PIPE, ctypes.byref(kind),
            ctypes.sizeof(kind), None, 0, ctypes.byref(got), None)
        self._log(f"reset read pipe: "
                  f"{'ok' if ok else 'WinError %d' % ctypes.get_last_error()}")

    # -- guards -------------------------------------------------------------

    def _expect(self, request: int, length: int) -> None:
        """Refuse a transfer usbscan.sys would send as a different request.

        The driver picks bRequest from the length and offers no way to say
        otherwise. Every transfer this driver makes happens to agree, and that
        is load-bearing rather than lucky -- so if one ever stops agreeing it
        should say so here rather than go out as the wrong request and be
        debugged from the far end.
        """
        actual = derived_request(length)
        if actual != request:
            raise UsbError(
                f"usbscan.sys would send bRequest {actual:#04x} for a "
                f"{length}-byte transfer, but {request:#04x} was wanted. "
                "This transfer needs IOCTL_SEND_USB_REQUEST; see the module "
                "docstring.")
