"""The single-scan tool, driven with no scanner underneath.

`tools/scan.py` is the primary way a frame is captured, and it had a NameError
on every path: `--bracket` and `--stops` were accepted, documented, and wired to
nothing, and the reference to the missing variable sat *after* the scan and
*before* the output was written -- so a run burned scanner time, filed the
entry, and then died without producing the file it was asked for.

What these hold it to is the part no hardware run would show quickly: that every
pass of a bracket is filed, not just the one whose raw bytes happen to survive
on the scanner.
"""

import sys
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import load_tool
from rps7200.direct import DirectScanner

scan_tool = load_tool("scan")


class FakeBracketScanner(DirectScanner):
    """Answers a scan with a flat frame whose level follows the exposure.

    Raw bytes are per pass and distinct, which is the property under test: the
    real scanner overwrites `last_raw` with every pass.
    """

    def __init__(self):
        self.verbose = False
        self._shading = None
        self._ccd_mask = b"\x00" * 16
        self.last_raw = None
        self.last_raw_layout = None
        self.scans = []
        self.scales = []
        self.kwargs = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def inquiry(self, refresh=False):
        return SimpleNamespace(
            vendor="Reflecta", product="RPS 7200", model=0x31, firmware="1.0"
        )

    def ensure_shading(self, path, reuse=False, skip=False):
        return {"action": "skipped", "reference": None, "path": None,
                "summary": "shading correction disabled"}

    def auto_exposure(self, **kw):
        return [1.0, 1.0, 1.0]

    def get_gain_offset(self):
        from conftest import settings
        return settings(8000, 20000, 50000, 8000)

    def scan(self, resolution=300, infrared=False, exposure_scale=1.0, **kw):
        n = 4 if infrared else 3
        k = exposure_scale[0] if isinstance(exposure_scale, list) else exposure_scale
        self.scans.append(float(k))
        self.scales.append(list(exposure_scale) if isinstance(exposure_scale, list)
                           else [exposure_scale] * 3)
        # Everything else the tool passed, for tests about wiring rather than
        # about pixels -- a flag dropped between the parser and the device is
        # invisible to a fake that only records exposures.
        self.kwargs.append(dict(kw))
        self.last_raw = f"pass-{len(self.scans)}".encode()
        self.last_raw_layout = {"format": "index", "pass": len(self.scans)}
        level = int(min(60000, 1000 * len(self.scans)))
        return (
            np.full((6, 6, n), level, np.uint16),
            {"resolution_dpi": resolution, "channels": n,
             "channel_order": list("RGBI")[:n], "exposure_metered": False,
             "exposure_scale": [k, k, k], "shading": None},
        )


def patch_scanner(monkeypatch):
    """Swap in the fake, keeping the class itself.

    A lambda would do for constructing one, but the tool reads
    MIN_BRACKET_PASSES off the class to validate --bracket before opening
    anything -- so the stand-in has to be a class, not a factory.
    """
    created = []

    class Patched(FakeBracketScanner):
        def __init__(self, **kw):
            super().__init__()
            created.append(self)

    monkeypatch.setattr(scan_tool, "DirectScanner", Patched)
    return created


def run(tmp_path, monkeypatch, *argv):
    """Run the tool's main() against the fake, and return (scanner, exit code)."""
    created = patch_scanner(monkeypatch)
    monkeypatch.setattr(
        sys, "argv",
        ["scan.py", "--out", str(tmp_path / "out.tif"),
         "--library", str(tmp_path / "lib"), "--no-shading", *argv],
    )
    code = scan_tool.main()
    return (created[0] if created else None), code


# --- the single pass, which is what most runs are -------------------------


def test_a_plain_scan_writes_the_file_it_was_asked_for(tmp_path, monkeypatch):
    """The NameError landed after the scan and before this write."""
    _, code = run(tmp_path, monkeypatch)
    assert code == 0
    assert (tmp_path / "out.tif").exists()
    assert (tmp_path / "out.json").exists()


def test_a_plain_scan_is_filed_once(tmp_path, monkeypatch):
    run(tmp_path, monkeypatch)
    assert len(list((tmp_path / "lib").glob("*/scan.json"))) == 1


# --- the bracket ------------------------------------------------------------


def test_a_bracket_takes_the_passes_it_was_asked_for(tmp_path, monkeypatch):
    scanner, code = run(tmp_path, monkeypatch, "--bracket", "3")
    assert code == 0
    assert len(scanner.scans) == 3


