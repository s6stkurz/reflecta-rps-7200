#!/usr/bin/env python3
"""Walk a strip and keep everything a registration study needs.

    RPS7200_DEBUG=1 uv run python tools/roll_registration_walk.py --dry-run
    RPS7200_DEBUG=1 uv run python tools/roll_registration_walk.py --label A

**Ask before running this.** It advances the film frame by frame, and with
`--ladder` it also moves one frame in sub-frame steps. Film must be loaded:
the calibration frame is the lower transport, which the film does not cover,
and calibrating an empty one preceded a wedge.

It calls only `prescan()`, `advance()` and `nudge()` -- three things that are
already characterised. It does not touch `scan_roll`: the production roll path
should not grow a research flag, and it guards an invariant (`the roll never
crops`) worth leaving undisturbed. Nothing here scans a frame at full
resolution; the whole study is 300 dpi prescans.

Two prescans per frame, deliberately. Two passes with nothing moved between
them measure the pass-to-pass noise, and every other number this study
produces is uninterpretable without it -- the decision band is under three
pixels wide and the noise floor is plausibly one. It is recorded at 3600 dpi
and at 600, never at 300.

The ladder is the part that has never been done. A sound strip contains no
misregistered frame, so on a walk alone a detector's true-positive rate cannot
be measured -- there is nothing for it to be right about. The ladder pushes one
frame through known offsets so there is ground truth, and its transitions give
the only absolute reference in the study: the offset at which the frame's own
edge reaches the aperture's is a geometric landmark that does not depend on the
photograph.

    off  -0.816 mm   (3 x the smallest step, one direction change)
    then +0.272 mm x 6, monotone, no further reversal
    then back

Backlash swallows two to three commands after a direction change, so the
ladder reverses once and only once, at the start, where the cost is a step
that may not land rather than a reading that is wrong.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rps7200 import tiff                                       # noqa: E402
from rps7200.console import use_utf8_stdout                    # noqa: E402
from rps7200.direct import DirectScanner                       # noqa: E402
from rps7200.framing import frame_contrast                     # noqa: E402
from rps7200.session import FINE_MIN_MM                        # noqa: E402

#: One ladder rung: the smallest move the transport has. Every rung is this,
#: so the abscissa is a lattice point and not a rounding of one.
RUNG_MM = FINE_MIN_MM

#: Rungs each side of the frame's starting position. Three is 0.816 mm, which
#: deliberately overshoots the 0.49 mm the aperture allows: the study wants
#: readings from beyond the legal range as well as inside it, because "refuses
#: correctly" is a thing a detector has to do and nothing has ever tested it.
RUNGS = 3

#: How far the film may sit from where a frame started, at any moment. This is
#: the safety number -- not total distance travelled, which a symmetric ladder
#: runs up without ever being far from home.
MAX_EXCURSION_MM = 1.2

#: And a stop on the whole run's travel, so a surprise cannot walk the film
#: across the aperture while nobody is counting.
TOTAL_TRAVEL_LIMIT_MM = 24.0


def estimate(frames: int, ladders: int) -> float:
    """Roughly how long this takes, in seconds.

    A 300 dpi prescan measured 10.3 s steady-state in this library and 19-20 s
    for the first of a session; an advance is ~7 s. Budgeted at the slow end,
    because the number that matters is whether it crosses the eight minutes
    past which a run has to be backgrounded.
    """
    per_frame = 2 * 20.0 + 7.0
    per_ladder = (2 * RUNGS + 1) * 20.0 + (2 * RUNGS + RUNGS) * 1.5
    return frames * per_frame + ladders * per_ladder


class Walk:
    """One pass down the strip, writing as it goes."""

    def __init__(self, scanner, out: Path, resolution: int, label: str):
        self.scanner = scanner
        self.out = out
        self.resolution = resolution
        self.label = label
        self.travel = 0.0                 # total distance the film has moved
        self.log: dict = {"label": label, "resolution": resolution,
                          "frames": [], "ladders": []}

    def _write(self, image, name: str) -> str:
        path = self.out / name
        tiff.write(str(path), image, resolution=self.resolution)
        return name

    def _prescan(self, tag: str):
        image, meta = self.scanner.prescan(resolution=self.resolution,
                                           keep_raw=True)
        return image, meta, self._write(image, tag)

    def _nudge(self, mm: float, excursion: float) -> dict:
        """Move, refusing anything that would leave the safe envelope."""
        after = excursion + mm
        if abs(after) > MAX_EXCURSION_MM:
            raise RuntimeError(
                f"refusing: {mm:+.3f} mm would put the film {after:+.3f} mm "
                f"from where this frame started, past the "
                f"{MAX_EXCURSION_MM} mm limit")
        if self.travel + abs(mm) > TOTAL_TRAVEL_LIMIT_MM:
            raise RuntimeError(
                f"refusing: this run has already moved the film "
                f"{self.travel:.2f} mm, and the limit is "
                f"{TOTAL_TRAVEL_LIMIT_MM} mm")
        sent = self.scanner.nudge(mm)
        self.travel += abs(mm)
        time.sleep(0.4)                   # the settle the hold loop uses
        return dict(sent, asked_mm=mm)

    def frame(self, number: int) -> dict:
        """Two prescans, nothing moved between them."""
        first, _meta, a = self._prescan(f"{self.label}{number:02d}_p1.tif")
        second, _meta2, b = self._prescan(f"{self.label}{number:02d}_p2.tif")
        record = {
            "number": number,
            "passes": [a, b],
            "contrast": [round(frame_contrast(first), 4),
                         round(frame_contrast(second), 4)],
            "position": self.scanner.position(),
        }
        self.log["frames"].append(record)
        print(f"  frame {number:2d}: contrast "
              f"{record['contrast'][0]:.4f} / {record['contrast'][1]:.4f}")
        return record

    def ladder(self, number: int) -> dict:
        """Known offsets on one frame, with the film put back afterwards."""
        print(f"  ladder on frame {number}: {2*RUNGS+1} rungs of "
              f"{RUNG_MM:.4f} mm")
        rungs, excursion = [], 0.0
        sent = self._nudge(-RUNGS * RUNG_MM, excursion)
        excursion += -RUNGS * RUNG_MM
        for step in range(2 * RUNGS + 1):
            image, _meta, name = self._prescan(
                f"{self.label}{number:02d}_L{step:02d}.tif")
            rungs.append({
                "step": step,
                "commanded_mm": round(excursion, 4),
                "pass": name,
                "contrast": round(frame_contrast(image), 4),
                "sent": sent,
            })
            print(f"    rung {step}: commanded {excursion:+.4f} mm")
            if step < 2 * RUNGS:
                sent = self._nudge(RUNG_MM, excursion)
                excursion += RUNG_MM
        # Put it back. Measured against where the frame started, not summed
        # from the commands, because backlash means the two differ.
        back = self._nudge(-excursion, excursion)
        record = {"number": number, "rungs": rungs, "restore": back,
                  # What the film was asked to travel in total, and how far
                  # from home it was at the furthest rung. The first is what
                  # backlash acts on; the second is the safety number.
                  "commanded_travel_mm": round((2 * RUNGS) * RUNG_MM
                                               + 2 * RUNGS * RUNG_MM, 4),
                  "max_excursion_mm": round(RUNGS * RUNG_MM, 4)}
        self.log["ladders"].append(record)
        return record


def main() -> int:
    use_utf8_stdout()
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--frames", type=int, default=16,
                    help="how many frames to walk (default 16: 15 pictures "
                         "plus one past the end, which is the only frame "
                         "film_bounds can legitimately fire on)")
    ap.add_argument("--resolution", type=int, default=300)
    ap.add_argument("--label", default="A",
                    help="names the files: A01_p1.tif, A01_p2.tif, ...")
    ap.add_argument("--out", type=Path, default=None,
                    help="where to write (default rolls/registration-<label>)")
    ap.add_argument("--ladder", default="",
                    help="frames to run the displacement ladder on, e.g. 1,15")
    ap.add_argument("--rewind", type=int, default=0,
                    help="go back this many frames first, one at a time. For "
                         "re-walking a strip the previous walk left further "
                         "down, without unloading it")
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and exit without opening the device")
    args = ap.parse_args()

    ladder_on = [int(n) for n in args.ladder.split(",") if n.strip()]
    out = args.out or Path("rolls") / f"registration-{args.label}"
    seconds = estimate(args.frames, len(ladder_on)) + args.rewind * 7.0

    print(f"walk {args.label}: {args.frames} frames at {args.resolution} dpi, "
          f"two prescans each")
    if ladder_on:
        print(f"  displacement ladder on frames {ladder_on}: "
              f"{2*RUNGS+1} rungs of {RUNG_MM:.4f} mm, "
              f"{RUNGS*RUNG_MM:.3f} mm each side, film put back after each")
    print(f"  writes to {out}")
    print(f"  roughly {seconds/60:.1f} minutes")
    if args.rewind:
        print(f"  FIRST: {args.rewind} frames back, one command each, to "
              f"re-walk a strip the last walk left further down")
    print(f"  moves: {args.frames - 1} whole-frame advances"
          + (f", {args.rewind} retreats" if args.rewind else "")
          + (f", and {len(ladder_on)*2*RUNGS} sub-frame nudges"
             if ladder_on else ""))
    print(f"  never further than {MAX_EXCURSION_MM} mm from a frame's start; "
          f"whole run capped at {TOTAL_TRAVEL_LIMIT_MM} mm of travel")
    print("  no full-resolution scans, no calibration of an empty transport, "
          "no SET_SCAN_HEAD")

    # Said on the dry run too, and especially there: the dry run is where
    # somebody decides how to launch it.
    if seconds > 8 * 60:
        print(f"\n  NOTE: {seconds/60:.1f} min is past the ~8 minute rule. "
              f"Run this backgrounded, or split it with --frames and")
        print("  --label -- a foreground command is killed at 10 minutes and "
              "a killed read is an abandoned read, which wedges the device.")

    if args.dry_run:
        print("\ndry run: no device was opened")
        return 0

    if not os.environ.get("RPS7200_DEBUG"):
        print("refusing to run without RPS7200_DEBUG=1: a walk that files "
              "nothing cannot be re-analysed, and this one is the corpus",
              file=sys.stderr)
        return 2

    out.mkdir(parents=True, exist_ok=True)
    scanner = DirectScanner(verbose=True)
    started = time.monotonic()
    walk = None
    try:
        scanner.open()
        state = scanner.read_state()
        print(f"\nstate: scanning={state.scanning:#04x} "
              f"position={state.position} warming={state.warming_up}")
        if state.scanning & 0x80:
            print("refusing: the device reports a scan in progress and "
                  "nothing here started one. Power cycle it at the unit's "
                  "own switch first.", file=sys.stderr)
            return 2
        scanner.session_start()
        scanner.wait_warm()

        walk = Walk(scanner, out, args.resolution, args.label)
        walk.log["state_before"] = {"position": int(state.position)}

        if args.rewind:
            # One frame per command. `retreat(steps=N)` would put N in the
            # payload's value byte, which is not a thing the vendor sends, and
            # the wait underneath only watches for the position to CHANGE --
            # so a multi-step call returns as soon as it has moved at all.
            # Single steps are the operation that has actually been driven.
            print(f"\nrewinding {args.rewind} frames, one at a time")
            went = []
            for step in range(args.rewind):
                where = scanner.retreat()
                went.append(where)
                if where is None:
                    print(f"  stopped after {step}: it would not go back "
                          f"further -- already at the start of the strip")
                    break
                print(f"  back to position {where}")
            walk.log["rewind"] = {"asked": args.rewind, "positions": went}
            print(f"  now at position {scanner.position()}")

        for number in range(1, args.frames + 1):
            walk.frame(number)
            if number in ladder_on:
                walk.ladder(number)
            if number < args.frames:
                where = scanner.advance()
                if where is None:
                    # The end of the strip. `scan_roll` stops here and so must
                    # this: without it the walk re-scans one position for the
                    # rest of its frames, which costs ~40 s each and produces
                    # a corpus that looks like a strip and is not one. That is
                    # not hypothetical -- walk B did exactly that for fourteen
                    # frames before this check existed.
                    #
                    # Contrast cannot substitute. Clear base past the last
                    # frame measured 0.292 here, well above BLANK_CONTRAST
                    # (0.02), so the blank test sees a picture. The transport
                    # refusing to move is the signal.
                    print(f"  the film stopped advancing at position "
                          f"{scanner.position()} -- that is the end of the "
                          f"strip, stopping after {number} frames")
                    walk.log["stopped_early"] = {
                        "after_frame": number,
                        "position": scanner.position(),
                        "asked_for": args.frames,
                    }
                    break
        walk.log["ok"] = True
    except KeyboardInterrupt:
        print("\ninterrupted -- the film is wherever the last move left it",
              file=sys.stderr)
        if walk is not None:
            walk.log["ok"] = False
            walk.log["interrupted"] = True
        return 130
    except Exception as exc:                              # noqa: BLE001
        print(f"\nfailed: {exc}", file=sys.stderr)
        if walk is not None:
            walk.log["ok"] = False
            walk.log["error"] = str(exc)
        return 1
    finally:
        try:
            scanner.close()
        except Exception:                                 # noqa: BLE001
            pass
        if walk is not None:
            walk.log["seconds"] = round(time.monotonic() - started, 1)
            walk.log["travel_mm"] = round(walk.travel, 4)
            target = args.json or (out / f"walk-{args.label}.json")
            target.write_text(json.dumps(walk.log, indent=2, default=str),
                              encoding="utf-8")
            print(f"\nwrote {target}")
            print(f"{len(walk.log['frames'])} frames, "
                  f"{len(walk.log['ladders'])} ladders, "
                  f"{walk.travel:.3f} mm of sub-frame travel, "
                  f"{walk.log['seconds']/60:.1f} min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
