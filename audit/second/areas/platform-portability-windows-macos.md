# Windows and macOS on the data paths (gap pass)

Area key `platform-portability-windows-macos`. 15 findings: 1 high, 3 medium, 9 low, 2 info.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

Platform portability of the data paths on Windows, macOS and Linux, judged from the code at HEAD. I read library.py (entry ids, _reserve, save, compact, _write_atomic, verify, entries) and session.py (new_roll_name, roll_dir, _safe, _unclaimed, _out_name, FrameWriter, RollManifest, _roll, _file, _run, _filed). In direct.py I read the debug spool (_debug_capture, _debug_flush with the mmap), the calibration archive and the read loop. In usb_transport.py I read the libusb loader, the open, _why_not_found and close. I also read tiff.py, export.py, settings.py and tasks.py. In tools/gui.py I read the roll browser's Rename, Delete and Duplicate, _roll_is_busy, the sheet and roll settings keys, roll_summary, rolls_on_disk, folder_created, read_survey and main. I also read tools/check_scanner.py, packaging/60-rps7200.rules and .github/workflows/test.yml.


Answers to the six questions:


(1) Path length. Nothing guards it. Library ids are bounded (at most about 110 characters plus -N). Roll folder names and delivered names carry `_safe(roll)`, which has no length cap. The longest names in an entry are its `.part` temp names, so the record is what fails first. On Windows without LongPathsEnabled the error is FileNotFoundError ([WinError 206], or a misleading '[Errno 2] No such file or directory'). In a roll that stops the roll and loses the frame. The roll folder is created only after the seek has moved the film.


(2) Case-insensitive filesystems. A case-only Rename is refused as "already there". roll_dir returns the typed spelling for an existing folder spelled with another case. On macOS, Path.resolve() does not canonicalise case, so the manifest cache, the roll-in-use guard and the gui-settings keys each split by spelling. On Linux the same typed name makes a different roll.


(3) DST and local time. Nothing in the code joins by time. Roll folder names and delivered single-scan names are local time with no zone. The window's survey.json, roll.json and approved.json carry no timestamp at all. Delivered copies carry no link to their entry. At the fall-back hour names repeat (collisions are avoided with -2 and -N), the browser misorders, and a name maps to two UTC instants.


(4) The debug spool in the OS temp dir. Deletion by a cleaner is not detected. A removed spool directory is never recreated, so every later capture fails with a log line only. The flush reports deleted spool files as 'kept in' the spool.


(5) Open files on Windows. The GUI holds no file open across calls. The np.load mmap is released before its unlink. Sharing violations come from transient readers and from outside programs; those are mostly already reported (CSA-06, GUI2-23, CRA-05, CONC-06). One new Windows case: a roll's own frameNN.tif or prescanNN.tif that an outside viewer holds cannot be rewritten. The old picture then stays beside a manifest describing the new take.


(6) Platform setup. The udev rule is correct for a local seat only. The libusb loader stops at the first candidate path that exists, even when that file cannot load. The Windows diagnosis for "another process holds the scanner" tells the operator to reinstall the driver with Zadig.


Beyond the questions: nothing inhibits system sleep during multi-hour rolls, which is a path to an abandoned read and a wedge. Provenance depends on a `git` on PATH. Stored links between rolls, entries and calibrations are OS-native, cwd-relative path strings.

## Findings at a glance

