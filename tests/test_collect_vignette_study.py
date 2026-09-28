"""`tools/collect_vignette_study.py`: what it will copy, and whether it can.

Offline: every entry here is filed by `library.save` into a temporary library,
with its raw bytes, reference and mask, as a real one is.
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pytest

from conftest import load_tool
from rps7200 import library
from rps7200.library import FilmNotes
from rps7200.shading import ShadingReference

collect = load_tool("collect_vignette_study")

TAG = "vignette-study"


def filed(root, *, compress=True, tags=(TAG,), seed=1, **extra):
    """A complete 3-channel entry: raw bytes, reference and mask."""
    width, lines, channels = 8, 6, 3
    rng = np.random.default_rng(seed)
    image = rng.integers(1000, 30000, (lines, width, channels), dtype=np.uint16)
    raw = bytearray()
    for y in range(lines):
        for c in range(channels):
            raw += "RGB"[c].encode() * 2 + image[y, :, c].astype("<u2").tobytes()
    meta = {"resolution_dpi": 600, "channels": channels, "film": "negative",
            "channel_order": list("RGB"), "width": width, "height": lines,
            "bytes_per_line": width * 2, "depth": 16, **extra}
    reference = ShadingReference(
        ref={c: np.linspace(28000, 32000, width) for c in range(3)},
        mean={c: 30000.0 for c in range(3)}, pixels_per_line=width)
    return library.save(
        image, meta, root=root, film=FilmNotes(frame="1"), tags=list(tags),
        reference=reference, ccd_mask=bytes(range(width)), raw=bytes(raw),
        raw_layout={"format": "index", "bytes_per_line": width * 2,
                    "line_stride": width * 2 + 2, "index_header": 2,
                    "width": width, "lines": lines, "channels": channels},
        compress=compress)


def run(monkeypatch, root, out, *argv):
    monkeypatch.setattr(sys, "argv", ["collect_vignette_study.py",
                                      "--root", str(root), "--out", str(out),
                                      *argv])
    return collect.main()


def test_a_plain_entry_is_complete_and_its_bytes_are_checked(tmp_path,
                                                              monkeypatch):
    """P13: an entry the window filed plain, `raw.bin` not yet compacted, was
    refused as "missing raw.bin.gz" and its bytes never checked."""
    entry = filed(tmp_path / "lib", compress=False)
    assert (entry / library.RAW_PLAIN).exists()
    assert collect.inspect(entry, checksum=True)["problems"] == []
    assert run(monkeypatch, tmp_path / "lib", tmp_path / "out") == 0
    assert (tmp_path / "out" / entry.name / library.RAW_PLAIN).read_bytes() \
        == (entry / library.RAW_PLAIN).read_bytes()

    (entry / library.RAW_PLAIN).write_bytes(b"not these bytes")
    assert collect.inspect(entry, checksum=True)["problems"] == [
        "raw.bin does not match its sha256"]


@pytest.mark.parametrize("damage", ["truncated", "corrupt"])
def test_a_damaged_gzip_is_one_refused_entry_not_a_crash(tmp_path, damage):
    """MES-04: EOFError and zlib.error escaped `except OSError`, so one damaged
    entry anywhere in the library ended the whole collection."""
    entry = filed(tmp_path / "lib")
    packed = (entry / library.RAW_FILE).read_bytes()
    if damage == "truncated":
        broken = packed[: len(packed) // 2]
    else:
        body = bytearray(packed)
        for i in range(12, len(body) - 8):
            body[i] ^= 0x5A
        broken = bytes(body)
    (entry / library.RAW_FILE).write_bytes(broken)
    problems = collect.inspect(entry, checksum=True)["problems"]
    assert any(p.startswith("raw.bin.gz") for p in problems), problems


@pytest.mark.parametrize("name", ["ccd_mask.bin", "shading.npz", "scan.tif"])
def test_every_file_is_held_to_its_recorded_digest(tmp_path, name):
    """MES-03: only the raw bytes were checked. A flipped bit in the mask or
    the reference changes the analysis silently, and `scan.tif` is the decode
    gate."""
    entry = filed(tmp_path / "lib")
    body = bytearray((entry / name).read_bytes())
    body[-1] ^= 0x01
    (entry / name).write_bytes(bytes(body))
    assert f"{name} does not match its sha256" in \
        collect.inspect(entry, checksum=True)["problems"]


def test_a_copy_that_does_not_match_its_source_is_not_counted(tmp_path,
                                                               monkeypatch):
    """MES-03: nothing read the copies back."""
    entry = filed(tmp_path / "lib")
    found = collect.inspect(entry, checksum=True)
    real = collect.shutil.copy2

    def flaky(source, target):
        real(source, target)
        if str(source).endswith("ccd_mask.bin"):
            with open(target, "r+b") as fh:
                fh.write(b"\xff")

    monkeypatch.setattr(collect.shutil, "copy2", flaky)
    assert collect.copy([found], tmp_path / "out") == 0
    assert found["problems"] == ["ccd_mask.bin: the copy does not match"]
    assert not (tmp_path / "out" / entry.name / "ccd_mask.bin").exists()


def test_an_entry_whose_copy_failed_is_not_there_to_be_analysed(tmp_path,
                                                                 monkeypatch):
    """MES-03 review: deleting the one bad copy left the rest of the entry,
    `scan.json` included, and `uniformity.select` finds entries by that and
    their tag rather than through the manifest -- so the entry was analysed
    anyway, without its mask. Nor may an earlier run's copy of it stay."""
    uniformity = load_tool("uniformity")
    entry = filed(tmp_path / "lib")
    out = tmp_path / "out"
    good = collect.inspect(entry, checksum=True)
    assert collect.copy([good], out) == 1
    assert uniformity.select(out, TAG) == [out / entry.name]

    real = collect.shutil.copy2

    def flaky(source, target):
        real(source, target)
        if str(source).endswith("ccd_mask.bin"):
            with open(target, "r+b") as fh:
                fh.write(b"\xff")

    monkeypatch.setattr(collect.shutil, "copy2", flaky)
    found = collect.inspect(entry, checksum=True)
    assert collect.copy([found], out) == 0
    assert uniformity.select(out, TAG) == []
    assert not (out / entry.name).exists()
    assert list(out.iterdir()) == []


