#!/usr/bin/env python3
"""Decide whether the delivered image has a vignette, by rotating a target.

Two phases. Phase 1 is the study proper -- RGB, 600 dpi, empty transport then
clear film then the IT8::

    python3 tools/uniformity.py capture --tag vignette-study

Phase 2 is a smaller follow-on that asks only whether the field *differs* in
infrared, using the IT8 alone so nothing in the transport has to change::

    python3 tools/uniformity.py capture --ir --tag vignette-study-ir

Then, with no scanner attached and re-runnable against any future pipeline::

    python3 tools/uniformity.py analyse --tag vignette-study

`analyse` rebuilds every image from its stored raw bytes through the *current*
correction code rather than reading the saved TIFF. Corrections here are still
being written, so a TIFF answers a question about the pipeline of the day it was
written; the raw bytes answer today's. This is what the library keeps them for.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from rps7200 import library, tiff, uniformity as un
from rps7200.direct import DirectScanner, ScanParameters
from rps7200.shading import apply_shading
from rps7200.uniformity import AS_IS, MIRROR_X, MIRROR_Y, ROT180

#: Subjects, in the order the transport is loaded: each is put in once and
#: never revisited. Handling is the largest uncontrolled variable in the study.
EMPTY = "empty transport"
CLEAR = "clear film"
IT8 = "IT8"

#: (subject, orientation, label, instruction, purpose). The instruction is what
#: gets printed at the scanner, so it says what to do with your hands, not which
#: group element it corresponds to.
#:
#: ``label`` becomes the entry's ``subject``, and **every one is distinct on
#: purpose**. `library.signature` keys on subject and deliberately ignores tags
#: and notes, so three passes all called "IT8 as-is" would share a signature:
#: `duplicates` would call them redundant and `prunable(keep=1)` would offer to
#: delete two of them. Those two are the repeat floors -- the threshold every
#: other number in the study is judged against -- so they must not look
#: interchangeable to the library.
PHASE1 = [
    (EMPTY, None, "empty transport",
     "Take everything out of the transport. Leave it empty.",
     "even component candidate, T = 1 exactly"),
    (CLEAR, AS_IS, "clear film as-is",
     "Load the clear film / blank mount, any way round.",
     "even component candidate, real light path"),
    (CLEAR, ROT180, "clear film 180",
     "Turn the clear film 180 degrees in its own plane.",
     "certifies the clear-film flat"),
    (IT8, AS_IS, "IT8 as-is",
     "Take the clear film out. Load the IT8 target, the way you would normally "
     "put it in.", "reference pass"),
    (IT8, AS_IS, "IT8 as-is repeat-untouched",
     "DO NOT TOUCH anything -- not the scanner, not the slide. This pass "
     "measures what the machine does when nothing changes.",
     "machine repeat floor"),
    (IT8, AS_IS, "IT8 as-is repeat-reinserted",
     "Take the IT8 out and put it straight back in, the same way round.",
     "handling floor"),
    (IT8, ROT180, "IT8 180",
     "Rotate the IT8 180 degrees in its own plane, same face toward the lens.",
     "d_rot180"),
    (IT8, MIRROR_X, "IT8 turned-over",
     "Turn the IT8 over front-to-back, so the other face points at the lens.",
     "d_mirror"),
    (IT8, MIRROR_Y, "IT8 turned-over-180",
     "Keep it turned over, and now also rotate it 180 degrees.",
     "d_mirror, the other axis"),
]

#: Phase 2 changes nothing in the transport: the IT8 stays where phase 1 left
#: it. Signatures also carry the channel count, so a 4-channel "IT8 180" and
#: phase 1's 3-channel one already differ -- but the repeat pass still needs its
#: own label for the same reason as above.
PHASE2 = [
    (IT8, AS_IS, "IT8 as-is", "Leave the IT8 exactly as it is.",
     "reference pass"),
    (IT8, AS_IS, "IT8 as-is repeat-untouched", "DO NOT TOUCH anything.",
     "IR repeat floor"),
    (IT8, ROT180, "IT8 180", "Rotate the IT8 180 degrees in its own plane.",
     "d_rot180"),
    (IT8, MIRROR_X, "IT8 turned-over", "Turn the IT8 over front-to-back.",
     "d_mirror"),
    (IT8, MIRROR_Y, "IT8 turned-over-180",
     "Keep it turned over, and rotate it 180 degrees.",
     "d_mirror, the other axis"),
]


# -- rebuilding an entry through today's pipeline ---------------------------


def rebuild(entry: Path) -> tuple[np.ndarray, dict]:
    """Decode an entry's raw bytes and correct them with the current code.

    Not ``library.load``: that returns the TIFF as it was written, which was
    corrected by whatever the pipeline looked like that day. The question here
    is about the image the pipeline delivers *now*, so the raw bytes are
    re-decoded and re-corrected on every run. When a correction lands, re-run
    this and the answer moves -- which is the property that makes the study
    worth keeping rather than repeating.
    """
    record = json.loads((entry / "scan.json").read_text())
    raw = library.read_raw(entry)
    if raw is None:
        raise SystemExit(f"{entry.name}: no raw bytes; it cannot be re-corrected")

    layout = ((record.get("raw") or {}).get("layout")) or {}
    scan = record.get("scan") or {}
    if not layout:
        raise SystemExit(f"{entry.name}: no raw layout; the bytes cannot be decoded")
    params = ScanParameters(
        width=int(layout["width"]),
        lines=int(layout["lines"]),
        bytes_per_line=int(layout["bytes_per_line"]),
        filter_offset1=0,
        filter_offset2=0,
        available_lines=int(layout["lines"]),
    )
    image = DirectScanner._deinterleave(raw, params, int(layout["channels"]))

    _, record_full = library.load(entry)
    reference = record_full.get("reference")
    mask = record_full.get("ccd_mask")
    trailing = 0
    if reference is not None:
        image, report = apply_shading(image, reference, mask)
        # At 600 dpi the CCD mask marks 860 used pixels against a width of 862,
        # so apply_shading leaves the last columns raw -- at the frame edge,
        # where a vignette is largest. Hand the count on so the fit can drop
        # them rather than read them as a field.
        trailing = max(0, int(report["width"]) - int(report["columns"]))
    return image, {
        "id": entry.name,
        "dpi": scan.get("resolution_dpi"),
        "channels": scan.get("channels"),
        "subject": (record.get("film") or {}).get("subject", ""),
        "trailing": trailing,
        "shaded": reference is not None,
    }


def select(root: Path, tag: str) -> list[Path]:
    """Entry directories carrying a tag, oldest first."""
    out = []
    for candidate in sorted(root.glob("*/scan.json")):
        try:
            record = json.loads(candidate.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if tag in (record.get("tags") or []):
            out.append(candidate.parent)
    return out


# -- analysis ---------------------------------------------------------------


def difference(a: np.ndarray, b: np.ndarray, orientation: str, dpi: int,
               trailing: int, grid: tuple[int, int]) -> tuple[np.ndarray, dict]:
    """Un-rotate ``a`` into target coordinates and difference it against ``b``.

    Returns the fitted field and a report. Registration first, always: two
    passes at an identical frame and dpi have correlated at lag -16 columns on
    this scanner, and differencing misaligned frames turns every patch border
    into a fake field.
    """
    turned = un.apply_orientation(a, orientation)
    dy, dx, confidence = un.register(turned, b)
    ca, cb = un.align(turned, b, dy, dx)
    samples = un.block_ratios(ca, cb, dpi=dpi, trailing=trailing)
    field = un.fit_field(samples)
    # Evaluate on a FIXED grid, never on this pair's crop. Registration trims a
    # different amount from each pair, so evaluating at ``ca.shape`` would give
    # the three differences three different shapes and they could not be
    # combined. The coefficients are in frame-normalised coordinates precisely
    # so the grid is a free choice.
    surface = field.evaluate(*grid)
    mask = field.support(*grid)
    # A repeat pass has no parity to violate: s∘e - s is zero everywhere, so
    # there is no expected symmetry for the residual to test against. Only the
    # three real orientation differences carry that redundancy.
    residual = (
        round(un.parity_residual(surface[:, :, 0], orientation), 3)
        if orientation in un.ALLOWED_COMPONENTS else None
    )
    return surface, {
        "support": mask,
        "span": round(un.peak_to_peak_percent(surface[:, :, 1][mask]), 3),
        "shift": [dy, dx],
        "registration_confidence": round(confidence, 1),
        "blocks_kept": samples.kept,
        "blocks_total": samples.total,
        "parity_residual": residual,
    }


def check_orientations(images: dict[str, np.ndarray], claims: dict[str, str]) -> dict:
    """Re-derive every orientation from the pixels and validate the whole set.

    A mislabelled orientation does not add noise -- it swaps one difference for
    another and the solver returns a confident, wrong decomposition, with every
    equation still balancing. So the labels are treated as claims.

    But a label disagreeing is **not automatically an error**, and this is the
    point the design turns on. "Turned over" produces a left-right mirror if you
    flip the slide about its vertical axis and a top-bottom mirror if you flip it
    about its horizontal one. Both are the same physical instruction and both are
    valid; which mirror you get is not something the label can know. So the two
    mirror passes are required to be *the two mirrors*, in either order, and the
    order is taken from the image.

    What is required, and what makes this strong: the set must be a complete
    permutation. Six passes of one target must yield exactly the four
    (edge, direction) combinations, every repeat pass must agree with the
    reference, and an in-plane 180 must read as 180 -- that one is unambiguous,
    being the only element that flips both the edge and the ramp. Six
    measurements constraining four outcomes is a far better check than any
    threshold on a single image.

    The reference is a pass *claimed as-is* rather than a convention: which edge
    the greyscale row lands on for an as-is insertion depends on how the film is
    handled, not on anything the pixels know. It must be chosen by claim and not
    by position -- entries arrive in alphabetical order, where "180" sorts before
    "as-is", so taking the first would silently make a rotated pass the origin
    and inverse every other label. Real capture ids are timestamps, so that
    happens to sort correctly; relying on it would be luck.
    """
    ids = list(images)
    origin = next((n for n in ids if claims[n] == AS_IS), None)
    if origin is None:
        return {"rows": [], "detected": {}, "swapped_mirrors": False, "ok": False,
                "problems": ["no pass is claimed as-is, so there is no reference "
                             "orientation to measure the others against"]}
    reference = un.orientation_signature(images[origin])
    rows, detected, problems = [], {}, []

    for name in ids:
        sig = un.orientation_signature(images[name])
        got = un.classify_relative(sig, reference) if sig.confident else "unsure"
        detected[name] = got
        rows.append({
            "id": name, "claimed": claims[name], "detected": got,
            "confidence": round(sig.confidence, 3),
            "steps": round(sig.step_fraction, 3),
            "margin": round(sig.margin, 3),
            "edge": sig.edge, "rising": sig.rising,
        })
        if got == "unsure":
            problems.append(f"{name}: no usable greyscale row "
                            f"(rho={sig.confidence:.2f} steps={sig.step_fraction:.2f} "
                            f"margin={sig.margin:+.2f})")

    # Every pass claimed as-is must actually be as-is. If a repeat pass moved,
    # handling changed something that was supposed to be untouched.
    for name in ids:
        if claims[name] == AS_IS and detected[name] not in ("unsure", AS_IS):
            problems.append(f"{name}: claimed as-is but reads as "
                            f"{detected[name]} -- the target moved")

    # An in-plane 180 is unambiguous: it is the only element flipping both.
    for name in ids:
        if claims[name] == ROT180 and detected[name] not in ("unsure", ROT180):
            problems.append(f"{name}: claimed 180 but reads as {detected[name]}")

    # The two mirror passes must be the two mirrors, in either order.
    mirrors = [n for n in ids if claims[n] in (MIRROR_X, MIRROR_Y)]
    got_mirrors = sorted(detected[n] for n in mirrors)
    swapped = False
    if mirrors:
        if got_mirrors != sorted([MIRROR_X, MIRROR_Y]):
            problems.append(
                f"the two turned-over passes read as {got_mirrors}, not as the "
                f"two mirrors")
        else:
            swapped = any(detected[n] != claims[n] for n in mirrors)

    # And the non-repeat passes must cover all four exactly once.
    distinct = {detected[origin]} | {detected[n] for n in ids
                                     if claims[n] != AS_IS}
    if "unsure" not in distinct and distinct != set(un.ORIENTATIONS):
        problems.append(f"not a complete set: covered {sorted(distinct)}")

    return {"rows": rows, "detected": detected, "problems": problems,
            "swapped_mirrors": swapped, "ok": not problems}


def profile_span(image: np.ndarray, trim: int = 10) -> dict[str, list[float]]:
    """Peak-to-peak of the median row and column profiles, per channel, in %.

    Separated by axis on purpose. Shading is a **per-column** correction: it can
    only flatten x, and it is measured over ``CALIBRATION_FRAME`` (y 3431..6888)
    while a scan covers y 0..6887. So an x number after shading says mostly how
    well the correction worked, while a y number is something the correction
    could not have produced either way -- which makes y the axis that carries
    the news about a genuine 2D vignette.
    """
    f = image.astype(np.float64)
    out = {"x": [], "y": []}
    for c in range(min(3, f.shape[2])):
        col = np.median(f[:, :, c], axis=0)
        row = np.median(f[:, :, c], axis=1)
        col = (col / np.median(col))[trim:-trim]
        row = (row / np.median(row))[trim:-trim]
        out["x"].append(round((col.max() / col.min() - 1) * 100, 2))
        out["y"].append(round((row.max() / row.min() - 1) * 100, 2))
    return out


def raw_image(entry: Path) -> np.ndarray:
    """Decode an entry's bytes with NO correction applied."""
    record = json.loads((entry / "scan.json").read_text())
    layout = ((record.get("raw") or {}).get("layout")) or {}
    raw = library.read_raw(entry)
    params = ScanParameters(
        width=int(layout["width"]), lines=int(layout["lines"]),
        bytes_per_line=int(layout["bytes_per_line"]),
        filter_offset1=0, filter_offset2=0, available_lines=int(layout["lines"]))
    return DirectScanner._deinterleave(raw, params, int(layout["channels"]))


