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
    SCSI_READ_GAIN_OFFSET,
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
    """Not refused at *this* level. `set_mode` is the primitive: it writes what
    it is told, so calibration, metering and anything else reaching it directly
    are unaffected by the gate. `scan()` is where the gate lives -- see below.
    Recorded so the split is deliberate rather than discovered."""
    s, t = scanner()
    s.set_mode(resolution=1800, passes=ONE_PASS_COLOR, fast_infrared=True)
    assert quality_of(t.payloads(SCSI_MODE_SELECT)[-1]) & QUALITY_FAST_INFRARED


# -- the gate, in `scan()` ---------------------------------------------------


def metered_transport() -> FakeTransport:
    """A fake that can answer `get_gain_offset`, which every real pass reads.

    The exposures are the device's own reference values, descending per channel
    so that a bracket's ceiling calculation has something to bind on.
    """
    blob = bytearray(123)
    for i, offset in enumerate((60, 62, 64, 66)):
        blob[offset:offset + 2] = (9604 - i * 1000).to_bytes(2, "little")
    return FakeTransport(replies={SCSI_READ_GAIN_OFFSET: bytes(blob)})


def quality_scan_sends(**kw) -> int:
    """The quality word of the MODE SELECT that a real `scan()` sends.

    Driven through `DirectScanner.scan` rather than `set_mode`, because the
    gate is at that call site and asserting on the primitive would not see it.
    The fake carries the pass as far as MODE SELECT and then fails on the image
    read, which is fine: the payload is already recorded by then, and it is the
    payload this is about.
    """
    transport = metered_transport()
    s = DirectScanner(transport=transport)
    s.verbose = False
    try:
        s.scan(shading=False, auto_exposure=False, **kw)
    except Exception:                                    # noqa: BLE001
        pass
    payloads = transport.payloads(SCSI_MODE_SELECT)
    assert payloads, "the pass never got as far as MODE SELECT"
    return quality_of(payloads[-1])


def test_an_rgb_scan_never_carries_the_bit_however_it_is_asked():
    """The bit governs how the infrared plane is acquired, so on a pass with no
    infrared plane there is nothing for it to govern -- and what it does there
    is simply unmeasured. `docs/fast-infrared-plan.md` characterises it on RGBI
    and says nothing about RGB.

    Both command line tools cleared it themselves (`tools/scan.py` with `if not
    args.ir`, `tools/scan_roll.py` with `args.fast_ir and args.ir`). The window
    did not, and `gui-settings.json` persists the box ticked, so an ordinary RGB
    scan from it sent 0x0080 on every pass. The gate is in `scan()` so that one
    place covers all three callers.
    """
    quality = quality_scan_sends(resolution=600, infrared=False,
                                 fast_infrared=True)
    assert not quality & QUALITY_FAST_INFRARED
    assert quality == QUALITY_SKIP_SHADING, f"0x{quality:04x}"


def test_an_infrared_scan_still_carries_it():
    """The other half: the gate must not have turned the default off."""
    quality = quality_scan_sends(resolution=600, infrared=True,
                                 fast_infrared=True)
    assert quality & QUALITY_FAST_INFRARED
    assert quality == QUALITY_SKIP_SHADING | QUALITY_FAST_INFRARED


def test_asking_for_the_untied_pass_is_still_honoured():
    """`--no-fast-ir` has to keep working on the path where it means
    something, or the gate would have closed the escape hatch instead of the
    hole."""
    quality = quality_scan_sends(resolution=600, infrared=True,
                                 fast_infrared=False)
    assert not quality & QUALITY_FAST_INFRARED


def test_the_bracket_can_be_told_which_infrared_pass_to_take():
    """`scan_bracket` had no `fast_infrared` parameter at all, so
    `tools/scan.py` parsed `--no-fast-ir`, set it, and then did not pass it on
    this path -- the bracket ran tied whatever was asked for, silently.

    It matters more than a dropped flag usually would: below 1800 dpi the tied
    pass's quality is waived rather than measured, which makes the flag the
    escape hatch from a waiver.
    """
    import inspect

    parameter = inspect.signature(DirectScanner.scan_bracket).parameters
    assert "fast_infrared" in parameter, "the flag cannot reach the bracket"
    assert parameter["fast_infrared"].default is True

    seen = []

    class Stop(Exception):
        pass

    s = DirectScanner(transport=metered_transport())
    s.verbose = False

    def spy(**kw):
        seen.append(kw.get("fast_infrared"))
        raise Stop

    s.scan = spy
    for asked in (True, False):
        seen.clear()
        try:
            s.scan_bracket(passes=3, resolution=600, infrared=True,
                           auto_exposure=False, exposure_scale=[1.0, 1.0, 1.0],
                           fast_infrared=asked)
        except Stop:
            pass
        assert seen == [asked], f"asked {asked}, forwarded {seen}"
