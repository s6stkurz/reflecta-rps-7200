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

**Pixels come from the stored raw bytes, not from the TIFF beside them.** The
TIFF is an output; `raw.bin.gz` is what the scanner actually sent, so decoding
it here runs the same `_deinterleave` and the same shading correction a real
pass runs, and `capture_record` can hand the session genuine bytes to file. An
entry the demo files reconstructs like any other.

Where a pass cannot be answered with the bytes in hand -- asking a four-channel
entry for RGB, say -- the bytes are dropped and only the calibration is kept.
They describe four channels and the image has three, so filing them would make
an entry whose raw decodes to a different picture, which is the one thing the
library exists to prevent. The log says when it happens.

It refuses what the device refuses: infrared on black and white or Kodachrome.
A stand-in that accepts what the hardware rejects teaches the window a shape
that does not exist.

    python3 tools/gui.py --demo
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from . import library, tiff
from .direct import DirectScanner, RollFrame, supports_infrared
from .framing import FULL_FRAME, frame_contrast, registration
from .protocol import ScanParameters
from .session import estimate_seconds
from .shading import ShadingReference, apply_shading
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
        #: The entry chosen for each film, so a prescan and the scan after it
        #: show one picture rather than two.
        self._by_film: dict[str, Path | None] = {}
        #: While a roll is on this frame, the entry it shows -- overriding the
        #: per-film choice above. A roll is the one place showing a single
        #: picture is wrong: a strip of one picture repeated is a contact sheet
        #: nobody can read, where a wrong pick looks exactly like a right one.
        #: It is held across the frame's prescan *and* its scan, so the two are
        #: still the same photograph, which is what the fixed pair is for.
        self._frame_source: Path | None = None
        #: Entries that kept a prescan, per film. What a roll walks.
        self._strips: dict[str, list[Path]] = {}
        #: The bytes and calibration behind the last pass. Filled in by
        #: :meth:`_decode`, handed to the session by :meth:`capture_record`.
        self._capture: dict[str, Any] = {
            "reference": None, "ccd_mask": None, "raw": None, "raw_layout": None,
        }
        #: What the last decode's shading correction did, or None if none ran.
        self._shading_report: dict[str, Any] | None = None
        #: The resolution the last decoded entry was taken at.
        self._source_dpi = 0
        #: What shape the device produces at each resolution, read from the
        #: library rather than derived. See :meth:`_shape_for`.
        self._shapes: dict[int, tuple[int, int] | None] = {}
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
        self._next = 0
        self._position = 0

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> DemoScanner:
        self._entries = sorted(
            p.parent for p in self.root.glob("*/scan.json")
        ) if self.root.exists() else []
        if self.pair is None:
            self.pair = best_pair(self.root)
        if self.pair is not None:
            self._log(f"demo mode: showing {self.pair.name}")
            self._log("its own prescan answers Prescan, its scan answers Scan "
                      "-- the same picture, as if you had just taken both")
        elif self._entries:
            self._log(f"demo mode: {len(self._entries)} stored entries to draw on")
        walkable = len(sorted(self.root.glob("*/prescan.tif")))
        if walkable:
            self._log(f"a roll walks {walkable} different stored pictures, so a "
                      "contact sheet has something to choose between")
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

    def _drop_raw(self, why: str) -> None:
        """Keep the calibration, forget the bytes."""
        if self._capture.get("raw") is not None:
            self._log(f"raw bytes not filed: {why}")
        self._capture = dict(self._capture, raw=None, raw_layout=None)

    def capture_record(self) -> dict[str, Any]:
        """The bytes and calibration behind the last pass, as the real one does.

        Not empty any more: the demo decodes stored raw bytes, so it can hand
        them straight back and the session files a complete entry -- one that
        `library.reconstruct` can re-decode like any other.
        """
        return dict(self._capture)

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
        self, resolution: int = 300, frame: Any = None, keep_raw: bool = False,
        film: str = "negative",
    ) -> tuple[np.ndarray, Any]:
        self._work(estimate_seconds(resolution, False), lines=int(resolution * 0.957))
        image = self._pair_image("prescan.tif", film, resolution)
        if image is None:
            image = self._pixels(channels=3)
        # A framing pass is RGB, always: the real one sets passes=0x80 and
        # 8-bit, so a four-channel prescan is a shape the window would never
        # see from the device.
        if image.ndim == 3 and image.shape[2] > 3:
            image = image[..., :3]
            self._drop_raw("a prescan is three channels")
        return image, None

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
        if infrared and not supports_infrared(film):
            # The demo refuses exactly what the device refuses. A stand-in that
            # accepts a combination the hardware will not is worse than no
            # stand-in: it teaches the window a shape that does not exist.
            raise ValueError(
                f"infrared is blind to {film}: its "
                + ("grain" if film == "bw" else "cyan layer")
                + " absorbs infrared, so the pass would spend its ~212 s floor "
                "and hand back the picture rather than the dust. Scan it RGB."
            )
        if auto_exposure:
            self._log("auto-exposure: probing in RGB")
            self._work(48.0)
            self._log("auto-exposure: [1.82, 0.94, 2.11, 1.0]")
        started = time.monotonic()
        self._work(
            estimate_seconds(resolution, infrared),
            lines=int(resolution * 0.957),
        )
        image = self._pair_image("scan.tif", film, resolution)
        if image is None:
            image = self._pixels(channels=4 if infrared else 3)
        elif not infrared and image.ndim == 3 and image.shape[2] > 3:
            image = image[..., :3]
            # The bytes described four channels and this is three, so they no
            # longer describe what is being returned. Say so by dropping them:
            # `ScanSession._file` compares the two and would refuse the entry
            # anyway, and filing raw bytes that decode to a different picture
            # is the one failure the library exists to make impossible.
            self._drop_raw("the infrared channel was dropped from this pass")
        meta = {
            "resolution_dpi": resolution,
            "channels": image.shape[2],
            "channel_order": list("RGBI"[: image.shape[2]]),
            "film": film,
            "depth": 16,
            "width": image.shape[1],
            "height": image.shape[0],
            # Only where one actually happened. Claiming a correction that did
            # not run makes the entry say it is shading-corrected while filing
            # no reference, and `library.reconstruct` is then right to report
            # that it cannot be reproduced.
            "shading": self._shading_report if shading else None,
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
        film: str = "negative",
        **kw: Any,
    ):
        # Up front, as the real one does: a roll spends minutes calibrating
        # before the first frame, so this cannot wait until one is taken.
        if infrared and not supports_infrared(film):
            raise ValueError(
                f"infrared is blind to {film}: its "
                + ("grain" if film == "bw" else "cyan layer")
                + " absorbs infrared, so every frame of this roll would spend "
                "its ~212 s floor and hand back the picture rather than the "
                "dust. Scan it RGB."
            )
        limit = frames if frames is not None else 6
        for i in range(limit):
            self._position = skip + i
            if only is not None and skip + i not in only:
                # Advanced past, not looked at -- the whole point of picking
                # frames off a contact sheet.
                self._log(f"frame {i}: not chosen, advancing past it")
                self._work(7.0)
                continue
            strip = self._strip_for(film)
            # Held across both passes of this frame, and dropped at the end, so
            # the frame's prescan and its scan are one picture and the next
            # frame is a different one.
            self._frame_source = strip[(skip + i) % len(strip)] if strip else None
            try:
                prescan, _ = self.prescan(film=film)
                marks = self._marks(prescan)
                self._log(
                    f"frame {i}: contrast {marks['contrast']:.3f}, "
                    f"offset {marks['offset_mm']:+.2f} mm, "
                    f"short by {marks['shortfall_mm']:.2f} mm"
                )
                image = meta = None
                if not dry_run:
                    image, meta = self.scan(
                        resolution=resolution, infrared=infrared, film=film,
                        keep_raw=True,
                    )
            finally:
                self._frame_source = None
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

    def _source_for(self, film: str) -> Path | None:
        """The entry to show for this film.

        Setting the film to black and white and being shown a colour negative
        rendered through its green channel is not a demonstration of anything.
        The library has real black and white scans in it; this finds one, and
        remembers it so the prescan and the scan that follows are the same
        picture rather than two different ones.

        Falls back to the chosen pair, then to anything at all, so a library
        with no entry of that film still drives the window.
        """
        if self._frame_source is not None:
            return self._frame_source
        if film in self._by_film:
            return self._by_film[film]

        # One entry per film, not one per resolution. Picking by resolution
        # too would be tidier in shape and wrong in substance: the library's
        # black and white scans are of *two different frames*, so a 300 dpi
        # prescan and a 900 dpi scan would show different photographs and the
        # framing the operator lined up would mean nothing. Every resolution is
        # served by rescaling this one instead.
        best, best_dpi = None, -1
        for path in self._entries:
            try:
                record = json.loads((path / "scan.json").read_text())
            except (OSError, ValueError):
                continue
            scan = record.get("scan") or {}
            if scan.get("film") != film or not (path / "raw.bin.gz").exists():
                continue
            found = int(scan.get("resolution_dpi") or 0)
            # Nearest 1800: high enough to downscale from for most passes,
            # small enough that decoding it every pass is not 140 MB of work.
            if best is None or abs(found - 1800) < abs(best_dpi - 1800):
                best, best_dpi = path, found
        if best is not None:
            self._log(f"{film}: showing {best.name} ({best_dpi} dpi)")
        self._by_film[film] = best or self.pair
        return self._by_film[film]

    def _shape_for(self, dpi: int) -> tuple[int, int] | None:
        """What the device actually produces at this resolution.

        A ratio gets close and not closer: the widths the scanner reports are
        428, 860, 1292, 2584, 5172 for 300 to 3600 dpi, which is not a constant
        multiple of the resolution -- it rounds its own way. The library has
        entries at each of these, so the shape is recorded rather than derived.
        Any film will do; the frame is the same size whatever is in it.
        """
        if dpi in self._shapes:
            return self._shapes[dpi]
        found = None
        for path in self._entries:
            try:
                scan = (json.loads((path / "scan.json").read_text())
                        .get("scan") or {})
            except (OSError, ValueError):
                continue
            if int(scan.get("resolution_dpi") or 0) != dpi:
                continue
            h, w = scan.get("height"), scan.get("width")
            if h and w:
                found = (int(h), int(w))
                break
        self._shapes[dpi] = found
        return found

    @staticmethod
    def _rescale(image: np.ndarray, factor: float) -> np.ndarray:
        """Nearest-neighbour to the size a resolution implies.

        Only so the shape matches the label. A pass reported as 900 dpi that
        hands back 1800 dpi pixels is a stand-in that lies about the one thing
        the window sizes everything from, and every readout downstream -- the
        estimate, the zoom, the crop -- is then off by a factor.
        """
        if abs(factor - 1.0) < 1e-9:
            return image
        h = max(1, round(image.shape[0] * factor))
        w = max(1, round(image.shape[1] * factor))
        return DemoScanner._resample(image, h, w)

    @staticmethod
    def _resample(image: np.ndarray, h: int, w: int) -> np.ndarray:
        if image.shape[:2] == (h, w):
            return image
        factor = h / image.shape[0]
        ys = np.clip((np.arange(h) / factor).astype(int), 0, image.shape[0] - 1)
        xs = np.clip((np.arange(w) / factor).astype(int), 0, image.shape[1] - 1)
        return image[ys][:, xs]

    def _decode(self, path: Path) -> tuple[np.ndarray, dict[str, Any]] | None:
        """An entry's pixels from its **raw bytes**, not from its TIFF.

        This is the point of the demo being backed by the library. The stored
        `scan.tif` is an output; `raw.bin.gz` is what the scanner actually sent,
        and decoding it here runs the same `_deinterleave` and the same shading
        correction a real pass runs. So the demo exercises the path that can
        break, and `capture_record` below can hand the session genuine bytes to
        file -- which a TIFF read could never do.

        Returns ``(image, capture)`` or None when the entry has no bytes.
        """
        raw = library.read_raw(path)
        if raw is None:
            return None
        try:
            record = json.loads((path / "scan.json").read_text())
            layout = (record.get("raw") or {}).get("layout") or {}
            params = ScanParameters(
                width=int(layout["width"]),
                lines=int(layout["lines"]),
                bytes_per_line=int(layout["bytes_per_line"]),
                filter_offset1=0, filter_offset2=0, available_lines=0,
            )
            image = DirectScanner._deinterleave(
                raw, params, int(layout["channels"])
            )
        except Exception as exc:                         # noqa: BLE001
            self._log(f"could not decode {path.name}: {exc}")
            return None

        cal = record.get("calibration") or {}
        reference = mask = None
        ref_file, mask_file = cal.get("shading"), cal.get("ccd_mask")
        if ref_file and (path / ref_file).exists():
            reference = ShadingReference.load(path / ref_file)
        if mask_file and (path / mask_file).exists():
            mask = (path / mask_file).read_bytes()
        # Corrected here if the stored image was, so what the demo shows is
        # what that scan looked like rather than a striped version of it.
        applied = (record.get("image") or {}).get("corrections_applied") or []
        self._shading_report = None
        if "shading" in applied and reference is not None:
            image, self._shading_report = apply_shading(image, reference, mask)

        self._source_dpi = int(
            (record.get("scan") or {}).get("resolution_dpi") or 0
        )
        self._log(f"{path.name}: {len(raw) / 1e6:.1f} MB of raw bytes "
                  f"-> {image.shape}")
        return image, {
            "reference": reference, "ccd_mask": mask,
            "raw": raw, "raw_layout": layout,
        }

    def _strip_for(self, film: str) -> list[Path]:
        """The entries a roll of this film walks, one per frame.

        Entries of the right film first -- being shown a colour negative for a
        black and white roll is no more a demonstration here than it is for a
        single pass. Two is the point at which a strip is worth calling one; a
        library with fewer of that film walks whatever kept a prescan instead.
        """
        if film in self._strips:
            return self._strips[film]
        matching, any_prescan = [], []
        for path in sorted(self.root.glob("*/prescan.tif")):
            entry = path.parent
            any_prescan.append(entry)
            try:
                record = json.loads((entry / "scan.json").read_text())
            except (OSError, ValueError):
                continue
            if (record.get("scan") or {}).get("film") == film:
                matching.append(entry)
        self._strips[film] = matching if len(matching) >= 2 else any_prescan
        return self._strips[film]

    def _marks(self, prescan: np.ndarray) -> dict[str, Any]:
        """Measure the frame the way the driver does, not with made-up numbers.

        `registration` and `frame_contrast` are the real ones. That is what
        makes a contact sheet's captions worth reading here: they differ
        because the pictures differ, and they are computed by the code that
        will compute them on the hardware.
        """
        try:
            marks = dict(registration(prescan, FULL_FRAME))
            marks["contrast"] = round(float(frame_contrast(prescan)), 4)
            return marks
        except Exception as exc:                         # noqa: BLE001
            # A stored prescan of an unexpected shape costs this frame's
            # numbers, not the run.
            self._log(f"could not measure this frame: {exc}")
            return {"offset_mm": 0.0, "shortfall_mm": 0.0, "contrast": 0.0}

    def _pair_image(self, name: str, film: str = "negative",
                    dpi: int = 0) -> np.ndarray | None:
        """The picture for this film: its prescan, or its scan.

        A prescan uses the entry's stored `prescan.tif` where there is one, and
        otherwise the entry's own scan -- a framing pass and a scan are the same
        photograph, and showing the right film matters more here than showing
        the right resolution.
        """
        source = self._source_for(film)
        if source is None:
            return None
        if name.startswith("prescan"):
            tif = source / name
            if tif.exists():
                try:
                    image = tiff.read(str(tif))
                    self._log(f"{name} from {source.name}  {image.shape}")
                    return image
                except Exception as exc:                 # noqa: BLE001
                    self._log(f"could not read {tif.name}: {exc}")

        got = self._decode(source)
        if got is None:
            return None
        image, capture = got
        self._capture = capture
        if dpi and self._source_dpi and dpi != self._source_dpi:
            shape = self._shape_for(dpi)
            image = (self._resample(image, *shape) if shape
                     else self._rescale(image, dpi / self._source_dpi))
            # The bytes were taken at another resolution, so they no longer
            # describe these pixels.
            self._drop_raw(
                f"resized from {self._source_dpi} dpi to {dpi}"
            )
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
            got = self._decode(path)
            if got is not None:
                image, capture = got
                self._capture = capture
                self._log(f"demo frame from {path.name}")
                if image.ndim == 3 and image.shape[2] > channels:
                    image = image[..., :channels]
                    self._drop_raw(
                        f"this entry has {capture['raw_layout'].get('channels')} "
                        f"channels and the pass wants {channels}"
                    )
                return image
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
