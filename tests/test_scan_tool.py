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
from pathlib import Path
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
    """Run the tool's main() against the fake, and return (scanner, exit code).

    ``tmp_path`` is made first, as a folder a run is pointed at is there: a
    library's or --out's own folder may be made, and never one above it."""
    tmp_path.mkdir(parents=True, exist_ok=True)
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
    tmp_path.mkdir(parents=True, exist_ok=True)       # see `run`
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


class KeepsNoRawPixels(FakeCorrectingScanner):
    """Corrects, and keeps nothing aside: a stand-in, never the driver."""

    def scan(self, **kw):
        image, meta = super().scan(**kw)
        self.last_pixels_raw = None
        return image, meta


def test_corrected_pixels_with_no_raw_ones_are_filed_labelled_corrected(
        tmp_path, monkeypatch, capsys):
    """With nothing in `last_pixels_raw` this tool filed what `scan()`
    returned -- corrected, by the meta's own report -- under a record that
    said nothing was baked in: `library.corrected` shaded it a second time.
    Labelled now, as `FrameWriter` labels the same case, and said."""
    _created, code = run_correcting(tmp_path, monkeypatch,
                                    scanner=KeepsNoRawPixels)
    assert code == 0
    [(image, record)] = _filed(tmp_path)
    assert int(image.max()) == CORRECTED_LEVEL
    assert record["image"]["corrections_applied"] == ["shading"]
    assert "labelled as corrected" in capsys.readouterr().out


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
    """Run with a library that refuses every `library.save` into it -- as a
    disk that filled during the pass does. A library under a file stood for
    one; it is refused before the device opens now, as a missing drive is."""
    from rps7200 import library

    refused = tmp_path / "lib"
    real = library.save

    def save(image, meta, *, root=library.DEFAULT_ROOT, **kw):
        if Path(root) == refused:
            raise OSError(28, "No space left on device")
        return real(image, meta, root=root, **kw)

    monkeypatch.setattr(library, "save", save)
    (tmp_path / "temp").mkdir()
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
         "--library", str(refused), "--film-loaded", *argv],
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


def test_the_runs_calibration_is_kept_in_its_own_library(tmp_path, monkeypatch):
    """Beside the frames its reference corrects: not beside the cache, and
    not where the operator's RPS7200_DEBUG_ROOT sends debug passes."""
    monkeypatch.setenv("RPS7200_DEBUG_ROOT", str(tmp_path / "elsewhere"))
    seen = []

    class Exiting(FakeCorrectingScanner):
        debug_root = None
        library_root = None

        def __exit__(self, *exc):
            seen.append(self.library_root)

    _created, code = run_correcting(tmp_path, monkeypatch, scanner=Exiting)
    assert code == 0
    assert seen == [str(tmp_path / "lib")]


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
    record = library.load(entry)[1]
    assert record["image"]["corrections_applied"] == []
    # This tool asks nothing of its bytes before filing; `library.save`
    # does, and on the pass that was really taken finds nothing to say.
    assert record["raw"]["disagrees"] == {}
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


# --- an --out that cannot be written -------------------------------------------


