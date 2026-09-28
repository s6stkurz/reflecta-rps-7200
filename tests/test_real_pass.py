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
from rps7200.protocol import SUB_SCAN_FRAME
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
    for opcode in ("15", "1b", "0f", "18"):         # mode, start, params, mask
        assert opcode in sent, f"{opcode} missing from the pass's own record"
    # The frame is a WRITE (0x0a) carrying sub-command 0x12, not an opcode
    # of its own -- 0x12 as an opcode is INQUIRY, which is outside the pass
    # -- and every setting is a WRITE, so an opcode alone would pass on the
    # exposure write with no frame anywhere. The payload says which it is.
    frame = SUB_SCAN_FRAME.to_bytes(2, "little").hex()
    assert any(c["cdb"][:2] == "0a" and c.get("out", "").startswith(frame)
               for c in commands["sent"]), "the scan frame is missing"
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


def test_a_calibration_archive_cut_short_says_so(monkeypatch, tmp_path):
    """Killed or out of space part way, `calibration/<UTC>/` held data.bin
    and no record of its width or line stride, so the bytes the archive
    exists for cannot be reduced again -- and nothing says the folder is
    unfinished. `ensure_shading` carries on (the reference is in hand), which
    is right, and makes the half-written folder the only trace."""
    scanner, _ = scanner_at_commands(monkeypatch)

    def full(*a, **k):
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as disk:
        disk.setattr(ShadingReference, "save", full)
        scanner.ensure_shading(tmp_path / "calibration" / "shading.npz")

    folders = [p for p in (tmp_path / "calibration").iterdir() if p.is_dir()]
    assert folders, "nothing was archived at all"
    for folder in folders:
        assert ((folder / "calibration.json").exists()
                or (folder / library.INCOMPLETE).exists()), (
            f"{folder.name} holds {sorted(p.name for p in folder.iterdir())} "
            "and does not say it is unfinished")


def test_a_reused_reference_still_names_the_calibration_it_came_from(
        monkeypatch, tmp_path):
    """T-08: a pass corrected by a reused reference recorded only the cache
    path, which the next calibration overwrites, and no link to the archived
    bytes that reference was reduced from -- so `verify` never checked them."""
    cache = tmp_path / "calibration" / "shading.npz"
    first, _ = scanner_at_commands(monkeypatch)
    first.ensure_shading(cache)
    archive = first._shading_origin["archive"]
    data = (Path(archive) / "data.bin").read_bytes()

    later, _ = scanner_at_commands(monkeypatch)
    assert later.ensure_shading(cache, reuse=True)["action"] == "loaded"
    _, meta = later.scan(resolution=300, infrared=False)
    assert meta["shading_origin"].get("archive") == archive
    import hashlib
    assert (meta["shading_origin"].get("archive_sha256")
            == hashlib.sha256(data).hexdigest())


def test_a_cache_replaced_behind_its_sidecar_names_no_calibration(
        monkeypatch, tmp_path):
    """The link is trusted only for the bytes it was written with: a cache
    copied over by hand, or by an older driver that knew nothing of the
    sidecar, would otherwise name a calibration it was never reduced from."""
    cache = tmp_path / "calibration" / "shading.npz"
    first, _ = scanner_at_commands(monkeypatch)
    first.ensure_shading(cache)
    other = tmp_path / "other.npz"
    reference = first._shading
    reference.mean[reference.channels[0]] += 1.0
    reference.save(other, compress=False)
    cache.write_bytes(other.read_bytes())

    later, _ = scanner_at_commands(monkeypatch)
    later.ensure_shading(cache, reuse=True)
    assert "archive" not in later._shading_origin


def test_a_calibration_whose_archive_raises_is_still_adopted_and_cached(
        monkeypatch, tmp_path):
    """Archiving is caught whatever it raises: a record that would not
    serialise threw away a successful calibration, neither cached nor in
    force, where a full disk (an OSError) did not."""
    scanner, _ = scanner_at_commands(monkeypatch)

    def unserialisable(*a, **k):
        raise TypeError("Object of type bytes is not JSON serializable")

    monkeypatch.setattr(DirectScanner, "archive_calibration", unserialisable)
    cache = tmp_path / "calibration" / "shading.npz"
    result = scanner.ensure_shading(cache)
    assert result["action"] == "calibrated"
    assert scanner._shading is result["reference"] is not None
    assert cache.exists()


