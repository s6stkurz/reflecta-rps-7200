"""What the driver writes beside the library keeps its values, as an entry does.

An entry's record is serialised losslessly (`library._plain`): an array as its
list, a numpy number as the number, bytes as hex. Three other writers used
`default=str`, which writes a value JSON has no type for as its printed form
-- a numpy float as the string "0.5", a long array as "[0.1 0.2 ... 0.9]",
bytes as "b'...'" -- and nothing failed, so nothing said the value was gone:

- the debug spool's sidecar, which `tools/library.py file-spool` files an
  entry from: the same pass filed at close() kept the value, filed by hand it
  did not;
- an archived calibration's `calibration.json`;
- a roll's manifest, `roll.json`, registration and hold records included.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from conftest import FakeTransport, scanner_at_commands
from rps7200 import library, session
from rps7200.direct import DirectScanner, file_spool


def measured() -> dict:
    """Values a pass's caller adds to its meta, as numpy hands them over."""
    return {
        "dy_px": np.float64(-0.25),
        "score": np.float32(0.75),
        "column": np.int64(412),
        "profile": np.arange(6, dtype=np.float32) / 4,
        "grid": np.eye(2, dtype=np.uint16),
        "reply": b"\x00\x0c\xff",
        "seen": np.bool_(True),
    }


PLAIN = {
    "dy_px": -0.25, "score": 0.75, "column": 412,
    "profile": [0.0, 0.25, 0.5, 0.75, 1.0, 1.25],
    "grid": [[1, 0], [0, 1]], "reply": "000cff", "seen": True,
}


def _without_ids(record: dict) -> dict:
    """A record less what differs between two filings of one pass: where and
    when it was filed, and the provenance of the process that filed it."""
    out = dict(record)
    for key in ("id", "created", "provenance", "_dir", "files"):
        out.pop(key, None)
    out["tags"] = sorted(set(out.get("tags") or ()) - {"from-spool"})
    return out


def test_a_spool_filed_by_hand_keeps_what_close_would_have_filed(monkeypatch,
                                                                 tmp_path):
    """A real pass's meta, from `scan()` on the command-level double, with a
    caller's measures added: filed by close() from memory and by `file_spool`
    from its sidecar, the two entries say the same."""
    scanner, _device = scanner_at_commands(monkeypatch)
    _image, meta = scanner.scan(resolution=300, infrared=False, shading=False,
                                keep_raw=True)
    raw_pixels = scanner.last_pixels_raw
    capture = scanner.capture_record()
    meta = dict(meta, aim=measured())

    def spooled(root: Path) -> DirectScanner:
        s = DirectScanner(transport=FakeTransport(), debug=True)
        s._own_transport = False
        s.debug_root = root
        s._debug_capture(raw_pixels, meta, capture=capture)
        return s

    closed = spooled(tmp_path / "closed")
    closed.close()                                     # from memory
    left = spooled(tmp_path / "by hand")
    spool = left._debug_pending[0]["image_path"].parent
    [entry] = file_spool(spool, tmp_path / "by hand", say=lambda m: None)

    [from_memory] = library.entries(tmp_path / "closed")
    [from_sidecar] = library.entries(tmp_path / "by hand")
    assert from_sidecar["extra"]["aim"] == PLAIN
    assert from_memory["extra"]["aim"] == PLAIN
    assert _without_ids(from_sidecar) == _without_ids(from_memory)
    # And the commands the pass sent, the bytes of each reply among them.
    assert from_sidecar["extra"]["commands"] == meta["commands"]
    assert library.reconstruct(entry)[1].startswith("identical")


def test_a_calibration_record_keeps_its_values(tmp_path):
    s = DirectScanner(transport=FakeTransport())
    folder = s.archive_calibration({
        "data": b"\x01" * 8,
        "resolution": np.int64(3600),
        "pixels_per_line": np.int32(5172),
        "duration_s": np.float64(212.5),
        "failed": {"stage": "during the read", "sense": b"\x70\x00\x06"},
        "commands": {"sent": [], "image_reads": {"reads": np.int64(40)}},
    }, tmp_path)
    record = json.loads((folder / library.CALIBRATION_RECORD).read_text(
        encoding="utf-8"))
    assert record["resolution"] == 3600
    assert record["pixels_per_line"] == 5172
    assert record["duration_s"] == 212.5
    assert record["failed"]["sense"] == "700006"
    assert record["commands"]["image_reads"]["reads"] == 40


def test_a_roll_manifest_keeps_its_values(tmp_path):
    """A frame's registration and hold records are numpy's measures, and the
    roll read back strings where it had measured numbers."""
    path = tmp_path / "roll.json"
    session.write_manifest(path, {"frames": [
        {"number": np.int64(1), "registration": measured(), "done": True}]})
    frame = session.read_manifest(path)["frames"][0]
    assert frame == {"number": 1, "registration": PLAIN, "done": True}
    manifest = session.RollManifest(path, {"frames": [
        {"number": 2, "hold": {"residual_units": np.float64(2.84)}}]})
    manifest.write()
    assert (session.read_manifest(path)["frames"][0]["hold"]
            == {"residual_units": 2.84})