@pytest.mark.parametrize("filed", [True, False])
def test_an_out_that_cannot_be_written_says_what_still_holds_the_pass(
        tmp_path, monkeypatch, capsys, filed):
    """Written after the passes were filed, unguarded and not deferred: a
    missing drive or a file held open raised a traceback, and the lines
    saying where the passes were filed never printed. With --no-library,
    the file was the only copy there was -- which has to be said.

    Held open by another program, as a folder that is not there is refused
    before the scanner opens now."""
    held = tmp_path / "held.tif"
    real = scan_tool.export.write

    def held_open(path, *a, **kw):
        if Path(path) == held:
            raise PermissionError(13, "held open by another program")
        return real(path, *a, **kw)

    monkeypatch.setattr(scan_tool.export, "write", held_open)
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(temp))
    _scanner, code = run(tmp_path, monkeypatch, "--out", str(held),
                         *(() if filed else ("--no-library",)))
    out, err = capsys.readouterr()
    assert code == 1
    (said,) = [line for line in err.splitlines()
               if line.startswith(f"could not write {held}")]
    kept = temp / "rps7200-undelivered"
    if filed:
        (entry,) = (tmp_path / "lib").glob("*/scan.json")
        assert f"the pass is filed in the library as {entry.parent.name}" \
            in said
        assert "nothing of the scan is lost" in said
        # Not the window's Save As: it reaches only its own session's passes
        # and the rolls it opens, and an entry this tool filed is neither.
        assert "Save As" not in said
        assert "filed 1 pass in the library:" in out, "the filed list"
        assert not kept.exists(), "a filed pass kept a second time"
    else:
        # With --no-library the file was the only copy there was, and it
        # was dropped: "nothing else holds this scan". Kept now, in the
        # system's temporary folder, and said.
        assert sorted(p.name for p in kept.iterdir()) == ["held.json",
                                                          "held.tif"]
        assert f"the picture is kept as {kept / 'held.tif'}, its record " \
               "beside it" in said
        assert said.endswith("the system may clear that folder")
        assert "nothing else holds" not in err
        assert f"keeping the picture in {kept}" in err, "said before"
        assert np.array_equal(tiff_read(kept / "held.tif"),
                              np.full((6, 6, 3), 1000, np.uint16))
    assert "wrote " not in out


def tiff_read(path):
    from rps7200 import tiff

    return np.asarray(tiff.read(str(path)))


def test_a_picture_nothing_holds_that_cannot_be_kept_either_is_said_lost(
        tmp_path, monkeypatch, capsys):
    """The rescue costs nothing if it fails as well: the run still ends
    saying nothing else holds the scan, with why the second place refused,
    and exit 1 -- never a traceback in place of the first failure."""
    def refused(path, *a, **kw):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(scan_tool.export, "write", refused)
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(temp))
    _scanner, code = run(tmp_path, monkeypatch, "--no-library")
    _out, err = capsys.readouterr()
    assert code == 1
    (said,) = [line for line in err.splitlines()
               if line.startswith(f"could not write {tmp_path / 'out.tif'}")]
    assert f"nor keep it in {temp / 'rps7200-undelivered'}" in said
    assert said.endswith("nothing else holds this scan")
    assert list((temp / "rps7200-undelivered").iterdir()) == []


def test_a_name_the_system_refuses_is_kept_under_one_made_for_it(
        tmp_path, monkeypatch, capsys):
    """`--out scan?.tif` passes every check before the scan on Windows and
    fails after it, Errno 22; the rescue tried the same name in the
    temporary folder, failed the same way, and the only copy was lost to
    the name alone. Refused here wherever the name is used, as Windows
    refuses it, so the test says the same on every system."""
    bad = tmp_path / "bad.tif"
    real = scan_tool.export.write

    def refuses_the_name(path, *a, **kw):
        if Path(path).name == bad.name:
            raise OSError(22, "Invalid argument")
        return real(path, *a, **kw)

    monkeypatch.setattr(scan_tool.export, "write", refuses_the_name)
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(temp))
    _scanner, code = run(tmp_path, monkeypatch, "--out", str(bad),
                         "--no-library")
    _out, err = capsys.readouterr()
    assert code == 1
    kept = temp / "rps7200-undelivered"
    (picture,) = kept.glob("undelivered-*.tif")
    assert picture.with_suffix(".json").exists()
    assert sorted(p.name for p in kept.iterdir()) == sorted(
        [picture.name, picture.with_suffix(".json").name])
    (said,) = [line for line in err.splitlines()
               if line.startswith(f"could not write {bad}")]
    assert f"the picture is kept as {picture}, its record beside it" in said
    assert "nothing else holds" not in said
    assert np.array_equal(tiff_read(picture),
                          np.full((6, 6, 3), 1000, np.uint16))


