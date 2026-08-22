"""Minimal ctypes binding to libsane.

Only the parts of the SANE 1.0 application interface this project needs are
declared. Struct layouts follow ``<sane/sane.h>``; ``SANE_Word`` is a plain C
``int``, and the option-descriptor constraint is a union of three pointers.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
from enum import IntEnum

# ---------------------------------------------------------------------------
# Scalar types
# ---------------------------------------------------------------------------

SANE_Word = ctypes.c_int
SANE_Bool = SANE_Word
SANE_Int = SANE_Word
SANE_Byte = ctypes.c_ubyte
SANE_Handle = ctypes.c_void_p
SANE_String_Const = ctypes.c_char_p

FIXED_SCALE_SHIFT = 16


def fix(value: float) -> int:
    """Convert a float to SANE's fixed-point representation."""
    return int(round(value * (1 << FIXED_SCALE_SHIFT)))


def unfix(value: int) -> float:
    """Convert SANE fixed-point back to a float."""
    return value / (1 << FIXED_SCALE_SHIFT)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Status(IntEnum):
    GOOD = 0
    UNSUPPORTED = 1
    CANCELLED = 2
    DEVICE_BUSY = 3
    INVAL = 4
    EOF = 5
    JAMMED = 6
    NO_DOCS = 7
    COVER_OPEN = 8
    IO_ERROR = 9
    NO_MEM = 10
    ACCESS_DENIED = 11


class ValueType(IntEnum):
    BOOL = 0
    INT = 1
    FIXED = 2
    STRING = 3
    BUTTON = 4
    GROUP = 5


class Unit(IntEnum):
    NONE = 0
    PIXEL = 1
    BIT = 2
    MM = 3
    DPI = 4
    PERCENT = 5
    MICROSECOND = 6


class ConstraintType(IntEnum):
    NONE = 0
    RANGE = 1
    WORD_LIST = 2
    STRING_LIST = 3


class Frame(IntEnum):
    GRAY = 0
    RGB = 1
    RED = 2
    GREEN = 3
    BLUE = 4


class Action(IntEnum):
    GET_VALUE = 0
    SET_VALUE = 1
    SET_AUTO = 2


CAP_SOFT_SELECT = 1 << 0
CAP_HARD_SELECT = 1 << 1
CAP_SOFT_DETECT = 1 << 2
CAP_EMULATED = 1 << 3
CAP_AUTOMATIC = 1 << 4
CAP_INACTIVE = 1 << 5
CAP_ADVANCED = 1 << 6

INFO_INEXACT = 1 << 0
INFO_RELOAD_OPTIONS = 1 << 1
INFO_RELOAD_PARAMS = 1 << 2


def is_active(cap: int) -> bool:
    return (cap & CAP_INACTIVE) == 0


def is_settable(cap: int) -> bool:
    return (cap & CAP_SOFT_SELECT) != 0


# ---------------------------------------------------------------------------
# Structures
# ---------------------------------------------------------------------------


class SANE_Device(ctypes.Structure):
    _fields_ = [
        ("name", SANE_String_Const),
        ("vendor", SANE_String_Const),
        ("model", SANE_String_Const),
        ("type", SANE_String_Const),
    ]


class SANE_Range(ctypes.Structure):
    _fields_ = [
        ("min", SANE_Word),
        ("max", SANE_Word),
        ("quant", SANE_Word),
    ]


class _Constraint(ctypes.Union):
    _fields_ = [
        ("string_list", ctypes.POINTER(SANE_String_Const)),
        ("word_list", ctypes.POINTER(SANE_Word)),
        ("range", ctypes.POINTER(SANE_Range)),
    ]


class SANE_Option_Descriptor(ctypes.Structure):
    _fields_ = [
        ("name", SANE_String_Const),
        ("title", SANE_String_Const),
        ("desc", SANE_String_Const),
        ("type", ctypes.c_int),
        ("unit", ctypes.c_int),
        ("size", SANE_Int),
        ("cap", SANE_Int),
        ("constraint_type", ctypes.c_int),
        ("constraint", _Constraint),
    ]