def test_a_metered_roll_frame_is_filed_as_metered_with_its_metering(
        monkeypatch):
    """A roll meters each frame and then scans it at those scales. The frame
    was filed `exposure_metered: false` with no metering block -- a
    commanded exposure, to `signature` -- and blue's headroom, the rounds
    and what was limited went nowhere (P09 rem. 1, RDM-A1)."""
    scanner, _ = _calibrated(monkeypatch)
    frame = list(scanner.scan_roll(frames=1, resolution=300, infrared=False,
                                   meter="each"))[0]
    assert frame.error is None, frame.error
    assert frame.meta["exposure_metered"] is True
    assert frame.meta["metering"] == _json(scanner.last_metering)
    assert frame.meta["metering"]["region"] is not None


def test_a_metered_bracket_carries_the_metering_its_ladder_came_from(
        monkeypatch):
    """Each rung's exposure is the one asked for -- that is what tells the
    rungs apart -- so it stays commanded; but what it was asked relative to
    was lost from every pass."""
    scanner, _ = _calibrated(monkeypatch)
    _, _, metas = scanner.scan_bracket(passes=3, resolution=300)
    assert [m["exposure_metered"] for m in metas] == [False] * 3
    assert all(m["metering"] == _json(scanner.last_metering) for m in metas)


def test_a_stop_asked_during_metering_is_taken_before_the_pass(monkeypatch):
    """Metering's probes run inside `scan`, and the pass followed them with
    no check between: a Ctrl-C through the probes cost the whole pass. Now
    the pass asks once more and raises before its first command -- nothing
    abandoned, the device not suspect."""
    from rps7200.direct import StoppedBeforePass

    scanner, device = _calibrated(monkeypatch)
    before = len(device.passes)
    with pytest.raises(StoppedBeforePass, match="after metering"):
        scanner.scan(resolution=300, infrared=False, auto_exposure=True,
                     should_stop=lambda: len(device.passes) > before)
    probes = len(device.passes) - before
    assert probes == len(scanner.last_metering["rounds"]), \
        "a pass beyond the probes was started"
    assert scanner.suspect is None
    # And a pass not stopped runs as before.
    scanner.scan(resolution=300, infrared=False, should_stop=lambda: False)
    assert len(device.passes) == before + probes + 1


# -- through the session ------------------------------------------------------


def _session(scanner, tmp_path, **kw):
    from rps7200.session import ScanSession

    return ScanSession(root=str(tmp_path / "library"),
                       rolls=str(tmp_path / "rolls"),
                       open_scanner=lambda: scanner, verbose=False, **kw)


def _events_until(session, done, timeout=30.0):
    """Every event up to and including the first batch ``done`` accepts."""
    events = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        batch = session.poll()
        events += batch
        if done(batch):
            return events
        time.sleep(0.01)
    raise AssertionError(f"waited {timeout} s; the last events were "
                         f"{[(e.kind, e.text) for e in events[-5:]]}")


def _closed(batch):
    return any(e.kind == "closed" for e in batch)


def _session_to_close(scanner, tmp_path, jobs, timeout=30.0, **kw):
    session = _session(scanner, tmp_path, **kw)
    session.start()
    for job in jobs:
        session.submit(job)
    session.shutdown()
    events = _events_until(session, _closed, timeout)
    session.join(timeout=5)
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


# -- when filing fails, or the session is abandoned ---------------------------


def _calibration(tmp_path):
    from rps7200.session import Calibrate

    return Calibrate(reference=str(tmp_path / "calibration" / "shading.npz"))


def _spool_where_a_test_can_look(tmp_path, monkeypatch):
    """Debug spools go to the system's temporary folder; here, one of ours."""
    import tempfile

    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    return temp


def _library_refuses(monkeypatch, refused_root):
    """`library.save` into ``refused_root`` fails as a full disk does; any
    other root -- debug filing's -- still works."""
    real = library.save

    def save(image, meta, **kw):
        if Path(kw.get("root") or "") == Path(refused_root):
            raise OSError(28, "No space left on device")
        return real(image, meta, **kw)

    monkeypatch.setattr(library, "save", save)