| ID | Severity | Category | Verdict | Title |
|---|---|---|---|---|
| [PLAT-01](#platform-portability-windows-macos-plat-01) | high | hardware-safety | confirmed | Nothing keeps the host awake during a pass or a roll: OS idle sleep, Modern Standby or a forced restart interrupts the read in flight |
| [PLAT-02](#platform-portability-windows-macos-plat-02) | medium | data-integrity | confirmed | A debug spool that the OS temp cleaner deletes mid-session is never recreated, and the flush reports deleted passes as 'kept in' the spool |
| [PLAT-04](#platform-portability-windows-macos-plat-04) | medium | user-error | confirmed | On Windows, 'another process holds the scanner' is diagnosed as the wrong driver and sends the operator to Zadig; on macOS the helpful text is attached to the wrong failure |
| [PLAT-A1](#platform-portability-windows-macos-plat-a1) | medium | hardware-safety | found-by-verifier | Ctrl-C, or closing the terminal or console that launched the window, kills the daemon worker mid-read; on_close's guarantee 'Quitting never abandons a read' covers only the title-bar close |
| [PLAT-03](#platform-portability-windows-macos-plat-03) | low | data-integrity | partly | No path-length handling: Windows MAX_PATH failures surface as misleading ENOENT after the scan (and after the seek has moved the film), and roll names are unbounded |
| [PLAT-05](#platform-portability-windows-macos-plat-05) | low | bug | confirmed | The libusb loader stops at the first candidate that exists, so a wrong-architecture copy hides a working one |
| [PLAT-06](#platform-portability-windows-macos-plat-06) | low | user-error | confirmed | Case-insensitive filesystems: case-only Rename is refused as 'already there', and a typed spelling of an existing roll splits keys and guards on macOS and makes a new roll on Linux |
| [PLAT-07](#platform-portability-windows-macos-plat-07) | low | bug | confirmed | On HFS+ (and libraries copied from it), every entry with a non-ASCII stock or roll name fails verify's id check |
| [PLAT-08](#platform-portability-windows-macos-plat-08) | low | design | confirmed | Generated names use local time without a zone while library ids are UTC; the window's manifests carry no timestamp, so a DST fold or zone change leaves names ambiguous and unjoinable by time |
| [PLAT-09](#platform-portability-windows-macos-plat-09) | low | data-integrity | confirmed | Links between rolls, entries and calibrations are stored as OS-native, cwd-relative path strings, and one of them is read back as a Path |
| [PLAT-10](#platform-portability-windows-macos-plat-10) | low | data-integrity | confirmed | Provenance depends on a `git` on PATH that accepts the checkout; without one every entry silently records driver_commit null |
| [PLAT-11](#platform-portability-windows-macos-plat-11) | low | data-integrity | confirmed | On Windows, a roll's frameNN.tif or prescanNN.tif held open by another program is not rewritten; the old picture stays under the name the new take's manifest records |
| [PLAT-12](#platform-portability-windows-macos-plat-12) | low | doc-mismatch | confirmed | The flush's 'mmap so a 570 MB frame need not be resident' does not hold on the built-in TIFF path, which copies the whole image into bytes |
| [PLAT-13](#platform-portability-windows-macos-plat-13) | info | design | confirmed | The udev rule only reaches a local seat session and has never run on hardware; the Linux error message repeats 'install the rule' when it is installed but inert |
| [PLAT-14](#platform-portability-windows-macos-plat-14) | info | design | confirmed | Answer to (5): the window holds no file open across calls; Windows sharing violations come from transient readers and outside programs, and settings.save has no replace retry |

## Findings in full

<a id="platform-portability-windows-macos-plat-01"></a>

### PLAT-01 -- Nothing keeps the host awake during a pass or a roll: OS idle sleep, Modern Standby or a forced restart interrupts the read in flight

**Severity** high · **Category** hardware-safety · **Verdict** confirmed

**Where:** `rps7200/session.py:1946-1967`, `rps7200/session.py:2490-2518`, `rps7200/direct.py:1838-1861`, `tools/gui.py:2204-2218`, `tools/gui.py:6553-6562`

A roll runs 1-2 hours unattended: a 30-frame 1800 dpi roll is about 30 × (85 + 70) s. The window keeps the device open between jobs as well. Laptop power plans on all three platforms idle-sleep after minutes to tens of minutes without keyboard or mouse input. None of them counts a process's USB bulk traffic as activity, so an application has to take a power assertion. On Windows, Modern Standby also has the desktop activity moderator freeze desktop processes once the screen is off, and Windows Update restarts outside active hours kill the process. When the host sleeps, the process stops and the USB bus is suspended mid-read. That is an abandoned read, the state CLAUDE.md names as the cause of wedges. On resume the pending libusb transfer returns an error, the pass is lost and the device is marked suspect or is actually wedged. The FrameWriter thread is frozen too. The documented 'budget above the median' and backgrounding advice covers the harness's 10-minute kill but not this. The CLI roll tool has the same gap.

**Evidence (from the code):**

```text
The worker owns the device for the window's whole life and runs jobs back to back: `self._writer = FrameWriter(on_done=self._filed)` / `while True: job = self._jobs.get() ... note = self._dispatch(job)` (session.py:1946-1956). A roll is one generator loop over the frames: `frames = self._scanner.scan_roll(...)` / `for rf in frames:` (session.py:2490-2518). The read loop polls READ until every line is in: `chunk = self.read_lines(n, bpl, retries=1)` ... `if now - idle_since > idle_timeout: raise ScanReadError(...)` (direct.py:1845-1859). The Roll dialog gives only a duration, `per = (23.0 if dry else estimate_seconds(dpi, self.v_ir.get(), self.v_fast_ir.get()) + 70)` and `return f"Roughly {_duration(per * frames + move_s)}."` (gui.py:2204-2206, 6562). A search of rps7200/, tools/, README.md, CLAUDE.md, TODO.md and docs/ finds no caffeinate, IOPMAssertion, SetThreadExecutionState, ES_SYSTEM_REQUIRED, systemd-inhibit or logind Inhibit, and no mention of sleep or power settings.
```

**Failure scenario:** A MacBook on its power adapter with default settings. The operator commissions 30 frames at 1800 dpi from the contact sheet (the dialog says roughly 1h 20m) and walks away. About 10-15 minutes later the display sleeps and the Mac sleeps with it. Frame 6's READ is cut off mid-pass. On wake the transfer fails, frame 6 is lost, and the scanner needs a power cycle. Frames 7-30 never run. On a Windows laptop the same thing happens at the plan's sleep timeout, or when the screen turns off under Modern Standby.

**Fix:** Take a sleep inhibitor for the life of each job that drives the device, not for the whole window, and release it when the worker goes idle. On Windows use SetThreadExecutionState(ES_CONTINUOUS|ES_SYSTEM_REQUIRED) from the worker thread (and ShutdownBlockReasonCreate for restarts). On macOS use IOPMAssertionCreateWithName('PreventUserIdleSystemSleep') via ctypes, or a `caffeinate -i -w <pid>` child. On Linux use org.freedesktop.login1.Manager.Inhibit('sleep:idle', ...) or systemd-inhibit. Say in the Roll and Scan dialogs when the inhibitor could not be taken, and test it with a fake inhibitor offline.

<details><summary>Second reader's check</summary>

Nothing in rps7200/ or tools/ takes a power assertion. A grep for caffeinate, SetThreadExecutionState, IOPMAssertion, systemd-inhibit and Inhibit finds nothing, and README, CLAUDE.md, TODO.md and docs/ say nothing about sleep or power settings. The worker runs jobs back to back with the device open (session.py:1946-1967). The read loop polls READ with an idle timeout (direct.py:1838-1861). If the host is suspended mid-pass, the read is abandoned, which is the documented wedge hazard. Whether a given OS idle-sleeps during active USB bulk I/O depends on its power plan, so the default-settings scenario is plausible rather than certain. The code gap itself is certain.

</details>

<a id="platform-portability-windows-macos-plat-02"></a>

### PLAT-02 -- A debug spool that the OS temp cleaner deletes mid-session is never recreated, and the flush reports deleted passes as 'kept in' the spool

**Severity** medium · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/direct.py:925-928`, `rps7200/direct.py:940-941`, `rps7200/direct.py:983-990`, `rps7200/direct.py:997-998`, `rps7200/direct.py:1060-1080`, `rps7200/direct.py:1106-1114`, `rps7200/library.py:281-303`

With RPS7200_DEBUG=1 (which CLAUDE.md requires of Claude), the passes nothing else files wait in $TMP/rps7200-debug-* until close(): metering probes, hold and aim prescans, and a real roll's raw prescans. In the window, close() comes at quit, so a session can span days. Several OS jobs delete old temp files that no process holds open, and np.save closes each spooled file at once:
- macOS's dirhelper purges $TMPDIR (/var/folders/../T) of files not accessed for about 3 days.
- Windows Storage Sense and Disk Cleanup remove unused %TEMP% files. Storage Sense runs by default when the system drive is low, which is exactly what a tens-of-GB spool on C: causes (CSA-01).
- systemd-tmpfiles-clean ages /tmp at 10 days.

Three things then go wrong:
(a) If the spool directory itself is removed, `_debug_spool` still points at it. Every later capture fails at np.save and only a log line says so, so the rest of the session's unfiled passes are lost.
(b) At flush, a pass whose files were deleted is logged as 'kept in <spool>', which is false. Nothing distinguishes 'deleted by the OS' from 'could not be filed'.
(c) A pass whose raw.bin went but whose .npy survived leaves an INCOMPLETE entry holding scan.tif, the reference, the mask and an empty raw.bin.gz, beside an orphaned .npy.
This is distinct from DOC-07 and DBG-8, which cover loss at reboot or after a crash.

**Evidence (from the code):**

```text
The spool is created once and never checked again: `if self._debug_spool is None: self._debug_spool = Path(tempfile.mkdtemp(prefix="rps7200-debug-"))` (direct.py:925-928), then `np.save(image_path, image)` (941). Every failure is swallowed: `except Exception as exc:  # never break a scan` / `self._log(f"debug: could not spool this scan ({exc})")` (997-998). The code expects a spooled file can vanish, but only checks for the shared reference: `if saved is None or saved[0] is not item["reference"] or not saved[1].exists():` (984-985). At flush time: `image = np.load(item["image_path"], mmap_mode="r")` ... `except Exception as exc: failed += 1; self._log(f"debug: could not file scan {n} ({exc}); its spooled pixels, bytes and record are kept in {self._debug_spool}")` (1063-1080). library.save creates the entry before it opens the spooled raw bytes: `path = _reserve(...)`, INCOMPLETE, scan.tif, then `with opener(path / raw_name) as fh: ... with open(raw_path, "rb") as src:` (library.py:259-303).
```

**Failure scenario:** On macOS the window is left open with RPS7200_DEBUG=1 from Friday to Monday. Friday's metering probes and hold prescans are purged Monday morning, and dirhelper also removes the emptied spool directory. Monday's roll logs 'debug: could not spool this scan' once per pass, which nobody reads. At quit the flush logs each Friday pass as 'could not file ... kept in /var/folders/.../rps7200-debug-x1y2'. That directory does not exist. None of the probes and prescans for either day reach the library.

**Fix:** Spool under the library root, e.g. <root>/.debug-spool or RPS7200_DEBUG_ROOT, instead of the OS temp dir: it survives cleaners and reboots, is on the disk the flush writes to, and CRA-02's startup scan could find it. At minimum, check `self._debug_spool.exists()` on every capture and recreate it with a loud message. At flush, stat an item's files before filing and report 'deleted before it could be filed' separately; say 'kept' only for paths that exist. In library.save, open raw_path before `_reserve`, so missing bytes never create an INCOMPLETE entry.

<details><summary>Second reader's check</summary>

`_debug_spool` is created only when it is None (direct.py:925-928) and is never checked for existence again. np.save and all other spool errors are swallowed with one log line (997-998). The flush's failure message always says the files are 'kept in {self._debug_spool}' (1079-1080), whether or not they exist. library.save reserves the entry, writes INCOMPLETE, scan.tif, the reference and the mask, and opens raw.bin.gz before it opens raw_path (library.py:259-303). A deleted raw.bin therefore leaves an INCOMPLETE entry holding an empty gzip. One refinement: the flush takes meta, the reference and the mask from memory (item['meta'], item['reference'], item['ccd_mask']), so only the loss of the .npy or raw.bin affects filing. It is reachable only when a window stays open with debug on for longer than the OS cleaner's age threshold (about 3 days on macOS, 10 on systemd), which is narrow but real.

</details>

<a id="platform-portability-windows-macos-plat-04"></a>

### PLAT-04 -- On Windows, 'another process holds the scanner' is diagnosed as the wrong driver and sends the operator to Zadig; on macOS the helpful text is attached to the wrong failure

**Severity** medium · **Category** user-error · **Verdict** confirmed

**Where:** `rps7200/usb_transport.py:444-449`, `rps7200/usb_transport.py:466-494`, `tools/check_scanner.py:128-143`, `rps7200/session.py:1928-1939`, `rps7200/session.py:5-8`

WinUSB allows one open handle per device. In a second process, libusb's Windows backend fails the open (LIBUSB_ERROR_ACCESS), so `libusb_open_device_with_vid_pid` returns NULL while the device is on the bus. `_why_not_found` then blames the WIA driver unconditionally. Every second opener hits this: a second window, `tools/check_scanner.py` or `pytest -m hardware` (both documented as harmless) run while the window is open, or a python.exe left alive by a killed window, which keeps its handle. All of them are told to replace the driver with Zadig. Doing that while the first process holds the device, possibly mid-roll, reinstalls the driver under it: the device is restarted under a live session, or Windows asks for a reboot. At best it wastes time; at worst it abandons a read.

On macOS it is the other way round, per libusb's darwin backend: an exclusive open by another app is tolerated at open and fails at claim. The window then shows a bare 'could not claim interface 0: LIBUSB_ERROR_ACCESS', while the darwin 'close any other scanning software' advice sits on a NULL-open branch that this case never reaches.

**Evidence (from the code):**

```text
`handle = _lib.libusb_open_device_with_vid_pid(self._ctx, VENDOR_ID, PRODUCT_ID)` / `if not handle: raise ScannerNotFound(self._why_not_found())` (usb_transport.py:445-449). `_why_not_found` distinguishes only 'not on the bus' from on-the-bus-by-platform: `if sys.platform == "win32": return (... "Windows binds its own Image/WIA driver to it, which libusb cannot go through. Replace it with WinUSB or libusbK using Zadig -- see the README." ...)` (477-484). check_scanner treats every NULL open as a driver problem: `except ScannerNotFound as exc: # It is on the bus -- rung 2 just said so -- so this is the driver binding, which is exactly what Zadig changes.` (131-135). The window shows `self._emit("failed", text=f"could not open the scanner: {exc}")` (session.py:1937). session.py's docstring: "The only mutual exclusion is `libusb_claim_interface`, which stops a second *process*" (6-8).
```

**Failure scenario:** With the window mid-roll on Windows, Claude runs `uv run python tools/check_scanner.py` to check the connection, as CLAUDE.md invites. The output is 'FAILED: ... Windows binds its own Image/WIA driver ... Replace it with WinUSB or libusbK using Zadig'. The operator runs Zadig > Replace Driver on the scanner the roll is reading from.

**Fix:** Find the device with libusb_get_device_list and call libusb_open on it directly, so the error code is kept. On Windows, LIBUSB_ERROR_ACCESS means 'in use by another program (another window, check_scanner, a leftover python.exe)', and LIBUSB_ERROR_NOT_SUPPORTED or NOT_FOUND means the driver binding. Map a claim failure of ACCESS or BUSY to the same 'in use' text on macOS and Linux. Optionally hold a lock file beside the library so a second window can name the first.

<details><summary>Second reader's check</summary>

`_raw_open` discards libusb's error code by using libusb_open_device_with_vid_pid (usb_transport.py:445-449). `_why_not_found` then branches only on bus presence and platform, and on win32 unconditionally blames the WIA driver and recommends Zadig (477-484). check_scanner's ScannerNotFound branch assumes a driver-binding problem (check_scanner.py:131-135). WinUSB permits one open per device, so a second process's open fails with ACCESS and gets the Zadig advice. On macOS and Linux the second opener fails at claim, and the window shows only 'could not claim interface 0: ...' (usb_transport.py:463-464). The darwin 'close other software' hint sits on the NULL-open branch. check_scanner's UsbError branch does give correct 'something else holds it' advice, but the window's does not.

</details>

<a id="platform-portability-windows-macos-plat-a1"></a>

### PLAT-A1 -- Ctrl-C, or closing the terminal or console that launched the window, kills the daemon worker mid-read; on_close's guarantee 'Quitting never abandons a read' covers only the title-bar close

**Severity** medium · **Category** hardware-safety · **Verdict** found-by-verifier

**Where:** `rps7200/session.py:1819`, `rps7200/session.py:1584`, `tools/gui.py:557`, `tools/gui.py:8908-8912`, `tools/gui.py:3437`, `rps7200/console.py:74-99`, `tasks.py:154`

`make run` launches the window as a console child: `run([sys.executable, "tools/gui.py"])` (tasks.py:154). A Ctrl-C in that terminal reaches the window's process. _tkinter's mainloop checks for signals between events, and the window's poll timer keeps events coming, so KeyboardInterrupt ends mainloop and main. The interpreter then exits while the daemon worker is inside the READ loop and FrameWriter is filing. Closing the terminal has the same effect: on macOS and Linux it sends SIGHUP, which has no handler, and on Windows it sends CTRL_CLOSE_EVENT, which terminates the process after a few seconds. So do session logoff and an OS-initiated shutdown (only WM_DELETE_WINDOW is handled). The result is an abandoned read, the documented wedge. Scanned frames still queued in FrameWriter are lost, the debug spool is never flushed (close() never runs), and a window entry filed with compress=False is never compacted. The CLI tools defer only SIGINT, so SIGHUP from a dropped SSH session or a closed terminal, and CTRL_BREAK or console close on Windows, also kill them mid-pass.

**Evidence (from the code):**

```text
The worker and the writer are daemon threads: `self._thread = threading.Thread(target=self._run, daemon=True, name="scanner")` (session.py:1819), and FrameWriter `self._thread = threading.Thread(target=self._run, daemon=True)` (1584). main ends in `root = tk.Tk() ... root.mainloop(); return 0` (gui.py:8908-8912) with no KeyboardInterrupt handling and no atexit. The only protocol the window registers is `root.protocol("WM_DELETE_WINDOW", self.on_close)` (557), whose dialog says 'Quitting never abandons a read -- that is what wedges the scanner.' (3437). The only signal deferral anywhere is `DeferredInterrupt`, which handles SIGINT alone and is used only by tools/scan.py:253 and tools/scan_roll.py:473.
```

**Failure scenario:** On Windows the operator runs `make run` from PowerShell and commissions a 24-frame roll. Tidying the desktop, they close the PowerShell window the scanner window came from. Windows terminates python.exe in the middle of frame 9's READ. The scanner needs a power cycle, frames already scanned but not yet filed are gone, and the debug spool in %TEMP% is left unfiled.

**Fix:** Make the scanner worker non-daemon, or join it on exit. Wrap mainloop in try/except KeyboardInterrupt and route the interrupt to on_close's 'stop after the frame in flight' path. Install handlers for SIGHUP and SIGTERM on POSIX, and for SIGBREAK and a console control handler (SetConsoleCtrlHandler) on Windows, that request a stop and wait for the pass in flight. Register WM_SAVE_YOURSELF / session-end handling in Tk. Extend DeferredInterrupt to SIGHUP, SIGTERM and SIGBREAK for the CLI tools. Adjust the on_close text so it does not promise more than the title-bar path delivers.

<a id="platform-portability-windows-macos-plat-03"></a>

### PLAT-03 -- No path-length handling: Windows MAX_PATH failures surface as misleading ENOENT after the scan (and after the seek has moved the film), and roll names are unbounded

**Severity** low · **Category** data-integrity · **Verdict** partly

**Where:** `rps7200/library.py:149-164`, `rps7200/library.py:460-491`, `rps7200/session.py:2286-2312`, `rps7200/session.py:2929`, `rps7200/session.py:2952-2958`, `tools/gui.py:3333-3337`

Library ids reach about 110 characters in the worst case (about 85 typically), roll folder names and delivered names are uncapped, and nothing detects or explains Windows MAX_PATH. With a library root of about 155 characters or more (about 130 with a long stock name) and no LongPathsEnabled, the entry's longest temp file (.prescan.tif.part or .scan.json.part) crosses 260. The failure appears as FileNotFoundError after the pass was scanned and the film was moved. The roll stops after the frame in flight, which stays as an INCOMPLETE entry. Typical checkouts under Documents stay below the limit.

**Evidence (from the code):**

```text
Ids are bounded per part only: `return "".join(keep).strip("-").replace("--", "-")[:40] or "scan"` (library.py:151). An id is timestamp + stock slug + `f` + frame slug + dpi + `ir` (154-164), up to about 110 characters, plus `-N` from `_reserve`. `_reserve` catches only `except FileExistsError:` (library.py:475). The record's temp file is the longest name written in an entry: `temp = path.with_name(f".{path.name}.part")` (486), and compaction uses `.raw.bin.gz.part` (428). Roll names have no cap: `kept = [c if (c.isalnum() or c in "-_.") else "-" for c in name.strip()]` / `cleaned = "".join(kept).strip("-.") or fallback` (session.py:2952-2953). They go into the folder, `return rolls / (cleaned or new_roll_name(rolls))` (834), and into every delivered name, `return f"{_safe(roll)}_frame{number:02d}_{dpi}dpi{ir}{end}"` (2929). The seek comes before the folder is made: `seek(self._scanner, first, ...)` (2288) ... `out.mkdir(parents=True, exist_ok=True)` (2312). A search finds no `\\?\`, LongPathsEnabled, MAX_PATH or ENAMETOOLONG handling anywhere.
```

**Failure scenario:** On Windows 11 the checkout is under a OneDrive for Business Documents folder nested in `Projekte\Scanner\` (library root about 140 characters). The operator types the roll name 'Kodak Portra 400 expired 2009 - Urlaub Suedtirol Sommer 2024 Film 3' and commissions 24 frames. Frame 1 scans for 90 s. library.save fails with "[Errno 2] No such file or directory: '...\\.scan.json.part'" after writing 60 MB of raw bytes into an entry nothing can see. The roll stops, and frame 1 exists only as an INCOMPLETE directory.

**Fix:** Cap `_safe` output at about 64 characters, and warn in the dialog when a name is cut. Before the device moves, compute the longest path the entry, the roll folder (including `.approved.json.part` and `prescanNN-before.tif`) and the output copy will need. Refuse with a message naming the length and LongPathsEnabled. On Windows, either detect HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled or use `\\?\`-prefixed absolute paths for library writes. Re-raise 206 and ENOENT-on-long-path as 'path too long (N characters)'.

<details><summary>Second reader's check</summary>

The mechanism is real. `_slug` caps each part at 40 characters (library.py:151). `_safe` has no length cap (session.py:2952-2953). `_reserve` catches only FileExistsError. The seek happens before out.mkdir (session.py:2288 vs 2312). `_write_approved` swallows the error with 'scanning anyway'. The code has no long-path handling anywhere. The worked scenario overstates reachability, though. With a typical stock ('kodak-portra-400', 16 characters) and a 40-character frame slug, the id is about 83 characters. A root of about 140 characters then gives an entry directory of about 224 and `.prescan.tif.part` of about 242, still under 260. Failure needs a library root of roughly 155 characters or more with a long roll name, or about 130 when the stock slug is also 40 characters. The 233-character roll name case is far-fetched. It is real on deep OneDrive or Documents checkouts without LongPathsEnabled, and the resulting error (WinError 206 mapped to ENOENT) is misleading.

</details>

<a id="platform-portability-windows-macos-plat-05"></a>

### PLAT-05 -- The libusb loader stops at the first candidate that exists, so a wrong-architecture copy hides a working one

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `rps7200/usb_transport.py:116-131`, `rps7200/usb_transport.py:174-186`, `pyproject.toml:25-31`, `pyproject.toml:42-45`

`_dll` raises OSError when a file exists but cannot be loaded, and nothing catches it, so the search ends there.
- On an Apple Silicon Mac migrated from Intel, a leftover x86_64 Homebrew libusb in /usr/local makes the arm64 Python fail with 'incompatible architecture'. The arm64 copy in /opt/homebrew is never tried.
- On Fedora and RHEL, /usr/lib is the 32-bit tree ('wrong ELF class' once libusbx.i686 is installed, e.g. for Steam), so /usr/lib64 is never reached.
- On Windows, any libusb-1.0.dll on PATH wins over the bundled libusb-package copy (from an Arduino, OpenOCD or MSYS directory, say). A 32-bit one fails with WinError 193, and the bundled DLL that pyproject installs for exactly this platform is never loaded.
- MacPorts' /opt/local/lib is not a candidate, and its install message says `brew install libusb`.
The failure comes before any device traffic, so this is a usability problem with a workaround (LIBUSB_PATH), not a data risk.

**Evidence (from the code):**

```text
`for path in ([override] if override else []) + list(_LIBUSB_PATHS): if path and os.path.exists(path): return _dll(path)` then `for name in _LIBUSB_NAMES: found = ctypes.util.find_library(name); if found: return _dll(found)` then `bundled = _bundled()` (usb_transport.py:176-185). The order is `"/usr/local/lib/libusb-1.0.dylib"` before `"/opt/homebrew/lib/libusb-1.0.dylib"`, and `"/usr/lib/libusb-1.0.so.0"` before `"/usr/lib64/libusb-1.0.so.0"` (116-125). pyproject: 'the loader still prefers whatever the system has' (42-45).
```

**Failure scenario:** On an M1 Mac restored from an Intel Time Machine backup, the window reports 'could not open the scanner: dlopen(/usr/local/lib/libusb-1.0.dylib ...) incompatible architecture (have x86_64, need arm64)', even though `brew install libusb` put a working copy in /opt/homebrew.

**Fix:** Try each candidate inside `try/except OSError`, collect the reasons, and continue. On Windows try the bundled copy before find_library. On macOS try /opt/homebrew before /usr/local when platform.machine() is arm64, and add /opt/local/lib. If nothing loads, raise one error listing every path tried and why each failed.

<details><summary>Second reader's check</summary>

`_load_libusb` returns `_dll(path)` for the first candidate that exists, with no try/except (usb_transport.py:176-185), so an OSError from a wrong-architecture file ends the search. The order is /usr/local before /opt/homebrew and /usr/lib before /usr/lib64 (116-125). find_library runs before the bundled libusb-package copy, which pyproject makes a Windows dependency (pyproject.toml:31). LIBUSB_PATH is a workaround. There is no data risk.

</details>

<a id="platform-portability-windows-macos-plat-06"></a>

### PLAT-06 -- Case-insensitive filesystems: case-only Rename is refused as 'already there', and a typed spelling of an existing roll splits keys and guards on macOS and makes a new roll on Linux

**Severity** low · **Category** user-error · **Verdict** confirmed

**Where:** `tools/gui.py:2852-2857`, `rps7200/session.py:829-832`, `rps7200/session.py:2306-2311`, `rps7200/session.py:2343-2345`, `tools/gui.py:2700-2702`, `tools/gui.py:8877-8880`, `tools/gui.py:2601`, `tools/gui.py:2615`

On NTFS and default APFS, 'portra' → 'Portra' is the same folder:
- The exists() check refuses a case-only rename as if another roll held the name, so the window cannot fix a roll's capitalisation.
- A roll name typed in another case than the folder resolves to the existing folder, but the returned Path keeps the typed spelling. Windows' resolve() canonicalises case (Python ≥3.10), macOS's realpath does not. So on macOS: `_manifests` can hold two RollManifest objects for one roll.json (a roll started while the last one's filings are pending re-reads a stale file, and both objects write it); `_roll_is_busy` fails to recognise a roll opened as `--open-roll portra`, so Delete and Rename of the open roll are allowed; and the gui-settings `sheet` and `rolls` sections record one folder under two keys.
- On Linux the same typed name creates a separate roll with separate `portra-NN` labels. The same operator action therefore means 'add to the existing roll' on two platforms and 'new roll' on the third.
- A Linux rolls/ or library/ holding both spellings collides when copied to Windows or macOS.
folder_note does warn that the folder 'already holds a walk', but it prints the typed spelling. Library ids are unaffected: `_slug` lowercases everything.

**Evidence (from the code):**

```text
Rename: `if not wanted or wanted.strip() == source.name: return` / `target = source.with_name(_safe(wanted.strip()))` / `if target.exists(): messagebox.showerror("Rename", f"{target.name} is already there.")` (gui.py:2852-2857). roll_dir keeps the typed spelling: `as_typed = rolls / name` ... `and as_typed.is_dir()): return as_typed` (session.py:829-832). The manifest cache is keyed by `key = manifest_path.resolve()` / `live = self._manifests.get(key)` (2343-2344). The roll-in-use guard compares `Path(summary["folder"]).resolve() == loaded` (gui.py:2702). `--open-roll` keeps the spelling: `for candidate in (Path(args.open_roll), rolls_dir / args.open_roll): if candidate.is_dir(): open_roll = candidate` (8878-8880). Settings are keyed by `rolls[Path(folder).name]` and `Path(self._sheet_roll).name` (2601, 2615).
```

**Failure scenario:** On macOS the window is launched with `--open-roll portra` for rolls/Portra. In the browser the operator picks 'Portra' and presses Delete. `_roll_is_busy` compares /…/rolls/portra with /…/rolls/Portra, finds no match, and the open roll's folder, approved.json included, is removed while the sheet still shows it.

**Fix:** Canonicalise a folder to its on-disk spelling wherever it is derived, keyed or compared: scan the parent with os.scandir and match casefold()/NFC, or use os.path.samefile. Allow a case-only rename by treating `target.exists() and target.samefile(source)` as 'same folder'. Key gui-settings by the canonical name.

<details><summary>Second reader's check</summary>

Rename refuses when `target.exists()`, which is true for a case-only change on case-insensitive filesystems (gui.py:2855-2857). roll_dir returns the typed spelling when `as_typed.is_dir()` (session.py:829-832). `--open-roll` keeps the typed candidate (gui.py:8878-8880), and open_roll stores it as `_loaded_roll` (2889). `_roll_is_busy` compares resolve() results (2700-2702), and macOS realpath does not canonicalise case, so the open roll can be deleted or renamed. The `_manifests` key uses resolve() (session.py:2343). Settings keys use the basename as spelled (gui.py:2601, 2615). It is narrow, and Delete still asks for confirmation.

</details>

<a id="platform-portability-windows-macos-plat-07"></a>

### PLAT-07 -- On HFS+ (and libraries copied from it), every entry with a non-ASCII stock or roll name fails verify's id check

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `rps7200/library.py:149-151`, `rps7200/library.py:307`, `rps7200/library.py:1040`, `rps7200/library.py:1088-1091`

HFS+ stores names decomposed (NFD) and readdir returns them that way. Text typed into Tk is normally composed (NFC). An entry whose stock or roll label contains an umlaut (a roll typed 'Urlaub Südtirol' gives frame labels 'Urlaub Südtirol-NN') therefore has an NFC id in scan.json and an NFD directory name from glob. verify reports 'records itself as …' for every such entry. The same happens on Linux or Windows for a library copied off an HFS+ disk, where the names stay NFD. APFS preserves the normalisation it is given, so the internal disk is not affected; external HFS+ archive disks and copies from them are. Loading still works, because entry_path uses `_dir`.

**Evidence (from the code):**

```text
`keep = [c.lower() if c.isalnum() else "-" for c in text.strip()]` (library.py:150) keeps letters such as 'ü' in the id. The record stores the name as constructed, `"id": path.name` (307). Entries are read back with the directory name the filesystem returns, `record["_dir"] = candidate.parent.name` (1040). verify compares the two: `path = entry_path(root, record)` / `if str(record.get("id")) != path.name: problems.append(f"{path.name}: records itself as {record.get('id')}")` (1089-1091).
```

**Failure scenario:** The library is moved to an external HFS+ disk (Mac OS Extended) used for photo archives. `make verify` reports every entry of the 'Südtirol' rolls as a mis-recorded id and exits 1, burying real problems in false ones.

**Fix:** Compare `unicodedata.normalize('NFC', …)` of both sides in verify and entry_path. Better, make `_slug` ASCII-only (NFKD then ASCII, dropping the rest) so ids are identical on every filesystem.

<details><summary>Second reader's check</summary>

`_slug` keeps Unicode alphanumerics (library.py:150). The record stores `id: path.name` as constructed (307). entries() records `_dir` from the glob result (1040), and verify compares the two strings directly (1090-1091). On HFS+, or with names copied from it, the directory name comes back NFD while the id is NFC, so verify reports a false mismatch. Loading is unaffected because entry_path uses `_dir`.

</details>

<a id="platform-portability-windows-macos-plat-08"></a>

### PLAT-08 -- Generated names use local time without a zone while library ids are UTC; the window's manifests carry no timestamp, so a DST fold or zone change leaves names ambiguous and unjoinable by time

**Severity** low · **Category** design · **Verdict** confirmed

**Where:** `rps7200/session.py:804-807`, `rps7200/session.py:2931-2932`, `rps7200/library.py:156`, `rps7200/library.py:257`, `tools/gui.py:5528-5546`, `tools/gui.py:5643-5648`, `rps7200/settings.py:95-96`

No code joins rolls, entries and approved.json by time: the joins are by roll name, `roll_membership`, and roll.json's per-frame `entry` once filed. Collisions are handled (the exists() loop, `_unclaimed`), so a DST fold overwrites nothing. What is lost is that a name cannot be placed in time:
- In the fall-back hour, 02:00-03:00 repeats. A roll at 02:40 CEST and one at 02:10 CET 30 minutes later list in the wrong order.
- A folder or file name maps to two UTC instants, and it does not record which zone the machine was in (travel, a VM or WSL on UTC).
- The window's survey.json, roll.json and approved.json carry no timestamp at all, so they cannot settle it. This also leaves nothing to disambiguate GUI1-01's name-only Export join by time.
- A delivered single scan `20261025T021500_1800dpi.tif` has the same layout as a library id `20261025T001500Z_…` but a different zone. No sidecar or tag links it to its entry, and the entry's time is taken later, on the writer thread.
- After local midnight a roll folder dated the 27th holds entries dated the 26th.
settings' `.unreadable-<local second>` names can collide within one second and os.replace overwrites the earlier one.

**Evidence (from the code):**

```text
Roll folders: `stamp = time.strftime("%Y-%m-%d-%H%M%S", time.localtime(now))` / `while (Path(rolls) / name).exists(): name, n = f"{stamp}-{n}", n + 1` (session.py:804-807). Delivered single scans: `return f"{time.strftime('%Y%m%dT%H%M%S')}_{dpi}dpi{ir}{end}"` (2932). Library ids: `when = datetime.now(timezone.utc)` / `when.strftime("%Y%m%dT%H%M%SZ")` (library.py:257, 156). The browser sorts by name, 'newest first': `for folder in sorted(root.iterdir(), key=lambda p: p.name, reverse=True):` (gui.py:5648). `folder_created` parses only the date, `time.mktime(time.strptime(folder.name[:10], "%Y-%m-%d"))` (5538). A search for "started", "created" or a UTC field in session.py's manifest writing finds none (scan_roll.py does write UTC 'started'/'finished').
```

**Failure scenario:** Two unnamed walks at 02:40 CEST and 02:10 CET on 2026-10-25 are listed with the older one first. Asked which library entries belong to `20261025T021500_1800dpi.tif`, the operator has to guess between 00:15Z and 01:15Z. Neither the file nor any manifest records the zone or the entry id.

**Fix:** Generate names in UTC with a Z (as the library and calibration archive already do), or append %z. Write a UTC `created` and `finished` into the window's survey.json, roll.json and approved.json, as scan_roll.py does. For delivered files, record the library entry id in an ImageDescription tag or a small sidecar, so the join is by id, not by time.

<details><summary>Second reader's check</summary>

Roll folder names use localtime (session.py:804). Single-scan names use localtime (2931-2932). Library ids are UTC with a Z (library.py:156, 257). The window's manifests contain no timestamp field: a grep finds no isoformat, strftime or created/started in session.py's manifest data. Collisions are handled by the exists() loop and `_unclaimed`, so nothing is overwritten. The settings `.unreadable-<second>` aside (settings.py:95-96) can collide within one second, and os.replace overwrites the earlier file, though that is very narrow. This is a design gap in provenance, not a defect in any join the code performs.

</details>

<a id="platform-portability-windows-macos-plat-09"></a>

### PLAT-09 -- Links between rolls, entries and calibrations are stored as OS-native, cwd-relative path strings, and one of them is read back as a Path

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/session.py:785-786`, `rps7200/session.py:1155`, `tools/gui.py:3317`, `tools/gui.py:5196`, `rps7200/direct.py:724-725`, `rps7200/direct.py:859-860`, `tools/gui.py:8885-8888`

Every link the owner relies on to re-derive a pass later is written with the writing OS's separator and relative to the cwd at the time. These are: the frame's entry in roll.json, the reference prescan's entry in approved.json, the roll folder in the entry, and the cached reference and calibration archive in `extra.shading_origin`. A library and rolls written on Windows and opened on macOS or Linux, which this owner does (Windows capture machine, Mac CI history), hold 'library\\2026…' and 'calibration\\2026…Z'. POSIX reads those as a single file name. read_survey turns `reference_entry` straight into a Path that never exists, so the full-resolution view of a reopened reference fails. The archive link from a corrected entry to its calibration bytes cannot be resolved at all. Relative paths also break when the window or a tool is started from another directory. LIB-05 notes that the calibration link is cwd-relative; the separator problem is new here.

**Evidence (from the code):**

```text
`return {"roll": str(roll), "number": int(number), "kind": kind, "folder": str(folder)}` (session.py:785-786). `record["entry"] = str(entry) if entry else None` (1155); entry is `_reserve(Path(root), …)` under root `"library"`. `"reference_entry": str(a.reference_entry or "")` (gui.py:3317), read back as `entry=Path(entries[number]) if number in entries else None` (5196). `"action": "loaded", "path": str(path)` (direct.py:725). `self._shading_origin["archive"] = str(archive)` (860). The roots are relative: `root=args.library or str(home / "library")`, `reference=... str(home / "calibration" / "shading.npz")` (gui.py:8886-8887).
```

**Failure scenario:** The library and rolls are copied from the Windows scanning PC to a Mac to run the demo and analysis. Each reopened roll's reference prescans report 'could not read library\\20260927T…' in the full-resolution view. An analysis that follows `shading_origin.archive` finds no calibration bytes for any entry.

**Fix:** Store links as the entry id or folder name relative to the library root (and to the rolls root), in POSIX form (`PurePath.as_posix()`). Resolve them against the configured root when read, accepting either separator for old files.

<details><summary>Second reader's check</summary>

`reference_entry` is written as `str(a.reference_entry)` (gui.py:3317). That value comes from `str(entry)` of a library.save path under the cwd-relative root 'library' (gui.py:8886, home=Path('.') at 8856). read_approved hands it back (gui.py:5471-5472) and read_survey turns it into `Path(entries[number])` (5196). A Windows backslash path is a single filename on POSIX, and a relative path breaks when launched from another cwd. The other links (roll.json frames[].entry at session.py:1155, roll_membership.folder at 785-786, and shading_origin path and archive at direct.py:724, 860) are written the same way but no code reads them back. They are record-only, which the finding already implies.

</details>

<a id="platform-portability-windows-macos-plat-10"></a>

### PLAT-10 -- Provenance depends on a `git` on PATH that accepts the checkout; without one every entry silently records driver_commit null

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/library.py:76-115`, `rps7200/library.py:118-138`

Which code produced an entry's scan.tif and meta is the provenance record. Two common Windows set-ups lose it for every entry, with no message:
- GitHub Desktop users (the Documents\GitHub layout) have no git on PATH. OSError returns None.
- A checkout on an exFAT or FAT drive or a network share fails git's safe.directory ownership check ('detected dubious ownership'). The return code is 128, so None again.
The same applies on macOS without Command Line Tools. Worse there: the /usr/bin/git shim opens an 'install developer tools' dialog on each of the two calls made per save.
The decode itself is re-runnable from the raw bytes. But when reconstruct later says 'decode CHANGED', the record can no longer say which commit wrote the stored pixels.

**Evidence (from the code):**

```text
`out = subprocess.run(["git", *args], capture_output=True, text=True, timeout=10, ...)` / `return out.stdout.strip() or None if out.returncode == 0 else None` / `except (OSError, subprocess.SubprocessError): return None` (library.py:83-90). `"driver_commit": head[:7] if head else None, "driver_dirty": None if head is None else bool(status)` (102-107). `_identity_now` does the same and returns `{}` (118-135). No other code identity (package version or source hash) is recorded, and no warning is logged.
```

**Failure scenario:** The checkout lives on an external exFAT SSD shared between the Mac and the Windows PC. Every entry filed on Windows records driver_commit null and driver_dirty null. Months later a reconstruct mismatch cannot be traced to the build that filed those entries.

**Fix:** Fall back to a content hash of rps7200/*.py (and the package version) computed at import, and record it always. Log once per session when git provenance is unavailable, naming the reason (not found, or dubious ownership).

<details><summary>Second reader's check</summary>

`git()` returns None on OSError or a nonzero return code, silently (library.py:83-90). `_identity_now` returns {} the same way (118-135). No fallback code identity (source hash or package version) is recorded, and nothing is logged. A missing git, or safe.directory's dubious-ownership refusal, therefore leaves driver_commit null in every entry. The python, numpy and tifffile versions and the platform are still recorded.

</details>

<a id="platform-portability-windows-macos-plat-11"></a>

### PLAT-11 -- On Windows, a roll's frameNN.tif or prescanNN.tif held open by another program is not rewritten; the old picture stays under the name the new take's manifest records

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/session.py:2549-2566`, `rps7200/session.py:2629`, `rps7200/session.py:2635-2636`, `rps7200/session.py:2667-2668`, `rps7200/session.py:1673-1699`, `rps7200/tiff.py:164`, `rps7200/tiff.py:167`

On POSIX, rewriting a file another program has open succeeds. On Windows, a viewer that holds the old file without FILE_SHARE_WRITE makes the new open fail with a sharing violation. Such viewers include the Explorer preview pane and the Photos app, often on a folder opened with the browser's 'Reveal'. The roll carries on, roll.json or survey.json records the new take (done, entry, rotation), and the folder keeps the previous take's picture under that name.
- For a frame: Export re-derives from the entry, so only the roll-folder copy is stale.
- For a re-walk of a different strip: walked_prescans finds the old prescanNN.tif and read_survey shows the old strip's picture for that frame on reopen. A commission then holds the frame against it.
This is distinct from SR-08 (non-atomic overwrite) and CRA-03/CONC-04 (survey written before the file).

**Evidence (from the code):**

```text
The roll's own files have fixed names: `surveyed = out / f"prescan{number:02d}.tif"` (2549) and `path=out / f"frame{number:02d}.tif"` (2629). They are written in place by tifffile or `with open(path, "wb")` (tiff.py:164, 167). A failure is only a note: `except Exception as exc: problems.append(f"could not write {path} ({exc})"); continue` ... `said = (f"picture {job['number']}: {problem}; the library entry is safe and can be exported again")` (session.py:1685-1699). The manifest ignores which files were written: `on_filed=(lambda entry, error, _written, n=number: record_of.filed(n, entry, error))` (2635-2636), and a walk records `record["prescan"] = f"prescan{number:02d}.tif"` whatever happened (2668).
```

**Failure scenario:** On Windows the operator previews prescan05.tif in Explorer's preview pane, then re-walks the roll folder with a new strip ('This walk replaces its walk'). prescan05.tif cannot be opened for writing. The log says 'could not write …prescan05.tif ([Errno 13] Permission denied); the library entry is safe'. After a restart the reopened sheet shows the previous strip's frame 5, and a commissioned frame 5 is held against it.

**Fix:** Pass `written` through to the manifest. For a file that was not written, record it as stale (or leave `prescan` unset), so readers never treat the old file as this take. Or write to a per-take name and record that name. Retry briefly as `_replace` does for manifests.

<details><summary>Second reader's check</summary>

The roll's own files have fixed names (session.py:2549, 2629) and are written in place by export.write, which calls tifffile.imwrite or open(path, 'wb') (tiff.py:164, 167). A write failure becomes a note and the frame carries on (session.py:1673-1699). The manifest callback ignores `written` (`_written` at 2635-2636). A walk records `record['prescan'] = f"prescan{number:02d}.tif"` whenever rf.prescan is not None (2667-2668). On Windows, a viewer holding the old file without FILE_SHARE_WRITE makes the open fail, so the old take stays under the recorded name.

</details>

<a id="platform-portability-windows-macos-plat-12"></a>

### PLAT-12 -- The flush's 'mmap so a 570 MB frame need not be resident' does not hold on the built-in TIFF path, which copies the whole image into bytes

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/direct.py:1061-1063`, `rps7200/tiff.py:190`, `rps7200/tiff.py:273`

On a bare install without tifffile, `data.tobytes()` materialises the entire mmapped frame as one bytes object before writing. That is 570 MB for a 7200 dpi RGBI pass, at the moment the flush runs, just after a long session. The tifffile path writes per strip. The mmap was released correctly before the unlink, the Windows WinError 32 fix noted at direct.py:1082-1089. The only problem is the memory claim.

**Evidence (from the code):**

```text
`# mmap the image rather than loading it: tiff.write walks it once, so a 570 MB frame need not be resident.` / `image = np.load(item["image_path"], mmap_mode="r")` (direct.py:1061-1063). The built-in writer (used whenever tifffile is absent) does `data = np.ascontiguousarray(image, dtype=image.dtype.newbyteorder("<"))` ... `fh.write(data.tobytes())` (tiff.py:190, 273).
```

**Failure scenario:** On a bare `pip install -e .` on an 8 GB laptop, a debug session with several 7200 dpi passes is flushed at close. Each save allocates about 570 MB for tobytes on top of the mapped pages, and the flush can hit MemoryError. The error is caught and logged, and the pass stays in the spool.

**Fix:** In `_write_builtin`, write strip by strip (`fh.write(data[rows].tobytes())` per strip) so memory is bounded by one strip, or correct the comment.

<details><summary>Second reader's check</summary>

The comment at direct.py:1061-1062 says a 570 MB frame 'need not be resident'. The built-in writer, used when tifffile is absent (tifffile is only an optional extra), does `np.ascontiguousarray(...)` (a no-op on a native little-endian memmap) and then `fh.write(data.tobytes())` (tiff.py:190, 273), which allocates a full-size bytes copy. The failure is caught and the spool is kept, so no data is lost.

</details>

<a id="platform-portability-windows-macos-plat-13"></a>

### PLAT-13 -- The udev rule only reaches a local seat session and has never run on hardware; the Linux error message repeats 'install the rule' when it is installed but inert

**Severity** info · **Category** design · **Verdict** confirmed

**Where:** `packaging/60-rps7200.rules:26`, `rps7200/usb_transport.py:489-494`, `TODO.md:720-721`

The rule is sound for a desktop login. The 60- prefix does come before systemd's 73-seat-late.rules, and it matches the usb_device node by VID:PID. But uaccess ACLs go only to the active local seat's session. A user who scans over SSH, from a systemd service or cron job, or from a Claude Code session attached remotely gets nothing, and there is no group fallback. The message then sends that user back to install a rule that is already installed. No finding about data; noted because this area had not been read.

**Evidence (from the code):**

```text
`SUBSYSTEM=="usb", ATTR{idVendor}=="05e3", ATTR{idProduct}=="0144", TAG+="uaccess"` (rules:26). The Linux branch says `"which is usually permissions. Install the udev rule at packaging/60-rps7200.rules and re-plug the scanner, or run as root to confirm that is what it is."` (usb_transport.py:489-494). TODO: "The udev rule in `packaging/` is written from the device reporting `bDeviceClass 0xff` and has not been exercised." (TODO.md:720-721).
```

**Failure scenario:** On a headless Linux box next to the scanner, reached over SSH, the rule is installed and the scanner re-plugged. Every open still fails, and the message says to install the udev rule.

**Fix:** Offer a second rule line, or document one: GROUP="scanner" (or plugdev), MODE="0660", for non-seat use. In the Linux message, say that uaccess covers only a local graphical login.

<details><summary>Second reader's check</summary>

The rule is exactly the single uaccess line (packaging/60-rps7200.rules:26). uaccess applies only to the active seat session. The Linux message always says to install the rule (usb_transport.py:489-494). TODO.md:720-721 says the rule has not been exercised. The rule's own header claim that a NULL open means permissions is right for Linux, because libusb_open returns ACCESS there. This is an observation, not a defect in the data path.

</details>

<a id="platform-portability-windows-macos-plat-14"></a>

### PLAT-14 -- Answer to (5): the window holds no file open across calls; Windows sharing violations come from transient readers and outside programs, and settings.save has no replace retry

**Severity** info · **Category** design · **Verdict** confirmed

**Where:** `tools/gui.py:4587-4603`, `rps7200/direct.py:1081-1100`, `rps7200/shading.py:95`, `rps7200/tiff.py:290-296`, `rps7200/settings.py:113-116`, `rps7200/session.py:868-883`

No memmap, tifffile handle or NpzFile outlives its call anywhere in the GUI, so Delete, compact and migrate-raw are never blocked by a handle the window keeps. They are blocked by:
- a read in progress: `_load_full` on a just-selected pass, or verify or duplicates in another process;
- Defender, the indexer or OneDrive briefly holding new files;
- outside viewers.
Those cases are already reported as CSA-06 (no retry on the library's os.replace), GUI2-23 (Delete during a load), CRA-05 (roll rmtree) and CONC-06 (duplicates --delete); PLAT-11 adds the roll's own files. One more place with no retry is settings.save. On Windows the rename over gui-settings.json fails while a second window or an editor has it open. The failure is logged, and the next save retries. It matters only because the sheet's pre-commission decisions live solely in that file (OUT-16, CRA-04).

**Evidence (from the code):**

```text
The full-resolution view reads into memory on a thread: `image, _ = library.corrected(entry)` / `self._reads.put((seq, image, problem))` (gui.py:4593-4601). Readers close their files: `with np.load(path) as z:` (shading.py:95), and `tifffile.imread(path)` or `with open(path, "rb") as fh:` (tiff.py:290-296). The flush drops the mapping before unlinking: `image = None` ... `if filed: stuck += self._debug_unlink(item)` (direct.py:1089-1100). settings: `temporary.replace(target)` inside `except (OSError, TypeError, ValueError): return None` (settings.py:116-119), where manifests use `_replace` with REPLACE_RETRY_S (session.py:868-883).
```

**Failure scenario:** -

**Fix:** Route every rename over an existing file through one helper with session._replace's retry (library._write_atomic, _replace_tiff, compact, settings.save, migrate-raw).

<details><summary>Second reader's check</summary>

The only mmap in the codebase is the flush's (direct.py:1063), which is dropped before unlinking. The full-resolution view reads into memory on a thread (gui.py:4587-4603). ShadingReference.load uses `with np.load` (shading.py:95). tiff.read closes its file (290-296). settings.save does `temporary.replace(target)` with no retry and swallows OSError (settings.py:113-119), where manifests go through `_replace` with REPLACE_RETRY_S (session.py:865-883). This accurately answers question (5).

</details>

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Library entry directory | <library root, default cwd-relative 'library'>/<YYYYmmddTHHMMSSZ>_<slug(stock)\|unknown-film>[_f<slug(frame)>]_<dpi>dpi[_ir][-N]/ | directory; name is UTC, lowercase slugs of at most 40 characters each (about 110 characters in all), Unicode letters kept | n/a (container) | library.save -> _reserve (library.py:259, 460-481); debug flush (direct.py:1064); FrameWriter._write (session.py:1656) | library.entries/verify/reconstruct/compact; gui roll_entry_index (gui.py:5498); tools/library.py | The name is not portable. On HFS+ it comes back NFD while scan.json's id is NFC (PLAT-07). With a deep root and no LongPathsEnabled on Windows it can exceed MAX_PATH (PLAT-03). |
| Entry temp files during atomic swaps | <entry>/.scan.json.part, .raw.bin.gz.part, .scan.tif.part, .prescan.tif.part | UTF-8 JSON / gzip / TIFF | same as the file each replaces | library._write_atomic (486-491), compact (428-438), _replace_tiff (509-511), tools/library.py migrate-raw | nothing except os.replace | These are the longest names in an entry (15-17 characters), so they are the first to cross a path-length limit. An os.replace refused by Windows leaves them behind. |
| Debug spool | $TMPDIR\|%TEMP%/rps7200-debug-XXXXXXXX/NNN-image.npy, NNN-raw.bin, NNN-meta.json, NNN-shading.npz (once per reference), NNN-ccd_mask.bin | .npy raw pixels (uint8/uint16 HxWxC); raw bytes exactly as received; JSON meta and layout; npz float64 reference; mask bytes | raw | DirectScanner._debug_capture (direct.py:908-998) after each pass, with the device open | DirectScanner._debug_flush (direct.py:1031-1125) at close(); np.load mmap_mode='r' | Exact bytes, but it lives in the OS temp dir. macOS dirhelper, Windows Storage Sense and systemd-tmpfiles can delete it mid-session; a deleted directory is never recreated and deleted items are reported as 'kept' (PLAT-02). |
| Roll folder | <rolls, default cwd-relative 'rolls'>/<_safe(typed name, unbounded length) \| YYYY-mm-dd-HHMMSS local time>[-N]/ | directory | n/a | session.roll_dir/new_roll_name (session.py:794-834), created by ScanSession._roll after the seek (2312); gui._write_approved (3281) | gui rolls_on_disk/roll_summary/read_survey/open_roll; tools/scan_roll.py | The name is local time with no zone (ambiguous in the DST fold, PLAT-08). A typed name has no length cap (PLAT-03). A typed spelling that matches an existing folder only case-insensitively is kept as typed (PLAT-06). |
| Roll manifests | <roll>/survey.json, roll.json, approved.json (+ .bak, .legacy, .unreadable, .<name>.part) | UTF-8 JSON | n/a (records) | RollManifest via write_manifest (session.py:886-922, retrying _replace); gui._write_approved (3265-3337) | read_manifest, read_survey, read_approved, roll_summary, scan_roll --approved | No timestamp at all in the window's versions. frames[].entry and approved frames[].reference_entry are OS-native, cwd-relative path strings (PLAT-09). |
| Roll's own picture files | <roll>/frameNN.tif, prescanNN.tif, prescanNN-before.tif | TIFF (deflate when tifffile present), 8-bit prescans / 8 or 16-bit frames | corrected and oriented | FrameWriter._write -> export.write, in place (session.py:1673-1690) | read_survey/walked_prescans (gui.py:5183-5184), the frame-edge reader, NegPy | On Windows a file held open by another program is not rewritten; the old take stays under the name the new manifest records (PLAT-11). |
| Delivered copies | <out>/<_safe(roll)>_frameNN_<dpi>dpi[_ir].tif\|.jpg (+ .dng); <out>/prescans/...; single scans <out>/<YYYYmmddTHHMMSS local>_<dpi>dpi[_ir].<ext>; exports <folder>/<_safe(roll)>_<batch_name> | TIFF / JPEG q95 + LinearRaw DNG for infrared | corrected; JPEG lossy | FrameWriter._write (session.py:1673-1690) via _out_name (2908-2932) and _unclaimed; gui on_export_rolls (2757-2775) | the operator, NegPy | Not an archive. The name is local time for single scans and nothing links it to its library entry (PLAT-08). An unbounded roll name makes the component exceed 255 characters (PLAT-03). |
| Calibration cache and archive | <reference dir, default 'calibration'>/shading.npz (+ .shading.part.npz); <reference dir>/<YYYYmmddTHHMMSSZ>[-N]/data.bin, calibration.json, shading.npz, ccd_mask.bin | npz float64; raw calibration bytes; JSON | data.bin raw; shading.npz reduced | DirectScanner.save_shading (736-747), archive_calibration (749-804) | load_shading (713-734); archive read by nothing | data.bin is exact. Entries link to it by `extra.shading_origin.archive`, an OS-native, cwd-relative string (PLAT-09). |
| Window settings | gui-settings.json (cwd-relative or RPS7200_SETTINGS) + .part + .unreadable-<local time> | UTF-8 JSON | n/a | settings.save via ScannerGui._remember (gui.py:692-725) | settings.load at launch; _recall_sheet_state; roll browser 'Last opened' | `sheet` and `rolls` are keyed by folder basename as spelled (case and Unicode form). The replace has no Windows retry (PLAT-14). |
| Linux device permissions | /etc/udev/rules.d/60-rps7200.rules (copied from packaging/) | udev rule: SUBSYSTEM==usb, 05e3:0144, TAG+=uaccess | n/a | the operator (sudo cp) | udev / systemd-logind 73-seat-late | Grants only the active local seat; never exercised on hardware (PLAT-13). |

**Second reader's corrections to this table:**

- **Debug spool.** Only NNN-image.npy (through np.load mmap_mode='r') and NNN-raw.bin (through raw_path) are read back by _debug_flush (direct.py:1063-1072). meta, the shading reference and the CCD mask are filed from the in-memory item (item['meta'], item['reference'], item['ccd_mask']). The spooled NNN-meta.json, NNN-shading.npz and NNN-ccd_mask.bin exist only for manual recovery, so deleting them does not affect filing. _debug_unlink removes image, raw, meta and mask per pass but never the shared shading.npz, which goes only when the spool is removed with rmtree. The spool's shading.npz is written by ShadingReference.save with np.savez_compressed (float64 means, plus arrays in their own dtypes), not as a float64-only file.
- **Roll manifests.** Among the path links, only approved.json frames[].reference_entry is read back as a Path, by read_approved and then read_survey (gui.py:5471-5472, 5196). roll.json frames[].entry (session.py:1155), roll_membership.folder in entries (785-786) and extra.shading_origin path and archive (direct.py:724, 860) are written but no code reads them back.
- **`.unreadable` asides.** A manifest's `.unreadable` aside goes through _unclaimed, so it never collides (session.py:964). Only settings' `.unreadable-<local second>` can collide (settings.py:95-96).
- **Calibration cache temp file.** The temp file is `.shading.part.npz`, from `.{stem}.part.npz` (direct.py:744).
- **Window settings.** Default path: cwd-relative gui-settings.json unless overridden. Not otherwise re-verified.

## What the operator can do

- Type a roll name of any length, and any capitalisation, into the roll box; it becomes the folder name and the prefix of every delivered file.
- Rename a roll folder from the roll browser (a case-only rename is refused on Windows and macOS as 'already there').
- Keep the window open for days with RPS7200_DEBUG=1, spooling every unclaimed pass to the OS temp directory until quit.
- Commission a multi-hour roll from the contact sheet and leave the machine unattended.
- Run tools/check_scanner.py, pytest -m hardware or a second window while the first window holds the scanner.
- Put the checkout, library, rolls and output folder anywhere, including deep OneDrive for Business paths, exFAT drives and network shares.
- Copy library/ and rolls/ between Windows, macOS (APFS or HFS+) and Linux and open them there.
- Open --open-roll with a spelling that differs in case from the folder.
- Open frameNN.tif or prescanNN.tif in a viewer (or the Explorer preview pane) while re-scanning or re-walking into the same roll.

## What the operator should not do

- Let the host sleep, enter Modern Standby or restart for updates while a pass or roll runs: nothing prevents it, and it abandons the read in flight.
- Follow the Windows 'replace the driver with Zadig' advice when another window or a leftover python.exe may hold the scanner.
- Use a long roll name under a deep Windows root without LongPathsEnabled.
- Rely on the OS temp dir to keep a debug spool across a long-running window session.
- Match a delivered single-scan file to its library entry by the time in its name (local vs UTC).

## Mistakes nothing guards against

- A 30-frame roll left running on a laptop with default power settings: the host sleeps mid-read, the frame is lost, and the scanner may need a power cycle (PLAT-01).
- A roll name long enough to push the entry or roll folder past MAX_PATH: the failure comes after the seek has moved the film and after the scan. The first frame is lost (or filed invisibly as INCOMPLETE) and the roll stops (PLAT-03).
- A roll name of 233 or more characters: every delivered and exported copy fails on every OS while the roll carries on (PLAT-03).
- A window open over a weekend with debug on under macOS: Friday's probes and hold prescans are purged by the OS, the spool is not recreated, and the flush says they are 'kept' (PLAT-02).
- Running check_scanner.py while the window holds the scanner on Windows yields 'replace the driver with Zadig'; doing so mid-roll reinstalls the driver under a live session (PLAT-04).
- Typing 'portra' for an existing 'Portra' adds to that roll on Windows and macOS but makes a new roll on Linux. On macOS it also defeats the roll-in-use guard for a roll opened with that spelling (PLAT-06).
- Previewing a roll's prescan in Explorer during a re-walk leaves the old strip's picture under the new walk's record (PLAT-11).
- Moving a Windows-written library to a Mac leaves every reference_entry and calibration archive link unresolvable (backslash separators) (PLAT-09).
- Filing from a checkout without git on PATH (GitHub Desktop) or on an exFAT drive: every entry records driver_commit null, with no warning (PLAT-10).

## Dataflow notes

Device → host: libusb is loaded lazily by usb_transport._load_libusb (174-186), which takes the first candidate path that exists (PLAT-05). Transport._raw_open (444-464) opens the device with libusb_open_device_with_vid_pid; a NULL handle is explained by _why_not_found (466-494) per platform (PLAT-04). DirectScanner.read_planes (direct.py:1799-1913) polls READ with a monotonic idle timer; no sleep inhibitor exists anywhere (PLAT-01). The bytes land in last_raw and last_raw_layout (1882-1896).

Spool path (debug only): _debug_capture (908-998) writes NNN-image.npy, raw.bin, meta.json, shading.npz and mask into tempfile.mkdtemp. That directory is created once and never re-checked (925-928). close() (1134-1145) calls _debug_flush (1031-1125), which np.load(mmap_mode='r')s each item and calls library.save(raw_path=...). It sets image=None before unlinking (Windows-safe), and on failure it forgets the spool dir.

Filing path: ScanSession._file (session.py:2781-2906) picks the output paths (_out_name, local time, 2908-2932; _unclaimed, 2961-2973) and submits to FrameWriter._write (1618-1703). That calls library.save first (1656): _reserve makes the UTC-named directory (library.py:460-481), then INCOMPLETE, scan.tif, prescan.tif, shading.npz, ccd_mask.bin, raw.bin(.gz), and last scan.json via _write_atomic, whose '.part' name is the longest (486-491). Only then are the delivered copies written, each via export.write (session.py:1673-1690). A failed save stops the roll through _filed (1998-2014).

Roll folders: roll_dir (829-834) keeps a typed name that matches an existing folder (case-insensitively on Windows and macOS), otherwise _safe with no length cap (2946-2958), or new_roll_name in local time (794-808). The folder is created after seek() (2288/2312). Manifests go through write_manifest with a Windows retry (_replace, 868-883) and are cached by manifest_path.resolve() (2343-2345; case is not canonicalised on macOS).

Close: ScanSession._run's finally (1968-1996) closes the device, drains FrameWriter, rewrites unsaved manifests, and compacts plain entries (library.compact, 414-453: os.replace with no retry).

GUI reads: full resolution via library.corrected on a thread (gui.py:4587-4603), with no lasting handles. The roll browser renames with source.rename after a case-insensitive exists() check (2844-2863), deletes with rmtree (2837-2842) and duplicates with copytree (2779-2808). Settings are written by settings.save with a single replace (settings.py:104-119), and 'sheet' and 'rolls' are keyed by folder basename (gui.py:2601, 2615). Links written back into manifests and entries (entry, reference_entry, roll_membership.folder, shading_origin.path/archive) are str() of relative, OS-native paths (PLAT-09). read_survey turns reference_entry back into a Path (gui.py:5196).
