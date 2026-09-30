"""The single-scan tool, driven with no scanner underneath.

`tools/scan.py` is the primary way a frame is captured, and it once had a
NameError on every path, sitting *after* the scan and *before* the output was
written -- so a run burned scanner time, filed the entry, and then died without
producing the file it was asked for. These hold it to writing what it was asked
for and filing what the scanner returned.

Its `--bracket` option and the tests for it were archived with the rest of the
multi-exposure study in `docs/multi-exposure/`.
"""

import os
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import load_tool
from rps7200.direct import DirectScanner

scan_tool = load_tool("scan")


class FakeScanner(DirectScanner):
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

    A class rather than a factory, as the driver is: a tool that reads
    anything off it before opening a scanner reads it off this.
    """
    created = []

    class Patched(FakeScanner):
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


def test_a_second_run_never_writes_over_the_first(tmp_path, monkeypatch):
    """Two runs in one directory replaced scan.tif and its .json, and with
    --no-library the first was the only copy of that scan."""
    run(tmp_path, monkeypatch, "--no-library")
    first = (tmp_path / "out.tif").read_bytes()
    run(tmp_path, monkeypatch, "--no-library")
    assert (tmp_path / "out.tif").read_bytes() == first
    assert (tmp_path / "out-2.tif").exists()
    assert (tmp_path / "out-2.json").exists()


def test_a_second_run_under_another_format_keeps_the_first_record(
        tmp_path, monkeypatch):
    """The record is `<name>.json` whatever the picture's format, so
    `--out out.jpg` then `--out out.tif` found out.tif free and wrote the
    second scan's record over the first's -- with --no-library, the only
    one there was."""
    run(tmp_path, monkeypatch, "--no-library", "--out",
        str(tmp_path / "out.jpg"))
    first = (tmp_path / "out.json").read_bytes()
    run(tmp_path, monkeypatch, "--no-library")
    assert (tmp_path / "out.json").read_bytes() == first
    assert (tmp_path / "out-2.tif").exists()
    assert (tmp_path / "out-2.json").exists()


def test_a_plain_scan_is_filed_once(tmp_path, monkeypatch):
    run(tmp_path, monkeypatch)
    assert len(list((tmp_path / "lib").glob("*/scan.json"))) == 1


# --- filing ------------------------------------------------------------------


def test_a_claimed_pass_is_answered_for_once_it_is_filed(tmp_path, monkeypatch):
    """With the entry, or with None when it could not be filed -- the answer
    is what lets debug filing delete its copy, or file it after all."""
    from rps7200 import library

    answers = []
    monkeypatch.setattr(
        FakeScanner, "debug_claim",
        lambda self, pixels: answers.append, raising=False)
    real = library.save

    def save(image, meta, **kw):
        if not answers:
            raise OSError(28, "No space left on device")
        return real(image, meta, **kw)

    monkeypatch.setattr(library, "save", save)
    monkeypatch.setattr(FakeScanner, "last_pixels_raw",
                        np.zeros((6, 6, 3), np.uint16), raising=False)
    run(tmp_path / "refused", monkeypatch)
    assert answers == [None]
    run(tmp_path / "filed", monkeypatch)
    assert len(answers) == 2 and answers[1] is not None


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


# --- what actually lands in the library -----------------------------------


RAW_LEVEL, CORRECTED_LEVEL = 111, 222


class FakeCorrectingScanner(FakeScanner):
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


def run_correcting(tmp_path, monkeypatch, *argv, scanner=FakeCorrectingScanner):
    created = []

    class Patched(scanner):
        def __init__(self, **kw):
            super().__init__()
            created.append(self)

    monkeypatch.setattr(scan_tool, "DirectScanner", Patched)
    # --film-loaded: these calibrate, and stand in for an operator who has
    # said the film is in (`test_a_calibration_asks_about_the_film_first`).
    monkeypatch.setattr(
        sys, "argv",
        ["scan.py", "--out", str(tmp_path / "out.tif"),
         "--library", str(tmp_path / "lib"), "--film-loaded", *argv],
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


def test_every_pass_it_files_is_claimed_from_debug_filing(tmp_path, monkeypatch):
    """RPS7200_DEBUG=1 files what this tool does not keep -- metering probes --
    and not what it does, which would write every pass twice. Claiming each
    filed pass is what keeps the two apart; this tool used to switch debug
    off instead, and so filed none of the probes either."""
    claimed = []
    monkeypatch.setattr(FakeCorrectingScanner, "debug_claim",
                        lambda self, pixels: claimed.append(pixels),
                        raising=False)
    created, code = run_correcting(tmp_path, monkeypatch)
    assert code == 0
    assert len(claimed) == 1
    assert all(int(p.max()) == RAW_LEVEL for p in claimed), \
        "claimed the corrected pixels, which debug filing never spooled"


def test_both_capture_tools_file_the_raw_pixels(tmp_path, monkeypatch):
    """`tools/uniformity.py capture` files the same way and had the same bug.
    It was held to naming the attribute, by grep, on the grounds that it wants
    a scanner and a target; it wants neither on a device double. One pass of
    its own `one_pass`, the operator's answers typed in, and the entry must be
    the pass's raw pixels, labelled raw, re-decoding from its own bytes."""
    from types import SimpleNamespace

    from conftest import DeviceAtCommands, load_tool as _load, scanner_at_commands
    from rps7200 import library

    uniformity = _load("uniformity")
    calibrating, _ = scanner_at_commands(monkeypatch)
    calibrating.calibrate_shading()
    reference = calibrating.save_shading(tmp_path / "shading.npz")
    device = DeviceAtCommands(seed=7)
    answers = iter(["", "accept"])            # Enter at the prompt, accept
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    entry, outcome = uniformity.one_pass(
        lambda: DirectScanner(transport=device, verbose=False, debug=False),
        ("empty", None, "flat", "leave the transport empty", "a flat field"),
        SimpleNamespace(dpi=300, ir=False, library=tmp_path / "lib",
                        tag="uniformity-test"),
        1.0, reference, "session-1", 1)

    assert outcome == "ok"
    stored, record = library.load(entry)
    assert np.array_equal(stored, device.passes[-1]["pixels"])
    assert record["image"]["corrections_applied"] == []
    assert library.reconstruct(entry)[1].startswith("identical")
    assert not np.array_equal(library.corrected(entry)[0], stored)