@pytest.mark.parametrize("where", ["folder gone", "disk full"])
def test_a_kept_black_and_white_picture_is_the_one_out_would_have_had(
        tmp_path, monkeypatch, capsys, where):
    """The one channel was decided after --out's claim and folder, so a
    folder gone since the scan had the rescue keep the colour pass with a
    record that said nothing of one channel, while a disk full during the
    write kept the one channel: two deliveries from one command, said
    alike."""
    import json

    from rps7200 import storage

    real_claim, real_write = scan_tool.export.claim, scan_tool.export.write

    def gone(path, *a, **kw):
        if where == "folder gone" and Path(path).parent == tmp_path:
            raise storage.DriveMissing(f"{tmp_path} is not there")
        return real_claim(path, *a, **kw)

    def full(path, *a, **kw):
        if where == "disk full" and Path(path).parent == tmp_path:
            raise OSError(28, "No space left on device")
        return real_write(path, *a, **kw)

    monkeypatch.setattr(scan_tool.export, "claim", gone)
    monkeypatch.setattr(scan_tool.export, "write", full)
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(temp))
    _scanner, code = run(tmp_path, monkeypatch, "--film", "bw",
                         "--no-library")
    capsys.readouterr()
    assert code == 1
    kept = temp / "rps7200-undelivered"
    picture = tiff_read(kept / "out.tif")
    assert picture.ndim == 2 or picture.shape[2] == 1, picture.shape
    record = json.loads((kept / "out.json").read_text(encoding="utf-8"))
    assert record["channels"] == 1 and "mono_channel" in record


def test_a_kept_single_channel_says_nothing_else_keeps_its_infrared(
        tmp_path, monkeypatch, capsys):
    """One channel of an infrared pass leaves the plane out, which a
    delivery says and the rescue did not; and the note said the library
    entry keeps it under --no-library, where there is none."""
    real = scan_tool.export.write

    def full(path, *a, **kw):
        if Path(path).parent == tmp_path:
            raise OSError(28, "No space left on device")
        return real(path, *a, **kw)

    monkeypatch.setattr(scan_tool.export, "write", full)
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(temp))
    _scanner, code = run(tmp_path, monkeypatch, "--mono", "--ir",
                         "--no-library")
    out, _err = capsys.readouterr()
    assert code == 1
    assert ("one channel delivered, so the infrared plane is not in this "
            "file, and nothing else keeps it") in out
    assert "library entry" not in out


def test_a_kept_picture_whose_record_would_not_go_is_said_as_that(
        tmp_path, monkeypatch, capsys):
    """The picture is the scan; a record that would not follow it is said,
    and the picture is not called lost."""
    from rps7200 import library

    real_write, real_atomic = scan_tool.export.write, library._write_atomic

    def held_open(path, *a, **kw):
        if Path(path).parent == tmp_path:
            raise PermissionError(13, "held open by another program")
        return real_write(path, *a, **kw)

    def no_record(path, text):
        if "rps7200-undelivered" in str(path):
            raise OSError(28, "No space left on device")
        return real_atomic(path, text)

    monkeypatch.setattr(scan_tool.export, "write", held_open)
    monkeypatch.setattr(library, "_write_atomic", no_record)
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(temp))
    _scanner, code = run(tmp_path, monkeypatch, "--no-library")
    _out, err = capsys.readouterr()
    assert code == 1
    kept = temp / "rps7200-undelivered" / "out.tif"
    assert kept.exists()
    (said,) = [line for line in err.splitlines()
               if line.startswith("could not write")]
    assert (f"the picture is kept as {kept}, but its record could not be "
            "written there") in said
    assert "nothing else holds" not in said


@pytest.mark.parametrize("case", ["jpeg", "dng refused", "record refused",
                                  "no pillow"])