def _surviving(blob, root):
    """Whether ``blob`` is still on disk under ``root``: as a filed entry's
    raw bytes, or as a file holding exactly those bytes."""
    import gzip

    for path in Path(root).rglob("*"):
        if not path.is_file():
            continue
        if path.name.endswith(".gz"):
            with gzip.open(path, "rb") as fh:
                if fh.read() == blob:
                    return True
        elif path.stat().st_size == len(blob) and path.read_bytes() == blob:
            return True
    return False


def test_a_scan_the_library_refused_is_still_delivered(monkeypatch, tmp_path):
    """The library on a drive that has filled, the output folder on another
    that has not: the operator's copy is the one thing that could still be
    written, and the scan's only chance of surviving the session."""
    from rps7200.session import Scan

    scanner, _ = scanner_at_commands(monkeypatch)
    _library_refuses(monkeypatch, tmp_path / "library")
    out = tmp_path / "out"
    _session_to_close(scanner, tmp_path,
                      [_calibration(tmp_path),
                       Scan(resolution=300, infrared=False,
                            auto_exposure=False)],
                      out_dir=str(out))
    assert list(out.glob("*.tif")), "the scan exists nowhere"


def test_a_pass_the_library_refused_keeps_its_raw_bytes(monkeypatch, tmp_path):
    """With RPS7200_DEBUG=1 the spool is an independent copy of every pass,
    and it was deleted at close for the one pass whose filing then failed."""
    from rps7200.session import Scan

    _spool_where_a_test_can_look(tmp_path, monkeypatch)
    monkeypatch.setenv(DirectScanner.DEBUG_ROOT_ENV, str(tmp_path / "debug"))
    scanner, device = scanner_at_commands(monkeypatch, debug=True)
    _library_refuses(monkeypatch, tmp_path / "library")
    _session_to_close(scanner, tmp_path,
                      [_calibration(tmp_path),
                       Scan(resolution=300, infrared=False,
                            auto_exposure=False)])
    assert _surviving(device.passes[-1]["blob"], tmp_path), (
        "the pass's raw bytes are gone from every place they were")


def test_what_debug_filing_spooled_before_a_force_abort_is_not_lost(
        monkeypatch, tmp_path):
    """The metering probes of a session that later had to be aborted are the
    passes an investigation of that wedge would want first."""
    from rps7200.session import Scan

    temp = _spool_where_a_test_can_look(tmp_path, monkeypatch)
    debug_root = tmp_path / "debug"
    monkeypatch.setenv(DirectScanner.DEBUG_ROOT_ENV, str(debug_root))
    scanner, device = scanner_at_commands(monkeypatch, debug=True)
    session = _session(scanner, tmp_path)
    session.start()
    session.submit(_calibration(tmp_path))
    session.submit(Scan(resolution=300, infrared=False, auto_exposure=True))
    events = []
    finished = 0
    while finished < 2:
        batch = _events_until(session, lambda b: any(
            e.kind in ("finished", "failed") for e in b))
        events += batch
        finished += sum(e.kind in ("finished", "failed") for e in batch)
    session.force_abort()
    session.shutdown()
    events += _events_until(session, _closed)
    session.join(timeout=5)

    probes = [p for p in device.passes if not p["calibrate"]][:-1]
    assert probes, "the metered scan ran no probe"
    filed = ({library.read_raw(debug_root / r["id"])
              for r in library.entries(debug_root)}
             if debug_root.exists() else set())
    said = " ".join(e.text or "" for e in events if e.kind == "log")
    for probe in probes:
        if probe["blob"] in filed:
            continue
        spooled = [p for p in temp.rglob("*-raw.bin")
                   if p.read_bytes() == probe["blob"]]
        assert spooled and str(spooled[0].parent) in said, (
            "a probe spooled before the abort is neither filed nor named")


def test_a_force_abort_mid_read_leaves_the_scanner_suspect(monkeypatch,
                                                           tmp_path):
    """The abort the window offers when a pass has hung: the transport is
    closed under the read. That pass stops before its last line, so the
    device may still be mid-scan, and nothing may drive it again this
    session -- only the idle case was tested."""
    from rps7200.session import Scan

    holder = {}

    def abort_during_the_scan(device, n):
        if not device.passes[-1]["calibrate"] and "fired" not in holder:
            holder["fired"] = n
            holder["session"].force_abort()

    scanner, device = scanner_at_commands(monkeypatch,
                                          on_read=abort_during_the_scan)
    session = _session(scanner, tmp_path)
    holder["session"] = session
    session.start()
    session.submit(_calibration(tmp_path))
    session.submit(Scan(resolution=300, infrared=False, auto_exposure=False))
    session.shutdown()
    events = _events_until(session, _closed)
    session.join(timeout=5)

    assert "fired" in holder, "the abort never happened mid-read"
    assert session.dead is True
    assert scanner.suspect is not None
    assert any(e.kind == "failed" for e in events)
    assert library.entries(tmp_path / "library") == [], \
        "a pass abandoned mid-read was filed as if whole"