# --- refused before the scanner is opened ------------------------------------


@pytest.mark.parametrize("argv", [
    ["--dpi", "7200"],            # cannot be shading-corrected at all
    ["--dpi", "0"],
    ["--out", "scan.png"],        # no such format; used to fail after the scan
    # The help says 60-100; Pillow turned 0 into 1, so a typo was a heavily
    # blocked JPEG delivered after the scan had been paid for.
    ["--out", "scan.jpg", "--quality", "9"],
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
    _, code = run(tmp_path, monkeypatch)
    assert code == 0
    assert "estimated" in capsys.readouterr().out


def test_the_backgrounding_warning_is_judged_on_the_slow_end(tmp_path,
                                                            monkeypatch, capsys):
    """The estimate sits at or below the library's medians, and the warning
    was judged on it: a 3600 dpi RGBI pass is about 3.6 minutes typically,
    and a dense frame with the lamp still to warm up takes it past the eight
    a foreground command gets."""
    _, code = run(tmp_path, monkeypatch, "--dpi", "3600", "--ir")
    assert code == 0
    assert "background" in capsys.readouterr().err


def test_a_short_run_is_not_told_to_go_to_the_background(tmp_path, monkeypatch,
                                                        capsys):
    _, code = run(tmp_path, monkeypatch, "--dpi", "300")
    assert code == 0
    assert "background" not in capsys.readouterr().err


# --- a library that will not take a pass --------------------------------------


def _refused(tmp_path, monkeypatch, *argv, scanner=FakeCorrectingScanner):
    """Run with the library under a file, so every `library.save` refuses --
    as a full disk, an unplugged drive or a --library naming a file does."""
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"")
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path / "temp"))
    created = []

    class Patched(scanner):
        def __init__(self, **kw):
            super().__init__()
            created.append(self)

    monkeypatch.setattr(scan_tool, "DirectScanner", Patched)
    # --film-loaded, as `run_correcting` passes it: these calibrate.
    monkeypatch.setattr(
        sys, "argv",
        ["scan.py", "--out", str(tmp_path / "out" / "out.tif"),
         "--library", str(blocker / "lib"), "--film-loaded", *argv],
    )
    return created, scan_tool.main()


