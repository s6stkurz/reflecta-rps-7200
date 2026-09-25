# USB transport, protocol and command sequence

31 findings: 1 high, 10 medium, 19 low, 1 info.

**Not verified.** In the first audit every finding was re-read by an adversarial second reader; here that stage did not run (the account's spend limit was reached), so these are one reader's findings. Treat them as leads to confirm against the code.

[Back to the summary](../README.md)

## What this area is

Transport and command layer at 03aacba (HEAD e47ceae differs only in audit/ files). The transport (rps7200/usb_transport.py) sends the vendor's shape of traffic: an IEEE1284 preamble plus 0xE0, six CDB bytes on port 0x85, a status read on 0x84, a length handshake on 0x82 for every 32 KB window and 16 KB bulk-in chunks. A zero-length first read raises NoDataYet, and a stall after part of a payload is waited out for 120 s. No code path sends SET_SCAN_HEAD (0xD2), IEEE1284 RESET or STOP SCAN. The functions that could (stop_scan, Transport.open(reset=True), reset) exist but have no callers. usbpcap never returns an interrupt or isochronous payload, and packets() only returns data for devices the caller names; the keystroke rule holds. DirectScanner has a working 'suspect' latch for reads abandoned inside read_planes, and _CommandLog keeps every command of a successful pass (CDB, data out, replies under 256 B, including REQUEST SENSE, READ STATE, READ/WRITE GAIN OFFSET, MODE SELECT and PARAM) in scan.json extra.commands.

What remains:
- **Calibration end detection (high).** Any read refusal is treated as 'finished'. A partial reference is adopted, cached and used, and the device is not marked suspect.
- **A shortened pass is accepted as complete.** EndOfData produces a truncated pass that is not flagged, and a payload that fully arrived is dropped when its trailing status is CHECK.
- **START SCAN failures.** A transport error on START SCAN itself happens before the suspect guard, in both scan and calibration.
- **Suspect device still configured.** scan() sends about 14 configuration commands to a suspect device before it refuses at SLIDE INIT.
- **Failed passes leave no record.** An abandoned or failed pass keeps no commands, no sense bytes and no partial bytes.
- **Calibration raw bytes are not with the library.** They live outside library/, no code reads them, and the non-GUI path does not keep them at all.
- **A scalar manual exposure also scales the infrared exposure.** The vendor never changes that value.
- **Unguarded operator paths.** A CLI calibration can run on an empty transport, Ctrl-C in most tools abandons the read, and Force Abort strands the debug spool.
- **Probe tools no longer run.** They prescan without calibrating, after session_start has already made an unannounced sub-frame move.
- **Doc and comment drift.** The transport law, gain persistence, the INFRARED_FLOOR_S guard, the vendor command order and verify_protocol's 'vendor bytes only' claim no longer match the code.

## Findings at a glance

| ID | Severity | Category | Title |
|---|---|---|---|
| [TP-01](#find-transport-protocol-tp-01) | high | bug | Calibration treats any read refusal as 'scanner finished': partial reference adopted, cached and used; device not marked suspect |
| [TP-02](#find-transport-protocol-tp-02) | medium | data-integrity | EndOfData mid-read marks the pass complete: truncated pass returned as success, device not marked suspect |
| [TP-03](#find-transport-protocol-tp-03) | medium | data-integrity | Transport drops a fully received READ payload when the trailing status is CHECK; FAIL/ERROR trailing statuses are ignored |
| [TP-04](#find-transport-protocol-tp-04) | medium | hardware-safety | A transport error on START SCAN is outside the suspect guard (scan and calibration) |
| [TP-05](#find-transport-protocol-tp-05) | medium | hardware-safety | A suspect device still receives ~14 configuration commands before a scan is refused |
| [TP-06](#find-transport-protocol-tp-06) | medium | data-integrity | A failed or abandoned pass leaves no record: its command log, sense bytes and partial raw bytes are discarded |
| [TP-07](#find-transport-protocol-tp-07) | medium | data-integrity | Raw calibration bytes are kept outside the library, read by nothing, and not kept at all on the non-ensure_shading path |
| [TP-08](#find-transport-protocol-tp-08) | medium | hardware-safety | A scalar exposure_scale also scales the infrared exposure, a value the vendor never changes |
| [TP-09](#find-transport-protocol-tp-09) | medium | user-error | CLI calibration on an empty transport is unguarded; calibrate_shading reads READ STATE but ignores the measured byte-8 media flag |
| [TP-10](#find-transport-protocol-tp-10) | medium | data-integrity | Force Abort skips DirectScanner.close(), so the session's debug spool is never filed and its location is never reported |
| [TP-11](#find-transport-protocol-tp-11) | medium | user-error | Ctrl-C in most tools abandons the read in flight; only scan.py and scan_roll.py defer it |
| [TP-12](#find-transport-protocol-tp-12) | low | design | Calibration loop re-reads and re-writes gain/offset on every empty poll, not between reads as the vendor does |
| [TP-13](#find-transport-protocol-tp-13) | low | doc-mismatch | session_start sends a sub-frame film move (SLIDE 00 01 00 00) that its docstring misdescribes and no probe tool records or undoes |
| [TP-14](#find-transport-protocol-tp-14) | low | bug | Probe tools prescan without calibrating and now always fail with ShadingUnavailable, after session_start has already moved the film |
| [TP-15](#find-transport-protocol-tp-15) | low | bug | read_planes counts lines from a READ that returned no data |
| [TP-16](#find-transport-protocol-tp-16) | low | error-handling | NoDataYet on READ STATE is tolerated in position() but aborts scan() and calibration; wait_ready's timeout is ignored |
| [TP-17](#find-transport-protocol-tp-17) | low | hardware-safety | STOP SCAN and IEEE1284 RESET remain public methods whose docstrings invite their use |
| [TP-18](#find-transport-protocol-tp-18) | low | user-error | RPS7200_MAX_WINDOW is not validated: 0 or a negative value loops forever mid-read; a non-integer breaks import |
| [TP-19](#find-transport-protocol-tp-19) | low | data-integrity | Command log fills with refused NoDataYet READs and omits every successful image READ |
| [TP-21](#find-transport-protocol-tp-21) | low | design | The pcap parser ignores status and silently desyncs on a data-out command that was refused |
| [TP-22](#find-transport-protocol-tp-22) | low | doc-mismatch | verify_protocol.py claims to send only vendor byte values; several stages send invented payloads, and results are written only at the very end |
| [TP-23](#find-transport-protocol-tp-23) | low | doc-mismatch | Stale transport law in code comments and in verify_protocol constants (1.57 vs 1.84 vs 1.948) |
| [TP-24](#find-transport-protocol-tp-24) | low | doc-mismatch | direct.py says SET GAIN OFFSET persists and compounds; protocol.md and CLAUDE.md say it is a fixed reference that cannot compound |
| [TP-25](#find-transport-protocol-tp-25) | low | doc-mismatch | INFRARED_FLOOR_S is described as guarding the read, but nothing uses it except estimates; the real guard is a literal |
| [TP-26](#find-transport-protocol-tp-26) | low | doc-mismatch | scan() claims the vendor's command order, but it differs from docs/protocol.md section 8, which does not note the difference |
| [TP-27](#find-transport-protocol-tp-27) | low | design | Two different CAL-INFO prepare payloads, and the calibration sends the prepare twice |
| [TP-28](#find-transport-protocol-tp-28) | low | demo-divergence | advance(steps) and retreat(steps) put the step count in the value byte, which the device ignores; the demo moves `steps` frames and retypes MM_PER_UNIT |
| [TP-29](#find-transport-protocol-tp-29) | low | bug | _whole_frames counts a move when the position before the move could not be read |
| [TP-30](#find-transport-protocol-tp-30) | low | hardware-safety | Calibration read deadline is an absolute 300 s, not idle-based, and is not scaled with resolution; on expiry the read is abandoned |
| [TP-31](#find-transport-protocol-tp-31) | low | data-integrity | A failed CCD-mask read after a complete calibration throws away the whole calibration, bytes included |
| [TP-20](#find-transport-protocol-tp-20) | info | design | Tight status polling: the BUSY loops spin on the control endpoint with no sleep for up to max_wait_s |

## Findings in full

<a id="find-transport-protocol-tp-01"></a>

### TP-01 -- Calibration treats any read refusal as 'scanner finished': partial reference adopted, cached and used; device not marked suspect

**Severity** high · **Category** bug

**Where:** `rps7200/direct.py:2305`, `rps7200/direct.py:2310`, `rps7200/direct.py:2323`, `rps7200/direct.py:2337`, `rps7200/direct.py:2351`, `rps7200/direct.py:1795`, `rps7200/direct.py:866`, `rps7200/shading.py:135`

The only end-of-calibration signal the code accepts is a refused READ, and it does not tell a real end (ASC 0x20 after the expected ~40 blocks / 1.66 MB per the docstring) from any other refusal. Examples: a one-shot UNIT ATTENTION, a NOT READY, a queued condition from the set_gain_offset in the same loop whose sense was swallowed at 2303, or a REQUEST SENSE that itself failed. The descriptor's declared lines (2269) and the documented 40 blocks are never compared to what arrived. When this happens: (1) `ended=True`, so the device is not marked suspect although the calibration pass may still be running; get_ccd_mask (COPY) and the next job's commands then go into it. (2) A reference is built from whatever blocks arrived (possibly only dark lines, which calculate_shading then treats as a single-point 'light' reference) and becomes the session reference. ensure_shading atomically replaces calibration/shading.npz with it, so later '--reuse' sessions inherit it too. (3) If zero blocks arrived, `self._shading` becomes None and the good reference already in the session is lost. The test suite states the principle this path breaks: tests/test_sense.py:128 'Reporting a bus failure as a finished scan would truncate the frame.'

**Evidence (from the code):**

```text
direct.py:2305-2313:
    try:
        chunk = self.read_lines(4, bpl, retries=1)
    except NoDataYet: ...
    except (EndOfData, ScanReadError):
        self._log(f"  scanner finished after {blocks} blocks")
        ended = True
        break
read_lines (1795) raises ScanReadError for ANY refusal whose sense is not ASC 0x20, including an unreadable sense (Sense.unreadable) and NOT READY. Then: `except BaseException as exc: if not ended: self._mark_suspect(...)` (2337-2340) and `self._shading = calculate_shading(data, width)` (2351). calculate_shading (shading.py:135-182) has no check on line or block count. ensure_shading then calls `saved = self.save_shading(path)` (866), which overwrites the cached reference.
```

**Failure scenario:** Calibrate is pressed. After the 10 s silence the first read_lines gets CHECK CONDITION with sense NOT READY (ASC 0x04), or a sense read that fails. read_lines raises ScanReadError, the loop logs 'scanner finished after 0 blocks', no reference is built and the previous good reference is wiped, while the scanner is still running its calibration pass. The window reports a finished calibration job and the next Scan drives a device that is mid-pass. Variant: the refusal comes after 3 of 40 blocks. A reference built from the dark phase is saved over the cache, and every later scan (and every later '--reuse' session) is flat-fielded against it, which produces wrong pixels silently.

**Fix:** End the calibration only on sense.end_of_data (EndOfData). Treat any other ScanReadError like a lost read: _mark_suspect and raise. Before adopting and caching a reference, check the received line count against the descriptor (sum of declared lines, or the documented 160 lines) or at least against both phases being present. Refuse to overwrite the cache or the session reference with a reference that fails that check. Keep the previous self._shading when the new one is None.

<a id="find-transport-protocol-tp-02"></a>

### TP-02 -- EndOfData mid-read marks the pass complete: truncated pass returned as success, device not marked suspect

**Severity** medium · **Category** data-integrity

**Where:** `rps7200/direct.py:1862`, `rps7200/direct.py:1881`, `rps7200/direct.py:3286`, `rps7200/protocol.py:367`, `rps7200/direct.py:2283`

ASC 0x20 is also what this scanner returns for a read that was refused for another reason (the calibration comment records exactly that). When it arrives before `got == total_lines` (PARAM's own line count), the pass is declared complete, `_read_complete` is set and suspect is not set. The truncated image is filtered, filed and delivered as a normal scan. Nothing in meta flags it. `height` is simply smaller, and only raw.layout.lines vs lines_received (when raw was kept) or the PARAM reply in extra.commands shows the shortfall. The operator sees one log line.

**Evidence (from the code):**

```text
direct.py:1862-1864:
    except EndOfData:
        self._log(f"end of data at {got}/{total_lines} lines")
        break
then 1881: `self._read_complete = True`. In _read_pass (3286): `if not self._read_complete: self._mark_suspect(...)`. protocol.py:367-373: EndOfData is 'indistinguishable by sense alone from a genuinely invalid command'. direct.py:2283-2286: polling during calibration 'left every read refused with ASC 0x20'.
```

**Failure scenario:** During a 3600 dpi pass a READ gets CHECK CONDITION with ASC 0x20 at 60% of the lines. read_planes breaks out, the scan is corrected, delivered and filed as a finished frame 40% short. In a roll, the loop advances the film and starts the next frame's prescan while the device may still be delivering the rest of the pass.

**Fix:** If EndOfData arrives with got < total_lines, record it explicitly in meta (e.g. `lines_expected`, `lines_received`, `ended_early: true`) and surface it as a warning or failed job. Decide deliberately whether that state should also set suspect. At minimum, do not set `_read_complete` for a short pass without recording that the device ended it early.

<a id="find-transport-protocol-tp-03"></a>

### TP-03 -- Transport drops a fully received READ payload when the trailing status is CHECK; FAIL/ERROR trailing statuses are ignored

**Severity** medium · **Category** data-integrity

**Where:** `rps7200/usb_transport.py:849`, `rps7200/usb_transport.py:852`, `rps7200/usb_transport.py:855`, `rps7200/direct.py:1787`, `rps7200/direct.py:1846`

Once every byte of a READ has arrived, the scanner considers those lines delivered. If the post-transfer status is CHECK, the transport throws them away. In read_planes (retries=1) that either becomes EndOfData, which ends the pass shorter by up to one batch (216 lines across channels) that did arrive, or ScanReadError, which abandons the pass. In calibration it ends the calibration (TP-01) and the chunk is not archived. Retrying the READ (read_lines default retries=3) would resume after the dropped lines and misalign every later line. Conversely, a device-reported FAIL or ERROR after a data phase is silently accepted.

**Evidence (from the code):**

```text
usb_transport.py:849-857:
    payload = self._read_payload(read_size, timeout_ms)
    final = self._wait_not_busy(deadline, ...)
    if final == UsbStatus.CHECK:
        raise CheckCondition(command[0])
    return payload
Any other final status (FAIL 0x88, ERROR 0xFF, AGAIN) falls through and the payload is returned as good.
```

**Failure scenario:** The last READ of a pass returns its full 216 lines and the scanner then reports CHECK (end of data) on the trailing status. The transport raises and the lines are discarded. read_lines reads sense 0x20, raises EndOfData, and the image loses its last ~54-72 rows (bottom-up passes: the top). raw.bin also lacks them, so reconstruct cannot recover them.

**Fix:** Return the payload together with the trailing status (e.g. raise a CheckCondition subclass that carries `payload`) so read_planes and calibration can keep the bytes and then decide on the sense. Treat FAIL and ERROR after a data phase as errors rather than success.

<a id="find-transport-protocol-tp-04"></a>

### TP-04 -- A transport error on START SCAN is outside the suspect guard (scan and calibration)

**Severity** medium · **Category** hardware-safety

**Where:** `rps7200/direct.py:3248`, `rps7200/direct.py:3252`, `rps7200/direct.py:2274`, `rps7200/direct.py:2280`, `rps7200/direct.py:1628`, `rps7200/direct.py:4147`

The SCAN CDB can be accepted by the device while the host's status read times out (30 s control timeout, a failure this module's own comment at usb_transport.py:78-79 records) or BUSY outlasts the 60 s default max_wait_s. In that case UsbError escapes start_scan before any try/except that would call _mark_suspect. The session keeps driving a device that may be scanning. In a roll the frame counts as an ordinary failure: the loop advances the film (SLIDE NEXT) and starts the next prescan into it, which is exactly the 'one lost frame became a lost roll' pattern the suspect latch exists to stop.

**Evidence (from the code):**

```text
_read_pass: `self.start_scan()` (3248), then `self._read_complete = False` (3251), then `try:` (3252). calibrate_shading: `self.start_scan()` (2274), then `try:` (2280). start_scan catches only CheckCondition: `try: self.t.command(_cmd(SCSI_SCAN, 1)) ... except CheckCondition:` (1629-1633). scan_roll catches UsbError as an ordinary frame failure (4147) and continues when `self.suspect is None`.
```

**Failure scenario:** At 1800 dpi the status read after the SCAN CDB times out. UsbError propagates, suspect stays None, scan_roll yields an error frame, advances the film and sends MODE SELECT, SLIDE INIT and SCAN to a scanner that is mid-pass. That leads to a wedge and a power cycle.

**Fix:** Treat any non-CheckCondition exception from the SCAN command as 'may have started': move start_scan inside the guarded block in both _read_pass and calibrate_shading, or have start_scan call _mark_suspect on UsbError.

<a id="find-transport-protocol-tp-05"></a>

### TP-05 -- A suspect device still receives ~14 configuration commands before a scan is refused

**Severity** medium · **Category** hardware-safety

**Where:** `rps7200/direct.py:2995`, `rps7200/direct.py:3016`, `rps7200/direct.py:3051`, `rps7200/direct.py:3062`, `rps7200/direct.py:1523`, `rps7200/direct.py:2476`, `rps7200/protocol.py:355`

**Doc claim:** CLAUDE.md:418-420 -- 'from then on the session refuses everything that would drive the device (DeviceSuspect); status queries still go through'

After a pass is abandoned, the window session keeps the same DirectScanner (session.py:1930-1956) and lets the operator submit Scan or Prescan again. Each attempt writes the scan frame, gain/offset and MODE SELECT to a device that is documented as possibly mid-scan, and may sit in wait_warm for up to 300 s, before DeviceSuspect is raised. Status queries are meant to pass, but these are configuration writes.

**Evidence (from the code):**

```text
scan() has no `_refuse_if_suspect` at its top. It sends read_state x1-4, wait_warm (TUR loop up to 300 s), test_unit_ready, read_state, set_exposure_time (3 WRITEs), set_highlight_shadow (3 WRITEs), set_scan_frame, cmd_17, get_gain_offset, set_gain_offset, set_mode (MODE SELECT) and test_unit_ready. Only then does `self.slide(SLIDE_INIT, ...)` (3062) reach `self._refuse_if_suspect(...)` (1523). auto_exposure also sends get_gain_offset/set_gain_offset before its first probe (2476, 2490). protocol.py:355-364: DeviceSuspect is 'Raised by every command that would drive the device -- a scan, a calibration, a film move'.
```

**Failure scenario:** A roll frame times out mid-read and suspect is set. The operator, seeing a failed job, presses Scan. The device receives SET SCAN FRAME, WRITE GAIN/OFFSET and MODE SELECT while it is still in the abandoned pass, and only then is DeviceSuspect raised.

**Fix:** Call self._refuse_if_suspect('a scan') at the top of scan() (before metering), and likewise in auto_exposure and prescan, so no configuration command reaches a suspect device.

<a id="find-transport-protocol-tp-06"></a>

### TP-06 -- A failed or abandoned pass leaves no record: its command log, sense bytes and partial raw bytes are discarded

**Severity** medium · **Category** data-integrity

**Where:** `rps7200/direct.py:3070`, `rps7200/direct.py:3074`, `rps7200/direct.py:1835`, `rps7200/direct.py:3222`, `rps7200/direct.py:2345`

The command log was added so that 'an entry could say how it was taken'. It is exactly the passes that fail (the ones that wedge the scanner or come back refused) that lose it: the CDBs, MODE SELECT and gain/offset payloads, the READ STATE before the pass, the REQUEST SENSE replies explaining the failure, and every byte already read. Only str(exc) survives, in a log line or a roll manifest's error field. Nothing reaches the library or the debug spool, even with RPS7200_DEBUG=1.

**Evidence (from the code):**

```text
direct.py:3070-3076:
    try:
        image, params, ccd_mask = self._read_pass(...)
    finally:
        stopper = getattr(self.t, "stop", None)
        commands = stopper() if callable(stopper) else None
On an exception `commands` is dropped with the frame. read_planes collects into a local `chunks: list[bytes] = []` (1835), which is lost on raise. _debug_capture runs only on success (3222). calibrate_shading calls stop() only on its success path (2345).
```

**Failure scenario:** A 3600 dpi RGBI pass stops at 70% with 'scanner stopped mid-payload' and the device needs a power cycle. Afterwards nobody can tell which READ, at what time and after which sense, preceded the wedge. The 400 MB already received are gone too.

**Fix:** On failure, write a small failure record: the command log, the exception, last_state and the partial blob, spooled like _debug_capture does, at least when debug is on. File it after close() as an entry tagged 'failed-pass', or into a failures/ directory.

<a id="find-transport-protocol-tp-07"></a>

### TP-07 -- Raw calibration bytes are kept outside the library, read by nothing, and not kept at all on the non-ensure_shading path

**Severity** medium · **Category** data-integrity

**Where:** `rps7200/direct.py:854`, `rps7200/direct.py:860`, `rps7200/direct.py:724`, `rps7200/direct.py:2132`, `rps7200/direct.py:2372`, `rps7200/direct.py:781`, `tools/uniformity.py:735`, `tools/uniformity.py:742`, `rps7200/library.py:271`

The owner's requirement is that everything can be recalculated later with newer code. What each entry stores is shading.npz, a reduction made by calculate_shading on the day of the scan (a level split and averaging). The input to that reduction, data.bin, is: (a) written only when calibration goes through ensure_shading; (b) stored in calibration/ outside library/, gitignored, not covered by library verify or checksums, and not moved with an entry. The entry links to it only by a cwd-relative path string, and only for a freshly calibrated session; a '--reuse' session has no link at all. (c) Never used: library.corrected and reconstruct cannot re-derive the reference with today's calculate_shading. archive_calibration also writes data.bin, ccd_mask.bin and calibration.json non-atomically.

**Evidence (from the code):**

```text
ensure_shading: `archive = self.archive_calibration(result, path.parent)` (856), which writes to calibration/<UTC>/ beside the cache, and `self._shading_origin["archive"] = str(archive)` (860). load_shading's origin (724-729) has no archive link. calibrate_shading(keep_data=False) is the default (2132) and returns `"data": data if keep_data else None` (2372). tools/uniformity.py:735 `result = scanner.calibrate_shading()` then `reference.save(reference_path)` (742, non-atomic). library.save stores only `reference.save(path / "shading.npz")` (271-272). grep finds no reader of data.bin or calibration.json anywhere.
```

**Failure scenario:** calculate_shading's phase split is improved next month. No existing entry benefits: their shading.npz is frozen, and even the calibrated ones point by a relative path string to a calibration/ folder that may be missing on the machine holding the library (a 'library 2/' copy, per .gitignore). Entries from uniformity captures and from every '--reuse' session have no raw calibration at all.

**Fix:** Store the raw calibration payload, or a content-addressed copy with a sha256, inside the library, e.g. library/calibrations/<sha>/ referenced by hash from each entry's calibration block, for loaded references too (record the archive hash in the cache). Make keep_data=True the default, or make ensure_shading the only public path. Add a library helper that recomputes the reference from the raw calibration bytes so reconstruct can use it.

<a id="find-transport-protocol-tp-08"></a>

### TP-08 -- A scalar exposure_scale also scales the infrared exposure, a value the vendor never changes

**Severity** medium · **Category** hardware-safety

**Where:** `rps7200/protocol.py:613`, `rps7200/direct.py:3028`, `rps7200/direct.py:1728`, `tools/gui.py:1801`, `tools/scan.py:216`

Typing one manual exposure value in the window (or `--exposure-scale 2` on the CLI) with infrared on multiplies the IR exposure too (e.g. 7745 to 15490), while '2 2 2' leaves it alone, so two inputs that look the same send different payloads. docs/protocol.md records that the IR exposure 'is constant within a session ... and the host never writes a value it did not read'. This is the same kind of untested combination that PROTOCOL_REVISION 4 closed for the fast-IR bit on RGB passes. It is recorded in meta.exposure, but it changes the IR plane and has never been characterised.

**Evidence (from the code):**

```text
protocol.py:613-614:
    if isinstance(factor, (int, float)):
        factors = [float(factor)] * len(self.exposure)
(self.exposure has 4 entries, R G B I). A list of 3 is padded with 1.0 for IR (617). direct.py:3028 `settings = self.get_gain_offset().scaled(exposure_scale)`; 1728 `data[18:20] = int(s.exposure[3]).to_bytes(2, "little")`. gui.py:1801 `return values[0] if len(values) == 1 else values`; tools/scan.py:216 `exposure_scale = parts[0] if len(parts) == 1 else parts`.
```

**Failure scenario:** The operator sets manual exposure '3' and scans RGBI. WRITE GAIN/OFFSET carries IR exposure 23235 where the vendor always sends the device's 7745. The IR plane comes back three times brighter or clipped, and the IR/dust threshold downstream no longer means what it did. The same scan entered as '3 3 3' behaves differently.

**Fix:** Treat a scalar as applying to the visible channels only (pad IR with 1.0, as the list form does), or reject a scalar when infrared is on. Say explicitly in the GUI and CLI that IR exposure is the device's.

<a id="find-transport-protocol-tp-09"></a>

### TP-09 -- CLI calibration on an empty transport is unguarded; calibrate_shading reads READ STATE but ignores the measured byte-8 media flag

**Severity** medium · **Category** user-error

**Where:** `tools/scan.py:270`, `tools/scan.py:273`, `rps7200/direct.py:2185`, `rps7200/protocol.py:584`, `tools/scan_roll.py:195`

**Doc claim:** CLAUDE.md:311-329 -- 'Calibrating an empty transport is a state the vendor never creates, and doing it once preceded a wedge ... Only Stefan can see the transport. Ask him.'

CLAUDE.md records that calibrating an empty transport 'preceded a wedge' and that only Stefan can see the transport. The window asks before calibrating (gui.py:1967-1982), but tools/scan.py calibrates immediately. tools/scan_roll.py seeks first, but the seek checks the counter, not whether film is present. The driver already holds the byte-8 reading (measured with one variable changed) at that moment and neither refuses nor warns.

**Evidence (from the code):**

```text
tools/scan.py:270-274 prints 'calibrating (about 3-4 minutes ...)' and calls `s.ensure_shading(ref_path, ...)` with no prompt about film. calibrate_shading: `for _ in range(4): ... if not self.read_state().warming_up: break` (2185-2191) never looks at `State.no_media` (protocol.py:584-587, byte 8: 1 empty, 0 loaded).
```

**Failure scenario:** The operator runs `uv run python tools/scan.py` right after powering on with the strip not yet inserted. The CLI calibrates the empty transport without a question, which is the condition that preceded a wedge.

**Fix:** Have tools/scan.py (and scan_roll.py before its calibrate) require explicit confirmation or a --film-loaded flag. In calibrate_shading, log prominently or refuse unless a force flag is given when READ STATE byte 8 reads 1 (empty), while still treating a 0 as not proof of film.

<a id="find-transport-protocol-tp-10"></a>

### TP-10 -- Force Abort skips DirectScanner.close(), so the session's debug spool is never filed and its location is never reported

**Severity** medium · **Category** data-integrity

**Where:** `rps7200/session.py:1863`, `rps7200/session.py:1869`, `rps7200/session.py:1973`, `rps7200/direct.py:1134`, `rps7200/direct.py:1145`, `rps7200/direct.py:926`

With RPS7200_DEBUG=1 the window spools every pass it does not file itself (metering probes, hold and aim prescans) for its whole lifetime and files them only at close(). After Force Abort, and after any crash or kill of the process, close() never runs. Those passes stay in a system temp directory, which can be cleared by the OS or a reboot on tmpfs, and nothing names the directory to the operator. The meta.json sidecars make hand-filing possible only if someone knows to look.

**Evidence (from the code):**

```text
force_abort: `transport = getattr(scanner, "t", None) ... transport.close()` (1866-1869), with `self.dead = True`. _run finally: `if not self.dead: self._scanner.close()` (1973-1974). DirectScanner.close() is the only caller of `self._debug_flush()` (1145). The spool is `tempfile.mkdtemp(prefix="rps7200-debug-")` (926-928).
```

**Failure scenario:** A multi-hour roll session with debug on ends in Force Abort. Every metering probe and verification prescan of the session is left unfiled in /tmp/rps7200-debug-XXXX, and a reboot deletes them.

**Fix:** In force_abort (and in an atexit or crash handler), run the debug flush after the transport is closed, or at least log the spool path. Consider spooling under the library root (e.g. library/.spool) instead of the system temp dir, so leftovers survive and can be found.

<a id="find-transport-protocol-tp-11"></a>

### TP-11 -- Ctrl-C in most tools abandons the read in flight; only scan.py and scan_roll.py defer it

**Severity** medium · **Category** user-error

**Where:** `rps7200/console.py:50`, `tools/verify_protocol.py:1394`, `tools/filing_load_test.py:204`, `tools/uniformity.py:733`, `tools/transport_truth.py:111`, `tools/hold_probe.py:118`

**Doc claim:** CLAUDE.md:421-422 -- 'Ctrl-C in tools/scan.py and tools/scan_roll.py finishes the pass in flight' (true only for those two)

A KeyboardInterrupt during read_planes unwinds through _read_pass, which marks the device suspect, and then through the with-block, which closes the transport under a device that is mid-scan. That is the documented wedge. Several of these tools are ones CLAUDE.md asks people to run for real, for example filing_load_test.py 'before trusting the roll path unattended' and uniformity capture, which prompts interactively between passes.

**Evidence (from the code):**

```text
DeferredInterrupt ('Ctrl-C asks the pass in flight to finish rather than abandoning it', console.py:50-60) is imported only by tools/scan.py and tools/scan_roll.py (grep). verify_protocol.py:1394 `with DirectScanner(verbose=False) as s:` and filing_load_test.py:204 `with DirectScanner(verbose=False) as s:` run passes with the default SIGINT handler.
```

**Failure scenario:** During tools/filing_load_test.py the operator presses Ctrl-C once to stop after the current pass. The read is abandoned and the scanner needs a power cycle.

**Fix:** Wrap the device-owning block of every tool that scans in DeferredInterrupt and check .requested() between passes, or install it inside DirectScanner.open() for the main thread.

<a id="find-transport-protocol-tp-12"></a>

### TP-12 -- Calibration loop re-reads and re-writes gain/offset on every empty poll, not between reads as the vendor does

**Severity** low · **Category** design

**Where:** `rps7200/direct.py:2300`, `rps7200/direct.py:2302`, `rps7200/direct.py:2307`

Every NoDataYet spin (every ~50 ms plus round trips) sends READ GAIN/OFFSET and WRITE GAIN/OFFSET before the next READ, possibly thousands of times per calibration. The docstring says the vendor re-writes gain/offset between its 4- and 72-line reads, and the comment at 2283-2286 warns that extra polling 'appears to disturb the calibration'. A swallowed CheckCondition from set_gain_offset also leaves its sense unread, and it lands on the next READ (feeding TP-01).

**Evidence (from the code):**

```text
while time.monotonic() < deadline:
    try:
        self.set_gain_offset(self.get_gain_offset())
    except (CheckCondition, ScanReadError): pass
    try:
        chunk = self.read_lines(4, bpl, retries=1)
    except NoDataYet:
        time.sleep(0.05)
        continue
```

**Failure scenario:** A calibration spends most of its read phase alternating gain writes and empty READs. A refused gain write's queued sense makes the next READ fail, which ends the calibration early.

**Fix:** Re-write gain/offset only after a successful block, as the vendor does. Read the sense when set_gain_offset is refused instead of swallowing the CheckCondition.

<a id="find-transport-protocol-tp-13"></a>

### TP-13 -- session_start sends a sub-frame film move (SLIDE 00 01 00 00) that its docstring misdescribes and no probe tool records or undoes

**Severity** low · **Category** doc-mismatch

**Where:** `rps7200/direct.py:2102`, `rps7200/direct.py:2124`, `rps7200/direct.py:1501`, `tools/transport_truth.py:122`, `tools/hold_probe.py:130`, `tools/roll_registration_walk.py:262`, `tools/exposure_probe.py:237`

Action 0x00 param 1 is a sub-frame forward move of about 2.84 units that does not touch the frame counter (protocol.md section 5). Seven probe tools call session_start before their baseline, so the film is moved by an amount no log records and nothing restores. transport_truth even prints 'NOT sent: SLIDE_INIT' while sending this unannounced move. The docstring also gets the value byte wrong and keeps a disproven hypothesis about 0xE7.

**Evidence (from the code):**

```text
Docstring (2102-2103): 'REQUEST SENSE and a SLIDE with `00 01 00 04`'. Code (2124): `self.slide(0x00, param=0x01)`, and slide's default is `value: int = 0` (1501), so the bytes on the wire are `00 01 00 00`. Docstring 2104-2106 says 0xE7 'may be what puts the scanner into a state where calibration is accepted'. protocol.md section 11 measured 0xE7 as an invalid opcode ('This closes it as a lead').
```

**Failure scenario:** transport_truth.py, which exists to account for every transport move, starts by moving the film +2.84 units that are absent from its plan and its net-drift guard (MAX_DRIFT_MM).

**Fix:** Either drop the move from session_start or make it explicit (value=0x04 as documented, logged, and counted by callers that track travel). Correct the docstring about the value byte and about 0xE7.

<a id="find-transport-protocol-tp-14"></a>

### TP-14 -- Probe tools prescan without calibrating and now always fail with ShadingUnavailable, after session_start has already moved the film

**Severity** low · **Category** bug

**Where:** `tools/transport_probe.py:55`, `tools/transport_truth.py:124`, `tools/hold_probe.py:136`, `tools/roll_registration_walk.py:108`, `tools/exposure_probe.py:252`, `rps7200/direct.py:2948`

Since corrected passes were made to refuse rather than calibrate inside a pass (1492d2c), these tools cannot get past their first prescan. transport_probe's docstring says 'no shading calibration needed', and exposure_probe's says 'calibration happens with it in'. Neither statement is true any more. The failure is safe (nothing is scanned), but it comes after session_start's +2.84-unit move (TP-13), and the tools look usable until they are run.

**Evidence (from the code):**

```text
transport_probe.look: `image, _ = scanner.prescan(resolution=dpi)` (shading defaults to True). hold_probe prints '(a calibration runs first if this session has none -- ~2 min)' and then calls `scanner.prescan(resolution=args.resolution, keep_raw=True)` with no calibration call. None of these tools call ensure_shading or load_shading (grep). scan(): `if self._shading is None or needed > ...: raise self.uncalibrated(reason)` (2948-2963).
```

**Failure scenario:** Stefan agrees to a hold_probe run. The tool opens the device, nudges the film, waits for warm-up, then dies with ShadingUnavailable, leaving the film moved.

**Fix:** Call ensure_shading (after confirming film is loaded) or pass shading=False explicitly in these tools, and fix their docstrings. Add a smoke test that constructs each tool's first pass against a fake scanner.

<a id="find-transport-protocol-tp-15"></a>

### TP-15 -- read_planes counts lines from a READ that returned no data

**Severity** low · **Category** bug

**Where:** `rps7200/usb_transport.py:828`, `rps7200/usb_transport.py:842`, `rps7200/direct.py:1846`, `rps7200/direct.py:1867`

If the device answers a READ with status OK and no data phase, the transport returns b"" and read_planes counts n lines as received. The read can then reach `got == total_lines` early and set `_read_complete` while the device still holds lines. The device is not marked suspect and the image comes back short. The calibration loop likewise counts an empty chunk as a block.

**Evidence (from the code):**

```text
usb_transport.py:828-842: `if status == UsbStatus.OK: if data: ... return b""`. This path is taken even when read_size > 0. read_planes: `chunk = self.read_lines(n, bpl, retries=1)` ... `chunks.append(chunk); got += n` (1846-1868). The length is never checked; _query does check `len(data) < read_size` (1215).
```

**Failure scenario:** The device returns an anomalous OK to a READ mid-pass. The host 'completes' the pass early, filters and files a short image, and sends the next commands into a device that still has lines queued.

**Fix:** In read_lines (or the transport), raise if a READ returns anything other than exactly lines x bytes_per_line bytes. Make _command reject status OK when read_size > 0.

<a id="find-transport-protocol-tp-16"></a>

### TP-16 -- NoDataYet on READ STATE is tolerated in position() but aborts scan() and calibration; wait_ready's timeout is ignored

**Severity** low · **Category** error-handling

**Where:** `rps7200/direct.py:1600`, `rps7200/direct.py:2995`, `rps7200/direct.py:2185`, `rps7200/direct.py:3063`, `rps7200/direct.py:3253`

An empty READ STATE (status READ followed by a zero-length packet, which the transport turns into NoDataYet) is expected just after a SLIDE. The hold loop does exactly that: nudge, sleep 0.4 s, then a prescan whose first command is READ STATE while the mechanism is busy for about 1.1 s. There it aborts the verification pass instead of being retried. Separately, if the device is not ready 120 s after START SCAN, _read_pass goes straight on to COPY, gets refused three times and abandons the pass (suspect).

**Evidence (from the code):**

```text
position(): `except (CheckCondition, UsbError, ScanReadError, IndexError): return None` (1600-1603); per the _whole_frames docstring, READ STATE right after a transport command 'came back empty every time'. scan(): `for _ in range(4): try: ... self.read_state() ... except (CheckCondition, ScanReadError): pass` (2995-3001), and NoDataYet is a UsbError, so it is not caught. calibrate_shading has the same loop (2185-2191). `self.wait_ready()` returns False on timeout and is ignored at 3063 and 3253.
```

**Failure scenario:** _hold_to_approved nudges and 0.4 s later its prescan's READ STATE comes back empty. UsbError fails the frame's hold and the roll records a failed frame, though nothing was wrong with the device.

**Fix:** Catch NoDataYet in the pre-pass READ STATE polls (retry after a short sleep). Act on wait_ready's result, for example by waiting longer before COPY after START SCAN rather than proceeding.

<a id="find-transport-protocol-tp-17"></a>

### TP-17 -- STOP SCAN and IEEE1284 RESET remain public methods whose docstrings invite their use

**Severity** low · **Category** hardware-safety

**Where:** `rps7200/direct.py:1674`, `rps7200/direct.py:1682`, `rps7200/direct.py:1660`, `rps7200/usb_transport.py:496`, `rps7200/usb_transport.py:686`

**Doc claim:** CLAUDE.md:425-426 and README.md:785 -- 'No IEEE1284 RESET, and no STOP SCAN -- the vendor sends neither, and both leave the device unresponsive'

No code path reaches them today. But they are the two commands CLAUDE.md and the README list as leaving the device unresponsive, and they are offered as the recovery and cleanup tools. That is exactly how a well-meaning probe script would come to use them.

**Evidence (from the code):**

```text
direct.py:1674-1679: `def stop_scan(self) -> None: """Stop scanning. Never raises -- it runs on the cleanup path. Leaving a scan running is what wedges the scanner badly enough to need a power cycle, so this always makes the attempt."""` then `self.t.command(_cmd(SCSI_SCAN, 0))`. finish_scan docstring (1660-1664): STOP SCAN 'is reserved for cancelling a scan that is still running'. Transport.open: 'Pass ``reset=True`` only to recover a device that is already unresponsive' (502-503); reset() sends `self.ieee_command(IEEE1284_RESET)` (689). None of these have callers.
```

**Failure scenario:** Someone writing a cleanup handler for a new tool reads stop_scan's docstring ('runs on the cleanup path') and calls it after a failed read. The scanner is left unresponsive.

**Fix:** Delete stop_scan, reset() and open(reset=...), or rename them to something like _forbidden_stop_scan and have them raise with a pointer to CLAUDE.md. Fix the finish_scan docstring.

<a id="find-transport-protocol-tp-18"></a>

### TP-18 -- RPS7200_MAX_WINDOW is not validated: 0 or a negative value loops forever mid-read; a non-integer breaks import

**Severity** low · **Category** user-error

**Where:** `rps7200/usb_transport.py:67`, `rps7200/usb_transport.py:743`, `rps7200/usb_transport.py:744`, `rps7200/usb_transport.py:768`

With max_window <= 0 the inner loop never runs, got never grows, and the outer loop re-announces lengths forever while the device holds a pending READ. The first INQUIRY at open already hangs. A value like '32k' raises ValueError at import of usb_transport, which direct.py imports, so offline decoding breaks too although it touches no device. The value is not recorded in any entry.

**Evidence (from the code):**

```text
`MAX_WINDOW = int(os.environ.get("RPS7200_MAX_WINDOW", 0x8000))` (67). _read_payload: `while got < size: window = min(self.max_window, size - got); self._announce_length(window); ... while window_got < window: ...; got += window_got` (743-768).
```

**Failure scenario:** A developer exports RPS7200_MAX_WINDOW=0 while probing. The GUI hangs on open inside a READ; killing it abandons that READ.

**Fix:** Parse the value defensively (fall back to the default and warn when it is not a positive integer). Assert max_window > 0 in Transport.__init__.

<a id="find-transport-protocol-tp-19"></a>

### TP-19 -- Command log fills with refused NoDataYet READs and omits every successful image READ

**Severity** low · **Category** data-integrity

**Where:** `rps7200/direct.py:486`, `rps7200/direct.py:490`, `rps7200/direct.py:1860`

extra.commands in scan.json lists every READ that came back empty, which can be thousands for a long pass at 25-50 per second of waiting, but no READ that delivered data. The record therefore shows only failures, and the read pacing (when each batch arrived, which matters for the 'scan time tracks sum(exposure)' analyses) is lost. It also inflates every scan.json and debug meta.json.

**Evidence (from the code):**

```text
_CommandLog.command: `except Exception as exc: entry["refused"] = type(exc).__name__; self.record.append(entry); raise` (486-489). But `if reply and len(reply) >= self.BULK and command[0] == SCSI_READ: self.bulk["reads"] += 1 ... return reply` (490-493), so the successful READ is never appended. read_planes polls every 0.02 s (1860).
```

**Failure scenario:** Someone investigating a slow 7200 dpi pass finds 7,000 'refused: NoDataYet' entries and no timestamps for the 700 data READs, so the delivery timeline cannot be reconstructed.

**Fix:** Record the successful image READs compactly (t, lines, bytes), and collapse runs of NoDataYet into a count with first and last t.

<a id="find-transport-protocol-tp-21"></a>

### TP-21 -- The pcap parser ignores status and silently desyncs on a data-out command that was refused

**Severity** low · **Category** design

**Where:** `tools/parse_capture.py:82`, `tools/parse_capture.py:92`, `tools/verify_capture.py:89`, `tools/verify_capture.py:19`, `tools/verify_capture.py:180`

When a data-out CDB is answered with CHECK CONDITION, the host sends no data (this driver's _command raises before the data phase). The parser then swallows the next command's CDB and bytes as 'data'. All bytes are still accounted for, so no warning is printed, and the command counts in docs/protocol.md that come from this parser would be off without anyone knowing. verify_capture also claims agreement it never tests: it reproduces MODE SELECT from the vendor's own field values but never compares the driver's own choices (byte14 0x10 on RGB, the fast-IR 0x80 bit, which protocol.md says CyberView never sends).

**Evidence (from the code):**

```text
parse(): `writes = op in (0x0A, 0x15, 0xDC, 0xD1); n = size if writes else 0; data = b[i+6:i+6+n]` (92-94). It always consumes `size` data bytes after a write CDB. stream() keeps only bytes written to 0x0085; status reads on 0x0084 are discarded. verify_capture's integrity check `accounted = sum(6 + len(d) ...); if accounted != len(raw)` (89-91) cannot see misattributed bytes. verify_capture docstring step 3 ('Each MODE SELECT field the driver can send is within the range the vendor is observed to use', 19-20) is only printed, never checked. The tool ends with 'this driver sends what CyberView sends.' (180).
```

**Failure scenario:** A vendor WRITE GAIN/OFFSET refused after a queued sense would make parse() read the following SET SCAN FRAME CDB as gain data, drop one command and mislabel the next, while verify_capture still reports a clean stream.

**Fix:** Use the 0x84 status reads in the stream to decide whether a data phase followed. Implement step 3 or remove it. Reword the final line to 'the driver's MODE SELECT builder reproduces the vendor's bytes'.

<a id="find-transport-protocol-tp-22"></a>

### TP-22 -- verify_protocol.py claims to send only vendor byte values; several stages send invented payloads, and results are written only at the very end

**Severity** low · **Category** doc-mismatch

**Where:** `tools/verify_protocol.py:11`, `tools/verify_protocol.py:448`, `tools/verify_protocol.py:779`, `tools/verify_protocol.py:1049`, `tools/verify_protocol.py:1149`, `tools/verify_protocol.py:1394`, `tools/verify_protocol.py:1400`

Anyone deciding whether it is safe to run the tool reads the docstring and gets the wrong answer. Separately, if any stage raises (DeviceSuspect, a refusal, Ctrl-C with no DeferredInterrupt), none of the earlier stages' results reach results.json. Only their TIFFs stay in probe/, and those are raw 8-bit pixels (shading=False) without raw bytes or command logs unless RPS7200_DEBUG=1.

**Evidence (from the code):**

```text
Module docstring (11): 'Everything here is 300 dpi RGB 8-bit and sends only byte values the vendor sends.' But: stage 8a `_ladder(s, 0x01, 0x04, ...)` sends `01 xx 00 04` (not in any capture, per protocol.md section 11); stage 12 `for param in (87, 120, 160, 200, 255)`; stage 14 `send(0, ...)` sends `00 00 00 04`; stage 15 sends `01 a0 00 04` (param 160). results.json is written only after the `with DirectScanner(...)` block completes (1394-1403).
```

**Failure scenario:** 'uv run python tools/verify_protocol.py 7 8 12' is run on the strength of 'vendor values only'. It sends params up to 255. When stage 12 fails, the stage 7 and 8 results are never written.

**Fix:** Correct the docstring and list which stages send non-vendor payloads. Require an explicit flag for stages 12, 14 and 15. Write results.json after every stage, atomically. Wrap the run in DeferredInterrupt.

<a id="find-transport-protocol-tp-23"></a>

### TP-23 -- Stale transport law in code comments and in verify_protocol constants (1.57 vs 1.84 vs 1.948)

**Severity** low · **Category** doc-mismatch

**Where:** `rps7200/protocol.py:240`, `rps7200/protocol.py:253`, `rps7200/protocol.py:290`, `rps7200/direct.py:3566`, `tools/verify_protocol.py:484`, `tools/verify_protocol.py:1165`, `tools/verify_protocol.py:1203`, `tools/verify_protocol.py:1240`, `tools/verify_protocol.py:1323`

The one law the mover obeys lives in protocol.COMMAND_UNITS (1.84), but the comments around it and the tool that re-measures it carry three different ramps. The runaway guard of stage 15 is computed from the ruled-out 1.57, so its 30% band is centred on the wrong prediction.

**Evidence (from the code):**

```text
protocol.py:240-241: 'The numbers are docs/protocol.md section 11's law ... `distance = 0.1057 mm x param + 0.1662 mm`', but COMMAND_UNITS = 1.84 gives 0.1945 mm. protocol.py:290: 'a command pays the ramp first, so `param 1` travels 2.57', while units_for_param(1) returns 2.84. protocol.py:253-257 still presents framing.COMMAND_COST's 1.84 as the disagreeing number. direct.py:3566-3568: 'distance = STEP_MM x param + OVERHEAD_MM. Worst residual 0.0185 mm across ten points' (the old fit). verify_protocol: `want = (param + 1.5724) * 1.2423` (1165) drives STAGE15_RUNAWAY, the restore uses 1.5724 (1203), prints 'direct.py says 1.572' (1240); stage 16 uses 1.948 (1323); MM_PER_UNIT, OVERHEAD_MM = 0.1057, 0.1662 (484).
```

**Failure scenario:** Stage 15 is rerun. Its runaway threshold and restore step are sized with 1.5724 while the driver moves by 1.84, and its printout tells the reader that direct.py uses 1.572.

**Fix:** Derive every figure from rps7200.protocol (COMMAND_UNITS, MM_PER_UNIT, units_for_param). Correct the protocol.py and direct.py comments to the law in force.

<a id="find-transport-protocol-tp-24"></a>

### TP-24 -- direct.py says SET GAIN OFFSET persists and compounds; protocol.md and CLAUDE.md say it is a fixed reference that cannot compound

**Severity** low · **Category** doc-mismatch

**Where:** `rps7200/direct.py:2473`, `rps7200/direct.py:4114`

**Doc claim:** docs/protocol.md:394-400 ('READ GAIN/OFFSET is a fixed reference, not a readback ... exposure cannot compound across scans'); CLAUDE.md:448

Two comments in one file contradict each other on the premise that metering depends on. The measured behaviour (protocol.md section 6: 36 identical responses after different writes) supports the second. The auto_exposure comment would lead a maintainer to 'fix' a compounding that does not happen.

**Evidence (from the code):**

```text
auto_exposure (2473-2475): 'scan multiplies whatever the device currently holds, and SET GAIN OFFSET persists, so re-reading it each round would compound the scales.' scan_roll (4114-4122): READ GAIN/OFFSET 'is a fixed reference, `scaled()` always yields base x scale, and exposure cannot compound frame to frame.'
```

**Failure scenario:** A maintainer reads auto_exposure's comment and adds a compensating division, which then under-exposes every metered scan.

**Fix:** Correct the auto_exposure comment to the measured fact and keep the explicit set_gain_offset(base) as the belt-and-braces write it is.

<a id="find-transport-protocol-tp-25"></a>

### TP-25 -- INFRARED_FLOOR_S is described as guarding the read, but nothing uses it except estimates; the real guard is a literal

**Severity** low · **Category** doc-mismatch

**Where:** `rps7200/direct.py:509`, `rps7200/direct.py:515`, `rps7200/direct.py:521`, `rps7200/direct.py:645`

The comment says the constant now guards the read. It does not: the timeout is computed from a separate literal 227, and INFRARED_FLOOR_S is dead in the scan path. That is harmless today, but anyone tuning the floor would change a constant with no effect.

**Evidence (from the code):**

```text
direct.py:509-514: '#: The infrared floor ... Here, beside the read it guards, rather than only in the estimates -- it used to guard nothing'. `INFRARED_FLOOR_S = 212.0` (515) is referenced only by session.py:65 (re-export for estimates) and tests. read_idle_s uses `UNTIED_INFRARED_IDLE_S = 227.0 + 60.0` (521, 645).
```

**Failure scenario:** Someone raises INFRARED_FLOOR_S to 260 after a new measurement, expecting the untied-IR idle timeout to follow. It does not, and the 287 s guard stays where it was.

**Fix:** Derive UNTIED_INFRARED_IDLE_S from a named measured maximum, e.g. INFRARED_FLOOR_MAX_S = 227 plus a 60 s margin, and fix the comment, or drop the claim.

<a id="find-transport-protocol-tp-26"></a>

### TP-26 -- scan() claims the vendor's command order, but it differs from docs/protocol.md section 8, which does not note the difference

**Severity** low · **Category** doc-mismatch

**Where:** `rps7200/direct.py:2863`, `rps7200/direct.py:3016`, `rps7200/direct.py:3263`, `rps7200/direct.py:3269`

**Doc claim:** docs/protocol.md:8-9 and :472-484

The driver issues COPY after SCAN rather than before it, PARAM before the READs rather than after, and gain/offset after the frame rather than before. protocol.md opens by promising 'Where the driver differs, that is noted, because those are the places bugs live', and these differences are not noted. The docstring asserts identity.

**Evidence (from the code):**

```text
scan() docstring (2863-2866): 'The command order here is the vendor software's, recovered from a USB capture.' Driver order: SET EXPOSURE TIME, HIGHLIGHT, SET SCAN FRAME, CMD 17, READ+WRITE GAIN/OFFSET, MODE SELECT, TUR, SLIDE INIT, SCAN, then COPY (get_ccd_mask, 3263) and PARAM (3269) before the READs. protocol.md section 8 (472-484): WRITE GAIN/OFFSET, SET SCAN FRAME, CMD 17, MODE SELECT, COPY, SLIDE INIT, SCAN, READ x N, PARAM. protocol.md section 2 also says READ carries the 'CCD mask', which the driver reads with COPY.
```

**Failure scenario:** An investigator comparing a wedge against the capture takes 'vendor order' at its word and never considers the COPY/PARAM placement as a variable.

**Fix:** List the driver's actual sequence in protocol.md section 8 beside the vendor's and mark the differences. Soften the scan() docstring accordingly.

<a id="find-transport-protocol-tp-27"></a>

### TP-27 -- Two different CAL-INFO prepare payloads, and the calibration sends the prepare twice

**Severity** low · **Category** design

**Where:** `rps7200/direct.py:1360`, `rps7200/direct.py:1362`, `rps7200/direct.py:2198`, `rps7200/direct.py:2255`

At most one of the two payloads can be the vendor's. The other is invented, contrary to the repo's own rule after the SET_SCAN_HEAD incident, and the calibration issues a second WRITE plus READ 32 that the vendor count suggests it does not. The calibrate docstring itself warns that an extra command of the driver's own ('a PARAM call of my own') was what broke calibration before.

**Evidence (from the code):**

```text
get_shading_parms: `prep = bytearray(6); prep[0] = SUB_CALIBRATION_INFO | 0x80` sends `95 00 00 00 00 00` (1360-1362). calibrate_shading: `prep[0:2] = (SUB_CALIBRATION_INFO | 0x80).to_bytes(2, "little"); prep[2:4] = (2).to_bytes(2, "little")` sends `95 00 02 00 00 00` (2198-2202). Both run in one calibration (2202 and 2255). protocol.md section 3 counts sub-command 0x95 twice across seven captures, two of which calibrate.
```

**Failure scenario:** A firmware that validates the sub-command length field refuses the zero-length prepare. The shading descriptor is then unreadable and the width falls back to the frame estimate.

**Fix:** Check both payloads against the captures (parse_capture) and send exactly the vendor's one, once.

<a id="find-transport-protocol-tp-28"></a>

### TP-28 -- advance(steps) and retreat(steps) put the step count in the value byte, which the device ignores; the demo moves `steps` frames and retypes MM_PER_UNIT

**Severity** low · **Category** demo-divergence

**Where:** `rps7200/direct.py:1543`, `rps7200/direct.py:1557`, `rps7200/demo.py:391`, `rps7200/demo.py:413`, `rps7200/demo.py:451`

The public signature suggests multi-frame moves. The hardware moves one frame, and _whole_frames reports success after the first change. The demo instead moves `steps` positions, so it would report what the scanner would not do. No current caller passes steps > 1 (session.rewind already avoids it), but the API invites it. The demo's backlash model also retypes 0.1057 instead of DirectScanner.STEP_MM, against CLAUDE.md's 'never retyped' rule for the stand-in.

**Evidence (from the code):**

```text
direct.py:1543 `self.slide(action, param=0x01, value=steps)`; protocol.md section 5: '`04 01 00 02` | advance | 1 | the `value` differs, the movement does not'. _whole_frames returns on the first change of position. demo.py:391 `self._position += steps`; 413 `self._position = max(0, self._position - steps)`; 451 `swallowed = min(abs(asked), 2.2 * 0.1057)` (MM_PER_UNIT retyped).
```

**Failure scenario:** A future caller uses advance(steps=3) to skip frames. The scanner moves one frame, the demo moves three, and the demo's roll numbering diverges from the hardware's.

**Fix:** Remove the steps parameter (always value=1) or loop single-frame moves. Make the demo take STEP_MM from DirectScanner.

<a id="find-transport-protocol-tp-29"></a>

### TP-29 -- _whole_frames counts a move when the position before the move could not be read

**Severity** low · **Category** bug

**Where:** `rps7200/direct.py:1542`, `rps7200/direct.py:1549`

If READ STATE fails just before the SLIDE, the first successful poll counts as 'advanced' even when it still shows the old position, because the counter updates 1.6-6.2 s later per the docstring. advance() then returns early. scan_roll can prescan while the transport is still moving, and on the next frame place_on_strip sees the counter behind the count and ends the roll.

**Evidence (from the code):**

```text
`before = self.position()` (1542) can be None, since position() swallows errors. Then `if now is not None and now != before: ... return now` (1549).
```

**Failure scenario:** The READ STATE before an advance comes back empty. The first poll at 1 s reads the old position 4, which counts as 'advanced to 4'. The next frame's check reads 5 or 4 inconsistently, and the roll ends as 'behind the roll'.

**Fix:** When `before` is None, re-read it before sending, or require two consecutive equal readings that differ from the last known good position.

<a id="find-transport-protocol-tp-30"></a>

### TP-30 -- Calibration read deadline is an absolute 300 s, not idle-based, and is not scaled with resolution; on expiry the read is abandoned

**Severity** low · **Category** hardware-safety

**Where:** `rps7200/direct.py:2131`, `rps7200/direct.py:2298`, `rps7200/direct.py:2323`, `rps7200/direct.py:2328`

read_planes uses an idle timeout plus a 3600 s ceiling, so a pass that keeps delivering is never abandoned. The calibration instead stops at 300 s even while data is flowing, which by the code's own account is an abandoned read, and it does not scale for calibrate_shading(resolution=7200), a supported argument that reads twice the bytes. The margin over the documented 4-minute upper estimate is about 25%.

**Evidence (from the code):**

```text
`timeout: float = 300.0` (2131); `deadline = time.monotonic() + timeout; while time.monotonic() < deadline:` (2298-2300); `if not ended: self._mark_suspect(f"the calibration was still sending after {timeout:.0f} s ..."); raise ScanReadError(...)` (2323-2332). load_shading's docstring puts a calibration at '3-4 minutes'.
```

**Failure scenario:** A slow calibration (or a 7200 dpi one) is still delivering blocks at 300 s. The driver abandons it, marks the device suspect, and the operator has to power-cycle.

**Fix:** Use an idle-based timeout (no block for N s) plus a generous ceiling, as read_planes does, and scale it with resolution.

<a id="find-transport-protocol-tp-31"></a>

### TP-31 -- A failed CCD-mask read after a complete calibration throws away the whole calibration, bytes included

**Severity** low · **Category** data-integrity

**Where:** `rps7200/direct.py:2336`, `rps7200/direct.py:2337`, `rps7200/direct.py:2347`

A calibration that delivered every block and ended cleanly is discarded because the COPY afterwards was refused (for example by a queued one-shot condition, since _query retries only 3 times). The raw calibration bytes, the thing ensure_shading exists to keep, are dropped with it.

**Evidence (from the code):**

```text
`mask = self.get_ccd_mask(width)` (2336) is inside the try. On ScanReadError the handler does not mark suspect (ended is True) and re-raises. `data = b"".join(collected)` (2347) and the archive are never reached.
```

**Failure scenario:** All 40 blocks arrive and end with ASC 0x20. The COPY is refused three times, the calibration raises, and the 3-4 minutes of scanner time and the 1.66 MB of calibration data are lost. The operator is asked to calibrate again.

**Fix:** Archive the collected bytes before reading the mask. If the mask read fails, keep the reference (the per-pass mask is what corrections use) and record that the calibration mask is missing.

<a id="find-transport-protocol-tp-20"></a>

### TP-20 -- Tight status polling: the BUSY loops spin on the control endpoint with no sleep for up to max_wait_s

**Severity** info · **Category** design

**Where:** `rps7200/usb_transport.py:711`, `rps7200/usb_transport.py:820`

The loops re-read port 0x84 as fast as the bus allows, which could be thousands of control transfers per second for as long as the device stays BUSY. The capture analysis counts 7,992 one-byte 0x0c reads across all six vendor sessions, so the vendor evidently polls far more slowly. Nothing shows harm, but it is traffic the device has never seen from CyberView.

**Evidence (from the code):**

```text
_wait_not_busy: `while status == UsbStatus.BUSY: if time.monotonic() > deadline: raise ...; status = self._control_in()` (711-714). _command: `while status == UsbStatus.BUSY: ... status = self._control_in()` (820-823). For image reads the deadline is 300 s (read_lines max_wait_s).
```

**Failure scenario:** A device that stays BUSY for tens of seconds (for example after a data-out) is hammered with tens of thousands of status reads.

**Fix:** Add a short sleep (for example 5-20 ms) inside the BUSY loops, matched to the vendor cadence measured in the captures.

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Raw image bytes of a pass (every successful image READ payload, concatenated) | library/<id>/raw.bin.gz (raw.bin before library.compact); debug spool $TMP/rps7200-debug-*/NNN-raw.bin | index-format lines: 2-byte channel tag + bytes_per_line, uint16 LE (16-bit) or uint8; gzip level 6 in the library | raw | DirectScanner.read_planes -> last_raw (direct.py:1885), capture_record (889) -> session._file/FrameWriter -> library.save (library.py:277-304); or _debug_capture (961) -> _debug_flush -> library.save(raw_path=...) | library.decode_raw / reconstruct / verify, via DirectScanner.decode_index | Byte-exact, with sha256 in scan.json, for the bytes kept. Missing: any READ payload the transport dropped on a trailing CHECK (TP-03), rows never read after an early EndOfData (TP-02), and everything from failed or abandoned passes (TP-06) |
| Raw layout of those bytes | library/<id>/scan.json -> raw.layout | JSON {format:'index', bytes_per_line, line_stride, index_header, width, lines (PARAM), channels, byte_order, lines_received} | raw metadata | read_planes (direct.py:1886-1896) -> library.save | library decode and reconstruct, session.raw_bytes_disagree | exact |
| Command log of one pass (CDBs, data-out, small replies incl. MODE SELECT, WRITE/READ GAIN OFFSET, READ STATE, REQUEST SENSE, PARAM, COPY mask) | library/<id>/scan.json -> extra.commands {sent:[{t,cdb,out,in,refused}], image_reads:{reads,bytes}} | JSON, hex strings, t rounded to ms | raw | _CommandLog (direct.py:433-497), started and stopped in scan() (2990-2992, 3074-3076) | nothing in code (forensic only) | Exact for commands whose reply is under 256 B. Image READs are only counted (CDBs and timings not kept), refused NoDataYet READs are all listed (TP-19), and a failed pass's log is discarded (TP-06) |
| Per-pass scan record fields tied to the protocol | library/<id>/scan.json -> scan.{protocol_revision, carriage_state{read_state hex, far_end, stale}, filter_offsets, width, height, bytes_per_line, read_direction, fast_infrared}, device_settings{exposure,gain,offset}, extra.{mode, started_utc, shading_origin}, device (parsed INQUIRY) | JSON | raw metadata (INQUIRY stored parsed only, the raw INQUIRY bytes are not kept) | DirectScanner.scan meta (direct.py:3155-3215) -> library.save | library.signature/duplicates/reconstruct, GUI | exact except INQUIRY (parsed at pieusb offsets; raw bytes dropped) |
| CCD mask of the pass | library/<id>/ccd_mask.bin; debug spool NNN-ccd_mask.bin | raw COPY reply, 5172 B (calibration width) | raw | _read_pass -> self._ccd_mask (direct.py:3263-3268) -> capture_record -> library.save | library.corrected (apply_shading) | exact, sha256 in record.files |
| Shading reference used for a pass | library/<id>/shading.npz; debug spool NNN-shading.npz | np.savez_compressed float64 per-channel light and dark column means + scalars | derived (a reduction of calibration bytes by calculate_shading) | ShadingReference.save via library.save | library.corrected | Lossless as stored, but it is a reduction frozen at calibration time. The input bytes are not in the entry (TP-07) |
| Session shading cache | calibration/shading.npz (default ScanSession.reference) | savez_compressed ShadingReference | derived | DirectScanner.save_shading (atomic temp + os.replace, direct.py:736-747) from ensure_shading; tools/uniformity.py writes its own non-atomically | load_shading via ensure_shading(reuse=True) | Lossless, but may be overwritten by a partial calibration (TP-01) |
| Calibration archive (raw calibration payload and its record) | calibration/<YYYYmmddTHHMMSSZ>[-n]/{data.bin, ccd_mask.bin, shading.npz, calibration.json} | data.bin: concatenated calibration READ payloads (16-bit LE lines + 2-byte tag), uncompressed; calibration.json: resolution, pixels_per_line, bytes_per_line, index_header, bytes, sha256, duration_s, commands, protocol_revision, measured_utc | raw | DirectScanner.archive_calibration (direct.py:749-804), only via ensure_shading; non-atomic writes, device open | nothing in code; linked from entries only by extra.shading_origin.archive (a string, calibrated sessions only) | Byte-exact when written. Outside library/, not verified, absent for loaded references and for calibrate_shading(keep_data=False) callers such as tools/uniformity.py (TP-07) |
| Debug spool of passes awaiting filing | $TMP/rps7200-debug-*/NNN-{image.npy, raw.bin, meta.json, shading.npz, ccd_mask.bin} | npy raw decoded pixels, raw bytes, JSON meta sidecar | raw | DirectScanner._debug_capture (direct.py:908-998) during the session | DirectScanner._debug_flush at close() (1031-1125) | Lossless. Stranded unfiled if close() never runs (force abort, crash), and nothing reports the path (TP-10) |
| Protocol probe outputs | probe/<stage>_*.tif, probe/results.json (tools/verify_protocol.py --out) | TIFF of 8-bit raw pixels (shading=False); JSON merged read-modify-write | raw pixels, no raw bytes or command log unless RPS7200_DEBUG=1 | verify_protocol.shot / main | verify_protocol later stages (stage 16 reads stage15_home.tif), humans | TIFF lossless. results.json written non-atomically and only after all stages succeed (TP-22) |
| USB captures | captures/*.pcapng (gitignored) | pcapng with USBPcap pseudo-header | raw | external (Wireshark/USBPcap) | rps7200.usbpcap._records (private) -> packets(devices)/setups/scanner_devices -> tools/parse_capture.py, tools/verify_capture.py | Read-only. packets() returns only CONTROL/BULK records of named devices, and setups() only 8 header bytes, so interrupt (keystroke) payloads are never returned |

## What the operator can do

- Run tools/check_scanner.py at any time: it opens and claims the device and sends only INQUIRY and READ STATE (plus REQUEST SENSE if a condition is queued). It exits 0 when no scanner is present.
- In the window: Calibrate (asks whether film is loaded first), Prescan, Scan, Roll or Walk, Move (sub-frame nudge through DirectScanner.nudge, capped at param 87), prev/next slide, Stop (takes effect between passes), and Force Abort (requires typing ABORT).
- Run tools/scan.py and tools/scan_roll.py, where the first Ctrl-C finishes the pass in flight and a second aborts (DeferredInterrupt).
- Set RPS7200_DEBUG=1 to spool every pass and file it after close(), RPS7200_DEBUG_ROOT to redirect filing, RPS7200_MAX_WINDOW to change the bulk window, and LIBUSB_PATH to pick a libusb.
- Run tools/parse_capture.py and tools/verify_capture.py offline against captures/*.pcapng. They never return interrupt (keyboard) payloads.
- Run tools/verify_protocol.py stages 1-16 against the hardware (with Stefan's agreement).

## What the operator should not do

- Press Force Abort, or Ctrl-C twice, while a pass or calibration is reading. That abandons the read and needs a power cycle.
- Call DirectScanner.stop_scan(), Transport.open(reset=True) or Transport.reset(). They send STOP SCAN and IEEE1284 RESET, which leave the device unresponsive.
- Send SET_SCAN_HEAD (0xD2). No code path does, but the opcode is exported from rps7200.direct.
- Calibrate with an empty transport.
- Keep scanning in the same window session after a pass failed mid-read (DeviceSuspect). Power-cycle and reopen instead.
- Run verify_protocol stages 8a, 12, 14 or 15 (invented SLIDE payloads, params up to 255, param 0) or any probe tool without asking.
- Set RPS7200_MAX_WINDOW to 0, a negative number or a non-integer.
- Run two programs against the scanner at once.

## Mistakes nothing guards against

- tools/scan.py calibrates immediately with no question about film. calibrate_shading reads READ STATE but ignores the byte-8 empty-transport flag, so an empty-transport calibration (which preceded a wedge) is one command away.
- After a pass is abandoned (suspect), pressing Scan or Prescan in the same window session still sends READ STATE, TEST UNIT READY, six WRITE sub-commands, SET SCAN FRAME, CMD 17, READ and WRITE GAIN OFFSET and MODE SELECT before DeviceSuspect is raised at SLIDE INIT.
- Typing a single manual exposure value with infrared ticked also multiplies the infrared exposure; '2' and '2 2 2' send different payloads.
- A single Ctrl-C in any tool other than scan.py and scan_roll.py (verify_protocol, filing_load_test, uniformity, the probes) abandons the read in flight.
- Force Abort (or a crash) leaves every debug-spooled pass of the session unfiled in a system temp directory, with no message giving its path.
- RPS7200_MAX_WINDOW=0 makes the first READ (INQUIRY at open) loop forever. A non-integer value breaks import of rps7200.usb_transport, including for offline decoding.
- Running transport_truth, hold_probe, roll_registration_walk or exposure_probe moves the film +2.84 units (session_start's SLIDE 00 01 00 00) and then fails with ShadingUnavailable at the first corrected prescan.
- A recalibration that is cut short by any refused read is accepted as complete, overwrites the good cached reference, and is used for every later scan (TP-01).

## Dataflow notes

**Opening the device.** DirectScanner.__init__ (direct.py:545) wraps Transport in _CommandLog (433-497). DirectScanner.open -> Transport.open -> _raw_open (usb_transport.py:444-464): libusb_open_device_with_vid_pid 05e3:0144, _discover_endpoints (first bulk IN), auto-detach, claim interface 0. There is no reset and no port reset.

**Sending a command.** DirectScanner method -> _CommandLog.command -> Transport.command/_command (usb_transport.py:772-864):
- _send_command: ieee_command writes the 7-byte preamble plus 0xE0 to port 0x88, strobes 0x87, then the 6 CDB bytes go to 0x85 and the status byte is read from 0x84.
- AGAIN: sleep 1 s and resend. BUSY: tight poll of 0x84.
- OK: data-out is written byte by byte to 0x85, then _wait_not_busy.
- READ: _read_payload (717-770) announces up to MAX_WINDOW (32 KB) on 0x82 (8-byte LE length), then reads 16 KB bulk chunks. A first empty read raises NoDataYet; a stall mid-payload is waited out for up to 120 s. Then the trailing status: CHECK raises CheckCondition and drops the payload.

**Status queries.** DirectScanner._query (1187-1220) absorbs CHECK by calling read_sense -> Sense.parse (protocol.py:437) and retrying up to 3 times. It refuses short replies. Used for READ STATE (1222, State with raw 13 bytes), READ GAIN/OFFSET (1693), PARAM (1746) and COPY (ccd mask, 1735).

**Scan path.** scan (2842):
1. Pre-flight refusals: IR-blind film, uncorrectable resolution, missing or narrow reference.
2. Optional auto_exposure (2385), whose probes are scan() calls.
3. _CommandLog.start.
4. READ STATE polls, wait_warm (TUR), TUR, media note.
5. SET EXPOSURE and HIGHLIGHT (3+3 WRITEs), SET SCAN FRAME, CMD 17, READ GAIN OFFSET, then WRITE GAIN OFFSET with Settings.scaled.
6. MODE SELECT (set_mode 1414, byte14_for), TUR, SLIDE INIT 10 16 00 00, wait_ready, carriage_record.
7. _read_pass (3237): start_scan (SCAN, retrying on sense), then inside the suspect guard wait_ready, COPY mask, PARAM, and read_planes (1799). read_planes calls read_lines batches (batch_for), polls on NoDataYet with an idle timeout of 120 s or 287 s for untied IR, breaks on EndOfData, keeps last_raw/last_raw_layout, and decodes with decode_index (1923, line tags, upright). Then finish_scan (3 READ STATE polls).
8. _CommandLog.stop, 7200 dpi stagger realign, apply_shading with this pass's mask and the session reference.
9. meta (protocol_revision, mode, commands, carriage_state, filter_offsets, exposure/gain/offset, shading_origin), then _debug_capture spool, then last_pixels_raw/last_scan_meta.

**Calibration path.** ensure_shading (806) -> calibrate_shading (2128): READ STATE, wait_warm, TUR, exposure and highlight writes, CAL INFO prepare + READ 128, SET SCAN FRAME (0,3431,10343,6888), CMD 17, gain read and write back, MODE SELECT with quality 0x0800, SLIDE INIT 10 01 00 00, wait_ready, the shading descriptor (95 prepare + READ 32), SCAN, 10 s of silence, TUR, then a loop of {gain read/write, READ 4 lines} until any refusal. Then COPY mask, calculate_shading, and _shading/_ccd_mask/_shading_origin are set. ensure_shading then calls archive_calibration (calibration/<UTC>/data.bin etc.) and save_shading (atomic cache).

**Transport moves.** slide (1500) with the suspect check -> SLIDE 0xD1 4-byte payload. _whole_frames (1528) handles advance and retreat (04/05 01 00 01) and polls READ STATE byte 2. nudge (3611) sends 00/01 <param_for_mm> 00 04 and is used by _hold_to_approved and _aim_frame. session_start (2099) sends INQUIRY, E7, REQUEST SENSE and SLIDE 00 01 00 00.

**Roll path.** scan_roll (3765), a generator: position() -> place_on_strip -> prescan -> hold/aim -> set_gain_offset(baseline) -> auto_exposure -> scan -> yield RollFrame. It catches UsbError, ScanReadError and similar per frame, and raises DeviceSuspect when suspect is set.

**Where data leaves.**
- Session path: session._file -> capture_record() (reference, mask, last_raw, layout) -> FrameWriter -> library.save.
- Debug path: _debug_flush after close() -> library.save.
- Device close: close() (1134) -> Transport.close (release, close, exit) -> _debug_flush.

**Offline capture path.** usbpcap._blocks/_records -> packets(raw, named devices; CONTROL/BULK only) -> parse_capture.stream (payload[8:] of setups to 0x0085) -> parse (CDB + size-based data for 0A/15/DC/D1) -> verify_capture (opcode set, 0xD2 count, MODE SELECT rebuild through tests/conftest FakeTransport).
