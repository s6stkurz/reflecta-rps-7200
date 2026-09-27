"""The driver's own passes, run to their last line on a device that answers.

Every entry the scanner files is built from what `DirectScanner.scan()`
hands back: the meta it assembles, the raw pixels it keeps aside, the bytes
`read_planes` kept, the command log, the CCD mask and the reference. Until
`conftest.DeviceAtCommands` no test could run that code to the end -- the
transport double answered GET PARAMETERS with nothing -- so the pieces were
pinned by grepping the source for `raw_pixels = image`, and the metas that
reached the library were typed by hand in the tests.

So these hold what the driver filed against what the device sent, byte for
byte: the raw bytes are the bytes, the raw pixels decode from them, the entry
re-decodes to the same pixels, and its correction is the picture the caller
was handed. The device is a double; everything above `Transport.command` is
the driver's own code.
"""
import json
import time
from pathlib import Path

import numpy as np
import pytest

from conftest import scanner_at_commands
from rps7200 import library
from rps7200.direct import DirectScanner, ScanParameters
from rps7200.shading import ShadingReference, calculate_shading


def _calibrated(monkeypatch, **kw):
    scanner, device = scanner_at_commands(monkeypatch, **kw)
    assert scanner.calibrate_shading()["reference"] is not None
    return scanner, device


def _layout_params(layout):
    return ScanParameters(width=layout["width"], lines=layout["lines"],
                          bytes_per_line=layout["bytes_per_line"],
                          filter_offset1=0, filter_offset2=0,
                          available_lines=0)


def _json(value):
    """A value as it reads back from `scan.json`."""
    return json.loads(json.dumps(value, default=str))


# -- one pass, and what it hands on -------------------------------------------


def test_a_pass_keeps_exactly_the_bytes_and_pixels_the_device_sent(monkeypatch):
    scanner, device = _calibrated(monkeypatch)
    image, meta = scanner.scan(resolution=300, infrared=True, keep_raw=True)
    sent = device.passes[-1]

    assert scanner.last_raw == sent["blob"]
    assert np.array_equal(scanner.last_pixels_raw, sent["pixels"])
    layout = scanner.last_raw_layout
    assert layout["lines_received"] == sent["lines"] * sent["channels"]
    decoded, _ = DirectScanner.decode_index(scanner.last_raw,
                                            _layout_params(layout), 4)
    assert np.array_equal(decoded, scanner.last_pixels_raw)
    # The caller gets the corrected picture and the raw one stays raw: the
    # correction must not have been done in place on the array kept aside.
    assert meta["shading"] is not None
    assert not np.array_equal(image, scanner.last_pixels_raw)
    assert np.array_equal(scanner.last_pixels_raw, sent["pixels"])
    assert scanner.capture_record()["ccd_mask"] == sent["masks"][-1]


def test_a_filed_pass_reconstructs_and_corrects_to_what_the_caller_was_given(
        monkeypatch, tmp_path):
    """The bargain end to end: raw in the entry, the bytes beside it re-decode
    to it, and `library.corrected` -- what every view and export uses -- is
    the image `scan()` returned."""
    scanner, _ = _calibrated(monkeypatch)
    image, meta = scanner.scan(resolution=300, infrared=True, keep_raw=True)
    entry = library.save(scanner.last_pixels_raw, meta, root=tmp_path,
                         **scanner.capture_record())

    assert library.reconstruct(entry)[1].startswith("identical")
    corrected, _ = library.corrected(entry)
    assert np.array_equal(corrected, image)
    record = json.loads((entry / "scan.json").read_text(encoding="utf-8"))
    assert record["image"]["corrections_applied"] == []


def test_everything_a_pass_says_about_itself_reaches_its_entry(monkeypatch,
                                                                tmp_path):
    """`test_library` checks this over a meta typed by hand, which can only
    keep up with `scan()` by someone remembering to. This one is `scan()`'s
    own: every key it returns is in the record, under `scan`, `extra` or the
    section `library.save` gives it, with the value it had."""
    scanner, _ = _calibrated(monkeypatch)
    _, meta = scanner.scan(resolution=300, infrared=False, keep_raw=True)
    entry = library.save(scanner.last_pixels_raw, meta, root=tmp_path,
                         **scanner.capture_record())
    record = json.loads((entry / "scan.json").read_text(encoding="utf-8"))

    placed = {"exposure": record["device_settings"]["exposure"],
              "gain": record["device_settings"]["gain"],
              "offset": record["device_settings"]["offset"],
              "shading": record["calibration"]["report"],
              "shading_skipped": record["calibration"]["skipped"],
              "metering": record["metering"],
              "registration": record["registration"]}
    for key, value in meta.items():
        if key in library.SCAN_FIELDS:
            got = record["scan"][key]
        elif key in placed:
            got = placed[key]
        else:
            assert key in record["extra"], f"{key} was dropped at filing"
            got = record["extra"][key]
        assert got == _json(value), key

    commands = record["extra"]["commands"]
    sent = [c["cdb"][:2] for c in commands["sent"]]
    for opcode in ("12", "15", "1b", "0f", "18"):   # frame, mode, start, params, mask
        if opcode == "12":
            continue                                 # INQUIRY is outside the pass
        assert opcode in sent, f"{opcode} missing from the pass's own record"
    assert commands["image_reads"]["bytes"] == len(scanner.last_raw)
    assert record["extra"]["shading_origin"]["action"] == "calibrated"