def test_a_pass_the_library_refuses_costs_neither_the_rest_nor_the_file(
        tmp_path, monkeypatch):
    """The pass is held only in memory until it is filed. A bare loop let
    the refusal escape as a traceback, and --out and the scanner time went
    with it."""
    _created, code = _refused(tmp_path, monkeypatch)
    assert code != 0, "a pass not in the library is a failed run"
    assert (tmp_path / "out" / "out.tif").exists(), "--out was not written"
    kept = sorted((tmp_path / "out" / "unfiled").glob("*/scan.json"))
    assert len(kept) == 1, "the refused pass's raw data went nowhere"
    from rps7200 import library
    for path in kept:
        image, _record = library.load(path.parent)
        assert int(image.max()) == RAW_LEVEL, "kept the corrected pixels"
        assert library.read_raw(path.parent) is not None


def test_a_refused_pass_is_kept_compressed(tmp_path, monkeypatch):
    """Kept after the device has closed, as every pass here is filed. It was
    kept plain -- the window's rule, for filing with the device open -- and
    nothing here compacts, so it stayed raw.bin and uncompressed for good."""
    from rps7200 import library

    _created, code = _refused(tmp_path, monkeypatch)
    assert code != 0
    (kept,) = [p.parent for p in
               (tmp_path / "out" / "unfiled").glob("*/scan.json")]
    assert (kept / library.RAW_FILE).exists()
    assert not (kept / library.RAW_PLAIN).exists()


def test_a_pass_is_claimed_from_debug_filing_only_once_filed(tmp_path,
                                                            monkeypatch):
    """Claimed as it was held, a pass whose filing then failed was deleted
    from the debug spool when the scanner closed -- before this tool had
    tried to file it at all. The claim is answered once the filing is over,
    and here the answer is that it was not filed, so debug filing keeps it."""
    answered = []
    monkeypatch.setattr(FakeCorrectingScanner, "debug_claim",
                        lambda self, pixels: answered.append,
                        raising=False)
    _created, code = _refused(tmp_path, monkeypatch)
    assert code != 0
    assert answered == [None]


def test_debug_filing_runs_after_this_tools_own(tmp_path, monkeypatch):
    """The scanner's exit files what debug filing spooled and nobody
    claimed; it used to run as the device closed, before any pass here had
    been filed, and dropped what was claimed. A claim is now answered once
    the pass is filed, and the exit waits for this tool's filing too."""
    from rps7200 import library

    order = []
    real_save = library.save

    def save(*a, **kw):
        order.append("filed")
        return real_save(*a, **kw)

    class Exiting(FakeCorrectingScanner):
        def debug_claim(self, pixels):
            order.append("claimed")
            return lambda entry: order.append("answered")

        def __exit__(self, *exc):
            order.append("scanner exited")

    monkeypatch.setattr(library, "save", save)
    _created, code = run_correcting(tmp_path, monkeypatch, scanner=Exiting)
    assert code == 0
    assert order == ["claimed", "filed", "answered", "scanner exited"]


def ctrl_c() -> None:
    """A Ctrl-C now, taken by whatever handler is in force: the deferral's,
    or Python's own, which raises KeyboardInterrupt where it lands."""
    import signal

    signal.getsignal(signal.SIGINT)(signal.SIGINT, None)


@pytest.fixture
def terminal_ctrl_c():
    """SIGINT as a tool started from a terminal has it, for the test only."""
    import signal

    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    yield
    signal.signal(signal.SIGINT, previous)


def test_a_ctrl_c_while_filing_waits_for_every_pass_and_debug_filing(
        tmp_path, monkeypatch, terminal_ctrl_c):
    """The filing and debug filing ran outside the tool's Ctrl-C deferral
    once `HeldOpen` moved debug filing after it. A Ctrl-C there raised in
    the middle of them: the pass went unfiled, and the spool
    debug filing had already taken off the scanner went nowhere."""
    from rps7200 import library

    order = []
    real_save = library.save

    def save(*a, **kw):
        if not order:
            ctrl_c()
        order.append("filed")
        return real_save(*a, **kw)

    class Exiting(FakeCorrectingScanner):
        def __exit__(self, *exc):
            order.append("scanner exited")

    monkeypatch.setattr(library, "save", save)
    try:
        _created, code = run_correcting(tmp_path, monkeypatch,
                                        scanner=Exiting)
    except KeyboardInterrupt:
        pytest.fail(f"one Ctrl-C cut the filing short, after {order}")
    assert order == ["filed", "scanner exited"]
    assert code == 0


def test_an_empty_library_means_do_not_file(tmp_path, monkeypatch):
    """`--library ''` is `tools/scan_roll.py`'s way of saying skip. Here it
    filed into the current directory, where nothing looks for a library."""
    patch_scanner(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["scan.py", "--out", "out.tif",
                                      "--library", "", "--no-shading"])
    assert scan_tool.main() == 0
    assert (tmp_path / "out.tif").exists()
    assert not list(tmp_path.glob("*/scan.json"))
    assert not (tmp_path / "index.json").exists()