def report_flats(entries, meta, images) -> dict:
    """The half of the question rotation cannot answer.

    A centred radial vignette is even under every insertion, so it cancels in
    every orientation difference along with the target. Only a flat can see it,
    and the empty transport is the strongest one available: with nothing in the
    path ``T = 1`` exactly, so after correction the image *is* the scanner field
    -- no target to disentangle and no blind spot.

    Reported raw and corrected, because the difference between them is the point.
    """
    out = {}
    print("\nflats -- the even component, which rotation cannot see:")
    print(f"  {'':34s} {'across the CCD (x)':>22s}  {'along the scan (y)':>22s}")
    for entry in entries:
        key = entry.name
        subject = meta[key]["subject"]
        if IT8.lower() in subject.lower():
            continue
        rows = {}
        for label, img in (("raw", raw_image(entry)), ("corrected", images[key])):
            span = profile_span(img)
            rows[label] = span
            xs = " ".join(f"{v:5.1f}%" for v in span["x"])
            ys = " ".join(f"{v:5.1f}%" for v in span["y"])
            print(f"  {subject[:22]:22s} {label:10s} {xs:>22s}  {ys:>22s}")
        out[subject] = rows
    return out


def cmd_analyse(args: argparse.Namespace) -> int:
    root = Path(args.library)
    entries = select(root, args.tag)
    if not entries:
        print(f"no entries tagged {args.tag!r} in {root}", file=sys.stderr)
        return 1

    print(f"pipeline: {library.provenance().get('commit', 'unknown')}")
    print(f"{len(entries)} entries tagged {args.tag!r}\n")

    images, meta = {}, {}
    for entry in entries:
        image, info = rebuild(entry)
        images[entry.name] = image
        meta[entry.name] = info
        print(f"  {entry.name}  {image.shape} {info['dpi']}dpi "
              f"{'shaded' if info['shaded'] else 'RAW'}"
              + (f"  {info['trailing']} trailing columns dropped"
                 if info["trailing"] else ""))

    flats = report_flats(entries, meta, images)

    it8 = {k: v for k, v in images.items() if IT8.lower() in meta[k]["subject"].lower()}
    if not it8:
        print("\nno IT8 passes found; orientation cannot be verified "
              "(clear film and empty transport carry no greyscale row)")
        return 1

    claims = {k: claimed_orientation(meta[k]["subject"]) for k in it8}
    verdict = check_orientations(it8, claims)
    print("\norientation, re-derived from the greyscale row:")
    for row in verdict["rows"]:
        mark = "" if row["detected"] == row["claimed"] else "  <- reads as this"
        print(f"  {row['id'][:40]:40s} claimed {row['claimed']:16s} "
              f"detected {row['detected']:16s} "
              f"rho={row['confidence']:.2f} steps={row['steps']:.2f} "
              f"margin={row['margin']:+.2f}{mark}")

    if verdict["swapped_mirrors"]:
        print("\n  Note: your front-to-back flip produced the *other* mirror "
              "from what the label assumed -- you flipped about the horizontal "
              "axis, not the vertical. That is not an error and nothing needs "
              "redoing: which mirror a flip gives is exactly what the label "
              "cannot know, which is why the orientation is taken from the "
              "image. The decomposition below uses the detected assignment.")

    if not verdict["ok"]:
        print("\nrefusing to decompose:", file=sys.stderr)
        for problem in verdict["problems"]:
            print(f"  - {problem}", file=sys.stderr)
        print("A wrong orientation swaps one difference for another and every "
              "equation still balances, so the result would be confident and "
              "wrong.", file=sys.stderr)
        return 1

    return decompose_and_report(it8, verdict["detected"], meta, args, flats)