def test_a_bracket_exposes_each_pass_differently(tmp_path, monkeypatch):
    """A bracket of identical exposures is not a bracket."""
    scanner, _ = run(tmp_path, monkeypatch, "--bracket", "4")
    assert len(set(scanner.scans)) == 4
    assert scanner.scans == sorted(scanner.scans), "ascending exposure order"


def test_every_pass_is_filed_not_only_the_last(tmp_path, monkeypatch):
    """last_raw holds one pass; waiting for the return value loses the rest."""
    run(tmp_path, monkeypatch, "--bracket", "3")
    entries = sorted((tmp_path / "lib").glob("*/scan.json"))
    assert len(entries) == 3


def test_each_filed_pass_keeps_its_own_raw_bytes(tmp_path, monkeypatch):
    """Three entries sharing one pass's bytes would be worse than useless."""
    import gzip

    run(tmp_path, monkeypatch, "--bracket", "3")
    raws = {
        gzip.open(p, "rb").read()
        for p in (tmp_path / "lib").glob("*/raw.bin.gz")
    }
    assert raws == {b"pass-1", b"pass-2", b"pass-3"}


def test_the_merged_result_is_written(tmp_path, monkeypatch):
    _, code = run(tmp_path, monkeypatch, "--bracket", "3")
    assert code == 0
    assert (tmp_path / "out.tif").exists()


def test_the_merge_is_recorded_in_the_sidecar(tmp_path, monkeypatch):
    import json

    run(tmp_path, monkeypatch, "--bracket", "3")
    meta = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert meta["bracket"]["passes"] == 3
    assert len(meta["bracket"]["ratios"]) == 3


def test_no_library_files_nothing_but_still_writes_the_scan(tmp_path, monkeypatch):
    patch_scanner(monkeypatch)
    monkeypatch.setattr(
        sys, "argv",
        ["scan.py", "--out", str(tmp_path / "out.tif"), "--no-library",
         "--no-shading"],
    )
    assert scan_tool.main() == 0
    assert (tmp_path / "out.tif").exists()
    assert not (tmp_path / "library").exists()


@pytest.mark.parametrize("n", ["1", "10", "99"])
def test_a_bracket_size_outside_the_range_is_refused_up_front(tmp_path, monkeypatch, n):
    """Refused before the device opens: a calibration already spent is wasted."""
    with pytest.raises(SystemExit):
        run(tmp_path, monkeypatch, "--bracket", n)


def test_bracket_zero_is_a_single_pass(tmp_path, monkeypatch):
    """0 is the default, so it has to mean "off" rather than be refused."""
    scanner, code = run(tmp_path, monkeypatch, "--bracket", "0")
    assert code == 0
    assert len(scanner.scans) == 1


# --- the fast-infrared bit reaching the device ----------------------------


def test_an_infrared_run_ties_the_plane_to_the_resolution_by_default(
        tmp_path, monkeypatch):
    """The default since 2026-09-16, and the whole saving lives here: an
    untied pass costs ~220 s at any resolution, a tied one costs what its lines
    cost. A tool that dropped it between the parser and `scan()` would silently
    give every operator the slow pass back."""
    s, code = run(tmp_path, monkeypatch, "--ir")
    assert code == 0
    assert s.kwargs[-1]["fast_infrared"] is True


def test_no_fast_ir_unties_it(tmp_path, monkeypatch):
    """The override has to actually reach the device, or it is a lie in the
    help text."""
    s, code = run(tmp_path, monkeypatch, "--ir", "--no-fast-ir")
    assert code == 0
    assert s.kwargs[-1]["fast_infrared"] is False


def test_an_rgb_run_never_sends_it(tmp_path, monkeypatch):
    """No plane to acquire, so the bit governs nothing. Cleared silently now
    that it is the default: warning on every RGB scan about a flag nobody
    asked for would be noise."""
    s, code = run(tmp_path, monkeypatch)
    assert code == 0
    assert s.kwargs[-1]["fast_infrared"] is False


def test_an_rgb_run_with_fast_ir_typed_explicitly_still_sends_nothing(
        tmp_path, monkeypatch):
    s, code = run(tmp_path, monkeypatch, "--fast-ir")
    assert code == 0
    assert s.kwargs[-1]["fast_infrared"] is False


