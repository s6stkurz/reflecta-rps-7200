"""A slide scanned with infrared: an RGB pass for its colour beside the RGBI one.

On slide film blue comes back 17.7-20.4x brighter in RGBI than in RGB, and on
2026-10-03 a bright slide's RGBI pass clipped its sky and drew lines through
it where an RGB pass straight after had neither. So such a frame is scanned
twice, filed as two raw entries, and delivered as one picture: the RGB pass's
colour with the RGBI pass's infrared registered onto it.

What matters, and is held here:

- which films take the second pass, and that the decision is the driver's;
- the RGB pass's exposure -- blue back with green, not still held down for an
  infrared that is not in this pass, and never carrying the RGBI pass's gain;
- each pass filed with its **own** raw bytes, never the other's;
- the frame's entry is the RGB pass, its infrared partner found again from it,
  and the delivered file the merged picture.
"""

import json

import numpy as np
import pytest

from conftest import settings
from rps7200 import library
from rps7200.direct import (
    BLUE_EXPOSURE_FLOOR,
    FILM_BW,
    FILM_NEGATIVE,
    FILM_POSITIVE,
    METER_NONE,
    ColourPass,
    DirectScanner,
    RollFrame,
    blue_gain_for,
)
from rps7200.session import Roll, Scan
from test_roll import FakeRoll, picture
from test_session import FakeScanner, run

# -- which films ---------------------------------------------------------------


def test_only_a_slide_with_infrared_takes_a_colour_pass():
    assert DirectScanner.wants_colour_pass(FILM_POSITIVE, True)
    assert not DirectScanner.wants_colour_pass(FILM_POSITIVE, False)
    assert not DirectScanner.wants_colour_pass(FILM_NEGATIVE, True)
    assert not DirectScanner.wants_colour_pass(FILM_BW, False)


def test_the_demo_takes_the_decision_from_the_driver():
    from rps7200.demo import DemoScanner
    assert DemoScanner.wants_colour_pass is DirectScanner.wants_colour_pass
    assert DemoScanner.colour_pass is DirectScanner.colour_pass
    assert (DemoScanner.colour_pass_exposure
            is DirectScanner.colour_pass_exposure)


# -- the RGB pass's exposure -----------------------------------------------------

BASE = [9604, 6506, 6506, 7745]
FLOOR_SCALE = BLUE_EXPOSURE_FLOOR / BASE[2]


def slide_metering(levels, asked, scales, blue_gain=1, blue_gain_short=1.4):
    """A slide metered for RGBI, as `auto_exposure` records it: blue held
    down by its headroom and sent at its floor, with a gain."""
    return {
        "target": 0.8, "locked": True, "film": FILM_POSITIVE,
        "infrared": True, "blue_headroom": 24.0,
        "base_exposure": list(BASE), "base_gain": [21, 21, 21, 21],
        "rounds": [{"round": 2, "scales": list(asked), "levels": list(levels),
                    "targets": [0.8, 0.8, 0.8 / 24], "clipped": [False] * 3}],
        "scales": list(scales), "blue_gain": blue_gain,
        "blue_gain_short": blue_gain_short,
    }


def test_blue_rejoins_green_and_drops_the_infrared_gain():
    sent = [2.0, 2.0, FLOOR_SCALE]
    record = slide_metering(levels=[0.80, 0.78, 0.15], asked=sent, scales=sent)
    scales, metering = DirectScanner.colour_pass_exposure(sent, record)
    # Blue read 0.15 at its floor; at green's scale that is 0.63, under target.
    assert scales == pytest.approx([2.0, 2.0, 2.0])
    assert metering["blue_gain"] is None, "the RGBI pass's gain came along"
    assert metering["infrared"] is False and metering["blue_headroom"] is None
    assert metering["colour_pass"]["held"] == 1.0


def test_a_blue_that_would_clip_holds_the_whole_group_back():
    """The probe read blue at its floor, never at the group's exposure; at
    green's scale this one would land at 2.1 of full scale."""
    sent = [2.0, 2.0, FLOOR_SCALE]
    record = slide_metering(levels=[0.60, 0.80, 0.50], asked=sent, scales=sent)
    scales, metering = DirectScanner.colour_pass_exposure(sent, record)
    blue_then = 0.50 * 2.0 / FLOOR_SCALE
    held = 0.8 / blue_then
    assert scales == pytest.approx([2.0 * held] * 3)
    assert metering["colour_pass"]["held"] == pytest.approx(held, abs=1e-4)
    # one factor for all three: the slide's own balance survives
    assert scales[0] == pytest.approx(scales[1]) == pytest.approx(scales[2])


