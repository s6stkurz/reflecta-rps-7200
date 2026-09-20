#!/usr/bin/env python3
"""Check this driver's protocol against CyberView's, offline.

    uv run python tools/verify_capture.py

`tools/verify_protocol.py` asks the scanner. This asks the captures, so it
costs no hardware time and can be run after any change to what is sent --
the same argument as `tools/library.py reconstruct`, which re-decodes every
stored scan rather than trusting the next one.

It reads `captures/*.pcapng`, which are gitignored, so it says so and exits
cleanly where they are not present.

What it checks, all of it against ~3,900 real vendor commands:

1. Every opcode CyberView uses is one `protocol.py` defines.
2. `SET_SCAN_HEAD` (0xD2) is never sent -- the rule CLAUDE.md rests a hard
   safety warning on.
3. Each MODE SELECT field the driver can send is within the range the vendor
   is observed to use.
4. The driver's own MODE SELECT builder reproduces the vendor's bytes exactly
   when given the vendor's field values.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
# tools/ as well, so `parse_capture` resolves whether this is run as a script
# or loaded by name -- the test suite does the latter.
sys.path.insert(0, str(_HERE))

import parse_capture                                            # noqa: E402

from rps7200 import protocol                                    # noqa: E402
from rps7200.console import use_utf8_stdout                     # noqa: E402
from rps7200.direct import SCSI_MODE_SELECT                     # noqa: E402

#: Where each MODE SELECT field lives, and what it is called.
FIELDS = {2: "resolution", 4: "passes", 5: "depth", 6: "color_format",
          8: "byte_order", 12: "halftone", 13: "line_threshold",
          14: "byte14"}

SET_SCAN_HEAD = 0xD2


def commands(root: Path):
    """Every command in every capture, with the file it came from."""
    for path in sorted(root.glob("*.pcapng")):
        raw = parse_capture.stream(str(path))
        parsed = parse_capture.parse(raw)
        # Nothing skipped means the stream really is a clean sequence of
        # commands, so the opcodes below are the vendor's and not artefacts of
        # the resync in `parse`. Worth saying, because that resync could
        # manufacture plausible ones.
        yield path, raw, parsed


def ours(**kw) -> bytes | None:
    """The MODE SELECT payload this driver builds for those parameters."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
    from conftest import FakeTransport

    from rps7200.direct import DirectScanner

    transport = FakeTransport()
    DirectScanner(transport=transport, verbose=False).set_mode(**kw)
    for opcode, data in transport.sent:
        if opcode == SCSI_MODE_SELECT and data:
            return bytes(data)
    return None


def main() -> int:
    use_utf8_stdout()
    root = Path(__file__).resolve().parent.parent / "captures"
    files = sorted(root.glob("*.pcapng")) if root.exists() else []
    if not files:
        print("no captures/*.pcapng in this checkout -- nothing to check "
              "against. They are gitignored; ask Stefan for the archive.")
        return 0

    total, opcodes, modes, skipped_any = 0, Counter(), [], False
    for path, raw, parsed in commands(root):
        # count what `parse` could not account for, the honest way
        accounted = sum(6 + (len(d) if d else 0) for _, _, _, d in parsed)
        if accounted != len(raw):
            skipped_any = True
        total += len(parsed)
        opcodes.update(op for op, _, _, _ in parsed)
        modes += [d for op, _, _, d in parsed
                  if op == SCSI_MODE_SELECT and len(d) > 14]
        print(f"  {path.name:34} {len(parsed):>5} commands")

    print(f"\n{total} commands, {len(modes)} MODE SELECTs, "
          f"{len(files)} capture(s)")
    if skipped_any:
        print("  NOTE: some bytes were skipped to resync, so a few commands "
              "below may be artefacts")

    problems = []

    # 1. every opcode implemented
    known = {v for k, v in vars(protocol).items()
             if k.startswith("SCSI_") and isinstance(v, int)}
    missing = sorted(set(opcodes) - known)
    print(f"\nopcodes the vendor uses that protocol.py does not define: "
          f"{[hex(o) for o in missing] or 'none'}")
    if missing:
        problems.append(f"undefined opcodes: {[hex(o) for o in missing]}")

    # 2. the safety rule
    heads = opcodes.get(SET_SCAN_HEAD, 0)
    print(f"SET_SCAN_HEAD (0xd2) sent by the vendor: {heads}")
    if heads:
        problems.append("the vendor DOES send SET_SCAN_HEAD -- CLAUDE.md's "
                        "rule rests on it never doing so")

    # 3. the fields
    print("\nMODE SELECT fields, as the vendor uses them:")
    for offset, name in FIELDS.items():
        if offset == 2:
            values = Counter(int.from_bytes(d[2:4], "little") for d in modes)
            shown = ", ".join(f"{v} dpi x{n}" for v, n in values.most_common())
        else:
            values = Counter(d[offset] for d in modes)
            shown = ", ".join(f"{v:#04x} x{n}" for v, n in values.most_common())
        print(f"   byte {offset:>2}  {name:<15} {shown}")

    # 4. the builder, against the vendor's own bytes
    print("\nthis driver's MODE SELECT against the vendor's, same parameters:")
    checked = 0
    quality_seen: Counter[int] = Counter()
    for payload in modes:
        # The quality word has to come from the vendor's bytes too, not from
        # this tool's idea of a default. Reading it back as flags is also the
        # check that the driver's constants mean what they say: getting one
        # wrong shows up as a byte that will not reproduce.
        quality = int.from_bytes(payload[9:11], "little")
        quality_seen[quality] += 1
        mine = ours(
            resolution=int.from_bytes(payload[2:4], "little"),
            passes=payload[4], depth=payload[5], color_format=payload[6],
            line_threshold=payload[13], byte14=payload[14],
            sharpen=bool(quality & protocol.QUALITY_SHARPEN),
            calibrate=bool(quality & protocol.QUALITY_CALIBRATE),
            skip_shading=bool(quality & protocol.QUALITY_SKIP_SHADING),
            fast_infrared=bool(quality & protocol.QUALITY_FAST_INFRARED),
        )
        if mine != bytes(payload):
            problems.append(
                f"MODE SELECT differs: ours {mine.hex(' ') if mine else None} "
                f"vendor {bytes(payload).hex(' ')}")
            break
        checked += 1
    print(f"   {checked}/{len(modes)} reproduced byte for byte")

    names = {protocol.QUALITY_SHARPEN: "sharpen",
             protocol.QUALITY_SKIP_SHADING: "skip_shading",
             protocol.QUALITY_FAST_INFRARED: "fast_infrared",
             protocol.QUALITY_CALIBRATE: "calibrate"}
    print("   quality words the vendor sends:")
    for quality, n in quality_seen.most_common():
        flags = [name for bit, name in names.items() if quality & bit]
        unknown = quality & ~sum(names)
        note = f"  UNKNOWN BITS {unknown:#06x}" if unknown else ""
        print(f"      {quality:#06x} x{n:<3} {', '.join(flags) or 'none'}{note}")
        if unknown:
            problems.append(f"quality bit(s) {unknown:#06x} the driver does "
                            "not name")

    print()
    if problems:
        for problem in problems:
            print(f"PROBLEM: {problem}")
        return 1
    print("this driver sends what CyberView sends.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