@pytest.mark.parametrize("extra", [[], ["--no-fast-ir"]])
def test_a_bracket_with_infrared_is_refused_not_merged(tmp_path, monkeypatch,
                                                       extra):
    """It used to run, and deliver a wrong file with a normal summary.

    One RGBI pass among RGB ones: metering aimed blue low for the RGBI pass
    and every RGB pass inherited it, and the merge fitted one relation on green
    and applied it to all three channels -- so the RGBI pass's blue, about five
    times brighter, entered five times too high, from the pass with the most
    weight. Refused before the device opens rather than half-fixed.

    Two tests here used to check that `--fast-ir` reached that pass. It still
    reaches `scan_bracket` -- `test_fast_infrared.py` holds that -- but no tool
    path takes an infrared bracket any more.
    """
    created = patch_scanner(monkeypatch)
    monkeypatch.setattr(
        sys, "argv",
        ["scan.py", "--out", str(tmp_path / "out.tif"), "--library",
         str(tmp_path / "lib"), "--no-shading", "--ir", "--bracket", "3",
         *extra],
    )
    with pytest.raises(SystemExit) as refused:
        scan_tool.main()
    assert refused.value.code == 2
    assert created == [], "the scanner was opened for a bracket that cannot merge"


def test_a_brackets_channel_balance_reaches_every_pass(tmp_path, monkeypatch):
    """Three values are the one form a bracket takes, and all three arrive.

    The ladder scales every channel by the same factor per pass, so what
    survives of `--exposure-scale` is the ratio between R, G and B."""
    s, code = run(tmp_path, monkeypatch, "--bracket", "3",
                  "--exposure-scale", "1.0,1.2,0.8")
    assert code == 0
    assert len(s.scales) == 3
    for scale in s.scales:
        assert [v / scale[0] for v in scale] == pytest.approx([1.0, 1.2, 0.8])


# --- what actually lands in the library -----------------------------------


RAW_LEVEL, CORRECTED_LEVEL = 111, 222


class FakeCorrectingScanner(FakeBracketScanner):
    """Like the real one: returns the CORRECTED image, keeps the raw.

    `DirectScanner.scan` takes `raw_pixels = image` before flat-fielding,
    rebinds `image` to the corrected array, returns that, and leaves the
    uncorrected one on `last_pixels_raw`. The two levels here are distinct so
    a test can say which of them was filed.
    """

    def scan(self, **kw):
        image, meta = super().scan(**kw)
        self.last_pixels_raw = np.full(image.shape, RAW_LEVEL, np.uint16)
        # Present whenever a correction happened, and recorded by
        # `library.save` as `calibration.report`.
        meta["shading"] = {"columns": 6, "width": 6, "clipped": 0}
        return np.full(image.shape, CORRECTED_LEVEL, np.uint16), meta


def run_correcting(tmp_path, monkeypatch, *argv):
    created = []

    class Patched(FakeCorrectingScanner):
        def __init__(self, **kw):
            super().__init__()
            created.append(self)

    monkeypatch.setattr(scan_tool, "DirectScanner", Patched)
    monkeypatch.setattr(
        sys, "argv",
        ["scan.py", "--out", str(tmp_path / "out.tif"),
         "--library", str(tmp_path / "lib"), *argv],
    )
    return created, scan_tool.main()


def _filed(tmp_path):
    from rps7200 import library
    entries = sorted((tmp_path / "lib").glob("*/scan.json"))
    return [(library.load(p.parent)) for p in entries]


def test_the_filed_entry_holds_raw_pixels_not_corrected_ones(tmp_path,
                                                             monkeypatch):
    """The library's whole bargain: it keeps what the scanner sent, and every
    correction is recomputed from it with today's code.

    `scan()` returns the *corrected* image and keeps the uncorrected one on
    `last_pixels_raw`, and this tool filed what it returned. That wrote
    shading into `scan.tif` while `corrections_applied` still said nothing was
    baked in -- so `library.corrected()` shaded it a second time, and
    `reconstruct` reported it as a changed decode, which is the one check that
    exists to catch a real regression. Measured on the hardware: 99.9% of
    samples differed on a single 300 dpi frame.
    """
    _created, code = run_correcting(tmp_path, monkeypatch)
    assert code == 0
    filed = _filed(tmp_path)
    assert len(filed) == 1
    image, record = filed[0]
    assert int(image.max()) == RAW_LEVEL, (
        "the corrected image was filed; the library must hold raw pixels")
    # And the record must agree with the pixels rather than merely be empty.
    assert record["image"]["corrections_applied"] == []
    assert record["calibration"]["report"], (
        "the correction that was computed still has to be recorded beside "
        "the pixels -- that is how a consumer tells 'not corrected' from "
        "'no correction was available'")


