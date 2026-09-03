"""Shared test setup.

``RPS7200_NO_TIFFFILE=1`` makes ``import tifffile`` fail for the whole run, so
the suite can be executed as it would be on a bare install::

    python3 -m pytest tests/ -q                       # tifffile present
    RPS7200_NO_TIFFFILE=1 python3 -m pytest tests/ -q # tifffile absent

Both must pass. ``tests/test_tiff.py`` monkeypatches ``_has_tifffile`` per test,
which is what makes the cross-implementation matrix possible, but a monkeypatch
only proves each call site picks the right branch. This proves the *package*
works with the dependency genuinely missing -- that no other module imports
tifffile behind the driver's back, and that the optional dependency really is
optional.
"""

import os
import sys
from importlib.abc import MetaPathFinder


class _BlockTifffile(MetaPathFinder):
    """Refuse tifffile the way an absent install does.

    ModuleNotFoundError rather than a bare ImportError, because that is what a
    missing package raises -- and ``pytest.importorskip`` re-raises anything
    else rather than skipping, on the grounds that a broken install should not
    look like an absent one.
    """

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "tifffile" or fullname.startswith("tifffile."):
            raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
        return None


if os.environ.get("RPS7200_NO_TIFFFILE"):
    sys.modules.pop("tifffile", None)
    sys.meta_path.insert(0, _BlockTifffile())


# ---------------------------------------------------------------------------
# Shared doubles
# ---------------------------------------------------------------------------
#
# Only what more than one module needs lives here. `FakeScanner`
# (test_metering) and `FakeRoll` (test_roll) stay beside their tests: each is
# tuned to the loop it exercises, and merging them would produce one double
# that models neither well.

import numpy as np  # noqa: E402

from rps7200.direct import Settings  # noqa: E402
from rps7200.usb_transport import CheckCondition  # noqa: E402

#: The device's own power-on gain and offset, as READ GAIN/OFFSET reports them.
#: Shared so a test that cares about exposure does not have to restate the two
#: it does not care about.
DEVICE_GAIN = [40, 33, 21, 25]
DEVICE_OFFSET = [12, 10, 28, 10]


def settings(*exposure: int) -> Settings:
    """A `Settings` at ``exposure``, with the device's own gain and offset."""
    return Settings(
        exposure=list(exposure), gain=list(DEVICE_GAIN), offset=list(DEVICE_OFFSET)
    )


class FakeTransport:
    """Records commands, and answers READ_STATE from a scripted position.

    Stands in for `rps7200.usb_transport.Transport` wherever a test is about
    *which bytes the driver sends*, which is most of the protocol surface. It
    deliberately implements only `command()`: anything reaching further into the
    transport should be tested against `FakeUsb` instead, which fakes libusb.
    """

    def __init__(self, positions=(0,), replies=None):
        # One entry per READ_STATE; None means the read fails, which is what the
        # scanner does on the reading right after an advance.
        self.positions = list(positions)
        # opcode -> bytes, or opcode -> callable(command) -> bytes, for commands
        # a test needs an answer from. Anything unlisted answers empty.
        self.replies = dict(replies or {})
        self.sent = []
        self.states = 0

    def command(self, command, data=None, read_size=0, timeout_ms=0, max_wait_s=60.0):
        self.sent.append((command[0], bytes(data) if data else b""))
        if command[0] == 0xDD:  # READ_STATE
            i = min(self.states, len(self.positions) - 1)
            self.states += 1
            position = self.positions[i]
            if position is None:
                raise CheckCondition(0xDD)
            blob = bytearray(13)
            blob[2] = position
            return bytes(blob)
        reply = self.replies.get(command[0])
        if callable(reply):
            return reply(command)
        return reply if reply is not None else b""

    def payloads(self, opcode: int) -> list[bytes]:
        """Every data payload sent with ``opcode``, in order."""
        return [data for sent, data in self.sent if sent == opcode]


def frame_of(value=0, shape=(4, 4, 3), dtype=np.uint16) -> np.ndarray:
    """A constant frame, for tests that only care about shape and dtype."""
    return np.full(shape, value, dtype=dtype)
