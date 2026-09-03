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
    meta = json.loads((tmp_path / "out.json").read_text())
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