def test_every_pass_of_a_bracket_is_filed_raw_too(tmp_path, monkeypatch):
    """The bracket files through the same callback, one pass at a time, and
    `last_pixels_raw` describes the pass that just ran -- so reading it late
    would give every entry the last pass's pixels."""
    _created, code = run_correcting(tmp_path, monkeypatch, "--bracket", "3")
    assert code == 0
    filed = _filed(tmp_path)
    assert len(filed) == 3
    for image, _record in filed:
        assert int(image.max()) == RAW_LEVEL


def test_the_merge_judges_saturation_on_what_the_sensor_returned(tmp_path,
                                                                 monkeypatch):
    """`scan()` returns corrected pixels, and the correction hides a railed
    sample inside the range the merge trusts wherever a column's gain is below
    one. So the tool hands the merge each pass's raw pixels as well -- with the
    library on, and with it off, where `hold` keeps nothing."""
    import rps7200.bracket as bracket_module

    seen = []
    real = bracket_module.merge_bracket

    def spy(frames, exposures, **kw):
        seen.append((frames, kw.get("sensor_frames")))
        return real(frames, exposures, **kw)

    monkeypatch.setattr(bracket_module, "merge_bracket", spy)
    for library_args in ([], ["--no-library"]):
        seen.clear()
        _created, code = run_correcting(tmp_path, monkeypatch, "--bracket", "3",
                                        *library_args)
        assert code == 0
        (frames, sensor), = seen
        assert sensor is not None and len(sensor) == len(frames) == 3, library_args
        assert all(int(f.max()) == CORRECTED_LEVEL for f in frames)
        assert all(int(p.max()) == RAW_LEVEL for p in sensor), (
            "the merge was handed the corrected pixels as the sensor's")


def test_every_pass_it_files_is_claimed_from_debug_filing(tmp_path, monkeypatch):
    """RPS7200_DEBUG=1 files what this tool does not keep -- metering probes --
    and not what it does, which would write every pass twice. Claiming each
    filed pass is what keeps the two apart; this tool used to switch debug
    off instead, and so filed none of the probes either."""
    claimed = []
    monkeypatch.setattr(FakeCorrectingScanner, "debug_claim",
                        lambda self, pixels: claimed.append(pixels),
                        raising=False)
    created, code = run_correcting(tmp_path, monkeypatch, "--bracket", "3")
    assert code == 0
    assert len(claimed) == 3
    assert all(int(p.max()) == RAW_LEVEL for p in claimed), \
        "claimed the corrected pixels, which debug filing never spooled"


def test_both_capture_tools_file_the_raw_pixels(tmp_path):
    """`tools/uniformity.py capture` files the same way and had the same bug.
    It cannot be driven from here -- it wants a scanner and a target -- so it
    is held to naming the attribute at all."""
    import inspect

    from conftest import load_tool as _load
    uniformity = _load("uniformity")
    source = inspect.getsource(uniformity.one_pass)
    assert "last_pixels_raw" in source
    assert "image if raw_pixels is None else raw_pixels" in source


# --- refused before the scanner is opened ------------------------------------


@pytest.mark.parametrize("argv", [
    ["--dpi", "7200"],            # cannot be shading-corrected at all
    ["--dpi", "0"],
    ["--out", "scan.png"],        # no such format; used to fail after the scan
    # A bracket's exposure is R,G,B or nothing. One value was dropped without a
    # word and the passes ran around the device's own settings, metering off.
    ["--bracket", "3", "--exposure-scale", "1.5"],
    ["--bracket", "3", "--exposure-scale", "1.0,1.2"],
    ["--bracket", "3", "--exposure-scale", "1.0,1.2,0.8,1.0"],
])
def test_what_cannot_work_is_refused_before_the_scanner_opens(tmp_path,
                                                              monkeypatch, argv):
    created = patch_scanner(monkeypatch)
    monkeypatch.setattr(
        sys, "argv",
        ["scan.py", "--out", str(tmp_path / "out.tif"),
         "--library", str(tmp_path / "lib"), *argv],
    )
    with pytest.raises(SystemExit) as refused:
        scan_tool.main()
    assert refused.value.code == 2
    assert created == [], "the scanner was opened for a request that cannot work"


def test_7200_dpi_raw_on_purpose_is_still_allowed(tmp_path, monkeypatch):
    _, code = run(tmp_path, monkeypatch, "--dpi", "7200")   # run() adds --no-shading
    assert code == 0


def test_the_run_says_how_long_it_will_take(tmp_path, monkeypatch, capsys):
    _, code = run(tmp_path, monkeypatch, "--bracket", "3")
    assert code == 0
    assert "estimated" in capsys.readouterr().out