# --- the reference, and where debug filing files ------------------------------


def test_a_reference_inside_a_library_entry_is_refused_before_opening(
        tmp_path, monkeypatch):
    """Calibrating replaces the file it is given: a library entry's
    shading.npz, from a command line missing its --reuse, was replaced --
    the one its checksum names -- with a calibration folder dropped in."""
    entry = tmp_path / "lib" / "20260927T000000Z_x_300dpi"
    entry.mkdir(parents=True)
    (entry / "scan.json").write_text("{}", encoding="utf-8")
    created = patch_scanner(monkeypatch)
    monkeypatch.setattr(sys, "argv", [
        "scan.py", "--out", str(tmp_path / "out.tif"),
        "--library", str(tmp_path / "lib"),
        "--reference", str(entry / "shading.npz")])
    with pytest.raises(SystemExit) as refused:
        scan_tool.main()
    assert refused.value.code == 2
    assert created == []


def test_a_reused_reference_says_how_old_it_is(tmp_path, monkeypatch, capsys):
    cached = tmp_path / "shading.npz"
    cached.write_bytes(b"")
    created = patch_scanner(monkeypatch)
    monkeypatch.setattr(sys, "argv", [
        "scan.py", "--out", str(tmp_path / "out.tif"),
        "--library", str(tmp_path / "lib"),
        "--reference", str(cached), "--reuse"])
    assert scan_tool.main() == 0
    assert created
    assert "reusing the reference cached" in capsys.readouterr().out


def test_debug_filing_files_into_the_runs_own_library(tmp_path, monkeypatch):
    """It filed into ./library whatever --library said, apart from the
    passes it is evidence for."""
    monkeypatch.delenv("RPS7200_DEBUG_ROOT", raising=False)
    seen = []

    class Exiting(FakeCorrectingScanner):
        debug_root = None

        def __exit__(self, *exc):
            seen.append(self.debug_root)

    _created, code = run_correcting(tmp_path, monkeypatch, scanner=Exiting)
    assert code == 0
    assert seen == [str(tmp_path / "lib")]
    assert "RPS7200_DEBUG_ROOT" not in os.environ, "left set for the process"


def test_a_debug_root_the_operator_set_still_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("RPS7200_DEBUG_ROOT", str(tmp_path / "elsewhere"))
    seen = []

    class Exiting(FakeCorrectingScanner):
        debug_root = None

        def __exit__(self, *exc):
            seen.append(self.debug_root)

    _created, code = run_correcting(tmp_path, monkeypatch, scanner=Exiting)
    assert code == 0
    # Left unset, so the scanner reads the operator's (`_debug_root`).
    assert seen == [None]
    assert os.environ["RPS7200_DEBUG_ROOT"] == str(tmp_path / "elsewhere")


# --- Ctrl-C and filing -------------------------------------------------------


def test_ctrl_c_through_the_calibration_scans_nothing(tmp_path, monkeypatch):
    """The console said 'stopping after the pass in flight' and the tool went
    on to meter and take a whole pass -- minutes of nothing visibly stopping,
    which is what makes an operator press it again, mid-read."""
    from rps7200 import console

    pressed = []
    monkeypatch.setattr(console.DeferredInterrupt, "requested",
                        lambda self: bool(pressed))

    class PressedWhileCalibrating(FakeCorrectingScanner):
        def ensure_shading(self, path, reuse=False, skip=False):
            pressed.append(True)
            return super().ensure_shading(path, reuse=reuse, skip=skip)

    created, code = run_correcting(tmp_path, monkeypatch,
                                   scanner=PressedWhileCalibrating)
    assert code == 130
    assert created[-1].scans == [], "scanned after the Ctrl-C"
    assert not (tmp_path / "out.tif").exists()


def test_ctrl_c_during_metering_is_asked_again_before_the_pass(tmp_path,
                                                              monkeypatch):
    """`scan_unless_stopped` asks before `scan` is called, and `scan` meters
    and then starts the pass with nothing in between -- so a Ctrl-C pressed
    during the probes still cost the whole pass. The tool hands `scan` the
    question to ask after metering, and a stop there exits as a Ctrl-C
    does."""
    from rps7200 import console
    from rps7200.direct import StoppedBeforePass

    created, code = run_correcting(tmp_path, monkeypatch)
    assert code == 0
    asked = [kw.get("should_stop") for kw in created[-1].kwargs]
    assert asked and all(
        getattr(a, "__func__", None) is console.DeferredInterrupt.requested
        for a in asked), asked

    class StopsAfterMetering(FakeCorrectingScanner):
        def scan(self, **kw):
            raise StoppedBeforePass("stopped before the pass, after metering")

    created, code = run_correcting(tmp_path / "stopped", monkeypatch,
                                   scanner=StopsAfterMetering)
    assert code == 130