def test_a_kept_infrared_jpeg_is_said_by_every_file_it_left(
        tmp_path, monkeypatch, capsys, case):
    """A JPEG of an infrared pass leaves its infrared in a DNG beside it, and
    the last line named the picture and its record alone, "move both": the
    DNG -- the only copy of that plane with --no-library -- stayed in a
    folder the same line said the system may clear. Without Pillow the
    picture is a TIFF, and the line named a .jpg that was not there. And
    `export.write`'s note was dropped, so a DNG that would not go was never
    said."""
    import builtins

    from rps7200 import library

    if case != "no pillow":
        pytest.importorskip("PIL")
    held = tmp_path / "held.jpg"
    real_write, real_atomic = scan_tool.export.write, library._write_atomic

    def held_open(path, *a, **kw):
        if Path(path) == held:
            raise PermissionError(13, "held open by another program")
        return real_write(path, *a, **kw)

    def no_room(*a, **kw):
        raise OSError(28, "No space left on device")

    def no_record(path, text):
        if "rps7200-undelivered" in str(path):
            raise OSError(28, "No space left on device")
        return real_atomic(path, text)

    real_import = builtins.__import__

    def no_pillow(name, *a, **kw):
        if name.startswith("PIL"):
            raise ImportError("no module named PIL")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(scan_tool.export, "write", held_open)
    if case == "dng refused":
        monkeypatch.setattr(scan_tool.export.dng, "write", no_room)
    elif case == "record refused":
        monkeypatch.setattr(library, "_write_atomic", no_record)
    elif case == "no pillow":
        monkeypatch.setattr(builtins, "__import__", no_pillow)
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(temp))
    _scanner, code = run(tmp_path, monkeypatch, "--out", str(held), "--ir",
                         "--no-library")
    out, err = capsys.readouterr()
    assert code == 1
    (said,) = [line for line in err.splitlines()
               if line.startswith(f"could not write {held}")]
    kept = temp / "rps7200-undelivered"
    left = sorted(p.name for p in kept.iterdir())
    expected = {
        "jpeg": (["held.dng", "held.jpg", "held.json"],
                 f"the picture is kept as {kept / 'held.jpg'}, with held.dng "
                 "and its record beside it -- move all three where they "
                 "belong",
                 "infrared does not fit in a JPEG, so it is in held.dng "
                 "beside it"),
        "dng refused": (["held.jpg", "held.json"],
                        f"the picture is kept as {kept / 'held.jpg'}, its "
                        "record beside it -- move both where they belong",
                        "infrared does not fit in a JPEG and held.dng could "
                        "not be written ([Errno 28] No space left on device), "
                        "so the infrared plane is not in this delivery"),
        "record refused": (["held.dng", "held.jpg"],
                           f"the picture is kept as {kept / 'held.jpg'}, with "
                           "held.dng beside it, but its record could not be "
                           "written there ([Errno 28] No space left on "
                           "device) -- move both where they belong",
                           "so it is in held.dng beside it"),
        "no pillow": (["held.json", "held.tif"],
                      f"the picture is kept as {kept / 'held.tif'}, its "
                      "record beside it -- move both where they belong",
                      "Pillow is not installed, so this was written as "
                      "held.tif instead"),
    }
    files, line, note = expected[case]
    assert left == files
    assert line in said, said
    assert said.endswith("; the system may clear that folder")
    for name in left:
        assert name.endswith(".json") or name in said, f"{name} left unsaid"
    assert note in out, "export.write's note was dropped"
    # Nothing else holds the pass, so no note may say a library entry does.
    assert "library entry" not in out


@pytest.mark.parametrize("held_by", ["library", "kept", "nothing"])
def test_a_dng_that_would_not_go_names_only_what_really_keeps_the_plane(
        tmp_path, monkeypatch, capsys, held_by):
    """The note said "the library entry keeps all 4 channels" whatever held
    the pass: with --no-library there was no entry, and the infrared plane
    was in no file at all."""
    from rps7200 import library

    pytest.importorskip("PIL")

    def no_room(*a, **kw):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(scan_tool.export.dng, "write", no_room)
    real_save = library.save

    def refused(image, meta, *, root=library.DEFAULT_ROOT, **kw):
        if Path(root) == tmp_path / "lib":
            raise OSError(28, "No space left on device")
        return real_save(image, meta, root=root, **kw)

    if held_by == "kept":
        monkeypatch.setattr(library, "save", refused)
        temp = tmp_path / "temp"
        temp.mkdir()
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(temp))
    _scanner, code = run(tmp_path, monkeypatch, "--out",
                         str(tmp_path / "out.jpg"), "--ir",
                         *(("--no-library",) if held_by == "nothing" else ()))
    out, _err = capsys.readouterr()
    assert code == (1 if held_by == "kept" else 0)
    assert (tmp_path / "out.jpg").exists()
    (note,) = [line for line in out.splitlines()
               if line.startswith("infrared does not fit in a JPEG")]
    assert "out.dng could not be written" in note
    if held_by == "library":
        assert note.endswith("; the library entry keeps all 4 channels")
    elif held_by == "kept":
        (entry,) = (tmp_path / "unfiled").glob("*/scan.json")
        assert note.endswith(f"; the pass kept in {entry.parent} keeps all "
                             "4 channels"), note
    else:
        assert note.endswith(", so the infrared plane is not in this "
                             "delivery"), note
        assert "library" not in note