def test_a_blue_under_its_floor_takes_the_gain_the_rgb_pass_needs():
    sent = [0.3, 0.3, FLOOR_SCALE]
    record = slide_metering(levels=[0.8, 0.8, 0.8], asked=sent, scales=sent)
    # Blue at 0.8 from the floor's exposure is 0.5 at 0.3: under target, and
    # 0.3 x 6506 = 1952 counts is under the floor.
    scales, metering = DirectScanner.colour_pass_exposure(sent, record)
    assert scales[2] == pytest.approx(FLOOR_SCALE)
    want, _ = blue_gain_for(BASE[2] * 0.3 / BLUE_EXPOSURE_FLOOR, 21)
    assert metering["blue_gain"] == want


def test_an_exposure_nobody_metered_is_taken_as_given():
    assert DirectScanner.colour_pass_exposure(1.5, None) == ([1.5] * 3, None)
    assert DirectScanner.colour_pass_exposure([1.0, 2.0, 3.0, 4.0], None) == (
        [1.0, 2.0, 3.0], None)


def test_the_colour_pass_writes_no_blue_gain_the_rgbi_pass_needed():
    s = DirectScanner.__new__(DirectScanner)
    s.verbose = False
    sent = [2.0, 2.0, FLOOR_SCALE]
    record = slide_metering(levels=[0.80, 0.78, 0.15], asked=sent, scales=sent)
    scales, metering = DirectScanner.colour_pass_exposure(sent, record)
    written = s.pass_settings(settings(*BASE), scales, metering)
    assert written.gain[2] == settings(*BASE).gain[2]


# -- the roll takes it -----------------------------------------------------------


class PassRecorder(FakeRoll):
    """A strip whose every pass leaves bytes of its own, numbered, so a test
    can tell which pass a frame's bytes came from."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.passes = []

    def scan(self, resolution=300, infrared=True, exposure_scale=1.0, **kw):
        image, meta = super().scan(resolution, infrared, exposure_scale, **kw)
        n = len(self.passes)
        self.last_pixels_raw = np.full(image.shape, n, np.uint16)
        self.last_raw = bytes([n])
        self.passes.append({"infrared": infrared, "metering": kw.get("metering")})
        return image, dict(meta, exposure_scale=exposure_scale)


def test_a_slide_roll_takes_an_rgb_pass_after_each_rgbi_one():
    s = PassRecorder([picture(seed=i) for i in range(2)])
    out = list(s.scan_roll(frames=2, meter=METER_NONE, film=FILM_POSITIVE,
                           infrared=True, colour_pass=True))
    assert [p["infrared"] for p in s.passes] == [True, False, True, False]
    for f in out:
        assert f.image.shape[2] == 4 and f.colour.image.shape[2] == 3
        # Each pass's own bytes: the RGBI pass's record was read before the
        # RGB pass rebound it.
        assert f.capture["raw"] != f.colour.capture["raw"]
        assert f.raw_image.flat[0] == f.capture["raw"][0]
        assert f.colour.raw_image.flat[0] == f.colour.capture["raw"][0]
        assert f.colour.meta["roll_index"] == f.index


def test_a_negative_roll_keeps_its_one_pass():
    s = PassRecorder([picture(seed=i) for i in range(2)])
    out = list(s.scan_roll(frames=2, meter=METER_NONE, film=FILM_NEGATIVE,
                           infrared=True, colour_pass=True))
    assert [p["infrared"] for p in s.passes] == [True, True]
    assert all(f.colour is None and f.capture is None for f in out)


def test_the_colour_pass_is_only_taken_when_asked():
    """`tools/scan_roll.py` files a frame from `capture_record()` after it is
    yielded, and would read the RGB pass's."""
    s = PassRecorder([picture(seed=i) for i in range(2)])
    list(s.scan_roll(frames=2, meter=METER_NONE, film=FILM_POSITIVE,
                     infrared=True))
    assert [p["infrared"] for p in s.passes] == [True, True]


# -- the session files it ----------------------------------------------------------

H, W = 120, 180


def field(seed=0):
    """A textured RGBI scene a few pixels larger than a pass, with a speck of
    dust in its infrared: broadband, as film is, so it can be registered."""
    rng = np.random.default_rng(seed)
    noise = rng.random((H + 12, W + 12, 4))
    blurred = sum(noise[dy:dy + H + 10, dx:dx + W + 10]
                  for dy in range(3) for dx in range(3)) / 9
    out = (blurred * 50000 + 5000)
    out[..., 3] = 40000
    out[60:63, 90:93, 3] = 8000           # dust, at field (60..62, 90..92)
    return out.astype(np.uint16)