def test_a_pass_read_bottom_up_is_kept_as_sent_and_filed_upright(monkeypatch,
                                                                 tmp_path):
    """Pass 1 -- the one after the calibration -- comes back B first."""
    scanner, device = _calibrated(monkeypatch, upward={1})
    image, meta = scanner.scan(resolution=300, infrared=False, keep_raw=True)
    sent = device.passes[-1]

    assert sent["upward"]
    assert scanner.last_raw == sent["blob"], "the bytes are never turned"
    assert np.array_equal(scanner.last_pixels_raw, sent["pixels"])
    assert meta["read_direction"]["turned"] is True
    entry = library.save(scanner.last_pixels_raw, meta, root=tmp_path,
                         **scanner.capture_record())
    assert library.reconstruct(entry)[1].startswith("identical")
    assert np.array_equal(library.corrected(entry)[0], image)


def test_a_prescan_is_filed_with_its_own_eight_bit_pass(monkeypatch, tmp_path):
    scanner, device = _calibrated(monkeypatch)
    image, params = scanner.prescan(resolution=300, keep_raw=True)
    meta = scanner.last_scan_meta

    assert image.dtype == np.uint8 and image.shape[2] == 3
    assert meta["depth"] == 8 and meta["height"] == params.lines
    assert np.array_equal(scanner.last_pixels_raw, device.passes[-1]["pixels"])
    entry = library.save(scanner.last_pixels_raw, meta, root=tmp_path,
                         **scanner.capture_record())
    assert library.reconstruct(entry)[1].startswith("identical")
    assert np.array_equal(library.corrected(entry)[0], image)


def test_a_7200_dpi_pass_says_which_rows_its_realignment_trimmed(monkeypatch,
                                                                 tmp_path):
    """The one host transform `scan.tif` carries beyond the decode. `height`
    has to be the realigned one and `stagger_realigned` the rows it lost, or
    the session's shape guard drops the bytes and every such entry reads
    "decode CHANGED". Raw on purpose: no reference covers 7200 dpi."""
    scanner, device = scanner_at_commands(monkeypatch)
    frame = (0, 0, 63, 39)
    image, meta = scanner.scan(resolution=7200, infrared=False, shading=False,
                               frame=frame, keep_raw=True)
    lines = device.passes[-1]["lines"]
    stagger = DirectScanner.NATIVE_COLUMN_STAGGER_LINES

    assert meta["stagger_realigned"] == stagger
    assert meta["height"] == image.shape[0] == lines - stagger
    entry = library.save(scanner.last_pixels_raw, meta, root=tmp_path,
                         **scanner.capture_record())
    assert library.reconstruct(entry)[1].startswith("identical")


# -- debug filing -------------------------------------------------------------


def test_debug_filing_files_the_bytes_that_reconstruct_the_pass(monkeypatch,
                                                                tmp_path):
    """The only record of a metering probe or a hold prescan is what debug
    filing writes after close(). No test had ever flushed a spool that held
    bytes: the doubles spooled none, and two asserts about it ended in
    ``or True``. Here the pass keeps no bytes of its own asking -- debug
    keeps them anyway -- and the entry must be that pass exactly."""
    monkeypatch.setenv(DirectScanner.DEBUG_ROOT_ENV, str(tmp_path))
    scanner, device = _calibrated(monkeypatch, debug=True)
    image, _ = scanner.scan(resolution=300, infrared=True, keep_raw=False)
    sent = device.passes[-1]
    scanner.close()

    entries = library.entries(tmp_path)
    assert len(entries) == 1, "one pass, one entry"
    entry = tmp_path / entries[0]["id"]
    assert library.read_raw(entry) == sent["blob"]
    assert (entry / "ccd_mask.bin").read_bytes() == sent["masks"][-1]
    assert (entry / "shading.npz").exists()
    assert library.reconstruct(entry)[1].startswith("identical")
    assert np.array_equal(library.corrected(entry)[0], image)
    assert entries[0]["raw"]["layout"]["lines_received"] == \
        sent["lines"] * sent["channels"]


# -- the calibration's own bytes ----------------------------------------------


