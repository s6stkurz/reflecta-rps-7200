"""Console and Python control of the Reflecta RPS 7200 film scanner.

Captures RGB and raw infrared together in one pass, at 16 bits, through SANE's
``pieusb`` backend.

    from rps7200 import Scanner

    with Scanner() as s:
        s.prescan()                     # also calibrates the scan below
        frame = s.scan(resolution=600)
        rgb, ir = frame.rgb, frame.ir
"""

from .device import (
    DEFAULT_RESOLUTION,
    RAW_SETTINGS,
    DeviceNotFound,
    Frame,
    Scanner,
    discover,
    sane_version,
)
from .sane_ffi import SaneError

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_RESOLUTION",
    "RAW_SETTINGS",
    "DeviceNotFound",
    "Frame",
    "SaneError",
    "Scanner",
    "discover",
    "sane_version",
]