def claimed_orientation(subject: str) -> str:
    """Read the orientation a pass claims out of its subject string.

    Longest first, because "turned-over-180" contains both "turned-over" and
    "180" and a shorter match would win on either. Anything with no orientation
    word in it -- including the repeat-floor labels -- is an as-is pass.
    """
    lowered = subject.lower()
    for name in (MIRROR_Y, MIRROR_X, ROT180):
        if name in lowered:
            return name
    return AS_IS


def decompose_and_report(it8, detected, meta, args, flats=None) -> int:
    """Decompose using the orientations read from the pixels, not the labels.

    ``detected`` rather than the claims on purpose: if a physical flip produced
    the opposite mirror to the one its label assumed, using the label would
    swap d_mirror_x with d_mirror_y and silently exchange the odd-in-x and
    odd-in-y components of the answer.
    """
    by_orientation: dict[str, list[str]] = {}
    for key, name in detected.items():
        by_orientation.setdefault(name, []).append(key)

    base = by_orientation[AS_IS][0]
    dpi = meta[base]["dpi"] or 600
    trailing = max(meta[k]["trailing"] for k in it8)
    grid = it8[base].shape[:2]          # one grid for every difference

    print(f"\nrepeat floors (as-is passes against the first):")
    floors = []
    for other in by_orientation[AS_IS][1:]:
        surface, info = difference(it8[other], it8[base], AS_IS, dpi, trailing, grid)
        ptp = info["span"]
        floors.append(ptp)
        print(f"  {other[:40]:40s} {ptp:6.2f}%  shift {info['shift']}  "
              f"{info['blocks_kept']}/{info['blocks_total']} blocks")
    floor = max(floors) if floors else 0.0
    if not floors:
        print("  none -- without a repeat pass there is no threshold to judge "
              "the components against")

    diffs, reports = {}, {}
    print("\norientation differences:")
    for name in (MIRROR_X, MIRROR_Y, ROT180):
        surface, info = difference(
            it8[by_orientation[name][0]], it8[base], name, dpi, trailing, grid)
        diffs[name] = surface
        reports[name] = info
        print(f"  d_{name:16s} {info['span']:6.2f}%  "
              f"shift {info['shift']}  parity residual {info['parity_residual']:.3f}  "
              f"{info['blocks_kept']}/{info['blocks_total']} blocks")

    # Report only where the target actually was. A degree-4 surface
    # extrapolates hard past its data and the frame border carries no target.
    support = reports[ROT180]["support"]
    for name in (MIRROR_X, MIRROR_Y):
        support = support & reports[name]["support"]

    solved = un.solve_components(diffs[MIRROR_X], diffs[MIRROR_Y], diffs[ROT180])
    threshold = max(3 * floor, 0.5)
    print(f"\nrecovered components (threshold {threshold:.2f}% = max(3x floor, 0.5%)):")
    names = {"mp": "odd in x", "pm": "odd in y", "mm": "odd in both"}
    channels = ["R", "G", "B", "I"]
    any_real = False
    for key, label in names.items():
        per_channel = []
        for c in range(solved[key].shape[2]):
            ptp = un.peak_to_peak_percent(solved[key][:, :, c][support])
            per_channel.append(f"{channels[c]} {ptp:5.2f}%")
            any_real |= ptp > threshold
        print(f"  {key} ({label:12s})  " + "  ".join(per_channel))
    print("  pp (even, even)  NOT MEASURABLE by rotation -- a centred radial "
          "vignette is invisible here; it needs a flat")

    print(f"\nverdict: {'a field is present above the floor' if any_real else 'nothing above the floor'}")
    if not any_real:
        print("  note: this rules out an *asymmetric* field only. A centred "
              "radial vignette would read exactly zero here.")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pipeline": library.provenance(),
            "tag": args.tag,
            "dpi": dpi,
            "floor_percent": round(floor, 3),
            "threshold_percent": round(threshold, 3),
            "differences": {k: {kk: vv for kk, vv in v.items() if kk != "support"}
                            for k, v in reports.items()},
            "components": {
                key: [round(un.peak_to_peak_percent(solved[key][:, :, c][support]), 3)
                      for c in range(solved[key].shape[2])]
                for key in names
            },
            "support_fraction": round(float(support.mean()), 3),
            "flats": flats,
            "even_component": "see flats -- not measurable by rotation",
        }
        out.write_text(json.dumps(payload, indent=2))
        print(f"\nwrote {out}")
    return 0