def two_passes(seed=0):
    """The colour pass, and an RGBI pass of the same film a line further on:
    colour[y, x] = rgbi[y - 1, x]."""
    f = field(seed)
    colour = f[4:4 + H, 4:4 + W, :3].copy()
    rgbi = f[5:5 + H, 4:4 + W, :].copy()
    return colour, rgbi, f


def capture_of(image, tag):
    return {"reference": None, "ccd_mask": None, "raw": tag * 32,
            "raw_layout": {"format": "index", "width": image.shape[1],
                           "lines": image.shape[0],
                           "channels": image.shape[2]}}


class SlideScanner(FakeScanner):
    """A roll of slides, each frame two passes as the driver yields them."""

    def scan_roll(self, frames=None, resolution=1800, infrared=True,
                  first_index=0, film="negative", colour_pass=False, **kw):
        self.asked_colour = colour_pass
        for i in range(frames or 1):
            colour, rgbi, _ = two_passes(seed=i)
            meta = {"resolution_dpi": resolution, "channels": 4,
                    "channel_order": list("RGBI"), "width": W, "height": H,
                    "depth": 16}
            cmeta = dict(meta, channels=3, channel_order=list("RGB"))
            yield RollFrame(
                index=first_index + i, position=self.pos, image=rgbi,
                meta=meta, prescan=None, registration={},
                raw_image=rgbi, capture=capture_of(rgbi, b"\x04"),
                colour=ColourPass(colour, cmeta, colour,
                                  capture_of(colour, b"\x03")),
            )
            self.pos += 1


def entries_tagged(root, tag):
    return [e for e in library.entries(root) if tag in e["tags"]]


def test_a_slide_frame_is_two_entries_and_one_merged_picture(tmp_path):
    scanner = SlideScanner()
    run(Roll(frames=1, resolution=600, infrared=True, film=FILM_POSITIVE,
             name="slides"), tmp_path, scanner=scanner)
    assert scanner.asked_colour is True

    infrared = entries_tagged(tmp_path, "infrared pass")
    colour = entries_tagged(tmp_path, "colour pass")
    assert len(infrared) == len(colour) == 1
    ir, rgb = infrared[0], colour[0]
    # Each with its own bytes.
    assert library.read_raw(library.entry_path(tmp_path, ir)) == b"\x04" * 32
    assert library.read_raw(library.entry_path(tmp_path, rgb)) == b"\x03" * 32
    # The RGB pass is the frame; the RGBI pass is not one Export could take.
    assert rgb["extra"]["roll_membership"]["kind"] == "frame"
    assert ir["extra"]["roll_membership"]["kind"] == "infrared pass"
    assert (rgb["extra"]["infrared_pair"]["id"]
            == ir["extra"]["infrared_pair"]["id"])

    # The manifest waits on the frame's entry, which is the RGB pass.
    manifest = json.loads((tmp_path / "rolls" / "slides" / "roll.json")
                          .read_text(encoding="utf-8"))
    assert manifest["frames"][0]["done"]

    # The delivered file: the RGB pass's colour, the dust where it shows.
    colour_pass, rgbi, f = two_passes(seed=0)
    from rps7200 import tiff
    delivered = tiff.read(str(tmp_path / "rolls" / "slides" / "frame01.tif"))
    assert delivered.shape == (H, W, 4)
    assert np.array_equal(delivered[..., :3], colour_pass)
    dust = delivered[..., 3] < 20000
    ys, xs = np.nonzero(dust)
    # field (60..62, 90..92) is colour (56..58, 86..88); unregistered, the
    # RGBI pass has it a line higher.
    assert set(ys) == {56, 57, 58} and set(xs) == {86, 87, 88}


def test_the_frames_entry_comes_back_with_its_infrared(tmp_path):
    run(Roll(frames=1, resolution=600, infrared=True, film=FILM_POSITIVE,
             name="slides"), tmp_path, scanner=SlideScanner())
    rgb = entries_tagged(tmp_path, "colour pass")[0]
    image, record = library.corrected(library.entry_path(tmp_path, rgb))
    assert record["infrared"] == "merged"
    assert image.shape == (H, W, 4)
    delivered = __import__("rps7200.tiff", fromlist=["read"]).read(
        str(tmp_path / "rolls" / "slides" / "frame01.tif"))
    assert np.array_equal(image, delivered)


