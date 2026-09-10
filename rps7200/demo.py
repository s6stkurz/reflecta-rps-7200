"""A stand-in scanner backed by the library, for driving the UI with no device.

Every control in the GUI -- the filmstrip, the channel switch, the progress bar,
both stop buttons -- can be exercised against real pixels this way, because the
library keeps each scan's raw bytes and correction alongside it. That is the same
reason the test suite never needs the hardware: "test the host side against
stored bytes, not against the scanner".

It is a demonstration, not a simulation. It does not model the protocol, and
nothing it reports about the device means anything. What it does model is the
*shape* of a session -- how long a pass takes, that progress arrives in batches,
that a roll yields a prescan before each frame, and that a stop lands between
frames rather than inside one.

    python3 tools/gui.py --demo
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from . import library, tiff
from .direct import RollFrame
from .framing import FULL_FRAME, frame_contrast, registration
from .session import estimate_seconds
from .usb_transport import UsbError

#: Wall-clock is divided by this. Slow enough that the progress bar has
#: something to do and a stop lands somewhere, fast enough that trying the
#: window out is not spent waiting: a 3600 dpi infrared pass, 334 s on the
#: hardware, takes under three seconds here.
SPEED = 120.0


class _FakeTransport:
    """Just enough of `Transport` for `ScanSession.force_abort` to work on."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Inquiry:
    def describe(self) -> str:
        return "DEMO  MF Scanner  fw 1.70  (no scanner attached)"