class SANE_Parameters(ctypes.Structure):
    _fields_ = [
        ("format", ctypes.c_int),
        ("last_frame", SANE_Bool),
        ("bytes_per_line", SANE_Int),
        ("pixels_per_line", SANE_Int),
        ("lines", SANE_Int),
        ("depth", SANE_Int),
    ]


# ---------------------------------------------------------------------------
# Library loading
# ---------------------------------------------------------------------------

_SEARCH_PATHS = (
    "/usr/local/lib/libsane.dylib",
    "/opt/homebrew/lib/libsane.dylib",
    "/usr/local/lib/libsane.so.1",
    "/usr/lib/libsane.so.1",
)


class SaneError(RuntimeError):
    """A SANE call returned a non-GOOD status."""

    def __init__(self, status: int, context: str = ""):
        self.status = Status(status) if status in Status._value2member_map_ else status
        text = strstatus(status)
        message = f"{context}: {text}" if context else text
        super().__init__(message)


def _load() -> ctypes.CDLL:
    override = os.environ.get("LIBSANE_PATH")
    candidates = ([override] if override else []) + list(_SEARCH_PATHS)
    for path in candidates:
        if path and os.path.exists(path):
            return ctypes.CDLL(path)
    found = ctypes.util.find_library("sane")
    if found:
        return ctypes.CDLL(found)
    raise OSError(
        "Could not locate libsane. Install it (brew install sane-backends) "
        "or point LIBSANE_PATH at the shared library."
    )


lib = _load()

# ---------------------------------------------------------------------------
# Prototypes
# ---------------------------------------------------------------------------

_AuthCallback = ctypes.CFUNCTYPE(
    None, SANE_String_Const, ctypes.c_char_p, ctypes.c_char_p
)

lib.sane_init.argtypes = [ctypes.POINTER(SANE_Int), ctypes.c_void_p]
lib.sane_init.restype = ctypes.c_int

lib.sane_exit.argtypes = []
lib.sane_exit.restype = None

lib.sane_get_devices.argtypes = [
    ctypes.POINTER(ctypes.POINTER(ctypes.POINTER(SANE_Device))),
    SANE_Bool,
]
lib.sane_get_devices.restype = ctypes.c_int

lib.sane_open.argtypes = [SANE_String_Const, ctypes.POINTER(SANE_Handle)]
lib.sane_open.restype = ctypes.c_int

lib.sane_close.argtypes = [SANE_Handle]
lib.sane_close.restype = None

lib.sane_get_option_descriptor.argtypes = [SANE_Handle, SANE_Int]
lib.sane_get_option_descriptor.restype = ctypes.POINTER(SANE_Option_Descriptor)

lib.sane_control_option.argtypes = [
    SANE_Handle,
    SANE_Int,
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.POINTER(SANE_Int),
]
lib.sane_control_option.restype = ctypes.c_int

lib.sane_get_parameters.argtypes = [SANE_Handle, ctypes.POINTER(SANE_Parameters)]
lib.sane_get_parameters.restype = ctypes.c_int

lib.sane_start.argtypes = [SANE_Handle]
lib.sane_start.restype = ctypes.c_int

lib.sane_read.argtypes = [
    SANE_Handle,
    ctypes.POINTER(SANE_Byte),
    SANE_Int,
    ctypes.POINTER(SANE_Int),
]
lib.sane_read.restype = ctypes.c_int

lib.sane_cancel.argtypes = [SANE_Handle]
lib.sane_cancel.restype = None

lib.sane_strstatus.argtypes = [ctypes.c_int]
lib.sane_strstatus.restype = ctypes.c_char_p


def strstatus(status: int) -> str:
    raw = lib.sane_strstatus(status)
    return raw.decode("utf-8", "replace") if raw else f"status {status}"


def check(status: int, context: str = "") -> int:
    """Raise :class:`SaneError` unless *status* is GOOD."""
    if status != Status.GOOD:
        raise SaneError(status, context)
    return status