def test_the_quality_range_lives_in_one_place(tmp_path, monkeypatch, capsys):
    """60-100 was typed into the help, the refusal and the window twice; a
    range moved in one place would have had the window offer what the tool
    refuses."""
    monkeypatch.setattr(scan_tool.export, "QUALITY_RANGE", (70, 90))
    created = patch_scanner(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["scan.py", "--help"])
    with pytest.raises(SystemExit):
        scan_tool.main()
    assert "JPEG quality 70-90" in " ".join(capsys.readouterr().out.split())
    for quality, refused in (("65", True), ("95", True), ("80", False)):
        monkeypatch.setattr(
            sys, "argv",
            ["scan.py", "--out", str(tmp_path / "out.jpg"), "--quality",
             quality, "--library", str(tmp_path / "lib"), "--no-shading"])
        if refused:
            with pytest.raises(SystemExit) as stopped:
                scan_tool.main()
            assert stopped.value.code == 2
            assert "JPEG quality is 70-90" in capsys.readouterr().err
        else:
            pytest.importorskip("PIL")
            assert scan_tool.main() == 0
    assert len(created) == 1, "opened only for the quality inside the range"


@pytest.mark.parametrize("filed", [True, False])
def test_a_record_that_cannot_be_written_is_not_called_a_lost_picture(
        tmp_path, monkeypatch, capsys, filed):
    """The picture and the record beside it shared one failure: the picture
    written and its .json refused said "could not write out.tif" and, with
    --no-library, "nothing else holds this scan" of a scan on disk."""
    from pathlib import Path

    from rps7200 import library

    real = library._write_atomic

    def refuses(path, text):
        if Path(path).suffix == ".json" and Path(path).stem == "out":
            raise OSError(28, "No space left on device")
        return real(path, text)

    monkeypatch.setattr(library, "_write_atomic", refuses)
    _scanner, code = run(tmp_path, monkeypatch,
                         *(() if filed else ("--no-library",)))
    out, err = capsys.readouterr()
    assert code == 1, "a record not written is still a failed run"
    assert (tmp_path / "out.tif").exists()
    assert f"wrote {tmp_path / 'out.tif'}" in out
    assert "could not write" not in err
    assert "nothing else holds" not in err
    (said,) = [line for line in err.splitlines()
               if line.startswith(f"wrote {tmp_path / 'out.tif'}, but")]
    assert f"its record {tmp_path / 'out.json'} could not be written" in said
    if filed:
        (entry,) = (tmp_path / "lib").glob("*/scan.json")
        assert said.endswith(f"in its library entry {entry.parent.name}")


def test_the_record_beside_the_scan_is_written_whole(tmp_path, monkeypatch):
    """Its values as an entry's record writes them, and written beside and
    renamed over, as the picture is: `default=str` wrote a numpy number as
    its printed form, in place."""
    import json

    from rps7200 import library

    writes = []
    real = library._write_atomic
    monkeypatch.setattr(library, "_write_atomic",
                        lambda path, text: (writes.append(path),
                                            real(path, text)))
    _scanner, code = run(tmp_path, monkeypatch)
    assert code == 0
    assert tmp_path / "out.json" in writes
    record = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert record["resolution_dpi"] == 1800


# --- the folders, the paths and the disks, before the scanner opens ----------


def _refused_before_opening(tmp_path, monkeypatch, *argv):
    """Run with ``argv`` alone, and return the refusal's words and the
    scanners made: none, for a run refused before the device opens."""
    created = patch_scanner(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["scan.py", "--no-shading", *argv])
    with pytest.raises(SystemExit) as refused:
        scan_tool.main()
    assert refused.value.code == 2
    return created