def test_ctrl_c_while_filing_waits_for_the_filing(tmp_path, monkeypatch):
    """The device is closed by then, so nothing can wedge -- but the pass is
    held only in memory, and filing it was outside any guard: a Ctrl-C there
    abandoned it unwritten."""
    import signal

    real = scan_tool.library.save
    calls = {"n": 0}

    def save(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            # What pressing Ctrl-C does: call whatever handles SIGINT now.
            handler = signal.getsignal(signal.SIGINT)
            if callable(handler):
                handler(signal.SIGINT, None)
        return real(*a, **kw)

    monkeypatch.setattr(scan_tool.library, "save", save)
    try:
        _created, code = run_correcting(tmp_path, monkeypatch)
    except KeyboardInterrupt:
        pytest.fail("one Ctrl-C while filing abandoned the pass held")
    assert code == 0
    assert len(_filed(tmp_path)) == 1


# --- on the driver itself ----------------------------------------------------
#
# Every double above hands out bytes that could never decode to the pixels it
# files -- b"pass-1" beside a 6x6 frame -- so no test here could say that an
# entry this tool files re-decodes to itself, or that the file it writes is the
# corrected picture. These run the real `DirectScanner` on a device double
# (`conftest.DeviceAtCommands`) under the tool's own `main()`.


def run_on_device(tmp_path, monkeypatch, *argv):
    from conftest import tool_on_device

    # --film-loaded: these calibrate, and stand in for an operator who has
    # said the film is in.
    devices = tool_on_device(scan_tool, monkeypatch)
    monkeypatch.setattr(
        sys, "argv",
        ["scan.py", "--out", str(tmp_path / "out.tif"),
         "--library", str(tmp_path / "lib"),
         "--reference", str(tmp_path / "calibration" / "shading.npz"),
         "--dpi", "300", "--film-loaded", *argv],
    )
    return devices, scan_tool.main()


def _entries(root):
    from rps7200 import library
    return [root / r["id"] for r in library.entries(root)]


@pytest.mark.parametrize("argv", [[], ["--ir"], ["--auto-exposure"]])
def test_what_it_files_reconstructs_and_what_it_writes_is_corrected(
        tmp_path, monkeypatch, argv):
    """The two halves, on the pass that was really taken: the entry holds the
    raw pixels its own bytes decode to, and out.tif is `library.corrected` of
    that entry -- what Save As would give -- rather than merely existing."""
    from rps7200 import library, tiff

    devices, code = run_on_device(tmp_path, monkeypatch, *argv)
    assert code == 0
    entries = _entries(tmp_path / "lib")
    assert len(entries) == 1
    entry = entries[0]
    assert library.reconstruct(entry)[1].startswith("identical")
    assert library.load(entry)[1]["image"]["corrections_applied"] == []
    assert library.read_raw(entry) == devices[0].passes[-1]["blob"]
    delivered = tiff.read(str(tmp_path / "out.tif"))
    assert np.array_equal(delivered, library.corrected(entry)[0])
    assert not np.array_equal(delivered, library.load(entry)[0]), \
        "the raw pixels were delivered"


def test_with_debug_on_every_pass_is_filed_once(tmp_path, monkeypatch):
    """What the tool keeps it files; the metering probes it does not keep,
    debug filing does; nothing is filed by both."""
    from rps7200 import library

    monkeypatch.setenv("RPS7200_DEBUG", "1")
    monkeypatch.setenv("RPS7200_DEBUG_ROOT", str(tmp_path / "debug"))
    devices, code = run_on_device(tmp_path, monkeypatch, "--auto-exposure")
    assert code == 0
    ours = [library.read_raw(e) for e in _entries(tmp_path / "lib")]
    debug = [library.read_raw(e) for e in _entries(tmp_path / "debug")]
    sent = [p["blob"] for p in devices[0].passes if not p["calibrate"]]
    assert len(ours) == 1 and debug, "no probe was filed"
    assert sorted(ours + debug) == sorted(sent), "a pass filed twice or lost"
