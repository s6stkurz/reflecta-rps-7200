"""Which way the carriage travelled for a pass, as the pass's own lines say.

The scanner sometimes reads a pass bottom-up and hands its rows back in
reverse order, with no status bit and no sense condition to say so. Nothing
this driver sends controls it reliably: MODE SELECT byte 14 bit 0 decides
whether the carriage stays at the far end after a pass, and the *next* pass
then reads upward whatever its own bit says -- but when the carriage goes
home between passes is not known (`docs/byte14-plan.md`). So the direction is
read from the data, pass by pass, never predicted from the commands.

**The evidence is in the line tags.** In the index format every line begins
with its colour's letter, and the trilinear CCD's three rows lead and trail in
a fixed physical order. A pass read top-down starts with R lines and ends with
B; one read bottom-up starts with B and ends with R. It is a property of the
sensor, not of the picture, so it answers on a blank frame too. Checked on all
311 passes of `library/` (four reversed, all known cases) and, against the
pictures themselves, on CyberView's own prescan pairs in the captures.

**Only rows reverse.** Columns run along the strip, and no reversed pass has
ever come back with them reversed -- so the transport's direction and every
position measured along the strip are unaffected. Turning the rows back is the
whole correction, and the three colour planes stay aligned with each other
when it is made (measured: 0 rows between R, G and B, as on a forward pass).

What is not corrected: a pass read bottom-up and turned back sits a few rows
and about one column off a forward pass of the same frame -- 3 rows and 1
column at 600 dpi, the two directions starting in slightly different places.
It is far below the smallest transport move, and correcting it would mean
inventing rows at one edge, so it is recorded as the direction and left.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .protocol import CHANNEL_ORDER, INDEX_HEADER

FORWARD, REVERSED, UNKNOWN = "forward", "reversed", "unknown"


@dataclass(frozen=True)
class ReadDirection:
    """One pass's direction and the evidence for it.

    ``lead`` is the first line's tag. ``trail`` is the last line's, and None
    when the read stopped short -- a short read's last line is mid-interleave
    and says nothing. ``state`` is :data:`UNKNOWN` whenever the evidence is
    missing or contradicts itself, and an unknown pass is never turned.
    """

    state: str
    lead: str | None = None
    trail: str | None = None
    why: str = ""

    @property
    def reversed(self) -> bool:
        return self.state == REVERSED

    @property
    def known(self) -> bool:
        return self.state != UNKNOWN

    def as_record(self) -> dict[str, Any]:
        """What a library entry keeps: the finding, the evidence, what was done."""
        return {
            "direction": self.state,
            "lead": self.lead,
            "trail": self.trail,
            # The decode turned the rows back. The raw bytes are untouched and
            # still in the order the scanner sent them.
            "turned": self.reversed,
            "evidence": "line tags",
            "why": self.why,
        }


def unknown(why: str) -> ReadDirection:
    return ReadDirection(UNKNOWN, why=why)


def read_direction(blob: bytes, line_stride: int, *, lines: int | None = None,
                   channels: int | None = None) -> ReadDirection:
    """The direction of the pass whose index-format bytes are ``blob``.

    Decided by which colour appears first: R before B is top-down, B before R
    bottom-up. The end of the stream corroborates it only when the read is
    complete -- ``channels * lines`` lines, both given -- and a complete read
    whose two ends disagree is :data:`UNKNOWN` rather than a guess.
    """
    if line_stride <= INDEX_HEADER:
        return unknown(f"a {line_stride}-byte line cannot hold a tag")
    count = len(blob) // line_stride
    if count < 2:
        return unknown("fewer than two lines")
    tags = np.frombuffer(blob, dtype=np.uint8, count=count * line_stride)[::line_stride]
    tags = tags.tobytes()
    first_r, first_b = tags.find(b"R"), tags.find(b"B")
    if first_r < 0 or first_b < 0:
        return unknown("the pass has no R or no B lines to compare")
    state = FORWARD if first_r < first_b else REVERSED
    lead = chr(tags[0])
    complete = (lines is not None and channels is not None
                and count >= int(lines) * int(channels))
    if not complete:
        return ReadDirection(state, lead, None,
                             "from the first lines; the read stopped short")
    tail = FORWARD if tags.rfind(b"B") > tags.rfind(b"R") else REVERSED
    if tail != state:
        return unknown(f"the first lines say {state}, the last say {tail}")
    return ReadDirection(state, lead, chr(tags[-1]), "first and last lines agree")


def encode_index(image: np.ndarray, *, reversed: bool = False) -> bytes:
    """``image`` as the scanner would send it in the index format.

    One line per plane per row, each prefixed with its tag twice, R G B (I)
    in turn. ``reversed`` sends the same lines in the opposite order -- rows
    bottom-up and, within a row, the planes the other way round -- which is
    what a pass read bottom-up delivers: B first, R last. The inverse of
    `DirectScanner.decode_index` without the CCD's lead lines, which only shift
    where the first B appears. The demo uses it to hand a picture it holds only
    as pixels to the same decode a real pass goes through.
    """
    a = np.asarray(image)
    if a.ndim == 2:
        a = a[..., None]
    dtype = np.dtype("<u2") if a.dtype.itemsize == 2 else np.dtype(np.uint8)
    rows = range(a.shape[0] - 1, -1, -1) if reversed else range(a.shape[0])
    order = CHANNEL_ORDER[:a.shape[2]]
    if reversed:
        order = order[::-1]
    out = bytearray()
    for y in rows:
        for tag in order:
            letter = ord(tag)
            out += bytes((letter, letter))
            plane = CHANNEL_ORDER.index(tag)
            out += np.ascontiguousarray(a[y, :, plane]).astype(dtype).tobytes()
    return bytes(out)


def reverse_lines(blob: bytes, line_stride: int) -> bytes:
    """The same pass, as the scanner sends it when the carriage reads upward.

    Every line kept whole, their order reversed: B first and R last, and each
    plane's rows bottom-up. What the demo hands over when its carriage starts
    at the far end, so a stored forward pass arrives exactly as a reversed one
    would -- and is decoded, turned and recorded by the same code.
    """
    count = len(blob) // line_stride
    lines = np.frombuffer(blob, dtype=np.uint8,
                          count=count * line_stride).reshape(count, line_stride)
    return lines[::-1].tobytes() + bytes(blob[count * line_stride:])