def test_a_full_disk_is_refused_before_the_scanner_opens(tmp_path, monkeypatch,
                                                        capsys):
    """Nothing asked how much room there was: a full disk showed itself as a
    pass kept in a temporary folder, after the scanner time was spent."""
    import collections
    import shutil

    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage",
                        lambda path: usage(10**13, 0, 1000))
    created = _refused_before_opening(
        tmp_path, monkeypatch, "--out", str(tmp_path / "out.tif"),
        "--library", str(tmp_path / "lib"))
    assert created == []
    err = capsys.readouterr().err
    assert "not scanning" in err and "GB free" in err


@pytest.mark.parametrize("missing", ["library", "out"])
def test_a_folder_on_a_drive_that_has_gone_is_refused_first(
        tmp_path, monkeypatch, capsys, missing):
    """--library and --out made every folder missing above them: one on a
    stick not plugged in was made again on C:, or failed after the pass. Its
    own folder may be made; anything above it is refused, and nothing made."""
    gone = tmp_path / "gone"
    created = _refused_before_opening(
        tmp_path, monkeypatch,
        "--out", str((gone if missing == "out" else tmp_path) / "x" / "o.tif"),
        "--library", str((gone if missing == "library" else tmp_path)
                         / "x" / "lib"))
    assert created == []
    assert "is the drive mounted?" in capsys.readouterr().err
    assert not gone.exists()


def test_the_spool_is_counted_where_debug_filing_is_on(tmp_path, monkeypatch):
    """Each pass waits in debug filing's spool twice over until it is filed,
    and the metering probes until close: on its own disk, which the check
    used to leave out."""
    from rps7200 import session

    asked = []
    real = session.check_space

    def recorded(needs, *a, **kw):
        needs = list(needs)
        asked.append({Path(p) for p, _ in needs})
        return real(needs, *a, **kw)

    monkeypatch.setattr(session, "check_space", recorded)
    monkeypatch.setenv("RPS7200_DEBUG", "1")
    _scanner, code = run(tmp_path, monkeypatch)
    assert code == 0
    assert tmp_path / "lib" / ".spool" in asked[0]
    asked.clear()
    monkeypatch.delenv("RPS7200_DEBUG")
    run(tmp_path / "quiet", monkeypatch)
    assert not any(p.name == ".spool" for p in asked[0])


def test_a_run_with_no_library_named_still_files_its_raw_bytes(tmp_path,
                                                               monkeypatch):
    """The default --library, `library` under where the tool runs: its own
    folder is made, as it always was, and the pass filed with its bytes."""
    from rps7200 import library

    patch_scanner(monkeypatch)
    monkeypatch.setattr(FakeScanner, "last_pixels_raw",
                        np.zeros((6, 6, 3), np.uint16), raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["scan.py", "--out", "out.tif",
                                      "--no-shading"])
    assert scan_tool.main() == 0
    (entry,) = (tmp_path / "library").glob("*/scan.json")
    assert library.read_raw(entry.parent) is not None


def test_the_example_its_help_gives_runs_in_a_fresh_checkout(tmp_path,
                                                            monkeypatch):
    """The example put --out two folders deep in `scans/`, which a fresh
    checkout does not have, and only --out's own folder may be made: the
    command the tool's help shows first was refused."""
    import shlex

    (line,) = [line for line in scan_tool.__doc__.splitlines()
               if line.strip().startswith("uv run python tools/scan.py")]
    patch_scanner(monkeypatch)
    monkeypatch.chdir(tmp_path)
    example = shlex.split(line)[4:]
    monkeypatch.setattr(sys, "argv", ["scan.py", *example, "--no-shading"])
    assert scan_tool.main() == 0
    out = Path(example[example.index("--out") + 1])
    assert (tmp_path / out).exists()


def _started_elsewhere(monkeypatch, tmp_path):
    """The tool run in ``tmp_path``, which is not the driver's own folder,
    with the library the driver falls back on as it is: ``./library``."""
    from rps7200 import library, storage

    monkeypatch.setattr(storage, "started_in_checkout", lambda: False)
    monkeypatch.setattr(library, "DEFAULT_ROOT", Path("library"))
    monkeypatch.chdir(tmp_path)


