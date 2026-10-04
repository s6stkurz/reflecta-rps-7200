"""A scan refused when the scanner's gain has moved off its shading reference.

The device sets red's gain itself during a calibration -- 37 or 39 on
2026-10-03/04 -- and has been seen to change it again with no calibration
from here: 96 of 122 scans after the 00:06Z reference ran at 37 against the
39 it was taken at, corrected red ~4% off. Stefan's choice: say so before the
scan, and offer a calibration or a way to go on anyway.
"""

from types import SimpleNamespace

from conftest import settings
from rps7200.direct import SCSI_READ_GAIN_OFFSET, DirectScanner, settings_in_log
from rps7200.session import Roll, Scan
from test_session import FakeScanner, kinds, run


def reply(gain, offset):
    """A READ GAIN/OFFSET answer with these R, G, B gains and offsets."""
    b = bytearray(123)
    b[66:69] = bytes(offset)
    b[72:75] = bytes(gain)
    return b.hex()


def log(*answers):
    """A command log: other commands, and READ GAIN/OFFSETs answering so."""
    sent = [{"t": 0.0, "cdb": "dd0000000d00", "in": "00" * 13}]
    for g, o in answers:
        sent.append({"t": 1.0, "cdb": f"{SCSI_READ_GAIN_OFFSET:02x}00007b00",
                     "in": reply(g, o)})
        sent.append({"t": 2.0, "cdb": "d20000000000", "in": ""})
    return sent


def test_a_calibrations_log_says_what_its_reference_was_taken_at():
    """The last answer, not the first: the gain settles during the pass."""
    sent = log(([37, 33, 21], [13, 10, 29]), ([39, 33, 21], [13, 11, 30]))
    assert settings_in_log(sent) == {"gain": [39, 33, 21],
                                     "offset": [13, 11, 30]}
    assert settings_in_log([]) is None
    assert settings_in_log([{"cdb": f"{SCSI_READ_GAIN_OFFSET:02x}00007b00",
                             "in": ""}]) is None


def scanner_with(reference):
    s = DirectScanner.__new__(DirectScanner)
    s.verbose = False
    s._shading = object()
    s._reference_settings = reference
    return s


def test_a_gain_off_the_reference_is_found_and_an_equal_one_is_not():
    ref = {"gain": [39, 33, 21], "offset": [14, 12, 31]}
    s = scanner_with(ref)
    same = settings(9604, 6506, 6506, 7745)
    same.gain[:3], same.offset[:3] = [39, 33, 21], [14, 12, 31]
    assert s.reference_mismatch(same) is None
    moved = settings(9604, 6506, 6506, 7745)
    moved.gain[:3], moved.offset[:3] = [37, 33, 21], [12, 10, 29]
    found = s.reference_mismatch(moved)
    assert found == {"now": {"gain": [37, 33, 21], "offset": [12, 10, 29]},
                     "reference": {"gain": [39, 33, 21], "offset": [14, 12, 31]}}


def test_a_reference_whose_settings_are_unknown_is_never_called_moved():
    s = scanner_with(None)
    now = settings(9604, 6506, 6506, 7745)
    assert s.reference_mismatch(now) is None


MOVED = {"now": {"gain": [37, 33, 21], "offset": [12, 10, 29]},
         "reference": {"gain": [39, 33, 21], "offset": [14, 12, 31]}}


class MovedScanner(FakeScanner):
    """A scanner whose gain has moved off the reference in force."""

    def __init__(self, moved=MOVED, **kw):
        super().__init__(**kw)
        self.moved = moved

    def get_gain_offset(self):
        return settings(9604, 6506, 6506, 7745)

    def reference_mismatch(self, now):
        return self.moved


def test_a_scan_is_refused_before_it_moves_anything(tmp_path):
    scanner = MovedScanner()
    s, _, events = run(Scan(resolution=600), tmp_path, scanner=scanner)
    assert not [c for c in scanner.calls if c[0] == "scan"]
    said = kinds(events, "reference_moved")
    assert len(said) == 1
    assert "37/33/21" in said[0].text and "39/33/21" in said[0].text
    assert s.reference_moved == MOVED


def test_a_roll_is_refused_too_but_a_walk_is_not(tmp_path):
    scanner = MovedScanner()
    _, _, events = run(Roll(frames=1, resolution=600, name="r"), tmp_path,
                       scanner=scanner)
    assert kinds(events, "reference_moved")
    assert not [c for c in scanner.calls if c[0] == "scan"]
    walker = MovedScanner()
    _, _, events = run(Roll(frames=1, resolution=600, name="w", dry_run=True),
                       tmp_path, scanner=walker)
    assert not kinds(events, "reference_moved"), "a walk only prescans"


def test_accepted_once_the_same_mismatch_scans(tmp_path):
    scanner = MovedScanner()

    def accept(session, _scanner):
        session.reference_moved = MOVED
        session.accept_reference()

    _, _, events = run(Scan(resolution=600), tmp_path, scanner=scanner,
                       extra=accept)
    assert not kinds(events, "reference_moved")
    assert [c for c in scanner.calls if c[0] == "scan"]


def test_a_new_mismatch_is_asked_about_afresh(tmp_path):
    scanner = MovedScanner(moved={"now": {"gain": [41, 33, 21],
                                          "offset": [12, 10, 29]},
                                  "reference": MOVED["reference"]})

    def accept_old(session, _scanner):
        session.reference_moved = MOVED
        session.accept_reference()

    _, _, events = run(Scan(resolution=600), tmp_path, scanner=scanner,
                       extra=accept_old)
    assert kinds(events, "reference_moved")


def test_a_scan_asking_for_no_correction_is_not_held_to_one(tmp_path):
    scanner = MovedScanner()
    _, _, events = run(Scan(resolution=600, shading=False), tmp_path,
                       scanner=scanner)
    assert not kinds(events, "reference_moved")


def test_a_stand_in_without_the_check_scans_as_before(tmp_path):
    scanner = FakeScanner()
    _, _, events = run(Scan(resolution=600), tmp_path, scanner=scanner)
    assert not kinds(events, "reference_moved")
    assert [c for c in scanner.calls if c[0] == "scan"]


def test_the_demo_holds_its_scans_to_the_drivers_check():
    from rps7200.demo import DemoScanner
    assert DemoScanner.reference_mismatch is DirectScanner.reference_mismatch
    demo = SimpleNamespace(_reference_settings=None, _shading=object())
    assert DirectScanner.reference_mismatch(demo, settings(1, 1, 1, 1)) is None
