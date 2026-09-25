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
it here runs the driver's own decode. The correction comes *last*, as it does
in `DirectScanner.scan`: the corrected picture is returned and the pixels
before it are kept in `last_pixels_raw`, which is what the session files. The
demo used to correct first and keep nothing, so the session filed corrected
pixels as raw beside a reference that corrected them a second time -- a branch
the scanner never takes, which hid the one bug of that shape it did have.
A prescan is drawn from the raw bytes too, not from the `prescan.tif` beside
them: that is the picture as it was shown, corrected when it was taken, and
serving it as a raw read filed every demo prescan as raw pixels that were not.
It is the fallback for an entry with nothing to correct, and is handed over as
the corrected picture it is.

Every pass drawn from raw pixels hands over bytes of its own: those pixels as
the index-format lines the scanner sends, in the order the carriage read
them, decoded by the driver's `decode_index`. Those, and the calibration that
describes *this* pass, are what `capture_record` gives the session -- never a
previous pass's -- so an entry the demo files re-decodes to exactly what it
holds and corrects to exactly what was shown. A pass resized or moved from
its stored picture shows each column somewhere the stored calibration did not
measure it, so it is corrected with that calibration read at the columns it
now shows (`_pass_reference`). The record says which entry it was drawn from.

It refuses what the device refuses, in the driver's own words: infrared on
black and white or Kodachrome, a corrected pass before any calibration, and a
pass wider than any reference the device will produce. A stand-in that
accepts what the hardware rejects teaches the window a shape that does not
exist. With no film in the transport every pass and every move says so, where
a transport would.

A roll is the driver's own loop, `DirectScanner.scan_roll`, run on this
stand-in: only what the film shows is the demo's.

    uv run python tools/gui.py --demo