def test_a_default_library_is_made_only_in_the_drivers_own_folder(
        tmp_path, monkeypatch, capsys):
    """GUI2-A2, Stefan's call 2026-10-12: run from anywhere else, the tool
    filed into a new library there that `make verify` never saw. Refused
    before anything opens, naming the folder and the flag; nothing made."""
    _started_elsewhere(monkeypatch, tmp_path)
    created = _refused_before_opening(tmp_path, monkeypatch,
                                      "--out", str(tmp_path / "out.tif"))
    assert created == []
    said = capsys.readouterr().err.strip().splitlines()[-1]
    assert f"{tmp_path / 'library'} is not there" in said
    assert said.endswith("give --library")
    assert os.listdir(tmp_path) == []


def test_a_typed_or_existing_library_is_used_as_before(tmp_path, monkeypatch):
    """A folder a flag names may have its last folder made wherever the tool
    runs; a default one that is there is used."""
    _started_elsewhere(monkeypatch, tmp_path)
    _scanner, code = run(tmp_path / "typed", monkeypatch)
    assert code == 0 and (tmp_path / "typed" / "lib").is_dir()
    (tmp_path / "library").mkdir()
    patch_scanner(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["scan.py", "--out", "here.tif",
                                      "--no-shading"])
    assert scan_tool.main() == 0
    assert list((tmp_path / "library").glob("*/scan.json"))


def test_a_calibration_to_a_default_folder_is_refused_elsewhere(
        tmp_path, monkeypatch, capsys):
    """The reference is cached in calibration/ here unless --reference names
    a place; its folder is a default like the library's, asked only when a
    calibration will run -- and before the film is asked about."""
    _started_elsewhere(monkeypatch, tmp_path)
    monkeypatch.setattr("builtins.input",
                        lambda *a: pytest.fail("the film was asked about"))
    patch_scanner(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["scan.py", "--out",
                                      str(tmp_path / "out.tif"), "--library",
                                      str(tmp_path / "lib")])
    with pytest.raises(SystemExit) as refused:
        scan_tool.main()
    assert refused.value.code == 2
    said = capsys.readouterr().err.strip().splitlines()[-1]
    assert f"{tmp_path / 'calibration'} is not there" in said
    assert "give --reference" in said and "--library" not in said
    assert not (tmp_path / "calibration").exists()


def test_no_library_with_nowhere_named_is_refused_elsewhere(tmp_path,
                                                           monkeypatch,
                                                           capsys):
    """--no-library still keeps a calibration's lines and a failed pass's
    record in the library the driver falls back on, ./library, which
    `DirectScanner.open` would refuse anyway: refused here first, with or
    without a calibration, and named by RPS7200_DEBUG_ROOT."""
    _started_elsewhere(monkeypatch, tmp_path)
    created = _refused_before_opening(tmp_path, monkeypatch, "--out",
                                      str(tmp_path / "out.tif"),
                                      "--no-library")
    assert created == []
    err = capsys.readouterr().err
    assert f"{tmp_path / 'library'} is not there" in err
    assert "set RPS7200_DEBUG_ROOT" in err
    monkeypatch.setenv("RPS7200_DEBUG_ROOT", str(tmp_path / "debug"))
    _scanner, code = run(tmp_path, monkeypatch, "--no-library")
    assert code == 0 and not (tmp_path / "library").exists()