def test_a_stop_while_the_driver_passes_unchosen_frames_moves_nothing_more(
        monkeypatch, tmp_path):
    """Frames 1 and 4 chosen: after frame 1 the driver advances past 2 and 3
    on its own, yielding nothing, so the session's check between frames
    never runs there. Stop pressed during that walk has to reach the driver
    itself -- `should_stop`, checked before each advance -- or the film goes
    on to frame 4 and scans it. The hand-off was held to it by a grep for
    ``should_stop=self._stop.is_set``, which a call moved into a branch not
    taken would still have passed."""
    from rps7200.protocol import SCSI_SCAN, SCSI_SLIDE, SLIDE_NEXT
    from rps7200.session import Roll

    holder = {}

    def stop_on_the_first_skip(device, opcode, data):
        scanned = [p for p in device.passes if p["channels"] == 4]
        if (opcode == SCSI_SLIDE and data[:1] == bytes([SLIDE_NEXT])
                and scanned and "at" not in holder):
            holder["at"] = len(device.sent)
            holder["session"].request_stop()

    scanner, device = scanner_at_commands(monkeypatch,
                                          on_command=stop_on_the_first_skip)
    session = _session(scanner, tmp_path)
    holder["session"] = session
    session.start()
    session.submit(_calibration(tmp_path))
    session.submit(Roll(frames=4, resolution=300, infrared=True, name="r",
                        only=(1, 4)))
    session.shutdown()
    _events_until(session, _closed)
    session.join(timeout=5)

    assert "at" in holder, "the roll never advanced after frame 1"
    frames = [r for r in library.entries(tmp_path / "library")
              if r["extra"]["roll_membership"]["kind"] == "frame"]
    assert len(frames) == 1
    after = device.sent[holder["at"]:]
    assert not [d for op, d in after if op == SCSI_SLIDE and d[:1] == bytes(
        [SLIDE_NEXT])], "the film went on advancing after Stop"
    assert not [op for op, _ in after if op == SCSI_SCAN], \
        "another pass was started after Stop"


def test_a_real_roll_through_the_session_files_frames_that_reconstruct(
        monkeypatch, tmp_path):
    """The window's roll on the driver's own loop -- metering, prescans,
    frame passes, whole-frame moves on a strip -- rather than a stand-in
    whose raw bytes were the same 64 placeholder bytes for every frame. Each
    frame's entry re-decodes to itself, and each rolls/<name>/frameNN.tif is
    that entry corrected."""
    from rps7200 import tiff
    from rps7200.session import Roll

    scanner, device = scanner_at_commands(monkeypatch, upward={3})
    _, events = _session_to_close(
        scanner, tmp_path,
        [_calibration(tmp_path),
         Roll(frames=2, resolution=300, infrared=True, name="r")])

    assert not [e.text for e in events if e.kind == "failed"]
    frames = {r["extra"]["roll_membership"]["number"]:
              tmp_path / "library" / r["id"]
              for r in library.entries(tmp_path / "library")
              if r["extra"]["roll_membership"]["kind"] == "frame"}
    assert sorted(frames) == [1, 2]
    sent = {p["blob"] for p in device.passes}
    for number, entry in frames.items():
        assert library.reconstruct(entry)[1].startswith("identical"), number
        assert library.load(entry)[1]["image"]["corrections_applied"] == []
        assert library.read_raw(entry) in sent
        delivered = tiff.read(str(tmp_path / "rolls" / "r" /
                                  f"frame{number:02d}.tif"))
        assert np.array_equal(delivered, library.corrected(entry)[0]), number
        # Equal to corrected() alone would pass a raw delivery wherever
        # corrected() came back raw too -- an entry filed with no reference.
        assert not np.array_equal(delivered, library.load(entry)[0]), number
