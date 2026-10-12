# rps7200

Console and Python scanning for the **Reflecta RPS 7200** film scanner, capturing
**RGB and infrared together** in one pass at 16 bits.

The infrared plane is handed to you as a plane of its own, flat-fielded like the colour,
rather than being spent internally on dust removal, so you can run your own IR-based
cleanup.

> **This is hobby reverse engineering, and it drives your hardware.** See
> [Legal](#legal) before you point it at a scanner you care about.

## Why this exists instead of just calling `scanimage`

The scanner works under SANE's `pieusb` backend, but three things stop the stock
command-line frontend from getting the data out.

**1. `scanimage` cannot write the 4-channel RGBI frame.** In `pieusb.c`, RGBI mode sets
`colors = 4` but reports the frame type as `SANE_FRAME_RGB` — the source comment reads
`/* was: SANE_FRAME_RGBI */`. `scanimage` hardcodes 3 channels for that frame type, so it
writes a header claiming 3 samples/pixel over a stream carrying 4. The result is sheared,
with the IR smeared through the visible channels.

**2. The device name changes on every open.** The backend resets the scanner during
discovery, so it re-enumerates and its SANE name moves — `020:057`, `020:060`, `020:010`,
… A name from one `scanimage -L` is already stale by the next command. Enumeration and
open have to happen in the same process.

**3. Prescan calibration lives on the open handle.** `sanei_pieusb_analyze_preview` stores
the per-channel bounds on the scanner handle, and `calibration="from preview"` reads them
back. Close the handle and the calibration is gone, so a shell loop cannot prescan and
then scan.

This package does not use SANE at all. It drives the scanner directly over USB the way the
vendor software does, holds one session across prescan and scan, and writes the 4-channel
data itself.

## Install

```sh
pip install -e .
```

numpy is the only hard dependency, plus a bundled libusb on Windows, where there is
nowhere conventional for a system one to live. The GUI adds nothing, because Tk ships with
Python and its `PhotoImage` reads the raw PPM bytes `rps7200.preview` produces. libusb is
loaded the first time something actually talks to the device, so decoding a stored scan,
correcting it or writing a TIFF works on a machine with no scanner drivers at all.
`tifffile` is optional and used automatically when present; the built-in TIFF
reader/writer is complete on its own.

## Supported platforms

**macOS, Linux and Windows.** Everything that does not touch the device — decoding,
correcting, the TIFF and DNG writers, the library, the window in `--demo` — works on all
three with nothing but Python and numpy. Driving the scanner needs libusb, and on two of
the three it needs one more thing.

The development commands are the same everywhere. Each Makefile recipe is one call into
`tasks.py`, because GNU make on Windows uses `cmd.exe` unless a POSIX `sh` is on PATH.

### macOS

```sh
brew install libusb
pip install uv && uv sync --all-groups
```

### Linux

```sh
sudo apt install libusb-1.0-0 python3-tk      # or libusbx, on Fedora and RHEL
sudo cp packaging/60-rps7200.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
pip install uv && uv sync --all-groups
```

The udev rule is what lets you open the scanner without being root; replug it afterwards.
The device is vendor-specific (`bDeviceClass 0xff`), so the kernel binds no driver and
nothing needs detaching — permissions are the whole problem.

### Windows

```powershell
pip install uv
winget install ezwinports.make      # GNU Make, native, no MSYS needed
uv sync --all-groups
```

**The scanner's driver has to be replaced, for now.** Windows binds its own `usbscan.sys`
to it — via an INF from Pacific Image, the one CyberView installs — and libusb cannot open
a device another driver holds. Every comparable project does the same thing; there is no
way round it that has been made to work.

> **Read this first.** Replacing the driver **stops CyberView and VueScan seeing the
> scanner**, because they talk to it through the driver you are replacing. It is
> reversible, but not by itself — see *Putting it back*.

1. **Plug the scanner in and switch it on first.** Zadig only lists devices that are
   enumerated.
2. Get [Zadig](https://zadig.akeo.ie/) — one `.exe` — and run it **as administrator**.
3. **Options → List All Devices.** This is the step everyone misses: by default Zadig
   hides devices that already have a driver, which is exactly what this one is.
4. Pick **Multiple Frames Film Scanner**, and check the line underneath reads `USB ID
   05E3 0144`.
5. Set the target driver to **WinUSB** (libusbK works too), then **Replace Driver**.

Then check it worked. This sends nothing but INQUIRY and READ STATE and moves nothing:

```powershell
uv run python tools/check_scanner.py
```

It climbs a ladder, stops at the first rung that fails, and prints what the failure means
— including, before Zadig, the whole paragraph about replacing the driver. After it, the
same command reaches rung 5 and reports model, firmware and CCD size. `uv run pytest
tests/ -m hardware` asserts the same nine things as a test run.

#### Putting it back

Zadig has no undo. In **Device Manager** the scanner is under *Universal Serial Bus
devices* after the swap, **not** *Imaging devices*, which is where it was and where people
look. *Uninstall device*, tick **Delete the driver software for this device**, then replug.
Windows re-binds the original Pacific Image driver and CyberView sees it again.

## Use

One calibration per power-on, then scan. **Load the film first**, wait for the lamp
(about 80 s), then calibrate and scan without unloading it — CyberView does everything
with the film in the transport, and calibrating an empty one is a state the vendor
software never creates. The calibration still measures the sensor rather than the film:
its frame is `(0, 3431, 10343, 6888)`, the lower part of the transport, which the film
does not cover.

```sh
# calibrate, scan, correct, and file the result with its raw bytes
uv run python tools/scan.py --dpi 1800 --ir \
    --stock "Kodak Gold 200" --frame 3 --notes "test frame"

uv run python tools/scan.py --dpi 600                  # faster, RGB only
uv run python tools/scan.py --dpi 1800 --no-shading    # raw pixels, for comparison
uv run python tools/scan.py --dpi 1800 --reuse         # reuse the cached reference
uv run python tools/scan.py --film positive            # a slide: keeps its colour cast
uv run python tools/scan.py --dpi 1800 --ir --no-fast-ir   # the old untied IR pass
```

`--fast-ir` is the default and is the largest single cost switch in the tool: it ties the
infrared plane to the scan resolution instead of paying a flat floor. See
[Resolution](#resolution). `tools/scan.py` meters only when asked (`--auto-exposure`);
without it, or `--exposure-scale`, a pass goes out at the device's own settings.

Before a calibration `tools/scan.py` and `tools/scan_roll.py` ask whether the film is in
the transport, and refuse where nobody can answer — a run in the background — unless given
`--film-loaded`; so does every hardware probe in `tools/`, through `tools/probing.py`.
`--reuse` says how old the reference it loads is. Each tool says what the
run should cost before it opens the scanner, and when to run it in the background. The
delivered file never replaces one already there: `--out` takes the next free name. An
`--out` that cannot be written says what still holds the pass — its library entry, the
folder it was kept in, or nothing — and exits 1; nothing writes the file again from an
entry yet. A picture written whose `.json` record could not be is said as that, and exits
1 too. Before the scanner opens, each tool refuses a folder above `--library`, `--out` or
`--reference` that is not there, a path past Windows' 259 characters, and a disk that
would keep less than 2 GB free beyond what the run writes (debug filing's spool
included), and warns below twice that. The `library/`, `rolls/` and `calibration/` a
tool uses when no flag names them are those in the folder it is started in: used there
if they exist, and made only in the driver's own folder — started anywhere else, the
tool refuses, naming each folder and the flag to give, rather than file into a new
library nothing else looks at.

### What `--film` changes

It is a metering and delivery decision, not a mode the scanner has. Every pass is RGB or
RGBI; there is no hardware black and white mode worth using, and CyberView does not use
one either.

| `--film` | metering | infrared | delivered |
|---|---|---|---|
| `negative` | per channel — takes the orange mask off before the ADC | allowed | 3 or 4 channels |
| `bw` | per channel — silver halide has no colour record to protect | **refused** | one channel, the average of R, G and B (`--mono-channel G` for a single one) |
| `positive` | locked — the cast *is* the picture | allowed | 3 or 4 channels |
| `kodachrome` | locked | **refused** | 3 channels |

**Infrared is refused on black and white and Kodachrome** rather than warned about.
Silver grain blocks infrared exactly as dust does and Kodachrome's cyan layer absorbs it,
so the plane comes back holding the photograph instead of what is lying on top of it —
measured at +0.97 correlation with green. Chromogenic black and white (XP2, BW400CN,
anything C-41) is dye-based and cleans properly, so scan it as `--film negative`.

**Black and white is deliberately not locked**, unlike a slide or a Kodachrome whose cast
*is* the picture. Silver halide has no dye layers, so what looks like a cast is the film
base and the sensor's own response; holding the channels together to preserve it cost
green and blue about half a stop each on repeat pairs at 900 dpi. This diverges from
`nkscan`, which locks monochrome — right for a Coolscan, whose exposure register is 26
bits wide with no channel that runs out, and wrong here, where red's ceiling is a rail the
lock propagates to the other two.

A whole strip or roll, unattended:

```sh
uv run python tools/scan_roll.py --dry-run --frames 6                 # prescan and advance only
uv run python tools/scan_roll.py --dpi 1800 --ir --frames 6 \
    --roll 2026-08-28-gold200 --stock "Kodak Gold 200"
```

Every scan is filed in `library/` by default, with the raw bytes the scanner sent, the
session's shading reference and that pass's CCD mask. None of those can be recovered from
a TIFF, and without them a scan can never be re-decoded or re-corrected. Every prescan a
walk or a roll takes is filed the same way, in an entry of its own, from `--dry-run` as
from the window, and so is the picture a hold or an aim replaced (tagged `before`); a
frame entry's own `prescan.tif` is the corrected prescan, and its record says so. A pass
read in full that then failed is filed too, tagged `failed`. Each calibration's own bytes
are kept, because the reference is a reduction of them — a calibration that failed part
way included. They are kept in the library, once each, in `calibrations/<sha256 of the
lines>/`, so copying `library/` takes them along. An entry filed since 2026-09-28 whose
reference names its calibration records that id; an older one names a path, and one
corrected by a cached reference that named nothing names nothing — both are found by
content once the path is gone. `calibration/` beside the library holds the cached
reference, and the archives of calibrations taken before 2026-10-09, in
`calibration/<UTC time>/`:

```sh
uv run python tools/library.py list          # what is stored
uv run python tools/library.py verify        # checksums and completeness
uv run python tools/library.py reconstruct   # re-decode every scan with current code
uv run python tools/library.py calibrations  # re-reduce every archived calibration
uv run python tools/library.py migrate-calibrations  # copy the old ones into the library
uv run python tools/library.py duplicates    # what is redundant, and why
uv run python tools/library.py compact       # gzip what a killed window left plain
uv run python tools/library.py file-spool    # file what debug filing left behind
uv run python tools/library.py trash         # what Delete moved aside
uv run python tools/library.py restore BATCH|ID   # bring it back
uv run python tools/library.py empty-trash --write   # remove it for good

uv run python tools/make_comparison.py <entry id or path>   # raw / corrected / inverted
```

**Delete moves things into a trash; nothing removes them until you empty it.** The
window's Delete on a pass, the roll browser's Delete and `duplicates --delete` each move
what they take, whole and by one rename, into `.trash/<UTC time>/` inside the library or
the rolls folder it was in, with a `trashed.json` saying what went, when, by whom and why.
`tools/library.py trash` lists each delete; `restore` takes a delete's folder name, or an
entry's id, a roll folder's name or `<roll>/strip-03`, and brings it back at once -- a
slide's infrared pass before its colour pass, an entry whose id the library holds again
left in the trash and said, a roll whose name is taken since, or held by a program about
to write into it, restored as the next free `<name>-2` with who held it said. A strip of
a roll of strips deleted on its own goes back into its own roll and nowhere else: while
that roll is not there, the roll there by its name is another film's made since, its
place in it is taken or another program has the roll, it stays in the trash and the
reason is said; a strip's name alone that two rolls' deletes hold is refused, naming both.
`empty-trash` removes them for good, the older ones only with `--older-than DAYS`, and is
a dry run unless given `--write`; run it with no window open, as `compact --write`. Until
then the space stays used: `verify`, `list`, the window's log as it opens and the roll
browser say how many GB the two trashes hold, and the window's questions say a Delete does
not free the disk. When the window's Delete moves a roll frame's entry, the frame is marked
first in each `roll.json` that names it as that frame's entry -- every roll under the rolls
folder and in its trash, the folder the entry was filed for, and the rolls the window is
writing; a roll kept anywhere else is not found (`entry_deleted`, beside the entry's name,
which stays). The frame counts as not scanned while that entry is out of the library --
reopening the roll, finishing it, the browser and `tools/scan_roll.py`'s advice offer it
again -- and as scanned once it is restored, with nothing written. An entry that is only
an earlier take of a frame scanned again since marks nothing, and the question says so.
Every take of a frame stays named in its record's `takes`, in `roll.json` and in a walk's
`survey.json`, and each take's entry stays in the library with its raw bytes; the folder
keeps only the latest `frameNN.tif`, and Export makes the latest take. Nothing exports an
earlier take yet (TODO.md, beside heavy work with the scanner open). A roll Delete takes
the passes the library refused, kept in its `unfiled/`, with it: `verify` still fails for
them there, and `empty-trash` leaves that delete alone, naming them, until each is moved
into the library or it is given `--include-unfiled`.

The window's Delete asks twice, as before: whether to move the entry to the trash as
well (No keeps it), and then, in its own words, how much stays on the disk. It goes at
once, even while the scanner works -- a rename on the library's own disk -- and a slide's
infrared pass goes with its colour pass. On a pass whose entry is still being written it
says so and keeps the entry; press Delete again a moment later to move it too.

`duplicates --delete` moves an entry only when its raw bytes (or pixels) are identical
to one kept: a shared description of what the scanner was asked is not enough, because
two different photographs with empty film notes share one. The copy kept is one that
passes `verify` on disk. An entry a roll's or a walk's records name (under `--rolls`,
default the `rolls` beside the library, and in its trash) is never moved, so no roll is
left naming one that went and none is marked; nor is one holding raw bytes or a
calibration its twin lacks. Otherwise the one that carries more is kept. The
twins go into the library's trash as one delete, so one `restore` undoes the run, and the
space comes back with `empty-trash --write`. `verify` checks that every
file an entry's record names is there and matches its checksum, that no partial write
(`.part`) is left, and every calibration archive -- each one the entries name, and each
one the library keeps; it names an entry a crash or a full disk cut short (it still holds
an `INCOMPLETE` marker) with what the marker says of the pass -- resolution, roll and
frame, the raw bytes' length and layout, and the files that reached the disk, the record
among them where only the marker's removal was cut short or refused (`save` warns of that
and hands the entry back) -- says which command finishes a `compact` or `migrate-raw` a
kill stopped, notes in one line when calibrations the entries name are still outside the
library -- a note, not a failure: `migrate-calibrations --write` copies them in, and
never moves or deletes the originals -- and leaves out scans taken raw on purpose;
`tools/library.py tag ENTRY --add uncalibrated-on-purpose` marks an older one that was.
`reconstruct` exits non-zero on a decode that changed or now raises and on a file the
record names that is gone or damaged, and not on a pass filed tagged `failed` because its
bytes would not decode, which it reports on a line of its own -- and says when today's
decode reads one -- nor on an entry filed with raw bytes its record says cannot be its
picture's (`raw.disagrees`: `save` warns of it, `verify` and `list` name it), which it
marks `?` on a line of its own; `calibrations` on a reduction that changed or an archive
that is damaged. `compact`, `migrate-raw` and `migrate-calibrations` are dry runs unless
given `--write`; run `compact` and `file-spool` with the scanner closed.

`compact --write`, `migrate-raw --write`, `migrate-direction --write`, `duplicates
--delete`, `empty-trash --write` and `file-spool` rewrite or remove what is in the
library, so each refuses -- changing nothing, and naming who -- while anything else holds
it: a window open on it, with a scanner or without, `tools/scan.py` or `scan_roll.py`
filing into it, or a script with the scanner open. Quit the window first. `tag`,
`restore`, `trash`, `list`, `verify`, `reconstruct` and the dry runs run beside a window;
`list` and `verify` say who holds the library as they
look, `verify` calls an entry whose save is still writing it "being written now" rather
than a problem, and a `.part` beside an entry or a calibration, while the library is held,
a note. `file-spool` leaves a spool whose session is still running, and says so.

`verify` also names what crashes, kills and failures left behind, each with what clears
it, in the library and in the rolls folder beside it (`--rolls` names another). Only a scan
that is not in the library fails it: passes left in a debug spool no running program
holds (`file-spool` files them), and passes the library refused and kept elsewhere -- in a
roll's `unfiled/`, in `rps7200-unfiled` in the system's temporary folder, or beside an
output folder (each such entry names the library that refused it, and that library keeps a
pointer to it in `unfiled-elsewhere/`) -- until they are moved into the library.
Everything else is a note: entries still plain (`compact --write`), a Delete or a
Duplicate that did not finish, a move into the trash or an `empty-trash` a kill cut short
(`restore` brings back what moved; `empty-trash --write` finishes the other), an
`approved.json` with no roll beside it -- what a roll that never started leaves -- a roll
record set aside as unreadable, a partial write (safe to delete), and what the trashes
hold. The window logs one line of this as it opens, and `tools/scan.py` and
`tools/scan_roll.py` print it before they open the device; nothing is filed, compacted or
emptied on its own. The locks
live in a folder of their own (`%LOCALAPPDATA%\rps7200\locks` on Windows,
`~/Library/Application Support/rps7200/locks` on macOS, `~/.local/state/rps7200/locks`
on Linux, chosen by who runs it and never by its environment), never in the library, and
go with the process that took them however it ends.

A pass that fails with nothing of it to file — no line arrived, which is how a wedge
looks from here, or its caller kept no bytes — leaves a small record instead, whatever
the debug setting: `failures/<UTC time>_scan.json` in the library it would have been filed
in (`_calibration.json` for a calibration that read nothing), holding what it was asked
for, every command it sent with what came back, what it raised, the last READ STATE, the
INQUIRY and a hash of the driver's source. Where the library cannot be written it goes to
`rps7200-failures` in the system's temporary directory. They are not entries; `list` and
`verify` say how many there are and where, and `verify` does not count them as problems.

`make_comparison.py` writes `1_nothing_done.tif`, `2_corrected.tif` and
`3_corrected_inverted.tif` from one library entry, through the path every delivered file
takes: the raw decode as it was filed (`library.load`; `reconstruct` is the check on the
decode itself), `library.corrected(entry)` — what Save As delivers — and its inversion.
It refuses an entry whose correction state is not "applied", and needs Pillow
(`uv sync --extra jpeg`, or the dev group) for the two preview PNGs it writes to
`previews/`.

From Python:

```python
from rps7200.direct import DirectScanner

with DirectScanner() as s:
    s.ensure_shading("calibration/shading.npz")  # once per power-on, film loaded
    image, meta = s.scan(resolution=1800, infrared=True)
    rgb, ir = image[..., :3], image[..., 3]      # (H,W,3) and (H,W), uint16
```

`scan()` refuses a corrected pass that no calibration covers rather than calibrating
inside it, and `shading=False` asks for raw pixels on purpose. A script like this files
nothing by itself; run it with `RPS7200_DEBUG=1` (see
[Filing every scan automatically](#filing-every-scan-automatically)) and each pass is filed
with its own raw bytes once the session closes.

## The window

```sh
make run                        # the scanner
make run-demo                   # no scanner: stored library entries drive the window
make run-sheet                  # no scanner, no film: the contact sheet on a
                                # stored walk, re-measured on every launch
```

`make run-sheet` opens `rolls/aligned-strip` with `--demo --look-only`. `rolls/` is
gitignored, so that walk exists only on the machine that walked it; `--open-roll` names
another. `--look-only` needs `--demo`: only the stand-in can be told there is no film.

One window per library, rolls folder and settings file: a second one that shares any of
the three -- `--library X` alone still shares the rolls and the settings -- prints one line
naming the first (its process, since when, which checkout) and exits, so `make run-demo`
and `make run-sheet` refuse each other. A demo window and the real one share nothing and
run side by side. A roll folder another program is writing into (`tools/scan_roll.py`)
is not renamed, deleted or copied; one another window is showing is not renamed or
deleted, and copying it goes ahead. A window shows a roll until it opens another or quits
-- closing its contact sheet does not let go of it. A roll into a folder another program
holds is refused before the film moves. Where a lock cannot be taken at all -- a locks
folder that cannot be written -- the window opens all the same and says so in its log.

The window's library, rolls and calibration folders are `library/`, `rolls/` and
`calibration/` in the folder it was started in, unless `--library`, `--rolls` and
`--reference` name others. Folders that are there are used wherever that is; missing
ones are made only in the driver's own folder, the main checkout. Started anywhere else
— double-clicked in `tools/`, or `make run` in a git worktree — it prints one line naming
each missing folder and its flag, and exits, rather than file into a new library that
`make verify` never sees. A worktree is never the main checkout: run one with the three
flags naming the shared folders. `--demo` keeps everything under `demo/` and is exempt.

On Windows the three start the window apart from the console they were typed in and
return once it is running, because closing a console there ends everything attached to it —
the scanner thread inside its read included — and nothing can hold that off for more
than about 5 s. What the window prints goes to `logs/window-<UTC time>.log`
(`demo/logs/` for the two demo targets), and the path is printed; one that ends at
once has its log shown there instead. A terminal that keeps what it runs in a job the
window cannot leave gets a warning in place of that assurance: closing it may still end
the window, so Quit in the window first. On macOS and Linux the window stays attached
to the terminal, as before.

The demo decodes each entry's **raw bytes**, not the TIFF beside them, so it runs the same
deinterleave a real pass runs, and corrects last with the calibration that describes the
pass, as `DirectScanner.scan` does. It picks its picture by film type, answers at the
resolution asked for, and refuses what the device refuses, in the driver's own words:
infrared on black and white or Kodachrome, a corrected pass before any calibration, a pass
wider than any reference. Every pass hands the session raw pixels and bytes of its own, so
an entry it files re-decodes to what it holds and corrects to what was shown; a stored
prescan or a test card is a finished picture with no calibration, and comes back as read.
A roll is the driver's own `DirectScanner.scan_roll`, run on the stand-in. Its output goes
under `demo/` — a `--library`, `--rolls`, `--reference`, `--settings` or `--out` outside it
is refused at launch, and an output folder outside it chosen in the window is used and
said once in the log, its pictures being made up under the names real scans get — its
metering is filed as simulated, and Delete on a filmstrip frame leaves alone a
library entry that is not the demo's own; its own go into `demo/library/.trash`, by the
same code, and the pictures it draws never come from a trash.

The first roll in a demo session walks the same strip it always has. Every roll started
from the Roll button after that loads another strip. It is drawn from every library next
to the one shown, such as `library 2`, one entry per photograph, with pictures not shown
yet coming first. Here that is 125 photographs, so the second and third rolls are new
throughout. Scanning chosen frames from the contact sheet scans the strip that was walked.

Prescan, scan, walk a roll, and look at what came off. The filmstrip along the bottom
holds every pass of the session; the channel selector switches between RGB and R, G, B or
**infrared alone**, which is the one plane no ordinary viewer will show you. The preview
is inverted by default so a negative can be judged by eye, and that inversion is display
only — what reaches `library/` is the raw negative. Inverting for real is NegPy's job.

The scanner sometimes reads a pass bottom-up, most often the prescan straight after an
infrared scan. The pass's own first and last lines say so. It is turned upright when it is
decoded, the caption under the picture says "read bottom-up, turned upright", and the
library entry records which way it was read. Only the rows are ever reversed, never the
columns, so where a frame sits along the strip is unaffected.

Opening the window claims the device, asks it who it is and reads the transport's frame
counter (`READ_STATE`) for the readout, and nothing else: no calibration, no lamp, and
nothing moves until a button is pressed. Nothing is asked either, and every setting can be
changed before calibrating. The first button that would take a picture — prescan, scan,
a roll or a walk, the contact sheet's scan, or one of their keys — says that without a
calibration no picture can be scanned and offers *Calibrate now*; the Calibrate button
opens the same prompt. It asks you to tick *The film is in the transport* first, every
time it opens, because only you can see it: *Calibrate now*, or Return, starts nothing
until the tick is set. Loading the cached reference moves nothing and needs no tick; with
*reuse* remembered, the button loads it without asking only when it was written since the
window opened, and an older one goes through the prompt, which says how old it is. Once
a calibration is started, nothing asks again.

**The contact sheet.** A dry run walks the strip prescanning and advancing only — about 20
seconds a frame — and opens every picture it found in a grid with its frame number and its
measured contrast. (A position you set by hand replaces the contrast with the offset in
units of the transport's own adjustment parameter; a frame already scanned reads
*scanned*.) Tick what is worth having and only
those frames are scanned: the roll goes to the first ticked frame by the transport's
counter, winding back or advancing from wherever the film is, and an unticked frame costs
its ~7 s advance instead of the minutes a scan would. Seventeen frames at 3600 dpi RGBI
is about an hour and a half, and a strip with four keepers should not cost the same as
one with seventeen. Return in a frame's big view leaves a frame already scanned unticked,
and the question before the roll names every chosen frame that is scanned already, whose
new take replaces its `frameNN.tif` (the library keeps both). The sheet stays open until
the roll has been handed over. The walk writes `survey.json` and a `prescanNN.tif` per
frame, so a strip can be looked at again tomorrow instead of walked again.

The sheet can be opened again while its roll runs, and changed: ticks, turns and
positions set then are for the next roll, and come back after a restart. The roll running
scans each frame the way it was commissioned and the window shows it so as it arrives, so
a cell turned since can differ from the frame just scanned -- the log says so as the turn
is made. Each frame is marked *scanned*, unticked and badged as the roll held it, as it is
filed, the last ones after the roll has ended included; one ticked again on purpose stays
ticked, after a restart too. The sheet offers no "nudge" tick: every frame ticked is held
to its position, and a held frame is never aimed, so it acted on nothing. A roll
commissioned from the sheet, or by `tools/scan_roll.py --approved`, records "aim each
frame" as not applicable, and reopening its walk keeps the walk's own answer.

**A film is one roll, and each strip of it a folder inside it** (see *Rolls of strips*
below): the Film panel's roll box names the roll, `rolls/Gold200/`, and each strip walked
under it is `strip-01`, `strip-02` and on. With the box empty a walk starts a new roll, by
the date and time to the second (`2026-09-25-143012`), and the box shows that name; the
next walk with it still in the box is that roll's next strip. A grey line under *Scan roll*
always says what the next walk will be -- `a new roll: frames from 1`, `Gold200: strip 3
next, numbered from 13`, or that strip 2's walk stopped and the next walk adds to it --
and *New roll* empties the box, so the next walk starts another roll. The box is not kept
across a restart -- a stale name there would file today's strips under yesterday's film --
so after one the line reads `a new roll -- or Continue Gold200: strip 4 next, numbered from
19` with a *Continue Gold200* button beside it, which puts that roll back in one click;
nothing goes under it unless you press it. A name typed that finds a roll from before
strips takes no second strip there: a walk goes into `<name>-2` (or the first of `-3` ...
that is a roll of strips or not there yet), the question says why, and the box then shows
that name. The question before a walk or a roll says where it goes -- the strip and its
numbers -- and what is there already. Each strip folder is what a roll folder always was:
beside the walk's files it holds the strip's `roll.json` and `frameNN.tif`, and
`approved.json`, the sheet's decisions. Each manifest is written beside and renamed over; a run's first write
keeps the file it replaces as `<name>.bak` (a Delete marking a frame leaves that copy as
the run kept it), one that cannot be read is set aside as
`<name>.unreadable` and said, and one numbered before frames were placed on the strip is
kept once as `<name>.legacy` before a resume renumbers it. In `roll.json` a frame is
`done` only once it is filed, with its library `entry`; one that could not be filed stays
not done and records a `filing_error`, and where its raw data was kept instead. Each frame
records the `rotation` and `flipped` its file was written with, and whether its own
`frameNN.tif` was written -- `file` once it is, `file_error` with why not, a slide's from
its picture's filing -- so a `frameNN.tif` left by an earlier take is never taken for
this one's. Every scanning run is kept in `runs`, earlier ones carried as the frames are:
its own settings, where its copies went (`output`: folder, format, quality), when it
`started` and `finished`, why it `stopped` where it did not simply finish -- the driver
giving up after its failures in a row among the reasons (`cut_short`) -- how far it got
(`reached`), and where it found the strip's end (`strip_end`). The top of the file says
the last run's, as it always has. A walk's
record names each prescan once the writer has written it, with its library entry
(`prescan_entry`) and the file's checksum (`prescan_sha256`), and never a file another
frame's record names; a prescan that could not be written says why in `prescan_error`
(`not written: ...` beside an entry that was filed), and reopening the walk says that
frame is left off the sheet. Only that file decides it: a copy in the output folder's
`prescans/` that could not be written leaves the sheet whole. Reopening reads each
prescan on its own: one that cannot be read, or no longer matches its checksum -- a power
cut can leave an uncompressed file reading as plain black -- is shown from the walk's own
library entry for it (its `prescan_entry`, never `approved.json`'s reference), corrected
with today's code, and the log says so; with no such entry, or one that is not that
frame's prescan or cannot be corrected, the frame is left off and the log says why. A
file the walk lists and the folder no longer holds is said too. `scan_roll --approved`
reads them the same way, and holds a frame it left out to nothing. A frame you put back
where the walk had it stays yours: `approved.json` marks it `as_walked`; a position the
detector decided carries what it read (`reading`) and which code read it (`read_by`), and
a position you set carries the detector's own reading of that frame beside it
(`detector_reading`).

The Roll panel takes a range, *first frame* to *last frame*, both included: 1 to 20 is
twenty frames. Leave *last frame* empty to go to the end of the strip. Walking again
while there is a sheet asks whether to keep it. *Yes* adds the new frames to that
strip's sheet and its `survey.json`, and the ticks, positions and turns already set on it
stay. A frame walked a second time replaces its old prescan, and a position set on
that frame is dropped, because it was measured from the old prescan. *No* walks the
next strip of the roll -- the question names it and the number it starts from, and what
closing this strip now fixes -- and asks you to put it in the way the last one went in.
The default button follows the strip: *No* after a walk to its end, *Yes* after one that
stopped; the meanings never change. A strip whose walk stopped is opened first, quietly,
when no sheet is up, so the same question offers to add to it before the next is
numbered: its own settings come back into the window before anything is read from it,
the question names those that moved, and your first and last frame stay as you set
them. So a walk of 1 to 10 on a strip of twelve is finished by walking 11 to the end,
not by walking the whole strip again. With an earlier strip's sheet up while the roll's
last strip stopped, the question says so and how to add to that one, and its default is
*Cancel*, since neither answer would. Adding to a strip whose numbers are fixed -- a
walk or a roll into it -- says first that a frame past them takes the number after the
roll's last, for good, and that the first such number closes the roll's open last strip
at the frames it had. Once a roll has 36 frames the question asks whether the next
strip is from another film, which *New roll* starts; so it does after a strip shorter than
the one before it, which is as a rule the film's last. A film that ends on a full strip
short of 36 -- 24 exposures -- is said only by the question naming the roll and the
number the next strip starts from: read it before *No*.

A sheet of a strip is titled by its roll and its numbers -- *Contact sheet -- Gold200,
strip 2 (frames 7-12)* -- each tick reads the frame's number across the roll (*Frame
8*), and its caption the place on the strip beside it (*strip 2 · 2*) wherever the two
differ; strip 1 and a roll from before strips read as they always did. Ticks, positions
and turns are places on the strip, as the transport counts. On the roll's open last
strip, a cell's right-click menu offers *Strip ends here*: a stub walked past the end of
the film's strip -- a part frame, a blank -- is left out of the numbers before the next
strip fixes them, its tick is taken off, and a scan of it is refused; *Count to the end of
the strip* undoes it. It is refused, in words, while the scanner works and past a frame
already scanned there. Set too early and found out once the next strip has fixed the
numbers, a frame past it scanned by name -- ticked, or with first and last frame on it --
takes a number after the roll's last, said in the question; a roll to the end of that strip
is still refused. The first frame of a new strip's walk is held up against the
strip before it's as it arrives, and the log warns where they look like one picture --
the strip in the transport was not swapped. A warning only.

Each frame's position is proposed by the frame-edge detector (`tools/frame_edges`),
which centres the picture between the edges it reads and draws them as red dotted lines.
It reads the prescans in the background as the walk delivers them, or as a stored walk
is opened, so the sheet never waits for it. The light beside the scanner's in the top
right is blue while it reads, green when every frame has its final reading, and red if
it failed on one, with a count beside it (`12/36`). While a roll runs, the middle of
the header says which frame it is on, as in *frame 12 of 36*. A reading only one of the
detector's members makes is labelled *unconfirmed* for you to accept or not, and a walk
that aims each frame does not move film on one; a frame the members call blank, or not a
negative, gets no position; a member that failed is named in the frame's note. A
position you set by hand stands whatever the detector reads. *Reset* in a frame's big
view puts that one frame back where the detector puts it, and *Reset positions* under the
sheet does the same for the whole roll after asking.

Every reading that is kept can be made again offline, so a detector changed since can be
checked against what it decided. It keeps each of the four members' own answers, not only
their vote (`members`), a digest of the prescan it read (`image_sha1`), and which frames
it was read against with one digest of their pictures (`against`, `against_sha1`). A walk
or roll that aims each frame keeps each frame's judgment in its `survey.json` or
`roll.json` (`registration.correction.ensemble`, the transport's indexes in
`against_index`), what each frame told the others (`registration.base`, its summary
exactly), and what a hold's second look read after each move (`second_look`). A
`scan_roll --approved` roll keeps what the detector read of each frame it holds on that
frame's record, as `approved.json` does: `reading`, or `detector_reading` beside a
position from `--positions`. The code is named by the driver's and the edge reader's
source hashes as they were imported, beside each reading and never once for a whole file
(`read_by`: on each frame of `approved.json`, and on each frame record in `survey.json` or
`roll.json` that holds a reading) -- a roll resumed, or a walk added to, carries the
earlier runs' frames on under the settings the latest run writes, and their readings were
made by the code that ran then. None of this bookkeeping can cost a frame: what it could
not keep it says in `record_error`.

**A roll that died can be finished, however much later.** *Rolls …* is a table of every
roll on disk — Roll, Frames, Resolution, Film, Created, Last opened, Size — sortable by
any column, with a search box and an "only unfinished" tick. A roll of strips is a row
(`Gold200 · 6 strips`, its frames counted across them) with its strips under it, each by
its numbers (`strip 5 · 25-30`) or as in the rolls' trash; a roll from before strips is a
row with none. A roll is unfinished while a frame it asks for is not done, or a run to the
end of the strip stopped before it got there and neither its walk nor a later run found
the strip's end -- `6 of 6, strip from 7 not reached` -- and
"finished" by what is left, never by a count. "Only unfinished" shows a roll while any of
its strips has anything left, and those strips under it. Unfinished rolls are marked
in amber, and so is one whose library entries have gone, because that is the one that
cannot be exported. A selection can be **exported** (every frame re-corrected from the
library at full resolution with today's correction code, not a copy of what was written at
the time — the entry `roll.json` names for each frame, so a duplicate and its original
each export their own; a roll of strips' frames in strip order, each named by its number
across the roll, `frame01` to `frame36`), **duplicated** to the next free `-2`,
**renamed**, revealed in the file manager, or **deleted**. *Open*, *Export* and *Delete*
work on a roll or on one strip; *Rename* and *Duplicate* on whole rolls, and on a strip's
row say so instead -- a strip's folder is named by its place in its roll. A selection of a
roll and strips of it is the roll. A case-only rename (`gold200` to `Gold200`) goes through
on a disk that folds case, and a name typed in another case finds the folder in its own
spelling. Duplicate checks the disk has room first, then copies under a
dialog with its progress and a *Stop* button, which holds the window until the copy ends:
the copy is the roll as it was when you pressed it. The passes the library refused, kept in
the roll's `unfiled/`, and any partial write are left out. The copy is marked
`INCOMPLETE` until its manifests -- written last -- are in, and lists as no roll until then;
a copy that fails or is stopped is removed, its marker last, and one a kill cut short -- or
holding a file another program has open -- keeps its marker, which `verify` names. The
room it checks counts what the window is still filing to that disk. A copy that finished
and whose marker would not go is kept, and `verify` says to delete only the marker.
Delete moves the roll folder whole — its manifests, prescans, frames, `approved.json` and `unfiled/` — into the rolls' trash,
`rolls/.trash/<UTC time>/`, and never touches `library/`: the frames' and the walk's
entries stay there with their raw bytes, each recording its roll and frame number
(`roll_membership`), though nothing in the window rebuilds a roll folder from them.
`tools/library.py restore` brings the folder back, and the log names the command. The
question says how much stays on the disk until the trash is emptied, and what goes for
good then, counted from the disk and the library: the walks, what the rolls have done, the
hand-set positions and turns, each prescan from before an in-walk correction and each
walk prescan with no library entry, and each frame whose entry has gone, its `frameNN.tif`
the last copy -- or whose entry is only in the library's trash. Each pass the library
refused, kept in `unfiled/`, is said apart with its size: `verify` names it in the trash,
and emptying the trash leaves that delete alone until it is moved into the library. (A
`tools/scan_roll.py --dry-run` walk from
before 2026-09-24 filed no prescans, and a picture from before an in-walk correction was
filed only from 2026-09-27; for those the folder holds the only copies.) The positions
and turns set by hand live only in `approved.json`. It refuses while the scanner is
working, while that roll is open or its sheet is, and while the session is still filing
into it. The Frames column says how many frames scanned have no entry now (`2 of 3 (1
without an entry)`), and the note under the table what the two trashes hold.

A strip goes on its own the same way, into the same rolls' trash, its record naming its
roll; its numbers stay given -- the roll's open last strip is closed at the frames it had
first -- so the next strip walked is numbered after the roll's last, and a delivered file
of the deleted strip never meets one of the next under its number. The question says so,
and that walking that strip again is done by opening it and adding to its walk.
`tools/library.py restore` puts a strip back into its place in its own roll, and nowhere
else; one whose roll or place has gone stays in the trash, with the reason, and so does
one whose roll's name another film was given since -- the delete records when its roll
was made, so the two are told apart. A window that
scanned into a roll holds it until it closes, so a strip of that roll is restored once the
window has closed, which the line naming the command says; a roll renamed or deleted is
let go of at once.

Opening a roll with frames left brings back its contact sheet and approvals, marks what is
already scanned, and restores **the roll's own settings** from its manifest rather than the
window's: resolution and prescan resolution, film, infrared and infrared-at-scan-
resolution, metering, the mono channel, the registration options, and the first and last
frame. Opening a roll of strips opens its first strip with frames left, or its last, and
the log says which; a strip's row opens that strip. The first and last frame come back
around what is left: the first frame left, and the last one -- or the last frame emptied
where a run to the end of the strip never reached its end, which the box says, with
"first frame" set where it stopped on a roll with a sheet, since the sheet holds only the
frames walked.
A year later the window has moved on to other film and the manifest still describes that
roll. Its name goes into the roll box, so the Roll button continues it in its own folder
-- a strip of a roll of strips, the strip the window has open -- skipping the frames its
`roll.json` calls done within the range, and its question says which; a strip with every
frame done that was walked to its end offers the next strip instead, and the roll's last
strip that stopped short is offered first, in three ways: finish it, scan the next strip
without walking it first, or nothing. *Finish it* opens the strip with its own settings
and asks again with them, naming the frames it skips: what that second question says is
what runs. With no last frame and frames recorded ahead -- done, or failed -- one job
finishes the roll: it scans the frames left up to the highest any run reached, passing the
done ones with the advance an unticked frame on a sheet gets, then winds on to the first
frame no run reached and scans from there to the end of the strip -- "Already scanned, and
passed over: 4-6, 8-9", and "then on from frame 10"; a later strip of a roll of strips
names them by their numbers across the roll, places beside them ("frame 10 (strip 2 ·
4)"), as its row in *Rolls …* does. Where its walk or a run found the strip's end, the
frames up to it are frames left and there is no rest. One Stop ends both; a roll metered
once meters again for the second. A roll to the end of the strip whose strip ends on the
frame just before it -- the rest of one stopped on its true last frame -- ends as
finished, and records where the strip ended; a seek that runs out anywhere else, or short
of a frame the strip's records hold, is the strip put in another way or the transport
stalling: the job fails, and nothing is recorded. A frame still being filed as the press
comes -- the last of a roll that has just ended -- is neither taken again nor left out
without a word: the question names it. A roll is finished at the settings its frames were taken at -- resolution, infrared,
film, metering -- across every strip of a roll of strips: a frame not done yet at other
settings is refused before anything moves, saying how to put the roll's back, and a
deliberate retake of done frames at other settings goes, named in the question, and the
roll keeps its own settings for what is left. A walk is never held to them. A roll opened
from outside this window's rolls (`--open-roll`) is never scanned back into: its plain
Roll is refused, saying to scan its frames from its sheet, which copies its walk across,
or to open the window on its own rolls folder; a strip of a roll of strips from outside
is refused anything that writes --
its sheet's commission, adding to its walk, *Strip ends here* -- since its numbers are its
roll's. No exposure is restored: a roll meters as its metering setting says. A window that
has not calibrated since it opened asks for a calibration before the first frame, as for
any scan; one that already has uses it — a reference describes the sensor at the exposure
and gain that measured it, so after a gap of months *Calibrate again* is worth pressing —
and a saved one can be loaded instead.

**Hover the picture for the numbers under the pointer**, every channel's value including
infrared. The histogram answers "is anything against the ceiling"; this answers "what is
*this*". It says `(approx)` where it is reading a reduced copy, because a working copy's
pixels are resampled averages.

**The histogram is always on screen**, top right, showing where values actually sit,
unstretched, with how much of each channel is at nothing, at full scale and near it. Once
a pass's library entry is on disk those three counts are taken on its raw pixels — the
sensor's own rail, which the correction moves — and the caption says so; the curve is the
corrected picture's. The full-resolution view says when it is showing uncorrected pixels,
and why. The preview is stretched so a negative can be judged by eye, and a stretch puts
the brightest pixel at white whether it was against the ceiling or merely near it — on
this scanner blue reaches the rail first and looks no different for it. Infrared is not in it: it is a dust
measurement rather than an exposure.

**Every panel says what you have changed, and puts it back.** A panel header reads `Scan ·
3` with a `↺` beside it when three of its controls differ from what the window shipped
with, and both appear only then. The arrow resets that panel, right-clicking a single
control offers `Reset to '1800'` for just that one, and **Restore settings …** puts
everything back. Your shortcuts and presets are left alone. The defaults are *captured*
when the window finishes building itself and before any settings file is read, rather than
written down a second time, because a table of default values would disagree with the
controls the first time either moved.

**A control that cannot apply greys out with the reason rather than disappearing.** JPEG
quality stays readable while TIFF is selected; the infrared-at-scan-resolution box stays
readable on an RGB pass, because it is a setting that is still *set* and the driver gates
it. A setting you cannot see the state of is worse than a dead control you can.

**Everything has a key, and the keys are yours.** ⌘ on a Mac, Ctrl elsewhere: ⌘R and ⌘⇧R
turn the picture, ⌘M flips it, ⌘0 straightens it, ⌘F fits it, ⌘1 shows one scanned pixel
per screen pixel, ⌘I inverts, ⌘C steps the channels, ⌘S saves and ⌘⇧S saves all. The
arrows are bare — they walk the filmstrip, move between contact-sheet frames and step a
frame's position in the position window (the position the roll will hold it to; nothing
moves until it is scanned), where Space ticks a frame and Return keeps it and moves on.
Every right-click menu shows its key as it is *now* rather than as it shipped, and
**Shortcuts …** lists them all: click a key to change it, × to clear, ↺ to restore. Only
what you changed is written to `gui-settings.json`, so a default improved later still
reaches you.

**No key submits without asking.** Prescan (⌘Return), scan (⌘⇧Return) and a whole roll
(⌘B) all have keys, and every one of them confirms first and says what the run will cost,
because a slip on a keyboard is not a decision to spend minutes of hardware. Moving film
and calibrating have no key of their own — the calibration prompt's Return starts nothing
until the film is ticked as loaded — because there is no undo for a moved negative.
Escape is Stop: a roll stops after the frame in flight, a single scan after the pass or
metering probe in flight.

**Stopping.** A pass in flight cannot be interrupted safely, and an abandoned read is what
costs a power cycle. *Stop* is cooperative — it ends a roll, a sheet's commission
included, after the frame in flight, and a single scan after the pass or metering probe
in flight: a stop during metering starts no further probe and no pass — and says which it
will do. It reaches a job from the moment the job is handed over, before the window has
heard it start. Ctrl-C or SIGTERM in the terminal the window was started from is taken
as Quit's "stop after what is in flight"; a closed terminal only asks. Quit's stop holds
for the rest of the session: a job handed over a moment before it is stopped before its
first command, and none starts after it. On Windows,
`make run` starts the window apart from its console, so closing that console or pressing
Ctrl-C in it reaches nothing, and Quit is in the window. A window started from a console
directly takes the console closing as Quit too, stopping the session at once from
Windows' own thread, but Windows ends the process about 5 s later whatever it is doing,
so a pass in flight is lost. A single Scan of a slide with infrared does not start its
RGB pass after that: its RGBI pass is filed alone, tagged `infrared pass`, with no copy
written, because on a slide its blue clips. A slide roll still starts each frame's RGB
pass (`TODO.md`, *Known problems*). `logs/console-closed.log`
(`demo/logs/` under `--demo`) records the close and when it came. *Force abort*
closes the transport out from under the read, which is the only thing that actually
unblocks it; the frame is lost and the scanner will almost certainly need a power cycle
at its own switch. It asks you to type ABORT first.

*Stop* also drops whatever was queued behind the running job, and while the scanner
works the roll key, aim clicks and fine moves are refused rather than queued. A roll that
cannot file a frame — a full disk, a library gone — stops after the frame in flight
instead of scanning on and discarding the rest. Every job that writes — a scan, a
prescan, a calibration, a roll or a walk — is refused before the film moves where a disk
it writes to would keep less than 2 GB free beyond what the job can put there (every
pass, a slide's colour pass too, debug filing's spool, and what is still being written),
and the log warns below twice that; a roll checks again before each frame and stops with
the numbers rather than scan a frame it cannot keep, and a disk whose room cannot be read
is said, and costs its copies, not the roll. A job is refused the same way where a
folder it writes in cannot be had, or where a path would pass Windows' 259 characters
(see *Folders* below). A delivered copy that cannot be written
— an output folder gone, a file held open — costs that copy and not the roll, a slide's
picture included while both its passes are filed. While a job reads, the computer is
kept from sleeping, and the log says so when it cannot be. A pass the library refused is
marked red on its thumbnail and said in a notice titled *Not filed*, not only in the
log, and its raw data is kept whole in an `unfiled` folder beside its delivered copy (or
in the system's temporary folder, which Windows may clear — move it into the library at
once), from where moving it into the library files it. A pass that is filed but whose
delivered copy was not written is marked blue and said in a notice titled *Copy not
written*; *Save as* or *Export* writes it again from the library. Each is marked from the
session's own record of the pass, and counted against the job that queued it however
late it lands. Quitting while the scanner works
offers to stop after what is in flight as well as to let everything finish, waits
for *Save as*, *Save all* and *Export* to finish writing, and stops a *Duplicate* in
progress, removing what it had copied. Quit saves the
window's settings first -- for a walk not yet commissioned they hold the contact sheet's
ticks, positions and turns -- and where they will not go it asks before anything is
stopped or put away: try again, quit without them, or keep the window open. Ctrl-C,
SIGTERM and a closed console ask nobody; where the settings will not go they write why, and
the sheet's decisions, to `logs/window-closed.log` (`demo/logs/` under `--demo`). After a read that was abandoned
— a timeout, a refused read, a SCAN whose answer never came — the driver sends nothing more
that could drive the device, status queries aside, and the window starts no job on it,
until it has been power-cycled and a new session opened.

**The log is kept**, one file per launch, in `logs/pane-<UTC start>.log` (`demo/logs/`
under `--demo`), and nothing deletes them. Every line the log pane shows is there with
its UTC time, written as it is said, so the file is whole up to the moment the window
went. It holds what the pane does not show as well: a header naming the code, the
folders, the setup the window opened on and the scanner that answered; a line where each
job starts and where it ends; every traceback, from the window's own handlers, a callback,
a thread that died, or a job the session ran that failed -- the pane says that one in a
line; Python's warnings, such as the library's that an index could not
be rewritten; and the session's last words before Quit, which come after the pane has
gone. Under `make run` on Windows, `logs/window-<UTC time>.log` beside it is the window's
raw stdout and stderr; the pane log is the account of the session. A log that cannot be
written is said once in the pane, which goes on as before.

**Right-click arranges a picture wherever it is shown**, and **the arrangement belongs to
the photograph, not to the window**: turn a frame in the contact sheet and the preview
behind it turns too where it shows that frame -- a turn never puts another frame in front
of the one being looked at, nor reads its pixels again -- and turn a walked prescan in the
filmstrip and its cell on the sheet turns with it. A scan comes back the way its prescan
was left however many other pictures were arranged in between, and a frame of a roll
running comes back the way that roll writes it. Quarter turns and a left-right flip, kept
per frame, so
a portrait among landscapes comes out right; the turn reaches the files you get and never
the library entry, whose pixels have to keep matching the raw bytes beside them. A
sheet's turns, ticks and positions are filed as they are made, in `gui-settings.json`
under the walk they belong to -- the `walk_id` its `survey.json` was given as the walk
started, which the writer's late answers never change -- and reach `approved.json` when
the sheet commissions a roll, with the walk they were decided on and when each frame was.
A walk reopened comes back as it was left, frame by frame: `approved.json`'s word for each
frame it records, unless the sheet was saved since, and the sheet's own for every other
frame, so what was decided after a part commission is not lost. A frame walked again after
its position was set takes the walk's own prescan as its reference: the position set on
the old one is dropped, and its turn kept. A commission from a sheet whose walk was written
over since it was opened -- `tools/scan_roll.py` walking into that folder -- is refused,
with nothing moved. A fresh walk never lands among another walk's records: from the window
it goes to the next strip of a roll, or starts a roll, and a strip there is is only ever
added to. The session refuses one into a folder holding scanned frames, positions decided
on another walk, prescans no library entry holds or anything else before the film moves,
and only a folder holding nothing but a walk whose prescans are all filed is walked again
in place. A walk that
takes no frame -- stopped after its seek, a fault, nothing found -- leaves the folder's
`survey.json` and its `.bak` as they were. A walk added to keeps a frame's earlier prescan
that no library entry holds beside the new one (`prescan03-2.tif`), listed as a take, and
Delete counts it.

**A pass that comes back the wrong way up is turned upright**, from its own line tags,
as it is decoded (above). The scanner does this with nothing else to say it has — a
pass's `MODE SELECT` byte 14 bit 0 leaves the carriage at the far end, and the pass after
it reads bottom-up with no status bit and no sense condition. Only a pass whose tags
cannot decide is judged against the framing pass of the same frame instead, and that
refuses far more readily than it corrects: being wrong stands a photograph on its head,
so a frame with nothing to correlate is left exactly as it came.

The resolution box offers 300, 600, 900, 1200, 1800, 3600 and 7200, and accepts any whole
number from 25 to 7200 typed in; the device refuses what it dislikes with sense
`0x26/0x82` before a byte of image data moves. A roll or a commission at 7200 dpi, which
no reference can correct, or with infrared on a film blind to it, is refused before the
film moves. **Bracketing and multi-pass averaging are absent** deliberately — measured,
and the gain is too small to see. The code, tests and measurements are archived in
`docs/multi-exposure/`.

**It remembers the setup**: resolution, infrared, film, exposure, metering, where files
go, the window size and the pane widths, from `gui-settings.json` beside the library
(`RPS7200_SETTINGS` moves it, `--settings` overrides it; its own folder is made, never one
above it, and one whose parent has gone is refused at launch). Scan settings can be saved as
named presets. A missing or corrupt file opens the window on its defaults rather than not
opening it, and a corrupt one — not JSON, not UTF-8, the wrong shape — is moved aside
(`gui-settings.json.unreadable-<UTC time>`, never over an earlier one) before anything is
saved over it, which the log says; a save that fails is said in the window. One the disk
will not let be read — held a moment by Defender, the indexer or a backup — is asked
again for half a second; if it still cannot be read it is left where it is, said, and
not written over until the window can read it, and then what it holds is kept with what
was changed in the window laid over it. A preset sets only its own keys, and only
choices the controls offer. What is deliberately *not* remembered is the roll name,
frame, subject and notes: those describe one shot, and a stale value would file today's
scan under yesterday's name. The roll of strips walked last -- or given its next strip
by a roll scanned without a walk -- is kept beside them (`last_roll`), and only ever
offered, by *Continue* (above); one only opened, to export it or take a frame again, is
not.

**Files are named by roll and frame**, because NegPy reads them next:

    2026-09-09-gold200_frame03_3600dpi_ir.tif

-- a strip's frame by its number across its roll, `Gold200_frame08_...` for strip 2's
second frame, while its file in the strip's folder is `frame02.tif` --
with prescans in a `prescans/` subdirectory, and the picture from before an aim as
`<name>-before`. In that output folder a frame rescanned after a failure gets a suffix
rather than overwriting the first attempt — the better of the two is not always the
second — and *Save all* or *Export* of a pass not filed yet writes the reduced copy on
screen as `<name>_preview`. Every other delivered file carries the scan's resolution. A
name the session has reserved for a frame still being written is not free to *Export*,
which takes the next one, and *Save as* asks before it writes beside a JPEG's DNG from an
earlier save, which would otherwise be replaced or left beside a picture it does not
belong to. The roll's own `frameNN.tif` and `prescanNN.tif` in `rolls/` are replaced by
the newer pass. Every delivered file is written beside under a temporary name of its own
writer, flushed to the disk and renamed over; every attempt keeps its own library entry,
with its own timestamped id, which is what makes it findable years later.

A scan, a prescan or a roll takes the output folder, the format and the JPEG quality as
it starts and keeps them to its end, so every copy of a roll lands in the folder its
checks covered before the film moved. One changed while a job runs is said in the log
and applies from the next job. A turn or a flip made while a roll from the Roll button
runs still reaches its later frames, as it always has; a roll commissioned from the
contact sheet keeps each frame's own. Either way the window shows each frame as it is
written. The two are one change: no frame is written with a new turn and an old flip.

**Folders.** The library, the rolls folder and the reference's folder may have their own
folder made, and never the ones above it: a folder on a stick that is not plugged in is
refused with "is the drive mounted?" rather than made again on C:. Create a new deep
folder by hand once. The output folder is never made by a scan: a remembered one that has
gone — the iCloud folder iCloud took away — is said in the log at launch and kept, and
Scan, Prescan and Roll are refused until it is back, chosen again or cleared; one that
goes during a roll costs its copies, each said, and never the frames. A typed `--out` is
made at launch, its own folder only. Under `--demo` the window makes `demo/` itself. A
roll name longer than 64 characters (or 160 bytes) is filed under its first 56, a dash
and seven characters of a hash of the whole name — the log says so, a resume finds the
same folder, and `survey.json` and `roll.json` keep the name as typed (`typed_name`),
through the sheet's roll and every resume that names no roll or names it by its folder.
The cap is for making a folder, not finding one: a folder of exactly the name given is
found at any length, so a Duplicate's `-2`, a roll restored beside its name, or a folder
made before the cap is finished in place.

## How scans are corrected

**The scanner returns raw pixels and never corrects them itself.** It measures its own
per-column sensor response during a calibration pass and hands that measurement back, but
applying it is the host's job. Run the calibration and discard the result — as this driver
did for a long time — and nothing changes in the image, which reads like a broken
calibration rather than a missing step.

The reference is two-point per column: a dark level with the lamp off and a light level
with it on, from which the host builds a per-column gain and offset. Applying it took the
worst column defect from 13.01% to 1.44%. Blue has no fixed column pattern to remove.
`docs/shading-calibration-plan.md` has the derivation and the numbers.

**The library holds raw pixels; everything a person sees is corrected.** `scan.tif` in an
entry is the decode alone with `shading.npz` beside it, and `library.corrected(entry)` is
what computes the usable image — with *today's* correction code rather than whatever ran
that day. A corrected file cannot be un-corrected, so storing one would foreclose every
later improvement on every scan ever taken. A roll's own `frameNN.tif` in `rolls/` is the
exception that is written once: corrected as it is scanned, by that day's code. Export
re-corrects from the library. The reference itself is the one filed with the entry;
`library.corrected(entry, rebuild=True)` reduces it again from its calibration's own
lines with today's code, to judge a change to that reduction on a picture.

The **infrared plane is corrected too**. The calibration pass is asked for in RGB, but the
device sends infrared lines in it as well, dark and lit like the colour's, so every
reference carries an infrared channel — all 1,911 on disk on 2026-10-09, `calibration/`
and the library together — and the host divides it as it divides the colour. Only the
headroom dimming that keeps a bright slide's colour off the rail leaves infrared alone.
Each pass's shading report, `calibration.report` in its `scan.json`, names the planes it
divided as `corrected_channels`; an entry filed before then says the same as
`uncorrected: 0`.

There is **no vignette and no vignette correction**, which was measured rather than
assumed: the ~39% falloff across the frame lives entirely in x, where shading already
takes it to 1.4%, and along y it is 1.1% *before* correction. An optic falls off in both
directions; this falls off in neither. See `docs/vignette-plan.md`.

## Exposure

`--auto-exposure` probes at 300 dpi and aims each channel's 99.5th percentile at **0.80**
of full scale, measured **inside the film** rather than across the whole transport window.
The probe is always RGB, in two rounds, plus one further round only if a channel came back
clipped — there the correction is a retreat rather than a measurement.

- **Exposure is a 16-bit timer that wraps.** Past 65535 a pass comes back *darker*, not
  brighter. `READ GAIN/OFFSET` hands back a fixed reference — `9604, 6506, 6506, 7745` —
  rather than what is in force, so every scan is `base × scale` and exposure cannot
  compound. Red's base is highest, so its ceiling is ×6.82 where green and blue get
  ×10.07, and red runs out first.
- **The band above the target is tighter than the band below it.** 0.08 under, 0.02 over.
  Landing under costs a little noise; landing over clips, and nothing downstream undoes
  that. With a symmetric band the top of the window went to 0.88 and a frame duly landed
  at 87% with samples at the rail.
- **Blue comes back several times brighter in an RGBI pass than in the RGB probe**, so its
  target is divided before an infrared scan: by **5.2** on colour negative, where the
  measurement is 4.98–5.02, and by **11.0** on anything unmeasured, where black and white
  measures ~9.6. Every divisor sits above its measurement on purpose, because too low
  clips blue — one constant for every film put 34% of a B&W scan's blue channel at the
  rail.
- **Metering looks inside the film, and finds it once.** It keeps 81% of the pass of
  whatever the film detector returned. The empty aperture beside a strip is far brighter
  than any part of a negative — 143/153/153 against the film's 34/15/7 on a C-41 prescan —
  so metering the whole window lets however much aperture is in view set the exposure: it
  read the percentile 5.9–11.1% high and the scan came out 5.6–10.0% short, silently and
  differently per frame. The film is located on the first probe *while it is still dark*,
  because metering's job is to brighten the film until that contrast is gone.

What the probe measured is filed with the scan, so these numbers stay checkable from
ordinary work. Fifteen frames of colour negative were walked that way:
metering landed in band on all fifteen, and the sensor's departure from linear above 70%
of scale is **−0.6% to −0.8%**, not the 1.5–1.9% long quoted for it. See
`docs/exposure-negative-plan.md`.

## Resolution

| dpi | pixels | RGB | RGBI | driven |
|---|---|---|---|---|
| 300 | 428 × 287 | ~22 s | ~25 s | yes |
| 600 | 860 × 574 | ~34 s | ~44 s | yes |
| 1800 | 2584 × 1722 | ~85 s | ~110 s | yes |
| 3600 | 5172 × 3444 | ~138 s | ~214 s | yes |
| 7200 | 10344 × 6884 | ~314 s | — | yes, and not worth it |

The widths are what the device returns, which is not a fixed multiple of the dpi — at
600 dpi it has returned 862 as well as 860. At 7200 dpi it reads 6888 lines, and the
column-stagger realignment trims four of them.

**Scan time tracks line count, and exposure moves it.** Roughly `8 + 0.036 × lines` for
RGB; an RGBI pass with infrared tied to the resolution costs `7.5 s + 59.9 ms/line` in
all. It used to look flat in resolution, and that was the *untied* infrared floor — a
flat ~220 s whatever was asked for — which `--fast-ir` removes and which is no longer the
default. Untied, a 300 dpi RGBI pass cost 219 s; tied, it costs 25 s. At 3600 dpi the
saving is only 3.4%, so the switch matters most where the pass is cheapest. (212 s
survives in the code as `INFRARED_FLOOR_S`, the conservative end of that range. The read
of an untied pass waits up to 287 s for data — the range's 227 s top plus a minute —
where any other gives up after 120 s, because giving up early is the abandoned read.)

**7200 dpi has been driven and is not recommended**: softer than 3600, twice the time and
four times the bytes. It also **cannot be shading-corrected** — the device caps its
reference at 5172 columns and will not produce one for a 10344-column pass — so `scan()`
refuses a 7200 dpi pass with shading on rather than shipping it uncorrected. 3600 dpi is
the least aliased rate. See `docs/dpi-tradeoff-plan.md` and `docs/7200dpi-plan.md`.

The device accepts far more rates than the window lists; a dpi probe has driven 150, 400,
450, 1000, 1440, 2000, 2400 and 3000 as well.

## Output

16-bit TIFF by default, RGB or RGBI, written by `rps7200/tiff.py` or by `tifffile` when it
is installed — held to identical behaviour by `tests/test_tiff.py`, which runs every
write/read pairing of the two against each other. Where `tifffile` is installed, writes
are deflate-compressed with a horizontal predictor, which is lossless and 8–18% smaller
(13.4% on average, RGBI best); `docs/tiff-compression-plan.md` verified the pixels come
back identical. The built-in writer writes uncompressed, with the same pixels, and both
readers read either. A single scan's copy that the window writes to its output folder
while it holds the scanner open is written uncompressed on either path, as its library
entry is. Every delivered file is written beside its name and renamed over, so a crash
or a full disk leaves no truncated file under a good name.

JPEG output needs Pillow (`uv sync --extra jpeg`) and falls back to TIFF with a note
rather than losing the scan. A JPEG has no room for infrared, so the infrared pass's JPEG
gets a DNG companion beside it with the same stem, holding R, G, B and infrared at full
depth; it is uncompressed, because the writer only deflates float data. A TIFF carries
the plane in-band and needs none. A one-channel delivery of an RGBI pass has no room for
it either, and says it was left out.

The fourth channel is tagged `ExtraSamples = unspecified`, which is what makes the IR
plane survive a round trip through readers that would otherwise treat it as alpha.
`rolls/` output is always TIFF.

## Filing every scan automatically

`tools/scan.py`, `tools/scan_roll.py` and the window file in `library/` by default: every
frame and every prescan a walk or a roll takes. Anything else — an ad-hoc script, a probe,
and the passes those three do not keep, such as metering probes and hold and aim
prescans, tagged `probe` and `hold` — files too if `RPS7200_DEBUG=1` is set, which is
worth doing for anything whose result might matter later. The three claim what they file
themselves, so nothing is filed twice, and a claimed pass stays spooled until its filing
has succeeded, so a pass whose filing failed is filed by debug filing instead:

```sh
RPS7200_DEBUG=1 uv run python your_script.py          # macOS, Linux
$env:RPS7200_DEBUG=1; uv run python your_script.py    # PowerShell
```

It is off by default because ordinary use should not be burdened: an 1800 dpi RGBI entry
is about 63 MB. They go into the library the tool or window files into;
`RPS7200_DEBUG_ROOT` moves them. A script or probe that names no library keeps its
passes, its calibrations' bytes and its failure records in `library/` in the folder it
was started in; one that is not there is made only in the driver's own folder, and
anywhere else the scanner is not opened, nothing is sent, and `RPS7200_DEBUG_ROOT` is
named as the way to point it at a library.

Each scan is spooled as it is taken — a plain sequential write into `.spool` beside the
library, or the system's temporary directory where the library cannot be written — and
the entries are assembled and gzipped **after the device is closed**, because gzipping one
with the scanner open and idle preceded a wedge. It is spooled rather than held in memory
because a 7200 dpi RGBI frame is 570 MB of pixels and about as much again of raw bytes, so
a seventeen-frame roll in RAM would want 19 GB. A spool that could not be filed — a full
disk, a crash before the session closed — is kept, with a record beside each pass naming
its pixels, bytes, mask and reference, and the log says where;
`tools/library.py file-spool` files it later, each pass under the time it was taken. It
looks in the system's temporary directory too, and files from there only a spool whose
records name this library: that directory is every library's fallback.

The window holds the device open for as long as it runs, so it files a single scan or
prescan plain — `raw.bin`, uncompressed TIFFs and reference — and gzips it once the device
has closed; `tools/library.py compact` does it for a window killed before it could. A
roll's frames are the exception: they are gzipped on their own thread while the next frame
scans, with the device busy rather than idle, which `tools/filing_load_test.py` exists to
measure. The last frame of a roll has no next one, so it is filed plain and compacted
after close like a single scan.

### What a roll costs on disk

Film grain barely compresses — the median `raw.bin.gz` across the library is about 94% of
the raw size — so a library entry is close to twice the pixel size. Compacting one after
close needs nearly as much again beside it while it runs, and is refused, the entry left
plain and whole, where that and 2 GB besides are not free; `tools/library.py compact
--write` finishes it later. A 38-frame roll, with deflate-compressed TIFFs (so with
`tifffile` installed; without it they are larger):

| dpi | entry | 38 frames |
|---|---|---|
| 1800 RGBI | ~63 MB | ~2.4 GB |
| 3600 RGBI | ~246 MB | ~9.3 GB |

## Whole-roll scanning

`tools/scan_roll.py` walks a strip: prescan, decide, scan, advance. A frame is about 3 to
5 minutes all in — ~16 s prescan, up to 48 s metering, the scan itself, ~7 s advance — so
a 36-frame roll at 1800 dpi RGBI is a couple of hours and one calibration (3–4 min) covers
all of it.

Frame numbers are places on the strip: frame N is where the transport's own counter
(`READ_STATE` byte 2) reads N-1, counted from where the strip went in. It has been seen
resetting to 0 as a strip goes in — once, in `full_17_strip` (`docs/protocol.md` §9) — so
the numbers are right as long as the strip is in the way it was when it was walked, and
only the operator can see that. A roll — from the window or from `--start-at N` here —
waits for the lamp, reads that counter and winds or advances the film to its first frame,
and refuses with nothing scanned if the counter will not answer or the film does not
arrive. A frame is numbered by that counter throughout, so a film that jumps two places
mid-roll is filed where it landed and the log names the frame it went past; a counter
that reads behind the roll's count ends the roll, naming the frames it went back over,
rather than scan them twice. A roll used to start wherever the film happened to be and
call that frame 1. Manifests written before this say so by lacking
`"numbering": "strip"`, and each frame in them is moved onto the strip's numbers by its
own recorded transport position rather than read by its number. Two readings are not
followed, and both are logged: a position no strip has, such as the stale 72, and one that
gives a number another frame's does where the frames around it put it elsewhere, which is
how a misread counter looks — those take the shift of their own walk or run. A walk, or a
`roll.json` this tool wrote, is one run, which numbered no two frames alike, so there a
frame that such a move lands on is moved along too, and that is logged. Two frames whose
own runs put them on one place in a `roll.json` the window merged, because the film went
over it twice between two rolls filed under one name, are both kept there, and that is
logged too.

`--start-at` resumes a roll that stopped, `--only 3,9,15` scans just the frames it lists,
`--max-failures 3` gives up after three bad frames rather than grinding through a whole
strip, and a resumed roll carries forward what the earlier session already did instead of
overwriting its manifest. Name the roll again with `--roll` (or `--out`) to resume it: a
run given no name gets a new folder, the date and time to the second. A roll that ends
with frames left names them in its advice, as `--start-at` and `--only`, and the rest of
the strip a run to its end never reached as a `--start-at` of its own -- worked out from
every run its `roll.json` records, as the window reads it, not this run's alone; without
`--only` a run scans every frame from `--start-at` on, done or not. A resume keeps what
the roll is asked for (`wanted`) and its earlier runs (`runs`), the window's included.
A resume is held to the settings its roll was taken with — resolution, infrared, film,
metering — and a difference is refused, a retake of done frames as well. The driver
giving up after `--max-failures` failures in a row is recorded as why the run stopped. A frame is recorded done only once the writer has filed it, so a
frame lost to a full disk or a crash is scanned again rather than skipped; a frame the
library refuses stops the roll after the frame in flight. The writer's thread, here and
in the window, is started again on the same queue if anything ever ends it, and says so:
nothing queued behind it is lost, and the scan is never left waiting on it. A frame
filed whose `frameNN.tif` could not be written is done all the same, with a `file_error`
in its record, and is counted apart — "1 filed without its copy" — with no advice to
scan it again: the window's *Save as* or *Export* writes it from the library. It still makes the
exit non-zero. A walk never advises `--start-at` or `--only`, which would replace a walk
of many frames with one: it says to walk the strip again, or to walk the missing frames
from the window keeping the sheet. A walk into a folder of its own only: one holding
anything a walk would mix with or write over is refused before the scanner opens, naming
the free folder, and one that ends before its first frame writes nothing. Everything
knowable before the
device opens is refused there — a resolution shading cannot correct, `--start-at 0`, a
negative `--frames`, an `--only` frame the roll never reaches, `--correct` at a prescan
resolution whose edges are not read, infrared on a film blind to it, a disk without room
for the first frame and 2 GB besides, a folder above `--out`, `rolls/` or `--reference`
that is not there, a path past Windows' 259 characters — rather than after a calibration
has spent minutes, a walk's as well as a roll's. A roll that ends
short of the frames it was asked for says so and exits non-zero; one stopped by Ctrl-C,
which finishes the frame in flight — its prescans, metering and scan — exits 130. Before
the first frame, Ctrl-C finishes the lamp's warm-up, rewind, seek, offset command or
calibration under way and stops there, exit 130 too, `--frames 0` or not: nothing
calibrates after it, and the roll's folder, a walk's `survey.json` included, is left as
it was. `--approved` holds every frame of a walk — one the detector left unplaced at
where the walk saw it, and each clamped to what one command delivers — and scans as the
walk's film unless `--film` says otherwise. A black and white roll is delivered in one
channel, as `tools/scan.py` delivers it.

### Rolls of strips

A film cut into strips of six is one roll: `rolls/Gold200/`, with a folder for each strip
walked into it, `strip-01`, `strip-02` and on, and `strips.json`, the roll's record of
which frame numbers each strip was given. Each strip folder is exactly what a roll folder
was: its walk, its contact sheet's decisions, its frames, its records. Frame numbers count
across the film -- strip 2's first frame is one past the frames strip 1 really had, so the
36 frames of a film are `Gold200-01` to `Gold200-36` in the library and in the files
delivered, with no gaps -- while the transport, *first frame*, *last frame* and the files
inside a strip (`frame02.tif`) count places on that strip, 1 to 6. A frame's record keeps
its number across the roll (`roll_number`), its entry the strip and that number beside its
place (`roll_membership`). Numbers never move once given: a strip is given its first when
the run that first walks or scans it has placed the film, and closed at the frames it
really had when the next strip is claimed. A frame of a closed strip found later past its
range is numbered after the roll's last, said as it happens, and in the same index write
the roll's open last strip is closed at the frames it had -- or numbered on past it, where
it has none -- or its next frame would take that number too. A number only a frame's
record kept, its index write refused, is found again there: the frame keeps it when taken
again, and the index gains it. A run that can pass a closed strip's numbers reads the
roll's highest number before the film moves, and is refused there where it cannot: read
between passes, a refusal lost the frame just scanned. A strip whose walk reached
its end records where (`strip_end`), and so does a scanning run's entry in its `runs`;
one that stopped, was cut short or limited is offered to be added to before the next is
numbered. Finishing a strip to the end of it past a closed range says first that those
frames take numbers after the roll's last.

    uv run python tools/scan_roll.py --roll Gold200 --strip next --dry-run
    uv run python tools/scan_roll.py --roll Gold200 --strip 2 --dpi 3600 \
        --only 2,4 --start-at 2

`--strip next` says the next strip's numbers before the scanner opens -- a strip folder
holding files the index does not list is refused there, as the window refuses it before
the film moves -- and claims it, writing `strips.json` and closing the strip before it,
only once the film is placed (`session.claim`, after the seek and the calibration), so a
run refused or stopped before then leaves no index and no folder. It is refused, naming
both ways on, while the last strip's walk stopped short: `--strip 3 --start-at 5` adds to
that strip, and `--strip 4` starts the next anyway, closing strip 3 at the frames it had.
`--strip N` walks into a strip already there, adding to its walk, or resumes its roll; `--out rolls/Gold200/strip-02` is that strip. A strip is scanned at the
settings the roll's other strips were. Without `--strip` the tool is what it always was --
one folder per walk or roll -- and refuses a roll of strips, listing its strips. A strip
deleted keeps its numbers: the roll's open strip is closed at the frames it had before it
goes into the rolls' trash (`session.close_strip`), and the next strip is numbered after
them; a closed one has any number its records alone kept written into the index first. A strip folder copied or carried out of its roll numbers nothing there, and a walk
or roll into it is refused before anything moves. A folder without `strips.json` is a
roll from before strips, and every path reads it as before.

Two layouts are made, deliberately: the window makes rolls of strips, every walk and roll
from it a strip of one (*The window*, above), and `tools/scan_roll.py` makes one only with
`--strip`; without it, a folder from before strips, which the window opens, finishes,
exports, renames, duplicates and deletes as it always did, and never adds a strip to. The
studies (`tools/hold_check.py`, `tools/roll_registration_study.py`,
`tools/registration_margin.py`) read each strip of a roll of strips as a roll folder of
its own, by its roll and strip.

### What drives the transport

Whole-frame moves are `SLIDE_NEXT` and `SLIDE_PREV` (`04 01 00 01` / `05 01 00 01`),
measured and safe. Sub-frame positioning also works and was calibrated from the host:
`SLIDE 00 <param> 00 04` forward and `01 <param> 00 04` back travel `param + 1.84` units,
one unit being what one increment of `param` adds and the 1.84 a ramp paid once per
command. `param 1`, 2.84 units, is the smallest move there is — `param 0` is accepted
and does nothing — and one correction goes up to `param 87`, 88.8 units. The window says
distances in these units (`rps7200/protocol.py`), and so do `tools/scan_roll.py`'s
`--nudge` and its registration lines. It ships as `nudge()` and the hold loop
behind `scan_roll.py --correct`, which is **off by default** — the vendor does not
reposition during a roll either, so drift is reported and only corrected when asked.

**`SET_SCAN_HEAD` (0xD2) is never sent by anything here**, and should not be. It drives a
mechanism with a step count whose unit is unknown and reports nothing at all: no error, no
sense condition, no byte of `READ_STATE` changing. 10 and 100 steps looked like a clean
no-op; 1000 turned the gears audibly and needed a power cycle. CyberView sends it zero
times across every capture, and the `pieusb` backend refuses the neighbouring mode as
"unreliable, possibly dangerous".

`State.media_loaded` is byte 8 and **is inverted** — 1 with an empty transport, 0 with
film. The captures cannot corroborate it, because every one was taken with film in, so the
driver reports it and lets the scanner refuse rather than gating on it; a calibration logs
what it said and keeps it in `calibration.json` as `media_loaded`, and the tools ask
instead.

### Registration

`film_bounds()` finds the frame in the aperture by looking for the bright clear gap beside
it, using the same contrast the metering crop relies on. A hold checks where a frame went
by phase correlation against the prescan it was set on (`measure_shift_mm`), and every
move it sends is recorded in units (`moves_sent`). Across 3850 pairs no wrong match ever
beat a correlation confidence of 55, and the weakest right match scored 54.9, which is
where the floor (`CONFIDENCE_FLOOR`) sits. Seventeen slides showed no drift at all: every
reading fell inside the 4.6 units of slack a 36 mm frame would leave in the aperture. That
slack has not survived measurement — a frame is 350.6 units wide against the aperture's
344.5, so a centred one shows no base at all — and on negatives the position is now
proposed by `tools/frame_edges` (above). See `docs/frame-adjustment-findings.md`.

## Scanner details

Read from the device's own INQUIRY response:

| | |
|---|---|
| vendor / product | `PIE` / `MF Scanner` |
| USB id | `0x05e3:0x0144` (Genesys Logic USB→SCSI bridge) |
| model | `0x0031` |
| firmware | `1.70` |
| optical resolution | 7200 dpi |
| filters | Infrared, Red, Green, Blue |
| colour depths | 16 / 12 / 8 / 1 bit |
| optional devices | ADF |
| fast preview | 300 dpi |
| scan area | 36.4913 × 24.2993 mm |

`Reflecta` / `RPS 7200` is what this driver writes into DNG `Make`/`Model`; it is not what
the device calls itself.

## Documentation

Each of these is an investigation with its measurements kept, not a design note. Four of
them record something that was tried and **rejected**, which is the part worth reading
before proposing it again.

| Doc | Contents | Status |
|---|---|---|
| `protocol.md` | The wire protocol, decoded from 8,133 vendor SCSI commands across nine captures | reference |
| `scanner-options-survey.md` | Every SilverFast/VueScan/pieusb scanning option and what this driver does about each | reference |
| `shading-calibration-plan.md` | Applying the scanner's own two-point reference — what removed the column stripes | shipped |
| `exposure-negative-plan.md` | Is the 0.80 exposure target right on colour negative? Fifteen metered frames walked | shipped |
| `fast-infrared-plan.md` | The quality bit that ties the infrared plane to the scan resolution | shipped |
| `dpi-tradeoff-plan.md` | Which resolution is worth it, measured by aliasing at each pass's Nyquist | shipped |
| `tiff-compression-plan.md` | Lossless deflate plus horizontal predictor, pixels verified identical | shipped |
| `whole-roll-plan.md` | Driving the transport for a whole roll, and hunting phantom drift | shipped |
| `frame-adjustment-code.md` | How the automatic frame adjustment is implemented: walk, edge detector, approval, hold loop | reference |
| `frame-adjustment-findings.md` | What the frame adjustment rests on: the transport law, the film, the edge study, the confidence floor | reference |
| `7200dpi-plan.md` | Why 7200 dpi cannot be shading-corrected, and the even/odd column stagger fix | mixed |
| `multi-exposure/` | Bracketing, multi-pass averaging and pass registration: built, measured, archived | **rejected** |
| `analog-gain-plan.md` | Is the gain field analog? A five-rung blue ladder answers | **rejected** |
| `vignette-plan.md` | Whether this scanner has a vignette, measured by rotating an IT8 | **rejected** |
| `byte14-plan.md` | Does MODE SELECT byte 14 change the line rate? And the silent row reversal, now read from each pass's own tags | **rejected** |

## Development

```sh
make all          # fix + lint + type + tests, before committing
make test         # pytest with coverage
make test-all     # both TIFF paths: tifffile present and absent
make fix          # safe autofixes only
make lint         # ruff
make type         # ty (not mypy)
make clean        # caches and build artefacts
make reconstruct  # re-decode every stored scan with current code
make verify       # the library's checksums and completeness
```

`make format` reformats every file and is deliberately **not** part of `make all`: this
source is hand-wrapped, and a wholesale reformat rewrites thousands of lines and buries
the real change.

The suite runs with no scanner on the bus — `addopts = -m 'not hardware'` deselects the
nine tests in `tests/test_hardware.py`, which open the device and send nothing but INQUIRY
and READ STATE. CI runs the whole thing on macOS, Linux and Windows.

Host-side behaviour is tested against **stored bytes, not against the scanner**: every
library entry keeps its raw bytes, shading reference and CCD mask, so decoding, correction
and merging are all re-runnable offline.

## Notes and limitations

- Dust removal is not done here. The infrared plane is delivered whole, flat-fielded like
  the colour and, for a slide scanned from the window, registered onto its RGB pass
  (`rps7200.merge`); otherwise as the sensor saw it, which is the point.
- Infrared does nothing for traditional silver-halide black and white, and is refused
  there. Chromogenic C-41 black and white is the exception.
- The gain field is a digital multiplier and buys nothing: measured ×1.484 signal against
  ×1.476 noise. Blue's rail limit in RGB cannot be lifted that way — scan RGBI, where blue
  is about 5× more sensitive.
- `SET GAIN OFFSET` does not persist across a scan sequence; exposure goes through each
  scan's `exposure_scale`.
- The captures are not distributed. They record traffic from every device on the bus,
  keyboard HID reports included.

### Recovering a wedged scanner

Power-cycle it at its own switch. Unplugging USB is not enough — the lamp and the carriage
are held by the scanner's own controller.

Avoid the four things that have caused it: abandoning a read mid-scan, holding the session
open through heavy local work, `IEEE1284 RESET` or `STOP SCAN` (the vendor sends neither,
and both leave the device unresponsive), and `SET_SCAN_HEAD`.

## Licence

GPL-3.0-or-later — see [LICENSE](LICENSE). Copyright © 2026 Stefan Kurzella.

Two pieces of this stand on other people's work:

- The shading correction in `rps7200/shading.py` follows the algorithm in SANE's `pieusb`
  backend (`pieusb_calculate_shading`, `sanei_pieusb_correct_shading`), and the INQUIRY
  field offsets follow `sanei_pieusb_cmd_inquiry`. No pieusb code is copied or
  distributed — its published source was read as a protocol reference and reimplemented in
  Python. That backend is GPL-2.0-**or-later**, and the "or later" is what makes GPL-3 an
  option; this project takes it, for the patent grant and the clearer terms.
- `docs/multi-exposure/code/bracket.py` (archived) adapts the inverse-variance merge from
  [pyopticfilm](https://github.com/jboneng/pyopticfilm)'s `exposure_merge.py`, on its
  `feat/me-n-brackets` branch. The structure is theirs; the noise constants and thresholds
  are ours, measured on this sensor. pyopticfilm is GPL-3.0-or-later, the same licence as
  this project.

The rest was derived from the scanner's own behaviour and from USB captures of the vendor
software, for interoperability.

## Legal

Not affiliated with, endorsed by or connected to **Reflecta**, **Pacific Image
Electronics**, or the makers of **CyberView**, **VueScan** or **SilverFast** in any way.
Those names, and any film stock named in the examples, are trademarks of their respective
owners and are used here only to say what this software talks to and what it was tested
against.

This is hobby reverse engineering. Nothing here is a supported product. I am not
responsible for damage to your scanner, your film, your computer or anything else, and on
Windows this asks you to replace a working driver with one of your own choosing, which
will stop the vendor software seeing the device until you put it back. Use it at your own
risk.