# -- capture ----------------------------------------------------------------


def confirm(prompt: str, expect: str | None = None) -> str:
    """Block until the operator says the step is done.

    A bare Enter is not accepted where an orientation is involved. Sixteen
    passes into a sequence whose steps differ only by how a slide is seated,
    Enter is reflex; typing the orientation back requires having read the
    instruction. This is the cheapest available guard against the one error
    that produces a confident wrong answer rather than a noisy one.
    """
    while True:
        reply = input(prompt).strip().lower()
        if expect is None:
            if reply in ("", "y", "yes", "ok", "done"):
                return "ok"
            if reply in ("a", "abort", "q", "quit"):
                return "abort"
            print("  type Enter when done, or 'abort'")
            continue
        if reply == expect.lower():
            return "ok"
        if reply in ("a", "abort", "q", "quit"):
            return "abort"
        print(f"  type {expect!r} exactly (or 'abort')")


def one_pass(scanner_factory, step, args, exposure_scale, reference_path,
             session, index) -> tuple[Path | None, str]:
    """Scan one step, close the device, file it, and report what it looks like.

    The device is opened for the scan and **closed before anything else
    happens**. Filing an entry gzips the raw bytes and the orientation check
    decodes them again -- heavy local work, and "gzipping a 140 MB library
    entry with the device open and idle preceded one wedge" (CLAUDE.md). The
    operator prompt that follows would hold it open and idle for minutes more.
    Closing is not a power cycle, so the scanner keeps its calibration and the
    host-side reference simply reloads from disk.
    """
    subject, orientation, label, instruction, purpose = step

    print(f"\n--- pass {index}: {subject} ({purpose})")
    print(f"    {instruction}")
    if orientation is None:
        if confirm("    press Enter when the transport is empty: ") == "abort":
            return None, "abort"
    elif confirm(f"    type the orientation to confirm [{orientation}]: ",
                 expect=orientation) == "abort":
        return None, "abort"

    from rps7200.shading import ShadingReference
    scanner = scanner_factory()
    try:
        scanner.open()
        scanner._shading = ShadingReference.load(reference_path)
        image, meta = scanner.scan(
            resolution=args.dpi,
            infrared=args.ir,
            exposure_scale=exposure_scale,
            film="positive",
            keep_raw=True,
        )
        raw, layout, mask = scanner.last_raw, scanner.last_raw_layout, scanner._ccd_mask
        shading_ref = scanner._shading
    finally:
        scanner.close()

    meta["uniformity_session"] = session
    entry = library.save(
        image, meta,
        root=args.library,
        film=library.FilmNotes(
            stock="IT8" if subject == IT8 else subject,
            subject=label,
            notes=purpose,
        ),
        tags=[args.tag],
        reference=shading_ref, ccd_mask=mask, raw=raw, raw_layout=layout,
    )

    if orientation is not None and subject == IT8:
        sig = un.orientation_signature(image)
        print(f"    detected: greyscale row on the {sig.edge}, "
              f"{'rising' if sig.rising else 'falling'} "
              f"(rho={sig.confidence:.3f}, steps={sig.step_fraction:.3f})")
        if not sig.confident:
            print("    NOT CONFIDENT -- no clear greyscale row found. Check the "
                  "target is fully in frame.")
        crop = Path("previews") / f"orientation_{entry.name}.png"
        write_crop(image, sig, crop)
        print(f"    crop for your eyes: {crop}")

    while True:
        reply = input("    accept / redo / abort [accept]: ").strip().lower()
        if reply in ("", "a", "accept", "y"):
            return entry, "ok"
        if reply in ("r", "redo"):
            (entry / "REJECTED").write_text(
                "rejected at capture time; kept because how a pass went wrong "
                "is evidence about handling\n")
            record = json.loads((entry / "scan.json").read_text())
            record["tags"] = list(record.get("tags") or []) + ["rejected"]
            (entry / "scan.json").write_text(json.dumps(record, indent=2))
            return entry, "redo"
        if reply in ("abort", "q", "quit"):
            return entry, "abort"