def test_an_unpaired_entry_comes_back_as_it_was(tmp_path):
    run(Roll(frames=1, resolution=600, infrared=True, film=FILM_POSITIVE,
             name="slides"), tmp_path, scanner=SlideScanner())
    ir = entries_tagged(tmp_path, "infrared pass")[0]
    image, record = library.corrected(library.entry_path(tmp_path, ir))
    assert "infrared" not in record
    assert image.shape == (H, W, 4)


def test_a_colour_pass_whose_partner_is_gone_says_so(tmp_path):
    import shutil
    run(Roll(frames=1, resolution=600, infrared=True, film=FILM_POSITIVE,
             name="slides"), tmp_path, scanner=SlideScanner())
    ir = entries_tagged(tmp_path, "infrared pass")[0]
    shutil.rmtree(library.entry_path(tmp_path, ir))
    rgb = entries_tagged(tmp_path, "colour pass")[0]
    image, record = library.corrected(library.entry_path(tmp_path, rgb))
    assert image.shape == (H, W, 3)
    assert "not in this library" in record["infrared"]


class SingleSlideScanner(FakeScanner):
    """One pass at a time, as `scan` is called: RGBI, then the colour pass."""

    def scan(self, resolution=1800, infrared=True, **kw):
        self.calls.append(("scan", resolution, infrared, kw.get("auto_exposure")))
        colour, rgbi, _ = two_passes()
        image = rgbi if infrared else colour
        self.last_pixels_raw = image
        self._capture = capture_of(image, b"\x04" if infrared else b"\x03")
        return image, {"resolution_dpi": resolution,
                       "channels": image.shape[2],
                       "channel_order": list("RGBI"[:image.shape[2]]),
                       "width": W, "height": H, "depth": 16,
                       "exposure_scale": 1.0}

    def capture_record(self):
        return dict(self._capture)

    def _log(self, message):
        pass

    colour_pass = DirectScanner.colour_pass
    colour_pass_exposure = staticmethod(DirectScanner.colour_pass_exposure)
    _pass_role = None


def test_a_single_slide_scan_takes_both_passes_and_files_both(tmp_path):
    scanner = SingleSlideScanner()
    _, _, events = run(Scan(resolution=600, infrared=True, film=FILM_POSITIVE),
                       tmp_path, scanner=scanner)
    assert [c[2] for c in scanner.calls if c[0] == "scan"] == [True, False]
    ir = entries_tagged(tmp_path, "infrared pass")
    rgb = entries_tagged(tmp_path, "colour pass")
    assert len(ir) == len(rgb) == 1
    assert library.read_raw(library.entry_path(tmp_path, ir[0])) == b"\x04" * 32
    assert library.read_raw(library.entry_path(tmp_path, rgb[0])) == b"\x03" * 32
    # The window was shown the merged picture.
    shown = [e.result for e in events if e.kind == "result"]
    assert shown and shown[-1].meta["channels"] == 4
    assert "RGB + infrared" in shown[-1].label


def test_a_negative_single_scan_is_one_pass(tmp_path):
    scanner = SingleSlideScanner()
    run(Scan(resolution=600, infrared=True, film=FILM_NEGATIVE), tmp_path,
        scanner=scanner)
    assert [c[2] for c in scanner.calls if c[0] == "scan"] == [True]
    assert not entries_tagged(tmp_path, "colour pass")


def test_an_entry_filed_with_the_old_one_map_merge_is_measured_again(tmp_path):
    """The twelve slides of 2026-10-03 were filed while the merge was one
    affine map, and nine were refused by it. Their records carry no bands,
    so a view or export measures them with today's code."""
    run(Roll(frames=1, resolution=600, infrared=True, film=FILM_POSITIVE,
             name="slides"), tmp_path, scanner=SlideScanner())
    rgb = entries_tagged(tmp_path, "colour pass")[0]
    path = library.entry_path(tmp_path, rgb)
    record = json.loads((path / "scan.json").read_text(encoding="utf-8"))
    record["extra"]["infrared_pair"]["registration"] = {
        "method": "affine from phase-correlated red tiles", "ok": False,
        "affine": [[0, 0, 0], [0, 0, 0]],
        "reason": "the tiles disagree with any one map by 1.72 px (median)"}
    (path / "scan.json").write_text(json.dumps(record), encoding="utf-8")
    image, out = library.corrected(path)
    assert out["infrared"] == "merged, measured again"
    assert image.shape == (H, W, 4)