class DemoScanner:
    """Serves stored library entries as though they had just been scanned."""

    def __init__(self, root: str | Path = "library", speed: float = SPEED,
                 entry: str | Path | None = None):
        self.root = Path(root)
        #: The one entry this demo is showing, when it found a good pair. Its
        #: prescan answers a prescan and its scan answers a scan, so the two
        #: are the same picture -- which is what makes the filmstrip's
        #: "the scan replaces its prescan" behaviour visible at all.
        self.pair: Path | None = Path(entry) if entry else None
        self.speed = max(1.0, speed)
        self.t = _FakeTransport()
        self.log_hook: Any = None
        self.progress_hook: Any = None
        self.shading = None
        self.ccd_mask = None
        self.last_raw = None
        self.last_raw_layout = None
        self._inquiry = _Inquiry()
        self._entries: list[Path] = []
        #: Entries that kept a prescan, which is what a roll walks. A demo roll
        #: used to hand back the same picture every frame, so a contact sheet of
        #: it was six identical thumbnails carrying six identical numbers --
        #: nothing to pick between, and nothing that would show a wrong pick.
        self._strip: list[Path] = []
        self._next = 0
        self._position = 0

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> DemoScanner:
        self._entries = sorted(
            p.parent for p in self.root.glob("*/scan.json")
        ) if self.root.exists() else []
        self._strip = sorted(p.parent for p in self.root.glob("*/prescan.tif"))
        if self.pair is None:
            self.pair = best_pair(self.root)
        if self.pair is not None:
            self._log(f"demo mode: showing {self.pair.name}")
            self._log("its own prescan answers Prescan, its scan answers Scan "
                      "-- the same picture, as if you had just taken both")
        elif self._entries:
            self._log(f"demo mode: {len(self._entries)} stored entries to draw on")
        if self._strip:
            self._log(f"a roll walks {len(self._strip)} different stored pictures, "
                      "so a contact sheet has something to choose between")
        else:
            self._log("no library entries found; showing generated frames instead")
        return self

    def close(self) -> None:
        self.t.close()

    def __enter__(self) -> DemoScanner:
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()

    def inquiry(self, refresh: bool = False) -> _Inquiry:
        return self._inquiry

    def position(self) -> int | None:
        return self._position

    def advance(self, steps: int = 1, timeout: float = 30.0, poll: float = 0.5):
        self._work(7.0)
        if self._position >= 16:                         # a strip runs out
            self._log("no advance: treating that as the end of the film")
            return None
        self._position += steps
        self._log(f"advanced to position {self._position}")
        return self._position

    def retreat(self, steps: int = 1, timeout: float = 30.0, poll: float = 0.5):
        self._work(7.0)
        if self._position <= 0:
            self._log("no movement: already at the first frame")
            return None
        self._position = max(0, self._position - steps)
        self._log(f"went back to position {self._position}")
        return self._position

    def nudge(self, millimetres: float) -> dict[str, Any]:
        param = max(1, min(8, round((abs(millimetres) - 0.1662) / 0.1057)))
        asked = 0.1057 * param + 0.1662
        asked = asked if millimetres >= 0 else -asked
        self._log(f"slide sub-frame: {asked:+.3f} mm (param {param})")
        self._work(1.5)
        return {"asked_mm": asked, "param": param,
                "forward": millimetres >= 0}

    def capture_record(self) -> dict[str, Any]:
        return {"reference": None, "ccd_mask": None, "raw": None, "raw_layout": None}

    # -- the parts the session calls --------------------------------------

    def ensure_shading(self, path: Any, reuse: bool = False, skip: bool = False) -> dict:
        if skip:
            return {"action": "skipped", "summary": "shading off (demo)"}
        self._work(210.0 if not reuse else 1.0)
        return {
            "action": "loaded" if reuse else "calibrated",
            "summary": f"shading {'loaded' if reuse else 'calibrated'} (demo)",
        }

    def prescan(
        self, resolution: int = 300, frame: Any = None, keep_raw: bool = False
    ) -> tuple[np.ndarray, Any]:
        self._work(estimate_seconds(resolution, False), lines=int(resolution * 0.957))
        stored = self._pair_image("prescan.tif")
        if stored is not None:
            return stored, None
        image = self._pixels(channels=3)
        return image[..., :3].astype(np.uint8) if image.dtype != np.uint8 else image, None

    def scan(
        self,
        resolution: int = 1800,
        infrared: bool = True,
        film: str = "negative",
        auto_exposure: bool = False,
        exposure_scale: Any = 1.0,
        shading: bool = True,
        frame: Any = None,
        keep_raw: bool = False,
        **kw: Any,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        if auto_exposure:
            self._log("auto-exposure: probing in RGB")
            self._work(48.0)
            self._log("auto-exposure: [1.82, 0.94, 2.11, 1.0]")
        started = time.monotonic()
        self._work(
            estimate_seconds(resolution, infrared),
            lines=int(resolution * 0.957),
        )
        image = self._pair_image("scan.tif")
        if image is None:
            image = self._pixels(channels=4 if infrared else 3)
        elif not infrared and image.ndim == 3 and image.shape[2] > 3:
            image = image[..., :3]
        meta = {
            "resolution_dpi": resolution,
            "channels": image.shape[2],
            "channel_order": list("RGBI"[: image.shape[2]]),
            "film": film,
            "depth": 16,
            "width": image.shape[1],
            "height": image.shape[0],
            "shading": {"columns": image.shape[1], "clipped": 0} if shading else None,
            "exposure_scale": exposure_scale,
            "duration_s": round(time.monotonic() - started, 1),
            "demo": True,
        }
        return image, meta

    def scan_roll(
        self,
        frames: int | None = None,
        resolution: int = 1800,
        infrared: bool = True,
        dry_run: bool = False,
        skip: int = 0,
        only: tuple[int, ...] | None = None,
        **kw: Any,
    ):
        limit = frames if frames is not None else 6
        for i in range(limit):
            self._position = skip + i
            if only is not None and skip + i not in only:
                # Advanced past, not looked at -- the whole point of picking
                # frames off a contact sheet.
                self._log(f"frame {i}: not chosen, advancing past it")
                self._work(7.0)
                continue
            prescan = self._strip_prescan(skip + i)
            marks = self._marks(prescan)
            self._log(
                f"frame {i}: contrast {marks['contrast']:.3f}, "
                f"offset {marks['offset_mm']:+.2f} mm, "
                f"short by {marks['shortfall_mm']:.2f} mm"
            )
            image = meta = None
            if not dry_run:
                image, meta = self.scan(
                    resolution=resolution, infrared=infrared, keep_raw=True
                )
            yield RollFrame(
                index=skip + i,
                position=skip + i,
                image=image,
                meta=meta or {},
                prescan=prescan,
                registration=marks,
            )
            self._work(7.0)                              # the advance

    # -- internals ---------------------------------------------------------

    def _log(self, message: str) -> None:
        if self.log_hook is not None:
            self.log_hook(message)

    def _work(self, seconds: float, lines: int = 0) -> None:
        """Spend `seconds` of pretend scanning, reporting progress as it goes."""
        total = max(1, lines)
        steps = 40
        for i in range(steps):
            if self.t.closed:
                # What a force abort feels like from in here.
                raise UsbError("transport is not open")
            time.sleep(seconds / self.speed / steps)
            if lines and self.progress_hook is not None:
                self.progress_hook(round(total * (i + 1) / steps), total)

    def _strip_prescan(self, index: int) -> np.ndarray:
        """The picture at `index` of the demo strip -- a different one each time.

        A roll is the one place the fixed pair is wrong. Everywhere else the
        demo shows one picture on purpose, so that a scan can visibly replace
        the prescan of the same frame; but a strip of one picture repeated is a
        contact sheet nobody can read, and a wrong pick would look identical to
        a right one.
        """
        self._work(estimate_seconds(300, False), lines=287)
        if self._strip:
            entry = self._strip[index % len(self._strip)]
            try:
                image = tiff.read(str(entry / "prescan.tif"))
                self._log(f"frame from {entry.name}")
                return image
            except Exception as exc:                     # noqa: BLE001
                self._log(f"could not read {entry.name}: {exc}")
        # No stored prescans: test cards, seeded per frame so they still differ.
        return _test_card(3, index + 1)

    def _marks(self, prescan: np.ndarray) -> dict[str, Any]:
        """Measure the frame the way the driver does, not with made-up numbers.

        `registration` and `frame_contrast` are the real ones. That is what
        makes the contact sheet's captions worth reading here: they vary
        because the pictures vary, and they are computed by the code that will
        compute them on the hardware.
        """
        try:
            marks = dict(registration(prescan, FULL_FRAME))
            marks["contrast"] = round(float(frame_contrast(prescan)), 4)
            return marks
        except Exception as exc:                         # noqa: BLE001
            # A stored prescan of an unexpected shape must not end the demo.
            self._log(f"could not measure this frame: {exc}")
            return {"offset_mm": 0.0, "shortfall_mm": 0.0, "contrast": 0.0}

    def _pair_image(self, name: str) -> np.ndarray | None:
        """One of the chosen entry's own files, or None if there is no pair."""
        if self.pair is None:
            return None
        path = self.pair / name
        if not path.exists():
            return None
        try:
            image = tiff.read(str(path))
        except Exception as exc:                         # noqa: BLE001
            self._log(f"could not read {path.name}: {exc}")
            return None
        self._log(f"{name} from {self.pair.name}  {image.shape}")
        return image

    def _pixels(self, channels: int) -> np.ndarray:
        """Real pixels from the library where there are any, else a test card."""
        wanted = [
            p for p in self._entries
            if _entry_channels(p) == channels
        ] or self._entries
        if wanted:
            path = wanted[self._next % len(wanted)]
            self._next += 1
            try:
                image, _ = library.load(path)
                self._log(f"demo frame from {path.name}")
                if image.ndim == 3 and image.shape[2] >= channels:
                    return image[..., :channels]
                return image
            except Exception as exc:                     # noqa: BLE001
                self._log(f"could not read {path.name}: {exc}")
        self._next += 1
        return _test_card(channels, self._next)


def best_pair(root: Path) -> Path | None:
    """A stored entry that has both a prescan and a full scan of one picture.

    The highest resolution one wins, because the point of showing a real pair
    is having something worth zooming into -- a 3600 dpi frame is 135 MB of
    actual grain, where the generated test card has none.
    """
    best, best_dpi = None, 0
    if not root.exists():
        return None
    for candidate in sorted(root.glob("*/scan.json")):
        entry = candidate.parent
        if not (entry / "prescan.tif").exists():
            continue
        try:
            record = json.loads(candidate.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        dpi = int((record.get("scan") or {}).get("resolution_dpi") or 0)
        if dpi > best_dpi:
            best, best_dpi = entry, dpi
    return best


def _entry_channels(path: Path) -> int:
    try:
        record = json.loads((path / "scan.json").read_text())
        return int((record.get("scan") or {}).get("channels") or 0)
    except Exception:                                    # noqa: BLE001
        return 0


def _test_card(channels: int, seed: int) -> np.ndarray:
    """A synthetic negative, for a checkout with an empty library."""
    h, w = 574, 862
    y, x = np.mgrid[0:h, 0:w]
    rng = np.random.default_rng(seed)
    base = (
        0.35
        + 0.25 * np.sin(x / 90.0 + seed)
        + 0.15 * np.cos(y / 60.0)
        + 0.05 * rng.standard_normal((h, w))
    )
    planes = []
    for c in range(channels):
        # An orange mask: a negative's channels sit at very different levels.
        level = (0.75, 0.45, 0.25, 0.85)[c] if c < 4 else 0.5
        planes.append(np.clip(base * level + level * 0.4, 0, 1))
    return (np.stack(planes, axis=-1) * 65535).astype(np.uint16)