def write_crop(image, signature, path: Path) -> None:
    """Save a strip around the greyscale row, for the operator to eyeball."""
    try:
        from PIL import Image
    except ImportError:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    h = image.shape[0]
    band = max(8, h // 8)
    top = int(np.clip(signature.row - band // 2, 0, max(0, h - band)))
    strip = image[top : top + band, :, :3].astype(np.float64)
    hi = np.percentile(strip, 99.5) or 1.0
    out = np.clip(strip / hi * 255, 0, 255).astype(np.uint8)
    Image.fromarray(out).save(path)


def cmd_capture(args: argparse.Namespace) -> int:
    from datetime import datetime, timezone
    steps = PHASE2 if args.ir else PHASE1
    session = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    reference_path = Path(args.reference)

    print(f"{'phase 2 (IR)' if args.ir else 'phase 1 (RGB)'}: "
          f"{len(steps)} passes at {args.dpi} dpi, session {session}")
    print("Exposure is locked for the whole phase and the shading reference is "
          "acquired once, both properties of this power-on. If the scanner has "
          "to be power-cycled mid-phase, the passes either side are not "
          "directly comparable -- start over, or re-run the repeat pair after "
          "the restart to measure the cross-session floor.")

    if args.ir:
        print("\nIR passes hold the device busy for a ~212 s floor each, "
              "whatever the resolution. Before committing to five of them, run "
              "one throwaway pass at these settings as a canary: if it stalls, "
              "plumb idle_timeout through scan() rather than retrying -- a "
              "wedge costs a power cycle and this whole locked session with it.")
        if confirm("    canary pass already done? Enter to continue: ") == "abort":
            return 1

    def factory():
        return DirectScanner(verbose=args.verbose)

    exposure_scale: float | list[float] = 1.0
    if args.exposure_scale:
        parts = [float(v) for v in args.exposure_scale.replace(",", " ").split()]
        exposure_scale = parts[0] if len(parts) == 1 else parts
        print(f"\nexposure locked at {exposure_scale} (given)")
    else:
        print("\nMetering on the EMPTY transport -- the brightest subject, so "
              "everything loaded afterwards is darker and nothing can clip.")
        if confirm("    press Enter with the transport empty: ") == "abort":
            return 1
        scanner = factory()
        try:
            scanner.open()
            exposure_scale = scanner.auto_exposure(
                target=args.target, infrared=args.ir, film="positive")
        finally:
            scanner.close()
        print(f"    exposure locked at {[round(v, 3) for v in exposure_scale]}")

    from rps7200.shading import ShadingReference
    if args.reuse:
        if not reference_path.exists():
            print(f"--reuse given but {reference_path} does not exist",
                  file=sys.stderr)
            return 1
        reference = ShadingReference.load(reference_path)
        print(f"\nreusing {reference_path}: {len(reference.channels)} channels, "
              f"two_point={reference.two_point}")
        print("    The reference describes the sensor under ONE power-on. If the "
              "scanner has been power-cycled since it was measured, this is a "
              "different sensor state -- drop --reuse and calibrate again.")
    else:
        print("\nCalibrating shading at that exposure (3-4 minutes) ...")
        scanner = factory()
        try:
            scanner.open()
            result = scanner.calibrate_shading(exposure_scale=exposure_scale)
        finally:
            scanner.close()
        if result["reference"] is None:
            print("no usable shading reference; stopping", file=sys.stderr)
            return 1
        reference = result["reference"]
        reference_path.parent.mkdir(parents=True, exist_ok=True)
        reference.save(reference_path)
        print(f"    saved {reference_path}, channels {reference.channels}")

    if args.ir and 3 not in reference.ref:
        print("REFUSING: the calibration produced no infrared reference, so "
              "apply_shading would leave the IR plane unshaded -- carrying the "
              "full ~34% falloff, which would read as an enormous IR vignette "
              "that is pure artefact.", file=sys.stderr)
        return 1

    index, done = 1, []
    for step in steps:
        while True:
            entry, status = one_pass(factory, step, args, exposure_scale,
                                     reference_path, session, index)
            if status == "abort":
                print(f"\naborted after {len(done)} passes", file=sys.stderr)
                return 1
            if status == "ok":
                done.append(entry)
                index += 1
                break
            print("    redoing this pass")

    print(f"\n{len(done)} passes filed with tag {args.tag!r}")
    print(f"now run: python3 tools/uniformity.py analyse --tag {args.tag}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    cap = sub.add_parser("capture", help="run the prompted scan sequence")
    cap.add_argument("--library", default="library")
    cap.add_argument("--tag", default="vignette-study")
    cap.add_argument("--dpi", type=int, default=600)
    cap.add_argument("--ir", action="store_true",
                     help="phase 2: infrared, IT8 only, nothing in the "
                          "transport changes")
    cap.add_argument("--reference", default="calibration/shading_uniformity.npz")
    cap.add_argument("--exposure-scale", default=None,
                     help="skip metering and lock exposure at this value")
    cap.add_argument("--reuse", action="store_true",
                     help="load --reference instead of calibrating. Only valid "
                          "within the power-on that measured it: the reference "
                          "describes the sensor under one calibration, so "
                          "reusing it across a power cycle describes a "
                          "different sensor state")
    cap.add_argument("--target", type=float, default=0.65)
    cap.add_argument("-v", "--verbose", action="store_true", default=True)
    cap.set_defaults(func=cmd_capture)

    an = sub.add_parser("analyse", help="decide, from library entries")
    an.add_argument("--library", default="library")
    an.add_argument("--tag", default="vignette-study")
    an.add_argument("--out", default=None, help="write the report as JSON")
    an.set_defaults(func=cmd_analyse)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
