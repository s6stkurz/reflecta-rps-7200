"""The fast-infrared quality bit: what reaches the device, and what it is worth.

`QUALITY_FAST_INFRARED` (0x80) has been defined and reachable through
`set_mode` since the protocol was first written, and has never once been sent.
The reference backend describes it as acquiring the infrared plane "in a
faster, lower-quality pass" (`pieusb/option.py:325`); **CyberView never uses
it**, so unlike every other field this driver sends there is no capture to
check the bytes against.

That is exactly why these are byte-level. What the payload contains is the only
thing a ladder on the hardware can be held to afterwards: if the bit is not
where this says it is, a run that measures "no difference" measures nothing.

See `docs/fast-infrared-plan.md`.
"""

from conftest import FakeTransport
from rps7200 import library
from rps7200.direct import DirectScanner
from rps7200.protocol import (
    ONE_PASS_COLOR,
    PROTOCOL_REVISION,
    ONE_PASS_RGBI,
    QUALITY_FAST_INFRARED,
    QUALITY_SKIP_SHADING,
    SCSI_MODE_SELECT,
)


def scanner():
    t = FakeTransport()
    s = DirectScanner(transport=t)
    s.verbose = False
    return s, t


def quality_of(payload: bytes) -> int:
    """Bytes 9 and 10 of a MODE SELECT payload, which behave as one 16-bit LE
    value -- see the comment above `QUALITY_SHARPEN` in `rps7200/protocol.py`."""
    return int.from_bytes(payload[9:11], "little")


# -- the bit itself ---------------------------------------------------------


def test_the_bit_is_set_when_asked_for():
    s, t = scanner()
    s.set_mode(resolution=1800, passes=ONE_PASS_RGBI, fast_infrared=True)
    assert quality_of(t.payloads(SCSI_MODE_SELECT)[-1]) & QUALITY_FAST_INFRARED


def test_set_mode_itself_still_defaults_the_bit_clear():
    """`set_mode` writes what it is told and decides nothing. The default that
    moved is `scan()`'s; this one stays off so that calibration, metering and
    anything else reaching the primitive directly are unaffected."""
    s, t = scanner()
    s.set_mode(resolution=1800, passes=ONE_PASS_RGBI)
    assert not quality_of(t.payloads(SCSI_MODE_SELECT)[-1]) & QUALITY_FAST_INFRARED


def test_an_ordinary_infrared_scan_now_sends_a_payload_the_vendor_never_did():
    """0x0088 where CyberView sends 0x0008, on every infrared pass.

    This is why `PROTOCOL_REVISION` moved to 3. It is the whole reason that
    field exists: `library.signature` keeps entries from reducing to each other
    across a change in what was sent, and a scan taken today is not the same
    measurement as one taken before this default moved.
    """
    s, t = scanner()
    s.set_mode(resolution=1800, passes=ONE_PASS_RGBI, fast_infrared=True)
    assert (quality_of(t.payloads(SCSI_MODE_SELECT)[-1])
            == QUALITY_SKIP_SHADING | QUALITY_FAST_INFRARED)
    assert PROTOCOL_REVISION >= 3


def test_it_rides_alongside_skip_shading_rather_than_replacing_it():
    """0x88, not 0x80. Skip-shading is what every ordinary scan sends -- 32 of
    33 vendor scan cycles -- and losing it sends the backend into the shading
    read this scanner cannot complete."""
    s, t = scanner()
    s.set_mode(resolution=1800, passes=ONE_PASS_RGBI,
               skip_shading=True, fast_infrared=True)
    quality = quality_of(t.payloads(SCSI_MODE_SELECT)[-1])
    assert quality & QUALITY_SKIP_SHADING, "the scan would then demand a shading pass"
    assert quality & QUALITY_FAST_INFRARED
    assert quality == QUALITY_SKIP_SHADING | QUALITY_FAST_INFRARED


def test_nothing_else_in_the_payload_moves():
    """The bit is the only variable a ladder has. If setting it also changed
    the resolution, the pass count or byte 14, a timing difference would not be
    attributable to it -- which is the one thing the run has to establish."""
    s, t = scanner()
    s.set_mode(resolution=1800, passes=ONE_PASS_RGBI, fast_infrared=False)
    s.set_mode(resolution=1800, passes=ONE_PASS_RGBI, fast_infrared=True)
    off, on = t.payloads(SCSI_MODE_SELECT)[-2:]

    assert len(off) == len(on) == 16
    differing = [i for i in range(16) if off[i] != on[i]]
    assert differing == [9], f"bytes {differing} changed; only byte 9 may"


# -- what scan() does with it ------------------------------------------------


def test_scan_and_scan_roll_both_tie_infrared_by_default():
    """The default moved to True on 2026-09-16, once the sweep showed the bit
    removes the infrared floor rather than trading it for quality. Both entry
    points, because a roll that kept the floor while a single scan did not
    would be the worst of both. Checked by introspection because the suite has
    no harness that drives a full `DirectScanner.scan`; the byte tests above
    hold the wiring and `tests/test_scan_tool.py` holds the tool's."""
    import inspect

    for name in ("scan", "scan_roll"):
        method = getattr(DirectScanner, name)
        parameter = inspect.signature(method).parameters["fast_infrared"]
        assert parameter.default is True, name


# -- telling two otherwise identical passes apart ---------------------------


def record(fast_infrared=None):
    """A library record shaped like one a ladder pass would file."""
    scan = {
        "resolution_dpi": 1800, "channels": 4, "depth": 16,
        "frame": [0, 0, 10343, 6887], "film": "negative",
        "protocol_revision": 2, "exposure_metered": False,
        "exposure_scale": [1.0, 1.0, 1.0, 1.0],
    }
    if fast_infrared is not None:
        scan["fast_infrared"] = fast_infrared
    return {"scan": scan, "film": {"stock": "", "frame": "5", "subject": ""}}


def test_a_ladder_is_not_a_pile_of_duplicates():
    """The whole ladder is one frame at one dpi, depth, channel count and
    commanded exposure. Without the flag in the signature every pass reduces to
    every other, `duplicates` calls them interchangeable, and `--delete` keeps
    one and destroys the comparison the run exists to make. This is the same
    trap a bracket fell into, and the same fix."""
    assert library.signature(record(True)) != library.signature(record(False))


def test_entries_taken_before_the_field_existed_do_not_move():
    """A new element that reads None on every older entry leaves their
    signatures equal to each other, which is what keeps `duplicates` honest
    about the 189 entries already filed."""
    assert library.signature(record()) == library.signature(record())
    assert library.signature(record()) != library.signature(record(True))


# -- the guard rails ---------------------------------------------------------


def test_an_rgb_pass_can_carry_the_bit_but_has_no_plane_for_it():
    """Not refused at this level -- `set_mode` writes what it is told, and the
    tool above it is where the warning belongs. Recorded so the behaviour is
    deliberate rather than discovered."""
    s, t = scanner()
    s.set_mode(resolution=1800, passes=ONE_PASS_COLOR, fast_infrared=True)
    assert quality_of(t.payloads(SCSI_MODE_SELECT)[-1]) & QUALITY_FAST_INFRARED