"""
from __future__ import annotations

import json
import random
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from . import library, tiff
from .direct import SHADING_SKIPPED_EXPLICIT, DirectScanner, supports_infrared
from .direction import encode_index
from .export import to_8bit
from .protocol import say_units
from .framing import APERTURE_MM, FULL_FRAME
from .protocol import (
    CHANNEL_ORDER,
    INDEX_HEADER,
    ONE_PASS_COLOR,
    ONE_PASS_RGBI,
    ScanParameters,
    Settings,
)
from .session import estimate_seconds
from .shading import ShadingReference, apply_shading, build_width_to_loc
from .usb_transport import UsbError

#: Wall-clock is divided by this. Slow enough that the progress bar has
#: something to do and a stop lands somewhere, fast enough that trying the
#: window out is not spent waiting: a 3600 dpi infrared pass, 334 s on the
#: hardware, takes under three seconds here.
SPEED = 120.0


#: How alike two pictures must be to count as one photograph. Their middles
#: are compared as small grey grids, allowing the picture to have moved
#: sideways by up to a quarter of the frame -- which is how two scans of one
#: frame differ. Measured over both libraries here: 500 negative entries,
#: 129 photographs.
SAME_PICTURE = 0.85
SIGNATURE_ROWS, SIGNATURE_COLUMNS, SIGNATURE_REACH = 24, 48, 12


def libraries_beside(root: str | Path) -> list[Path]:
    """``root`` and every library next to it, with any nested ones.

    `library 2` beside `library`, and its `300dpi` and `600dpi` folders, which
    hold entries of their own. What a roll in the demo draws its pictures
    from, so a second roll has other photographs to show.
    """
    root = Path(root)
    found: list[Path] = []
    siblings = sorted(p for p in root.parent.glob(f"{root.name} *") if p.is_dir())
    for library_dir in [root, *siblings]:
        if not library_dir.is_dir():
            continue
        if any(library_dir.glob("*/scan.json")):
            found.append(library_dir)
        for sub in sorted(p for p in library_dir.iterdir() if p.is_dir()):
            if not (sub / "scan.json").exists() and any(sub.glob("*/scan.json")):
                found.append(sub)
    return found


def picture_signature(entry: Path) -> np.ndarray | None:
    """One photograph's likeness, small enough to compare hundreds of.

    The middle of the picture as a grey grid: from the entry's prescan where
    it kept one, else from its scan when that is no larger than 600 dpi.
    None where there is nothing small enough to read quickly.
    """
    source = entry / "prescan.tif"
    if not source.exists():
        try:
            record = json.loads((entry / "scan.json").read_text(encoding="utf-8"))
            if int((record.get("scan") or {}).get("resolution_dpi") or 0) > 600:
                return None
        except (OSError, ValueError, TypeError):
            return None
        source = entry / "scan.tif"
    try:
        image = tiff.read(str(source))
    except Exception:                                    # noqa: BLE001
        return None
    if image.ndim != 3 or min(image.shape[:2]) < 32:
        return None
    grey = image[..., :3].astype(np.float32).mean(axis=2)
    h, w = grey.shape
    grey = grey[h // 8: h - h // 8, w // 8: w - w // 8]
    rows = np.linspace(0, grey.shape[0] - 1, SIGNATURE_ROWS).astype(int)
    columns = np.linspace(0, grey.shape[1] - 1, SIGNATURE_COLUMNS).astype(int)
    grid = grey[np.ix_(rows, columns)]
    return grid if float(grid.std()) > 0 else None


def _windows(signature: np.ndarray) -> np.ndarray:
    """The signature's middle at every sideways shift, each normalised."""
    reach, width = SIGNATURE_REACH, SIGNATURE_COLUMNS - 2 * SIGNATURE_REACH
    out = np.stack([signature[:, reach + s: reach + s + width].ravel()
                    for s in range(-reach, reach + 1)]).astype(np.float64)
    out -= out.mean(axis=1, keepdims=True)
    norms = np.linalg.norm(out, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return out / norms


def group_pictures(signatures: Sequence[np.ndarray]) -> list[int]:
    """Which photograph each signature shows, as a group number per signature.

    Greedy, in the order given: a signature joins the first photograph whose
    middle it matches at some sideways shift, or starts a new one.
    """
    centres: list[np.ndarray] = []
    labels: list[int] = []
    for signature in signatures:
        windows = _windows(signature)
        if centres:
            scores = (np.stack(centres) @ windows.T).max(axis=1)
            best = int(scores.argmax())
            if scores[best] > SAME_PICTURE:
                labels.append(best)
                continue
        centres.append(windows[SIGNATURE_REACH])
        labels.append(len(centres) - 1)
    return labels


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
                 entry: str | Path | None = None, no_film: bool = False,
                 seed: int | None = None,
                 libraries: Sequence[str | Path] | None = None,
                 cache: str | Path | None = None):
        self.root = Path(root)
        #: Where a roll after the first draws its pictures from: ``root`` and,
        #: as the window passes them, every library beside it
        #: (`libraries_beside`). Single passes keep to ``root``.
        self.libraries = [Path(p) for p in (libraries or [root])]
        #: Where each entry's picture signature is kept between sessions, so
        #: telling the photographs apart costs once rather than every launch.
        self.cache = Path(cache) if cache else None
        #: entry -> signature, filled on a thread `open` starts.
        self._signatures: dict[Path, np.ndarray] = {}
        self._signing: threading.Thread | None = None
        #: The entry chosen for each film, so a prescan and the scan after it
        #: show one picture rather than two.
        #: An empty transport. The film is what a demo cannot have when it
        #: is showing a walk from disk, and saying so here rather than in
        #: the window keeps the whole path between the sheet and the hold
        #: loop running.
        self._no_film = bool(no_film)
        self._by_film: dict[str, Path | None] = {}
        #: The film of the roll in progress, or None outside one. While it is
        #: set, each frame shows the strip's entry for where the film is,
        #: overriding the per-film choice above. A roll is the one place
        #: showing a single picture is wrong: a strip of one picture repeated
        #: is a contact sheet nobody can read, where a wrong pick looks exactly
        #: like a right one. Chosen by position, so a frame's prescan, its
        #: metering and its scan are one photograph, which is what the fixed
        #: pair is for.
        self._rolling: str | None = None
        #: The film a scan is metering for. The driver's probes are `scan`
        #: calls with no film -- on the device that changes nothing, and here
        #: it would change the picture.
        self._metering: str | None = None
        #: The last entry decoded, so a frame's metering probes and its scan
        #: decode it once rather than once each.
        self._decoded: tuple[Path, dict[str, Any]] | None = None
        #: The strip in the transport, per film: one entry per frame, frame N
        #: showing entry N. What a roll walks. See `_next_strip`.
        self._strips: dict[str, list[Path]] = {}
        #: Every entry that kept a prescan in ``root``, per film -- what the
        #: first strip is -- and the entries strips have shown this session.
        self._pools: dict[str, list[Path]] = {}
        self._shown: dict[str, set[Path]] = {}
        #: Rolls run this session. The first walks the strip as it always has;
        #: each one after it started from the Roll button gets a new strip.
        self._rolls = 0
        #: The roll `scan_roll` has been asked for and `_begin_roll` has not
        #: yet counted: its film, and whether it reads a new strip.
        self._starting: tuple[str, bool] | None = None
        #: Lays each new strip. Seeded only where a test wants it repeatable.
        self._rng = random.Random(seed)
        #: The bytes and calibration behind the last pass, and nothing older:
        #: emptied as each pass starts and filled by :meth:`_take`, handed to
        #: the session by :meth:`capture_record`.
        self._capture: dict[str, Any] = {
            "reference": None, "ccd_mask": None, "raw": None, "raw_layout": None,
        }
        #: What shape the device produces at each resolution, read from the
        #: library rather than derived. See :meth:`_shape_for`.
        self._shapes: dict[int, tuple[int, int] | None] = {}
        #: The one entry this demo is showing, when it found a good pair. Its
        #: prescan answers a prescan and its scan answers a scan, so the two
        #: are the same picture -- which is what makes the filmstrip's
        #: "the scan replaces its prescan" behaviour visible at all.
        self.pair: Path | None = Path(entry) if entry else None
        self.speed = max(1.0, speed)
        #: Where the film sits, in millimetres from where this frame started.
        #: The demo moves it for real so the hold loop has something to
        #: converge on -- before this, `nudge` reported a move and the next
        #: prescan came back identical, so the loop could only ever be
        #: pretended at.
        self._film_mm = 0.0
        #: Backlash, as the transport really has it: two to three commands are
        #: swallowed after a direction change and the distance arrives later.
        #: Modelled because it is the reason the loop iterates at all.
        self._owed_mm = 0.0
        self._last_way = 0
        #: One frame per strip whose transport slips, so `not_converged` and
        #: the end-of-roll warning can be seen rather than taken on trust.
        #: Keyed on where the film is, and only inside a roll.
        self._slipping_index = 2
        self.t = _FakeTransport()
        self.log_hook: Any = None
        self.progress_hook: Any = None
        self.shading = None
        self.ccd_mask = None
        self.last_raw = None
        self.last_raw_layout = None
        #: The last pass's pixels before correction, and its meta: the same
        #: contract as the real one's. Set on every pass, so neither is ever a
        #: leftover -- `ScanSession` files `last_pixels_raw`, and a roll copies
        #: it onto the frame as the pass happens.
        self.last_pixels_raw: np.ndarray | None = None
        self.last_scan_meta: dict[str, Any] | None = None
        #: What the last metering measured, left by the driver's own
        #: `auto_exposure` for the scan that asked for it.
        self.last_metering: dict[str, Any] | None = None
        #: Where this pretend carriage waits between passes. A pass that
        #: starts at the far end is read bottom-up and ends at home; one that
        #: starts at home is read top-down and stays at the far end when its
        #: byte-14 bit 0 is set -- `DirectScanner.byte14_for`, not a copy.
        #: That is the documented mechanism and nothing more: the hardware
        #: sometimes goes home between passes for reasons not known, so the
        #: demo reads bottom-up at least as often as the scanner does, never
        #: less, which is the useful direction for an exercise.
        self._carriage_far = False
        self._inquiry = _Inquiry()
        self._entries: list[Path] = []
        self._next = 0
        self._position = 0

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> DemoScanner:
        self._entries = sorted(
            p.parent for p in self.root.glob("*/scan.json")
        ) if self.root.exists() else []
        # Off the thread that answers the window: reading every library's
        # pictures takes a while the first time, and only a second roll needs
        # them.
        self._signing = threading.Thread(target=self._sign_pictures, daemon=True,
                                         name="demo-pictures")
        self._signing.start()
        if self.pair is None:
            self.pair = best_pair(self.root)
        if self.pair is not None:
            self._log(f"demo mode: showing {self.pair.name}")
            self._log("a prescan and a scan are drawn from the one entry "
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
        self._decoded = None

    def __enter__(self) -> DemoScanner:
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()

    def inquiry(self, refresh: bool = False) -> _Inquiry:
        return self._inquiry

    #: Where this pretend strip ends: thirty-eight frames, 0 to 37 on the
    #: counter -- a whole 35 mm roll, as Stefan asked, where it used to be the
    #: seventeen of `full_17_strip`. Each frame is a library prescan
    #: (`_pool_for`: 41 on this machine, so a roll repeats none of them), and
    #: 37 stays inside what the driver believes a strip can reach
    #: (`DirectScanner.LAST_PLAUSIBLE_POSITION`, 39). A fact about the film
    #: being pretended, not about the driver, so it is the demo's own number.
    LAST_POSITION = 37

    def wait_warm(self, timeout: float = 300.0, poll: float = 5.0) -> None:
        """Answered, because `session.seek` asks it of the real one first.

        There is no lamp here to wait for, so it returns at once -- which is
        also what the real one does once the lamp is warm. The seek above the
        seam calls it on whatever stands at the seam, with no branch for the
        demo, so the demo runs the same seek the scanner does.
        """

    def position(self) -> int | None:
        """Where the film is, or None where the real one would not say.

        The real one answers None rather than raise when READ STATE fails,
        and its callers have a branch for that. The failure this stand-in can
        have is its transport closed under it -- a force abort -- and there
        the real read fails too, so this says nothing either.
        """
        if self.t.closed:
            return None
        return self._position

    def advance(self, steps: int = 1, timeout: float = 30.0, poll: float = 0.5):
        self._need_film("advance")
        self._work(7.0)
        if self._position >= self.LAST_POSITION:         # a strip runs out
            self._log("no advance: treating that as the end of the film")
            return None
        self._position += steps
        if self._rolling is not None:
            self._new_frame()
        self._log(f"advanced to position {self._position}")
        return self._position

    def _new_frame(self) -> None:
        """A roll's next frame starts where the advance left it.

        The offset an operator asked for is what the hold loop then puts in;
        carrying the last frame's over would hand it a frame already moved.
        """
        self._film_mm = 0.0
        self._owed_mm = 0.0
        self._last_way = 0

    def retreat(self, steps: int = 1, timeout: float = 30.0, poll: float = 0.5):
        self._need_film("wind back")
        self._work(7.0)
        if self._position <= 0:
            self._log("no movement: already at the first frame")
            return None
        self._position = max(0, self._position - steps)
        self._log(f"went back to position {self._position}")
        return self._position

    def nudge(self, millimetres: float) -> dict[str, Any]:
        """Move the simulated film, by the driver's own arithmetic.

        Only the film is pretend. Which `param` byte a distance becomes, what
        that param delivers, and where the cap falls are all taken from
        `DirectScanner` -- `param_for_mm` is a `@staticmethod` precisely so
        there is one home for the snapping.

        This used to be typed out here, and it went stale exactly as that
        arrangement always does: it kept `param` capped at 8 and a ramp of
        0.1662 mm after the driver moved to 87 and 0.1945. A frame set 38
        units out then held in one command on the hardware and came back
        `not_converged` in the demo -- which reads as a weak hold loop and was
        a stale copy.
        """
        self._need_film("move")
        param = self.param_for_mm(millimetres)
        asked = self.STEP_MM * param + self.OVERHEAD_MM
        short = abs(millimetres) - asked
        clamped = short > 1e-9
        asked = asked if millimetres >= 0 else -asked
        way = 1 if millimetres >= 0 else -1

        delivered = asked
        if (self._rolling is not None
                and self._position == self._slipping_index):
            # A frame whose transport slips. The command is accepted and
            # reports normally -- which is exactly what makes it worth
            # simulating, because that is how the real one fails too.
            delivered = 0.0
            self._log("slide sub-frame: commanded, and the film did not move")
        elif way != self._last_way and self._last_way:
            # Backlash: the first move after a reversal mostly disappears into
            # the gear train and comes back on the move after.
            swallowed = min(abs(asked), 2.2 * 0.1057)
            self._owed_mm += swallowed * way
            delivered = asked - swallowed * way
        else:
            delivered += self._owed_mm
            self._owed_mm = 0.0

        self._film_mm += delivered
        self._last_way = way
        self._log(f"slide sub-frame: {say_units(asked)} (param {param}), "
                  f"film now {say_units(self._film_mm)}")
        self._work(1.5)
        # The same keys the real one returns, including the two the hold loop
        # reads: `_hold_to_approved` takes `clamped` to decide whether to say
        # a command fell short, and without them that warning was unreachable
        # at any distance.
        return {"param": param, "forward": millimetres >= 0,
                "asked_mm": round(asked, 3),
                "requested_mm": round(millimetres, 3), "clamped": clamped,
                "short_mm": round(short, 4) if clamped else 0.0}

    def _shift(self, width: int) -> int:
        """How many columns the film has moved in the aperture, at this width.

        The whole point of moving the film in this stand-in: a pass has to
        come back showing where the film actually is, or a loop that looks
        again after moving learns nothing and the code under test is never
        really exercised. A scan as well as a prescan -- only prescans used
        to move, so a frame held to its approved position was scanned where
        it had been before the hold.
        """
        if not self._film_mm or width <= 0:
            return 0
        return int(round(self._film_mm / (APERTURE_MM / width)))

    def capture_record(self) -> dict[str, Any]:
        """The bytes and calibration behind the last pass, as the real one does.

        This pass's, and no other's. It is emptied as every pass starts, so a
        prescan served from a stored `prescan.tif` -- which has no bytes and
        no calibration of its own -- hands over none, where it used to hand
        over whatever the previous decode had left. What the session files
        from a raw read re-decodes and corrects like any other entry; what it
        files from a stored `prescan.tif` is labelled corrected, and is.
        """
        return dict(self._capture)

    # -- the parts the session calls --------------------------------------

    def ensure_shading(self, path: Any, reuse: bool = False, skip: bool = False) -> dict:
        if skip:
            return {"action": "skipped", "summary": "shading off (demo)"}
        self._work(210.0 if not reuse else 1.0)
        self._calibrated = True
        return {
            "action": "loaded" if reuse else "calibrated",
            "summary": f"shading {'loaded' if reuse else 'calibrated'} (demo)",
        }

    def _refuse_uncalibrated(self, shading: bool) -> None:
        """Refuse a corrected pass before any calibration, as the real one does.

        The real scanner used to calibrate inside such a pass; it now refuses
        it, and so does this -- with its words, not a copy of them.
        """
        if shading and not getattr(self, "_calibrated", False):
            raise DirectScanner.uncalibrated()

    def _refuse(self, resolution: int, frame: Any, shading: bool) -> None:
        """Refuse a corrected pass wherever the real one would, before it.

        The driver's own tests and words, in its order: a pass wider than any
        reference the device will produce (`correctable_at`), then one this
        session has not calibrated for. The demo used to take 7200 dpi,
        resize a stored picture to it and file a roll the scanner refuses
        frame by frame -- after the calibration and metering had been spent.
        """
        if shading and not DirectScanner.correctable_at(resolution, frame):
            raise DirectScanner.uncorrectable(resolution, frame)
        self._refuse_uncalibrated(shading)

    #: What READ GAIN/OFFSET answers on this device, whatever was written: a
    #: fixed reference rather than a readback -- across 17 responses in the
    #: strip capture only the live offsets ever moved. The driver meters every
    #: scale against it, and reads its exposures as the timer's ceilings, so
    #: these are the device's own numbers rather than round ones.
    DEVICE_SETTINGS = Settings(exposure=[9604, 6506, 6506, 7745],
                               gain=[40, 33, 21, 25], offset=[12, 10, 28, 10])

    def get_gain_offset(self) -> Settings:
        return self.DEVICE_SETTINGS.scaled(1.0)

    def set_gain_offset(self, settings: Settings, infrared: bool = False) -> None:
        """Accepted and forgotten, as the device forgets it across a sequence.

        A pass here is a stored picture, which no exposure moves.
        """

    def auto_exposure(self, *args: Any, film: str = "negative",
                      **kw: Any) -> list[float]:
        """The driver's own metering, run on this stand-in's passes.

        Its probes are passes here as they are on the device, so a metered
        scan or roll costs what metering costs and files what metering
        measured. They are sent as `scan` calls with no film -- which on the
        device changes nothing, and here would change the picture -- so they
        are held to the film being metered. The stored pictures carry the
        exposure they were taken at: the scales this arrives at change the
        record, not the pixels.
        """
        held, self._metering = self._metering, film
        try:
            return self._drivers_metering(*args, film=film, **kw)
        finally:
            self._metering = held

    def prescan(
        self, resolution: int = 300, frame: Any = None, keep_raw: bool = False,
        film: str = "negative", shading: bool = True,
    ) -> tuple[np.ndarray, ScanParameters]:
        """A framing pass, as the real one takes it: RGB, 8-bit, whole window.

        At the resolution asked for, which a roll's `prescan_resolution`
        now reaches; corrected unless ``shading=False``; and refused, like a
        scan, where there is no film in the transport. An 8-bit pass comes
        back 8-bit, whatever the stored picture was.
        """
        frame = frame or FULL_FRAME
        self._refuse(resolution, frame, shading)
        self._need_film("prescan")
        self._forget_last_pass()
        started_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        started = time.monotonic()
        self._work(estimate_seconds(resolution, False), lines=int(resolution * 0.957))
        # A framing pass is RGB, always: the real one sets passes=0x80 and
        # 8-bit, so a four-channel prescan is a shape the window would never
        # see from the device.
        image, meta = self._take("prescan", film, resolution, channels=3,
                                 depth=8, shading=shading, keep_raw=keep_raw,
                                 passes=ONE_PASS_COLOR)
        meta.update(self._settings_meta(1.0, metered=False, fast=False),
                    resolution_dpi=resolution, film=film, depth=8,
                    frame=list(frame), started_utc=started_utc,
                    duration_s=round(time.monotonic() - started, 1))
        self.last_scan_meta = dict(meta)
        return image, ScanParameters(
            width=meta["width"], lines=meta["height"],
            bytes_per_line=meta["bytes_per_line"], filter_offset1=0,
            filter_offset2=0, available_lines=0)

    def _read_as_carriage(self, raw: np.ndarray, passes: int, keep_raw: bool
                          ) -> tuple[np.ndarray, dict[str, Any]]:
        """This pass, handed over the way the scanner would hand it over.

        Encoded as index-format lines in the order the carriage reads them --
        bottom-up when it starts at the far end -- and decoded by the driver's
        own `decode_index`, which turns it upright and says which way it came.
        Those lines are this pass's bytes, kept as `last_raw` when the pass
        keeps its bytes, as the real one keeps them: so what is filed decodes
        to exactly what is held, and its record agrees with its bytes
        whichever way it was read. Stored bytes used to be reversed instead,
        which disagreed with the record wherever the stored pass had itself
        been read bottom-up.
        """
        reversed_now = self._carriage_far
        lines, width, channels = raw.shape
        per_line = width * raw.dtype.itemsize
        params = ScanParameters(
            width=width, lines=lines, bytes_per_line=per_line,
            filter_offset1=0, filter_offset2=0, available_lines=0)
        blob = encode_index(raw, reversed=reversed_now)
        upright, direction = DirectScanner.decode_index(blob, params, channels)
        upright = upright.reshape(raw.shape).astype(raw.dtype, copy=False)
        if keep_raw:
            self.last_raw = blob
            self.last_raw_layout = {
                "format": "index",
                "bytes_per_line": per_line,
                "line_stride": per_line + INDEX_HEADER,
                "index_header": INDEX_HEADER,
                "width": width,
                "lines": lines,
                "channels": channels,
                "byte_order": "little",
                "lines_received": len(blob) // (per_line + INDEX_HEADER),
            }
        if reversed_now:
            self._log("the carriage started at the far end: read bottom-up, "
                      "turned upright")
            self._carriage_far = False
        else:
            self._carriage_far = bool(DirectScanner.byte14_for(passes) & 1)
        return upright, {
            "read_direction": direction.as_record(),
            "carriage_state": {"far_end": reversed_now, "stale": False,
                               "modelled": True},
        }

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
        fast_infrared: bool = True,
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
        frame = frame or FULL_FRAME
        self._refuse(resolution, frame, shading)
        self._need_film("scan")
        if auto_exposure:
            self._log(f"auto-exposure: probing in RGB (scan is "
                      f"{'RGBI' if infrared else 'RGB'})")
            target = kw.get("exposure_target")
            exposure_scale = self.auto_exposure(
                **({"target": target} if target is not None else {}),
                infrared=infrared, film=film, shading=shading)
            self._log(f"auto-exposure: {[round(v, 3) for v in exposure_scale]}")
        self._forget_last_pass()
        started_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        started = time.monotonic()
        fast = bool(fast_infrared and infrared)
        self._work(
            estimate_seconds(resolution, infrared, fast),
            lines=int(resolution * 0.957),
        )
        # Four planes whenever infrared was asked for, as the device sends:
        # an RGBI request used to come back three wide from an RGB entry.
        image, meta = self._take(
            "scan", film, resolution, channels=4 if infrared else 3, depth=16,
            shading=shading, keep_raw=keep_raw,
            passes=ONE_PASS_RGBI if infrared else ONE_PASS_COLOR)
        meta.update(self._settings_meta(exposure_scale, metered=auto_exposure,
                                        fast=fast),
                    resolution_dpi=resolution, film=film, depth=16,
                    frame=list(frame), started_utc=started_utc,
                    duration_s=round(time.monotonic() - started, 1))
        # Only for a scan that did its own metering, as on the real one.
        if auto_exposure and self.last_metering is not None:
            meta["metering"] = self.last_metering
        self.last_scan_meta = dict(meta)
        return image, meta

    def scan_roll(
        self,
        frames: int | None = None,
        resolution: int = 1800,
        infrared: bool = True,
        film: str = "negative",
        only: tuple[int, ...] | None = None,
        first_index: int = 0,
        **kw: Any,
    ):
        """The roll, walked by the driver's own loop on this stand-in's film.

        `DirectScanner.scan_roll` itself, with this stand-in where the scanner
        would be: its per-frame failures and `max_failures`, its end on a
        blank frame, its `prescan_resolution`, its metering, the stop it hands
        the hold and aim loops, and the frames it yields. The demo kept a loop
        of its own that borrowed the driver's decisions one at a time, and it
        had drifted in the parts it had not borrowed: one failed frame ended
        the roll, it prescanned at 300 dpi whatever was asked, and it logged
        every machine proposal as the operator's.

        What is the demo's own is the film. A roll from the Roll button reads
        a strip of its own; a roll scanning frames chosen on the contact sheet
        scans the strip that was walked, or the positions set there would be
        for other pictures. So does one that starts further along than frame
        1: that is more of the strip in the transport -- a walk of 11 to the
        end after one of 1 to 10, added to the same sheet -- and a new strip
        would put other pictures there. An empty transport refuses a roll
        before anything moves.

        The roll is counted, and a new strip laid, at its first pass
        (`_begin_roll`), not here: the driver's loop makes its own refusals
        on its first step, and a roll it refuses has walked nothing.
        """
        self._need_film("roll")
        self._starting = (film, only is None and first_index == 0)
        self._rolling = film
        self._new_frame()
        try:
            yield from self._drivers_roll(
                frames=frames, resolution=resolution, infrared=infrared,
                film=film, only=only, first_index=first_index, **kw)
        finally:
            # Outside a roll again, so a single pass shows its own film rather
            # than the last frame's, and a manual nudge from the window is not
            # mistaken for the slipping frame.
            self._rolling = None
            self._starting = None

    def _begin_roll(self) -> None:
        """Count the roll in progress, and lay its strip, at its first pass.

        Not as `scan_roll` is called. The driver's loop refuses infrared on
        film blind to it, an unknown film or an unknown meter mode on its
        first step, before any pass -- and a roll it refused used to have
        laid a strip and been counted already. The roll after the operator
        fixed his settings then showed a third set of pictures rather than
        the second, and frames chosen on the sheet were scanned from a strip
        nobody had walked. A roll that ends before its first pass for any
        reason -- an empty choice, a stop -- walked nothing either.
        """
        if self._starting is None:
            return
        film, fresh = self._starting
        self._starting = None
        if fresh and self._rolls:
            self._next_strip(film)
        self._rolls += 1

    #: The real loop, run against the simulated film above rather than
    #: reimplemented. It only needs `nudge`, `prescan` and `_log`, all of
    #: which this class has -- so the demo exercises the correlation, the
    #: decision table, the move cap, the refused reversal and the direction
    #: check as the scanner would, instead of a hand-written imitation that
    #: cannot disagree with it.
    _hold_to_approved = DirectScanner._hold_to_approved
    #: The roll and the metering, taken the same way: `scan_roll` and
    #: `auto_exposure` above are these, with only the film chosen around them.
    _drivers_roll = DirectScanner.scan_roll
    _drivers_metering = DirectScanner.auto_exposure
    #: Same argument as the lines above, for the same reason: the demo runs the
    #: real ensemble and the real aiming loop, so a change that breaks either
    #: shows up with no scanner on the bus.
    _aim_frame = DirectScanner._aim_frame
    _rejudge_for = DirectScanner._rejudge_for
    HOLD_GIVE_UP_FRAMES = DirectScanner.HOLD_GIVE_UP_FRAMES
    #: The transport's law, taken and not retyped. `_aim_frame`'s dry run
    #: reaches for all three and raised `AttributeError` without them -- so the
    #: one place the aimer can be watched with no device was the one place that
    #: died.
    # Re-wrapped:  is a @staticmethod, so the
    # plain function comes back through the class and assigning it here
    # would bind  as its first argument.
    param_for_mm = staticmethod(DirectScanner.param_for_mm)
    #: When a roll ends and what a frame is numbered: the loop that asks is
    #: the driver's own now, and these stay taken for the tests and the
    #: window that ask the stand-in directly.
    roll_ends = staticmethod(DirectScanner.roll_ends)
    place_on_strip = staticmethod(DirectScanner.place_on_strip)
    STEP_MM = DirectScanner.STEP_MM
    OVERHEAD_MM = DirectScanner.OVERHEAD_MM
    MAX_CORRECTION_PARAM = DirectScanner.MAX_CORRECTION_PARAM
    #: No real settling to wait out; the film here is an array.
    HOLD_SETTLE_S = 0.0
    #: The roll loop ends a roll on a device left mid-scan. Nothing here can
    #: be: a force abort closes the stand-in's transport and every pass after
    #: it refuses, which is the failure the loop sees instead.
    suspect: str | None = None

    # -- one pass ------------------------------------------------------------

    def _forget_last_pass(self) -> None:
        """Nothing the previous pass left may describe this one."""
        self._capture = {"reference": None, "ccd_mask": None, "raw": None,
                         "raw_layout": None}
        self.last_pixels_raw = None
        self.last_raw = None
        self.last_raw_layout = None

    def _settings_meta(self, exposure_scale: Any, metered: bool, fast: bool
                       ) -> dict[str, Any]:
        """The exposure part of a pass's meta, as the real one records it."""
        settings = self.get_gain_offset().scaled(exposure_scale)
        return {
            "exposure": settings.exposure,
            "gain": settings.gain,
            "offset": settings.offset,
            "exposure_scale": (exposure_scale
                               if isinstance(exposure_scale, (int, float))
                               else list(exposure_scale)),
            "exposure_metered": bool(metered),
            "fast_infrared": bool(fast),
        }

    def _take(self, kind: str, film: str, resolution: int, channels: int,
              depth: int, shading: bool, keep_raw: bool, passes: int,
              ) -> tuple[np.ndarray, dict[str, Any]]:
        """One pass, handed over the way `DirectScanner.scan` hands one over.

        The stored picture fitted to this pass (`_fit`) is its raw read: what
        `last_pixels_raw` holds, and what the session files. It is corrected
        *last*, with the calibration that describes these very pixels, and
        the corrected picture is what comes back -- or the raw one, when
        ``shading=False`` asked for it.

        Two stored pictures are not a raw read. A test card, or an entry
        filed without a reference, has no calibration: it comes back as it
        was read, and a pass that asked for correction records why it got
        none (`UNCALIBRATED_SOURCE`), where the real one would have refused.
        A picture corrected before it was stored -- a `prescan.tif`, a
        legacy `scan.tif` -- has no raw pixels to give: it comes back with no
        `last_pixels_raw` and no bytes, its report saying the correction ran
        (`CORRECTED_WHEN_STORED`), so the session files it labelled
        corrected. Handing it over as raw is what filed every demo prescan
        as raw pixels that were not.

        Returns the picture and the part of the meta the pass decides.
        """
        source = self._stored(kind, film, channels)
        raw, reference, mask = self._fit(source, resolution, channels, depth)
        finished = bool(source.get("corrected"))
        raw, read = self._read_as_carriage(raw, passes,
                                           keep_raw and not finished)
        image, report, skipped = raw, None, None
        if finished:
            if not shading:
                self._log(f"{source['file']} was corrected when it was kept; "
                          "there are no raw pixels behind it to hand over")
            report = {"applied": CORRECTED_WHEN_STORED, "file": source["file"]}
        else:
            self._capture = {"reference": reference, "ccd_mask": mask,
                             "raw": self.last_raw,
                             "raw_layout": self.last_raw_layout}
            self.last_pixels_raw = raw
            if not shading:
                skipped = SHADING_SKIPPED_EXPLICIT
            elif reference is not None:
                image, report = apply_shading(raw, reference, mask)
            else:
                skipped = UNCALIBRATED_SOURCE
        return image, {
            "channels": raw.shape[2],
            "channel_order": list(CHANNEL_ORDER[: raw.shape[2]]),
            "width": raw.shape[1],
            "height": raw.shape[0],
            "bytes_per_line": raw.shape[1] * raw.dtype.itemsize,
            "shading": report,
            "shading_skipped": skipped,
            # Recorded, not left to be inferred: the bytes filed are this
            # pass's, so nothing was trimmed from their decode.
            "stagger_realigned": 0,
            "demo": True,
            # Which stored picture this pass was drawn from. The pixels and
            # bytes are the pass's own, so without it a demo entry could not
            # be traced to the photograph it shows.
            "demo_source": {"entry": source["entry"], "file": source["file"]},
            **read,
        }

    def _fit(self, source: dict[str, Any], resolution: int, channels: int,
             depth: int) -> tuple[np.ndarray, ShadingReference | None,
                                  bytes | None]:
        """The stored picture as this pass reads it, and the calibration for it.

        Sized to what the device produces at this resolution (`_shape_for`):
        a pass reported as 900 dpi that hands back 1800 dpi pixels is a
        stand-in lying about the one thing the window sizes everything from,
        and every readout downstream -- the estimate, the zoom, the crop -- is
        then off by a factor. Moved to where the film sits (`_shift`). As
        many planes as the pass has, and at its depth: an RGBI pass is four
        planes and a prescan is 8-bit, on the device and so here.

        Nearest-neighbour, as a pair of index maps rather than image
        operations, because the column map is also what says which stored
        column each column of the pass now shows -- and so which calibration
        column corrects it.
        """
        pixels = np.asarray(source["pixels"])
        if pixels.ndim == 2:
            pixels = pixels[..., None]
        if pixels.shape[2] < 3:
            pixels = np.repeat(pixels[..., :1], 3, axis=2)
        height, width, planes = pixels.shape
        dpi = source.get("dpi")
        shape = None if dpi == resolution else self._shape_for(resolution)
        if shape:
            h, w = shape
        elif dpi:
            h = max(1, round(height * resolution / dpi))
            w = max(1, round(width * resolution / dpi))
        else:
            h, w = height, width
        # Each axis by its own ratio, in integers so the map is exact: a
        # recorded shape need not be the stored one's aspect to the pixel.
        rows = (np.arange(h) * height) // h
        columns = np.roll((np.arange(w) * width) // w, self._shift(w))
        same_columns = w == width and bool(np.array_equal(columns, np.arange(width)))
        kept = min(planes, channels)
        if (h, w) != (height, width):
            self._log(f"{source['entry'] or source['file']}: "
                      f"{height}x{width} fitted to {h}x{w} for {resolution} dpi")
        if h == height and same_columns:
            raw = pixels[..., :kept]
        else:
            raw = pixels[rows[:, None], columns[None, :], :kept]
        raw = _at_depth(raw, depth)
        if kept < channels:
            # A stored picture with no infrared record, asked for RGBI. The
            # device always sends four planes, so a clear one is added --
            # film with no dust on it -- rather than a pass one plane short.
            level = int(CLEAR_INFRARED * np.iinfo(raw.dtype).max)
            clear = np.full(raw.shape[:2] + (channels - kept,), level, raw.dtype)
            raw = np.concatenate([raw, clear], axis=2)

        reference, mask = source.get("reference"), source.get("ccd_mask")
        if reference is None:
            return raw, None, None
        added = set(range(kept, channels))
        if same_columns and not added & set(reference.ref):
            # Every column is still the one the stored calibration measured.
            return raw, reference, mask
        return raw, self._pass_reference(reference, mask, width, columns,
                                         drop=added), None

    @staticmethod
    def _pass_reference(reference: ShadingReference, mask: bytes | None,
                        width: int, columns: np.ndarray,
                        drop: set[int] | frozenset[int] = frozenset(),
                        ) -> ShadingReference:
        """The stored calibration, read at the columns this pass now shows.

        A pass resized or moved from its stored picture shows each stored
        column somewhere else, or twice, or not at all. The device's mask
        cannot say that -- it names CCD columns in order, once each -- but a
        reference as wide as the pass can: its column ``j`` is the stored
        reference column for whatever column ``j`` now shows, so correcting
        with it is the stored correction, moved with the picture. Filed
        beside the pass, it gives `library.corrected` the same picture back.

        A column the stored correction never reached passes through
        unchanged: a gain of one and no dark floor. Channels in ``drop`` --
        a plane the stored picture did not have -- are left out, so nothing
        corrects what no calibration measured.
        """
        width = int(width)
        loc = (build_width_to_loc(bytes(mask), width) if mask is not None
               else np.arange(min(width, reference.pixels_per_line)))
        reached = columns < loc.size
        at = (loc[np.minimum(columns, loc.size - 1)] if loc.size
              else np.zeros_like(columns))
        ref: dict[int, np.ndarray] = {}
        dark: dict[int, np.ndarray] = {}
        for c in reference.channels:
            if c in drop:
                continue
            light = np.asarray(reference.ref[c], dtype=np.float64)[at]
            if c in reference.dark:
                floor = np.asarray(reference.dark[c], dtype=np.float64)[at]
                light[~reached] = reference.mean[c] - reference.dark_mean[c]
                floor[~reached] = 0.0
                dark[c] = floor
            else:
                light[~reached] = reference.mean[c]
            ref[c] = light
        return ShadingReference(
            ref=ref, mean={c: reference.mean[c] for c in ref},
            pixels_per_line=len(columns), dark=dark,
            dark_mean={c: reference.dark_mean[c] for c in dark})

    # -- internals ---------------------------------------------------------

    def _need_film(self, doing: str) -> None:
        """Refuse what an empty transport would refuse, where it would.

        A window showing a walk that was stored earlier has no film behind it,
        and the honest place to say so is here -- the same place a real
        transport fault is raised. `ScanSession` turns it into a `failed`
        event and the window reports it through the path it already has.

        Greying the button instead was the first attempt, and it skipped the
        work: `on_scan_chosen` is the sole writer of `approved.json` and the
        sole submitter of a `Roll`, so nothing between the sheet and the hold
        loop ran at all. A demo that cannot reach the code it is demonstrating
        is not demonstrating it.
        """
        if self._no_film:
            raise UsbError(
                f"there is no film in the transport, so there is nothing to "
                f"{doing}. This window is showing a walk that was stored "
                "earlier; the positions and the ticks are real.")

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

        Inside a roll, the strip's entry for where the film is; and for the
        film being metered, whatever film the probe was sent with.
        """
        if self._rolling is not None:
            self._begin_roll()
            strip = self._strip_for(self._rolling)
            if strip:
                return strip[self._position % len(strip)]
        film = self._rolling or self._metering or film
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
                record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            scan = record.get("scan") or {}
            if scan.get("film") != film or not (
                    (path / library.RAW_FILE).exists()
                    or (path / library.RAW_PLAIN).exists()):
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
                scan = (json.loads((path / "scan.json").read_text(encoding="utf-8"))
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

    def _decode(self, path: Path) -> dict[str, Any] | None:
        """An entry's pixels from its **raw bytes**, not from its TIFF.

        This is the point of the demo being backed by the library. The stored
        `scan.tif` is an output; `raw.bin.gz` is what the scanner actually
        sent, and decoding it here runs the driver's own decode
        (`library.decode_raw`, which replays whatever `scan()` did after it).
        The pixels come back **uncorrected**, with the entry's reference and
        mask beside them, to be corrected last as a real pass is (`_take`).
        Correcting here, first, is what used to leave the session nothing but
        corrected pixels to file.

        An entry filed without its bytes -- older ones, and some a roll filed
        -- has nothing to decode, and its `scan.tif` is then shown instead:
        that is the decode, stored when it was taken, and it is this entry's
        photograph, where falling back to another entry would show a frame's
        prescan and its scan as two different pictures. One filed before the
        library held raw pixels already carries its correction, and comes
        with no calibration, or it would be corrected twice -- marked
        ``corrected``, so a pass drawn from it is not passed off as raw.

        The same keys as :meth:`_stored` hands back, or None when neither can
        be read. Kept for the next pass: a frame's metering probes and its
        scan are one entry, decoded once.
        """
        if self._decoded is not None and self._decoded[0] == path:
            return self._decoded[1]
        try:
            record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
            image = library.decode_raw(path)
            name = "raw.bin.gz"
            if image is None:
                image = tiff.read(str(path / "scan.tif"))
                name = "scan.tif"
        except Exception as exc:                         # noqa: BLE001
            self._log(f"could not decode {path.name}: {exc}")
            return None

        # Corrected whenever there is a reference to correct with, which is
        # what a real pass does -- the demo shows the picture, not a striped
        # version of it -- unless it was taken raw, or its stored pixels
        # already carry the correction.
        cal = record.get("calibration") or {}
        applied = (record.get("image") or {}).get("corrections_applied") or []
        reference = mask = None
        if not cal.get("skipped") and not (name == "scan.tif"
                                           and "shading" in applied):
            ref_file, mask_file = cal.get("shading"), cal.get("ccd_mask")
            try:
                if ref_file and (path / ref_file).exists():
                    reference = ShadingReference.load(path / ref_file)
                if mask_file and (path / mask_file).exists():
                    mask = (path / mask_file).read_bytes()
            except Exception as exc:                     # noqa: BLE001
                self._log(f"could not read {path.name}'s calibration: {exc}")
                reference = mask = None
        self._log(f"{path.name}: "
                  + ("its raw bytes" if name == "raw.bin.gz"
                     else "no raw bytes, its stored scan.tif")
                  + f" -> {image.shape}")
        got = {
            "pixels": image,
            "dpi": int((record.get("scan") or {}).get("resolution_dpi") or 0) or None,
            "reference": reference, "ccd_mask": mask,
            "entry": path.name, "file": name,
            # A legacy `scan.tif` that carries its correction is handed over
            # as corrected pixels, not as a raw read with nothing to take off.
            "corrected": name == "scan.tif" and "shading" in applied,
        }
        self._decoded = (path, got)
        return got

    def _strip_for(self, film: str) -> list[Path]:
        """The entries the strip in the transport shows, one per frame.

        The first strip of a session is the library's own order, so the demo
        opens on the roll it always has. `_next_strip` lays the ones after it.
        """
        if film not in self._strips:
            self._strips[film] = self._pool_for(film)
            self._shown.setdefault(film, set()).update(
                self._strips[film][: self.LAST_POSITION + 1])
        return self._strips[film]

    def _next_strip(self, film: str) -> list[Path]:
        """Put another strip in the transport: other photographs, in another order.

        Stefan: the first roll is the one it always was, and a second roll in
        the same session reads other pictures. So a new strip is laid from
        every library the demo was given, one entry per *photograph* -- the
        same frame scanned six times is one picture, not six -- taking the
        photographs no strip has shown yet first, then the ones already seen,
        shuffled. Only when the libraries run out of new ones does a strip
        repeat any, and never in the same places.
        """
        self._strip_for(film)                   # the first strip, if not yet laid
        pictures = self._pictures_for(film)
        shown_entries = self._shown.setdefault(film, set())
        shown = {group for group, entries in pictures.items()
                 if shown_entries & set(entries)}
        fresh = [g for g in pictures if g not in shown]
        seen = [g for g in pictures if g in shown]
        self._rng.shuffle(fresh)
        self._rng.shuffle(seen)
        chosen = (fresh + seen)[: self.LAST_POSITION + 1]
        self._rng.shuffle(chosen)
        strip = [self._rng.choice(pictures[g]) for g in chosen]
        if not strip:                           # nothing readable anywhere
            strip = list(self._pool_for(film))
        self._strips[film] = strip
        shown_entries.update(strip)
        self._log(f"a new strip in the transport: {len(strip)} pictures, "
                  f"{min(len(fresh), len(strip))} not shown before, from "
                  f"{len(pictures)} photographs in {len(self.libraries)} "
                  f"librar{'y' if len(self.libraries) == 1 else 'ies'}")
        return strip

    def _sign_pictures(self) -> None:
        """Every entry's picture signature, from the cache where it has one."""
        known: dict[str, np.ndarray] = {}
        if self.cache is not None and self.cache.exists():
            try:
                with np.load(self.cache) as data:
                    known = dict(zip(data["paths"].tolist(), data["signatures"]))
            except (OSError, ValueError, KeyError):
                known = {}
        entries = sorted({p.parent for lib in self.libraries
                          for p in lib.glob("*/scan.json")})
        added = False
        for entry in entries:
            key = entry.as_posix()
            signature = known.get(key)
            if signature is None:
                signature = picture_signature(entry)
                if signature is None:
                    continue
                known[key], added = signature, True
            self._signatures[entry] = signature
        if added and self.cache is not None and known:
            try:
                self.cache.parent.mkdir(parents=True, exist_ok=True)
                np.savez(self.cache, paths=np.array(list(known)),
                         signatures=np.stack(list(known.values())))
            except OSError as exc:
                self._log(f"could not keep the picture signatures: {exc}")

    def _pictures_for(self, film: str) -> dict[int, list[Path]]:
        """This film's photographs across the libraries: group -> its entries.

        Entries of the right film where there are two or more, as for the first
        strip; each photograph lists every entry that shows it, and a strip
        takes one of them.
        """
        if self._signing is not None:
            self._signing.join()
        entries = sorted(self._signatures)
        films = {}
        for entry in entries:
            try:
                record = json.loads((entry / "scan.json").read_text(encoding="utf-8"))
                films[entry] = (record.get("scan") or {}).get("film")
            except (OSError, ValueError):
                continue
        matching = [e for e in entries if films.get(e) == film]
        chosen = matching if len(matching) >= 2 else [e for e in entries if e in films]
        labels = group_pictures([self._signatures[e] for e in chosen])
        pictures: dict[int, list[Path]] = {}
        for entry, label in zip(chosen, labels):
            pictures.setdefault(label, []).append(entry)
        return pictures

    def _pool_for(self, film: str) -> list[Path]:
        """Every entry a strip of this film can be laid from, in library order.

        Entries of the right film first -- being shown a colour negative for a
        black and white roll is no more a demonstration here than it is for a
        single pass. Two is the point at which a strip is worth calling one; a
        library with fewer of that film walks whatever kept a prescan instead.
        """
        if film in self._pools:
            return self._pools[film]
        matching, any_prescan = [], []
        for path in sorted(self.root.glob("*/prescan.tif")):
            entry = path.parent
            any_prescan.append(entry)
            try:
                record = json.loads((entry / "scan.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (record.get("scan") or {}).get("film") == film:
                matching.append(entry)
        self._pools[film] = matching if len(matching) >= 2 else any_prescan
        return self._pools[film]

    def _stored(self, kind: str, film: str, channels: int) -> dict[str, Any]:
        """The stored picture a pass is drawn from, and what is known of it.

        A prescan is drawn from the entry's own scan -- its raw bytes, fitted
        to the framing pass and corrected last like any other pass -- because
        a framing pass and a scan are the same photograph, and showing the
        right film matters more here than showing the right resolution.

        The entry's stored `prescan.tif` is the fallback, for an entry with
        no raw bytes or no reference to correct them. It is the picture as
        the operator was shown it, **corrected** when it was taken, and it
        used to be served as though it were this pass's raw read: every demo
        prescan and walk frame was then filed as raw pixels that were not,
        the failure CLAUDE.md records of 26 prescans. It is handed over as
        what it is (``corrected``), so the session files it labelled so.

        Keys: ``pixels``; ``dpi``, None where unknown; ``reference`` and
        ``ccd_mask``, None where nothing describes the pixels; ``entry``
        and ``file``, which stored picture it is; and ``corrected``, true
        where the pixels already carry their correction.
        """
        source = self._source_for(film)
        if source is None:
            return self._pixels(channels)
        if kind == "prescan" and not _correctable(source):
            tif = source / "prescan.tif"
            if tif.exists():
                try:
                    image = tiff.read(str(tif))
                    self._log(f"prescan.tif from {source.name}  {image.shape}")
                    return {"pixels": image, "dpi": None, "reference": None,
                            "ccd_mask": None, "entry": source.name,
                            "file": "prescan.tif", "corrected": True}
                except Exception as exc:                 # noqa: BLE001
                    self._log(f"could not read {tif.name}: {exc}")
        got = self._decode(source)
        return got if got is not None else self._pixels(channels)

    def _pixels(self, channels: int) -> dict[str, Any]:
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
                self._log(f"demo frame from {path.name}")
                return got
        self._next += 1
        return {"pixels": _test_card(channels, self._next), "dpi": None,
                "reference": None, "ccd_mask": None, "entry": None,
                "file": "test card"}


#: Why a demo pass that asked to be corrected was not: nothing describes the
#: stored picture it was drawn from -- a test card, or an entry filed without
#: a reference. Not `SHADING_SKIPPED_EXPLICIT`, which says the caller chose
#: raw pixels; this pass asked for a correction there was none to give, and
#: its record says so rather than passing for one that needed none.
UNCALIBRATED_SOURCE = ("demo: no calibration describes the stored picture "
                       "this pass was drawn from")

#: What a pass drawn from a picture corrected before it was stored reports
#: as its correction: that one ran, and when. Truthy, as a real report is, so
#: the session files the pixels labelled corrected (its "no raw pixels came
#: with this pass" branch) rather than as raw.
CORRECTED_WHEN_STORED = "before the stored picture was kept"


#: The infrared plane of clear film: what a stored picture with no infrared
#: record is given when an RGBI pass is asked of it. Film with no dust on it
#: is flat in infrared; the level is the test card's.
CLEAR_INFRARED = 0.85


def _at_depth(image: np.ndarray, depth: int) -> np.ndarray:
    """``image`` as a pass at this depth delivers it: 8 or 16 bits a sample.

    The same reduction the driver's own exports make (`export.to_8bit`), so
    an 8-bit pass drawn from a 16-bit stored picture is that picture, not a
    stretched one.
    """
    if depth == 8:
        if image.dtype in (np.uint8, np.uint16):
            return to_8bit(image)
        return np.clip(image, 0, 255).astype(np.uint8)
    if image.dtype == np.uint16:
        return image
    if image.dtype == np.uint8:
        return image.astype(np.uint16) * 257
    return np.clip(image, 0, 65535).astype(np.uint16)


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
            record = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        dpi = int((record.get("scan") or {}).get("resolution_dpi") or 0)
        if dpi > best_dpi:
            best, best_dpi = entry, dpi
    return best


def _correctable(path: Path) -> bool:
    """Whether an entry holds raw bytes and a reference that corrects them.

    Read from its record, without decoding it: a walk asks this of every
    frame's entry before deciding whether its prescan can be drawn from the
    raw bytes. The same test `_decode` makes before it loads a reference.
    """
    try:
        record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    cal = record.get("calibration") or {}
    ref_file = cal.get("shading")
    return bool(not cal.get("skipped") and ref_file and (path / ref_file).exists()
                and ((path / library.RAW_FILE).exists()
                     or (path / library.RAW_PLAIN).exists()))


def _entry_channels(path: Path) -> int:
    try:
        record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
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
