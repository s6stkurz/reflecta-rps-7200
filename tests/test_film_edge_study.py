"""`tools/film_edge_study.py`: which stored passes make its corpus, and how.

Offline: every entry is filed by `library.save` into a temporary tree.
"""
from __future__ import annotations

import numpy as np
import pytest

from rps7200 import framing, library, tiff
from rps7200.library import FilmNotes
from rps7200.shading import ShadingReference
# Imported, not `load_tool`ed: its dataclasses need their module registered.
from tools import film_edge_study as study

W = 428


def probe(root, *, round_no=1, channels=3, demo=False, seed=1):
    """A filed metering probe at the prescan's width, with its reference."""
    rng = np.random.default_rng(seed)
    image = rng.integers(20000, 30000, (12, W, channels), dtype=np.uint16)
    meta = {"resolution_dpi": 300, "channels": channels, "film": "negative",
            "channel_order": list("RGBI"[:channels]), "width": W,
            "height": 12, "depth": 16,
            "pass_role": {"kind": "metering probe", "round": round_no}}
    if demo:
        meta["demo"] = True
    reference = ShadingReference(
        ref={c: np.linspace(20000, 40000, W) for c in range(3)},
        mean={c: 30000.0 for c in range(3)}, pixels_per_line=W)
    return library.save(image, meta, root=root, film=FilmNotes(),
                        reference=reference), image


def test_a_probe_is_the_round_one_rgb_pass_corrected(tmp_path):
    """MES-01: the corpus took every 16-bit 428-column scan.tif -- the raw
    decode, with the lamp falloff shading removes, of RGBI passes and every
    metering round -- as "what metering_slice actually sees"."""
    lib = tmp_path / "library"
    first, raw = probe(lib)
    probe(lib, round_no=2, seed=2)
    probe(lib, channels=4, seed=3)
    probe(lib, demo=True, seed=4)
    corpus = [f for f in study.load_corpus(tmp_path) if f.kind == "probe"]
    assert [f.path for f in corpus] == [str(first / "scan.tif")]
    assert np.array_equal(corpus[0].image, library.corrected(first)[0])
    assert not np.array_equal(corpus[0].image, raw)


def test_a_file_cut_short_is_skipped_and_named(tmp_path):
    """MES-08: one truncated TIFF ended the whole study with a traceback, and
    a prescan.tif in an entry still being written was read."""
    good = tmp_path / "rolls" / "a"
    good.mkdir(parents=True)
    tiff.write(str(good / "prescan01.tif"),
               np.zeros((8, 16, 3), np.uint8), resolution=300)
    (good / "prescan02.tif").write_bytes(
        (good / "prescan01.tif").read_bytes()[:40])
    unfinished = tmp_path / "library" / "x"
    unfinished.mkdir(parents=True)
    (unfinished / "INCOMPLETE").write_text("", encoding="utf-8")
    tiff.write(str(unfinished / "prescan.tif"),
               np.zeros((8, 16, 3), np.uint8), resolution=300)
    skipped: list[str] = []
    corpus = study.load_corpus(tmp_path, skipped)
    assert [f.path for f in corpus] == [str(good / "prescan01.tif")]
    assert len(skipped) == 1 and "prescan02.tif" in skipped[0]


def test_the_crop_is_insetting_as_production_does():
    """MES-01: an abstaining rule read 0.0% against the whole pass, where
    production still crops to 81% of it; the baseline is production's own
    abstaining crop, and every crop is stepped in as `metering_slice` does."""
    h, w = 100, 200
    sl = study.inset((slice(None), slice(None)), h, w)
    pad_y = int(round(h * framing.METERING_INSET))
    pad_x = int(round(w * framing.METERING_INSET))
    assert sl == (slice(pad_y, h - pad_y), slice(pad_x, w - pad_x))
    image = np.linspace(0, 1000, h * w * 3).reshape(h, w, 3)
    assert study.meter_delta(image, sl, sl) == [0.0, 0.0, 0.0]


def test_the_shortfall_is_counted_in_columns_of_the_pass(monkeypatch):
    """MES-09: it went through the aperture over the width in millimetres,
    the expression `framing.units_per_column` replaced as 0.7% wrong."""
    image = np.full((50, W, 3), 100.0)
    rule = study.Rule("stub")
    monkeypatch.setattr(study.Rule, "span", lambda self, profile: (
        (10, W - 11) if profile.size == W else None))
    got = study.assess(study.Frame("x", image, "roll"), rule)
    assert got["shortfall_units"] == pytest.approx(
        round(20 * framing.units_per_column(W), 2))


def test_the_percentiles_are_the_ones_in_force():
    """MES-09: both were retyped, and would have stayed put if production's
    moved."""
    import inspect

    from rps7200.direct import DirectScanner

    assert study.CLEAR_PERCENTILE is framing.CLEAR_PERCENTILE
    assert study.METER_PERCENTILE == inspect.signature(
        DirectScanner.auto_exposure).parameters["percentile"].default