def test_a_path_too_long_for_windows_is_refused_before_opening(
        tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace as Namespace

    from rps7200 import storage

    monkeypatch.setattr(storage, "sys", Namespace(platform="win32"))
    deep = tmp_path / ("d" * 200)
    created = _refused_before_opening(
        tmp_path, monkeypatch, "--out", str(deep / "o.tif"),
        "--library", str(tmp_path / "lib"))
    assert created == []
    assert "past the 259" in capsys.readouterr().err
    assert not deep.exists()


# --- other programs on the same library ---------------------------------------


def test_a_library_being_rewritten_refuses_before_the_scanner_opens(
        tmp_path, monkeypatch, capsys):
    """`compact --write` or a migration rewriting the entries this would
    file beside: refused, naming it, before anything is asked of the
    scanner -- no device made, nothing sent."""
    from conftest import HeldElsewhere, tool_on_device

    from rps7200 import locks

    devices = tool_on_device(scan_tool, monkeypatch)
    monkeypatch.setattr(sys, "argv", [
        "scan.py", "--out", str(tmp_path / "out.tif"),
        "--library", str(tmp_path / "lib"), "--no-shading", "--dpi", "300"])
    with HeldElsewhere(locks.LIBRARY, tmp_path / "lib", exclusive=True,
                       what="tools/library.py compact --write") as other:
        with pytest.raises(SystemExit) as refused:
            scan_tool.main()
    assert refused.value.code == 2
    said = capsys.readouterr().err
    assert "not scanning: tools/library.py compact --write (" in said
    assert f"pid {other.pid}" in said and "Nothing was sent." in said
    assert devices == []
    assert not (tmp_path / "out.tif").exists()


def test_the_library_is_held_while_the_scan_runs_and_let_go_after(
        tmp_path, monkeypatch):
    import os

    from rps7200 import locks

    seen = []

    class Watching(FakeScanner):
        def __init__(self, **kw):
            super().__init__()

        def scan(self, *a, **kw):
            seen.append([h["pid"] for h in locks.holders(
                locks.LIBRARY, tmp_path / "lib")])
            return super().scan(*a, **kw)

    monkeypatch.setattr(scan_tool, "DirectScanner", Watching)
    monkeypatch.setattr(sys, "argv", [
        "scan.py", "--out", str(tmp_path / "out.tif"),
        "--library", str(tmp_path / "lib"), "--no-shading"])
    assert scan_tool.main() == 0
    assert seen == [[os.getpid()]]
    assert locks.holders(locks.LIBRARY, tmp_path / "lib") == []


def test_what_earlier_runs_left_is_said_before_the_device_opens(
        tmp_path, monkeypatch, capsys):
    """As the window says it when it opens (`library.leftovers`): a pass
    nobody filed was found only by whoever reread the log that named it."""
    import numpy as np

    from conftest import tool_on_device
    from rps7200 import library

    lib = tmp_path / "lib"
    plain = library.save(np.zeros((8, 16, 3), np.uint8),
                         {"resolution_dpi": 300, "channels": 3, "width": 16,
                          "height": 8, "depth": 8}, root=lib,
                         compress=False, raw=b"\x00" * 16)
    assert (plain / library.RAW_PLAIN).exists()
    devices = tool_on_device(scan_tool, monkeypatch)
    opened = []
    real = scan_tool.DirectScanner

    class Watched(real):
        def open(self):
            opened.append(capsys.readouterr().out)
            return super().open()

    monkeypatch.setattr(scan_tool, "DirectScanner", Watched)
    monkeypatch.setattr(
        sys, "argv",
        ["scan.py", "--out", str(tmp_path / "out.tif"), "--library", str(lib),
         "--reference", str(tmp_path / "calibration" / "shading.npz"),
         "--dpi", "300", "--film-loaded"])
    assert scan_tool.main() == 0 and devices
    [before] = opened
    assert "left behind -- library: 1 entry still plain" in before


def test_what_earlier_runs_left_in_the_rolls_is_said_too(
        tmp_path, monkeypatch, capsys):
    """The window looks in its rolls folder and `tools/scan_roll.py` in its
    own; this tool looked in none, and said nothing of a Duplicate cut short,
    the rolls' trash or a roll's unfiled passes. The rolls beside the
    library, as `tools/library.py` takes them."""
    from conftest import tool_on_device

    lib = tmp_path / "lib"
    orphan = tmp_path / "rolls" / "roll"
    orphan.mkdir(parents=True)
    (orphan / "approved.json").write_text('{"frames": []}', encoding="utf-8")
    devices = tool_on_device(scan_tool, monkeypatch)
    opened = []
    real = scan_tool.DirectScanner

    class Watched(real):
        def open(self):
            opened.append(capsys.readouterr().out)
            return super().open()

    monkeypatch.setattr(scan_tool, "DirectScanner", Watched)
    monkeypatch.setattr(
        sys, "argv",
        ["scan.py", "--out", str(tmp_path / "out.tif"), "--library", str(lib),
         "--reference", str(tmp_path / "calibration" / "shading.npz"),
         "--dpi", "300", "--film-loaded"])
    assert scan_tool.main() == 0 and devices
    [before] = opened
    assert "rolls: 1 folder of decisions with no roll" in before