def test_a_kept_calibration_reduces_again_to_the_reference_it_archived(
        monkeypatch, tmp_path):
    """`data.bin` is kept because `shading.npz` is a reduction of it, and a
    reduction cannot be redone once its input is gone. That promise was
    tested with bytes unrelated to the reference beside them; these are the
    calibration's own, and today's reduction of them must be the archive's."""
    scanner, device = scanner_at_commands(monkeypatch)
    cache = tmp_path / "calibration" / "shading.npz"
    result = scanner.ensure_shading(cache)
    assert result["action"] == "calibrated"
    archive = Path(scanner._shading_origin["archive"])

    data = (archive / "data.bin").read_bytes()
    assert data == device.passes[0]["blob"]
    record = json.loads((archive / "calibration.json").read_text(encoding="utf-8"))
    width = record["pixels_per_line"]
    assert record["bytes_per_line"] == 2 * width + record["index_header"]
    assert len(data) % record["bytes_per_line"] == 0
    assert (archive / "ccd_mask.bin").read_bytes() == device.passes[0]["masks"][-1]

    again = calculate_shading(data, width)
    kept = ShadingReference.load(archive / "shading.npz")
    assert again is not None and again.channels == kept.channels
    for c in kept.channels:
        assert np.array_equal(again.ref[c], kept.ref[c])
        assert np.array_equal(again.dark[c], kept.dark[c])

    # And the pass it corrects says which archive that was.
    _, meta = scanner.scan(resolution=300, infrared=False)
    assert meta["shading_origin"]["archive"] == str(archive)


@pytest.mark.xfail(strict=True, reason=(
    "T-08: a pass corrected by a reused reference records only the cache "
    "path, which the next calibration overwrites, and no link to the archived "
    "bytes that reference was reduced from"))
def test_a_reused_reference_still_names_the_calibration_it_came_from(
        monkeypatch, tmp_path):
    cache = tmp_path / "calibration" / "shading.npz"
    first, _ = scanner_at_commands(monkeypatch)
    first.ensure_shading(cache)
    archive = first._shading_origin["archive"]

    later, _ = scanner_at_commands(monkeypatch)
    assert later.ensure_shading(cache, reuse=True)["action"] == "loaded"
    _, meta = later.scan(resolution=300, infrared=False)
    assert meta["shading_origin"].get("archive") == archive


# -- through the session ------------------------------------------------------


def _session_to_close(scanner, tmp_path, jobs, timeout=30.0):
    from rps7200.session import ScanSession

    session = ScanSession(root=str(tmp_path / "library"),
                          rolls=str(tmp_path / "rolls"),
                          open_scanner=lambda: scanner, verbose=False)
    session.start()
    for job in jobs:
        session.submit(job)
    session.shutdown()
    events = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        batch = session.poll()
        events += batch
        if any(e.kind == "closed" for e in batch):
            break
        time.sleep(0.01)
    session.join(timeout=5)
    assert any(e.kind == "closed" for e in events), "the session never closed"
    return session, events


def test_the_session_files_every_real_pass_once_and_each_reconstructs(
        monkeypatch, tmp_path):
    """The window's jobs on the driver itself, debug filing on as CLAUDE.md
    asks. What the session keeps -- the prescan and the scans -- is filed by
    the session, raw, re-decoding exactly and correcting to what was shown;
    what it does not keep -- the metering probes -- is filed by debug filing;
    and no pass is filed twice. Until now the session was only ever driven
    with stand-ins whose bytes could not decode to the pixels they filed."""
    from rps7200.session import Calibrate, Prescan, Scan

    debug_root = tmp_path / "debug"
    monkeypatch.setenv(DirectScanner.DEBUG_ROOT_ENV, str(debug_root))
    scanner, device = scanner_at_commands(monkeypatch, debug=True, upward={2})
    jobs = [Calibrate(reference=str(tmp_path / "calibration" / "shading.npz")),
            Prescan(resolution=300),
            Scan(resolution=300, infrared=True, auto_exposure=False),
            Scan(resolution=300, infrared=False, auto_exposure=True)]
    _, events = _session_to_close(scanner, tmp_path, jobs)

    assert not [e.text for e in events if e.kind == "failed"]
    shown = {e.result.seq: e.result.image for e in events if e.kind == "result"}
    filed = {e.done: e.text for e in events if e.kind == "filed"}
    assert len(filed) == 3
    kept_blobs = set()
    for seq, path in filed.items():
        assert library.reconstruct(path)[1].startswith("identical"), path
        assert np.array_equal(library.corrected(path)[0], shown[seq]), path
        kept_blobs.add(library.read_raw(path))

    probes = [p for p in device.passes if not p["calibrate"]
              and p["blob"] not in kept_blobs]
    assert probes, "the metered scan ran no probe"
    debug = library.entries(debug_root)
    assert len(debug) == len(probes), "a pass filed twice, or not at all"
    for record in debug:
        path = debug_root / record["id"]
        assert library.read_raw(path) not in kept_blobs
        assert library.reconstruct(path)[1].startswith("identical"), path
