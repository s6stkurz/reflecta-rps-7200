"""Bracket capture: the two methods `DirectScanner` carried until 2026-09-26.

Archived with the rest of the multi-exposure study (see ../README.md). They were
methods of `rps7200.direct.DirectScanner`, verbatim; a mixin here so the ladder
tests keep running against them:

    class Scanner(BracketMixin, DirectScanner): ...

`scan_bracket` drives the device -- it calls `self.scan()` once per rung -- so it
belongs in the driver if the feature is ever brought back, not in a script.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import numpy as np

from rps7200.direct import FILM_NEGATIVE, Settings


class BracketMixin:
    MIN_BRACKET_PASSES = 2
    MAX_BRACKET_PASSES = 9

    def bracket_ladder(
        self,
        scales: Sequence[float],
        passes: int,
        stops: float = 2.0,
        base: Settings | None = None,
    ) -> list[float]:
        """Geometric multipliers for a bracket, topping out at the timer ceiling.

        ``scales`` is the metered per-channel exposure scale. Every pass
        multiplies all three channels by the *same* number, so the ratio between
        two passes is the same in every channel -- which is what lets the merge
        weight a pass with one exposure value instead of three.

        The top of the ladder is the largest multiplier that keeps every channel
        inside the 16-bit exposure timer. Past 65535 the timer wraps and the pass
        comes back darker, so a bracket that ignored this would not merely
        saturate, it would fold. The rest of the ladder steps down from there by
        ``stops``.

        Which channel binds depends on the film: green metered highest in every
        negative measured here, so green usually sets the ceiling.
        """
        if not self.MIN_BRACKET_PASSES <= passes <= self.MAX_BRACKET_PASSES:
            raise ValueError(
                f"a bracket is {self.MIN_BRACKET_PASSES} to "
                f"{self.MAX_BRACKET_PASSES} passes, got {passes}"
            )
        if stops <= 0:
            raise ValueError(f"stops must be positive, got {stops}")

        base = base or self.get_gain_offset()
        metered = [
            base.exposure[c] * (scales[c] if c < len(scales) else 1.0)
            for c in range(3)
        ]
        headroom = min(
            (65535.0 / e for e in metered if e > 0), default=1.0
        )
        if headroom < 1.0:
            # The metered exposure is already at the rail; the bracket can only
            # go down from here.
            headroom = 1.0
        top = headroom
        bottom = top / (2.0 ** stops)
        ladder = list(np.geomspace(bottom, top, passes))

        binding = min(range(3), key=lambda c: 65535.0 / metered[c] if metered[c] else 1e9)
        self._log(
            f"bracket ladder: {passes} passes over {stops:g} stops, "
            f"x{bottom:.3f} to x{top:.3f} of the metered exposure "
            f"({'RGB'[binding]} binds the ceiling)"
        )
        return ladder

    def scan_bracket(
        self,
        passes: int = 3,
        stops: float = 2.0,
        resolution: int = 300,
        infrared: bool = False,
        film: str = FILM_NEGATIVE,
        frame: tuple[int, int, int, int] | None = None,
        auto_exposure: bool = True,
        exposure_scale: Sequence[float] | None = None,
        keep_raw: bool = True,
        shading: bool = True,
        retain: bool = True,
        fast_infrared: bool = True,
        on_pass: Callable[[int, np.ndarray, dict[str, Any], dict[str, Any]], None]
        | None = None,
    ) -> tuple[list[np.ndarray], list[float], list[dict[str, Any]]]:
        """Scan one frame several times at different exposures.

        Returns ``(frames, ratios, metas)`` in ascending exposure order, ready
        for :func:`rps7200.bracket.merge_bracket`. The film is not advanced and
        the shading reference is acquired once for the whole bracket, so every
        pass describes the same frame through the same sensor state.

        Infrared is deliberately *not* bracketed. It costs its own ~212 s floor
        per pass however few lines are asked for, and its exposure is a device
        constant the vendor never meters, so bracketing it would multiply the
        scan time for nothing. With ``infrared`` set, one pass -- the brightest,
        which carries the most signal -- is taken as RGBI and the rest as RGB.

        ``fast_infrared`` reaches that one pass and is inert on the others,
        which :meth:`scan` gates on ``infrared`` for itself. It is here because
        it was missing: `tools/scan.py` parsed ``--no-fast-ir`` and then did not
        pass it on this path, so a bracket ran tied whatever was asked for --
        and below 1800 dpi the tied pass's quality is waived rather than
        measured, which makes that flag the escape hatch from a waiver.

        ``on_pass(index, image, meta, capture)`` is called as each pass lands,
        with that pass's :meth:`capture_record`. It exists because only one
        pass's raw bytes survive on the scanner: ``last_raw`` is overwritten by
        the pass after it, so a caller that waits for the return value can file
        the last pass and no other. Do no heavy work in it -- the session is
        open and the next pass is about to start.
        """
        if not self.MIN_BRACKET_PASSES <= passes <= self.MAX_BRACKET_PASSES:
            raise ValueError(
                f"a bracket is {self.MIN_BRACKET_PASSES} to "
                f"{self.MAX_BRACKET_PASSES} passes, got {passes}"
            )

        if exposure_scale is not None:
            scales = list(exposure_scale)
        elif auto_exposure:
            scales = self.auto_exposure(film=film, infrared=infrared)
        else:
            scales = [1.0, 1.0, 1.0]

        ladder = self.bracket_ladder(scales, passes, stops)

        frames: list[np.ndarray] = []
        ratios: list[float] = []
        metas: list[dict[str, Any]] = []
        for i, k in enumerate(ladder):
            last = i == len(ladder) - 1
            pass_scale = [s * k for s in scales[:3]]
            self._log(
                f"bracket pass {i + 1}/{passes}: x{k:.3f} "
                f"({'RGBI' if (infrared and last) else 'RGB'})"
            )
            image, meta = self.scan(
                resolution=resolution,
                infrared=infrared and last,
                frame=frame,
                exposure_scale=pass_scale,
                keep_raw=keep_raw,
                shading=shading,
                film=film,
                fast_infrared=fast_infrared,
            )
            meta["bracket_index"] = i
            meta["bracket_ratio"] = float(k)
            meta["bracket_passes"] = passes
            meta["bracket_stops"] = float(stops)
            ratios.append(float(k))
            metas.append(meta)
            if on_pass is not None:
                on_pass(i, image, meta, self.capture_record())
            # `retain` off keeps only one pass alive at a time. Nine passes at
            # 3600 dpi are 960 MB of pixels and as much again in raw bytes, and
            # a caller that has already written each one to disk in `on_pass`
            # has no use for the list. Writing there is safe as long as it stays
            # quick -- an uncompressed dump is well under a second, where
            # gzipping a whole library entry with the device open and idle is
            # what preceded a wedge.
            if retain:
                frames.append(image)
            else:
                del image
        return frames, ratios, metas