def test_what_would_stop_the_analysis_is_refused_here(tmp_path):
    """MES-05, MES-A2: an entry with no raw layout, or a record that does not
    parse, went out as re-analysable, and `analyse` stopped on the other
    machine; a record that was not UTF-8 ended this run instead."""
    no_layout = filed(tmp_path / "lib", seed=2)
    record = json.loads((no_layout / "scan.json").read_text(encoding="utf-8"))
    record["raw"]["layout"] = None
    (no_layout / "scan.json").write_text(json.dumps(record), encoding="utf-8")
    assert "no raw layout: the bytes cannot be decoded" in \
        collect.inspect(no_layout, checksum=False)["problems"]

    for body in (b"{ not json", b"\xff\xfe\x00 not utf-8"):
        entry = filed(tmp_path / "lib", seed=3)
        (entry / "scan.json").write_bytes(body)
        assert "scan.json unreadable" in \
            collect.inspect(entry, checksum=False)["problems"]


def test_the_date_is_the_records_and_a_demo_entry_is_not_a_scan(tmp_path):
    """MES-06: `when` was read from keys no record has, and a demo pass --
    another picture moved by a pretend transport -- was sensor evidence."""
    entry = filed(tmp_path / "lib")
    record = json.loads((entry / "scan.json").read_text(encoding="utf-8"))
    assert collect.inspect(entry, checksum=False)["when"] == record["created"]
    demo = filed(tmp_path / "lib", seed=4, demo=True)
    assert "a demo entry, not a scan" in \
        collect.inspect(demo, checksum=False)["problems"]


def test_a_tag_that_matches_nothing_fails(tmp_path, monkeypatch):
    """MES-06: a mistyped tag copied the spread alone and returned 0."""
    filed(tmp_path / "lib", tags=())
    assert run(monkeypatch, tmp_path / "lib", tmp_path / "out",
               "--tag", "vignette_study") == 1
    assert not (tmp_path / "out").exists()

