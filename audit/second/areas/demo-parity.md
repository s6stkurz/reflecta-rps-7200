# Demo scanner vs the real scanner

Area key `demo-parity`. 21 findings: 3 medium, 14 low, 4 info.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

Demo seam, audited at HEAD 8e29136. Its code is identical to 03aacba. `tools/gui.py` main() (8894-8906) puts `lambda: DemoScanner(source, entry=..., no_film=args.look_only, libraries=libraries_beside(source), cache=demo/pictures.npz)` into `ScanSession._open_scanner`. Everything above that point is unmodified: the session, FrameWriter, library.save, seek and rewind. No `if demo` exists in session.py or direct.py. In the GUI, `self.demo` only sets the window title and `look_only` only changes wording (gui.py:377, 545, 3026, 7948). `library.verify` is the one place that branches on demo entries (library.py:1116-1121).


DemoScanner does not subclass DirectScanner. It borrows the real functions as class attributes: scan_roll, auto_exposure, _hold_to_approved, _aim_frame, _rejudge_for, param_for_mm, roll_ends, place_on_strip, STEP_MM, OVERHEAD_MM, MAX_CORRECTION_PARAM and HOLD_GIVE_UP_FRAMES. The refusals `uncalibrated`, `uncorrectable`, `correctable_at` and `byte14_for` are called on DirectScanner itself.


The demo writes its own versions of: open/close, inquiry, wait_warm, position, advance, retreat, nudge, ensure_shading, get/set_gain_offset, prescan, scan and capture_record. It adds a film model: position, _film_mm, backlash, a slipping frame at position 2, and a carriage model.


Default filing is isolated. Entries go to demo/library, rolls to demo/rolls and settings to demo/gui-settings.json. Pictures are only read from `library/` and the `library *` folders beside it. Filed demo entries hold raw pixels labelled raw, and the raw bytes are synthesized by `encode_index`. They reconstruct identically and correct to what was shown. They are marked only by `extra.demo`, `extra.demo_source`, a DEMO device description and `protocol_revision: None`.


Main problems found:
- Backlash is never modelled where the real hold loop meets it, so backward holds converge in one move in the demo.
- `--library` and `--rolls` are accepted with `--demo` without any guard, so demo output can pollute the real library and real roll folders.
- `--demo-entry` is ignored in any normal library.
- Several stand-in methods still carry retyped words, arithmetic or constants: the infrared refusal, nudge, the progress totals and the backlash constant.
- ensure_shading does not behave like the real one: reuse with no cache, no cache written, no 'no reference' outcome.
- In the driver itself, hold and aim verification prescans record film='negative' for every roll, and the demo copies that.

## Findings at a glance

| ID | Severity | Category | Verdict | Title |
|---|---|---|---|---|
| [DEMO-01](#demo-parity-demo-01) | medium | demo-divergence | confirmed | Demo backlash is never applied to the first move of a roll frame, so backward holds converge in one command where the hardware loses the first ones |
| [DEMO-02](#demo-parity-demo-02) | medium | user-error | partly | `--demo` accepts `--library` and `--rolls` overrides without a guard, so synthetic demo entries and walks can land in the real library and real roll folders |
| [DEMO-03](#demo-parity-demo-03) | medium | data-integrity | confirmed | The driver's hold and aim verification prescans record film='negative' whatever the roll's film, and the demo copies it |
| [DEMO-04](#demo-parity-demo-04) | low | doc-mismatch | confirmed | `--demo-entry` and the logged 'showing <pair>' are ignored whenever any entry of the requested film has raw bytes |
| [DEMO-05](#demo-parity-demo-05) | low | demo-divergence | confirmed | The demo's infrared refusal is a retyped copy of the driver's message and has already drifted |
| [DEMO-06](#demo-parity-demo-06) | low | demo-divergence | partly | The ensure_shading stand-in behaves differently from the real one in reuse, caching and outcome |
| [DEMO-07](#demo-parity-demo-07) | low | demo-divergence | confirmed | Demo nudge still re-implements the driver's distance arithmetic, clamped decision and return shape |
| [DEMO-08](#demo-parity-demo-08) | low | demo-divergence | confirmed | The demo's progress totals differ from the device's by the channel count and use a retyped lines-per-dpi constant |
| [DEMO-09](#demo-parity-demo-09) | low | error-handling | confirmed | The picture-signature cache is written non-atomically, and np.load errors on a damaged cache kill the signing thread silently |
| [DEMO-10](#demo-parity-demo-10) | low | demo-divergence | confirmed | With an empty library or no usable source, the demo returns shapes and correction states the device never produces, and the prescan and scan can come from different photographs |
| [DEMO-11](#demo-parity-demo-11) | low | doc-mismatch | confirmed | Stale docstrings about the demo seam: seek says each backend has its own scan_roll, and direction.reverse_lines says the demo uses it |
| [DEMO-12](#demo-parity-demo-12) | low | doc-mismatch | confirmed | Session docs still describe the move cap as param 8 |
| [DEMO-13](#demo-parity-demo-13) | low | data-integrity | confirmed | Demo entries do not record how the pass was derived from its source, so they cannot be re-derived and synthetic content is not marked |
| [DEMO-14](#demo-parity-demo-14) | low | demo-divergence | confirmed | _shape_for takes the first entry at a dpi regardless of its scan window, so one partial-frame entry distorts every demo pass at that resolution |
| [DEMO-16](#demo-parity-demo-16) | low | test-gap | confirmed | The only end-to-end tests of the demo's hold loop skip without a real library, and the autouse fixture bypasses ensure_shading |
| [DEMO-V01](#demo-parity-demo-v01) | low | user-error | found-by-verifier | In `make run-sheet` (--look-only) the sheet's scan path can be reached only by ticking 'The film is in the transport', which is false there |
| [DEMO-V02](#demo-parity-demo-v02) | low | design | found-by-verifier | Picture-signature cache is keyed by path only, so a re-filed or migrated entry keeps a stale likeness |
| [DEMO-15](#demo-parity-demo-15) | info | design | confirmed | session keeps hand-built substitute prescan metas as fallbacks, the exact shape CLAUDE.md forbids |
| [DEMO-17](#demo-parity-demo-17) | info | demo-divergence | confirmed | Demo scan() silently drops driver arguments and differs in defaults and hook error handling |
| [DEMO-18](#demo-parity-demo-18) | info | demo-divergence | confirmed | Real driver paths the demo cannot reach |
| [DEMO-19](#demo-parity-demo-19) | info | demo-divergence | confirmed | Demo metering records probe rounds that cannot respond to exposure |

## Findings in full

<a id="demo-parity-demo-01"></a>

### DEMO-01 -- Demo backlash is never applied to the first move of a roll frame, so backward holds converge in one command where the hardware loses the first ones

**Severity** medium · **Category** demo-divergence · **Verdict** confirmed

**Where:** `rps7200/demo.py:392-393`, `rps7200/demo.py:397-405`, `rps7200/demo.py:448-456`, `rps7200/demo.py:275-279`, `rps7200/demo.py:749`, `rps7200/direct.py:3334-3338`, `rps7200/framing.py:1191-1195`

The demo says it models backlash because "it is the reason the loop iterates at all". In practice its backlash branch fires only when two nudges within one frame go in opposite directions. `hold_plan` never allows that (it returns `would_reverse`), and every roll advance and every roll start resets `_last_way` to 0 instead of leaving the transport loaded forward. So inside a roll the branch is never reached. Where it does fire, on manual GUI nudges, it swallows 2.2 units. The driver, `hold_plan` and the demo's own comment all say two to three commands are swallowed. The number is also retyped as `0.1057` instead of `self.STEP_MM`.

**Evidence (from the code):**

```text
demo.py advance: `if self._rolling is not None:\n    self._new_frame()`; _new_frame: `self._film_mm = 0.0; self._owed_mm = 0.0; self._last_way = 0`; nudge: `elif way != self._last_way and self._last_way:  ... swallowed = min(abs(asked), 2.2 * 0.1057)`. Driver: `a backward offset spends its first command on backlash: the transport advances forward between frames, so it enters each one loaded forward, and a move the other way loses two to three commands before anything happens. One shot would report that as a failure. Two converge.` Checked in memory (no files written): after `_new_frame()`, `nudge(-0.5)` moved the film -0.5116 mm with nothing owed. After a manual +/- reversal only 0.2325 mm (2.2 units) was swallowed, which is less than one param-1 command (2.84 units).
```

**Failure scenario:** The contact sheet sets frame 5 to -8 units and a roll is commissioned in `make run-demo`. The demo reports `held` after 1 move. On the scanner the first backward command goes into backlash and the frame needs 2-3 moves, or ends `not_converged` at MAX_HOLD_MOVES. Someone tuning MAX_HOLD_MOVES or the hold logic against the demo sees a loop that always succeeds on the first try. This is the 'plausible and wrong' case CLAUDE.md warns about, in the opposite direction from the old nudge-cap bug.

**Fix:** Model the forward-loaded entry. After a whole-frame advance set `_last_way = +1`; after a retreat set it to -1. Keep `_new_frame` for position and owed distance only. Size the swallowed amount in commands, taken from a DirectScanner or protocol constant such as `BACKLASH_COMMANDS` × a param-1 move, not from a literal. Add a test that a backward approved offset in a demo roll needs more than one move.

<details><summary>Second reader's check</summary>

demo.py:392-393 calls _new_frame() on every in-roll advance, and scan_roll calls it at demo.py:749. _new_frame (demo.py:403-405) zeroes _last_way, so the backlash branch at demo.py:448 (`elif way != self._last_way and self._last_way`) never fires on a frame's first move. The driver's own reasoning says the transport enters each frame loaded forward and a backward move loses its first commands: _hold_to_approved docstring at direct.py:3332-3337, hold_plan at framing.py:1191-1195, and the session comment at session.py:100-101 ('A roll leaves the transport loaded forward'). The demo's comment at demo.py:275-277 claims 'two to three commands are swallowed' and 'Modelled because it is the reason the loop iterates at all'. What it actually swallows is `min(abs(asked), 2.2 * 0.1057)` (demo.py:451), which is 2.2 units. That is less than one param-1 command (2.84 units), and 0.1057 is MM_PER_UNIT (protocol.py:260) retyped instead of self.STEP_MM. hold_plan refuses reversals within a frame (would_reverse), so inside a roll the branch cannot fire at all. tests/test_demo.py:686 even asserts 'an offset should cost exactly one move'. The film model is input, which the demo may change, but this model contradicts both the driver's stated premise and the demo's own comment, and it makes the hold loop look better than the hardware allows.

</details>

<a id="demo-parity-demo-02"></a>

### DEMO-02 -- `--demo` accepts `--library` and `--rolls` overrides without a guard, so synthetic demo entries and walks can land in the real library and real roll folders

**Severity** medium · **Category** user-error · **Verdict** partly

**Where:** `tools/gui.py:8816-8818`, `tools/gui.py:8828`, `tools/gui.py:8868-8874`, `tools/gui.py:8885-8890`, `tools/gui.py:2462-2468`, `tools/gui.py:3164-3166`, `rps7200/library.py:1119-1121`, `rps7200/library.py:1048-1060`

Under --demo, the --library and --rolls overrides are accepted without a guard. Synthetic demo entries (resampled or shifted pixels, bytes encoded by the demo, a synthetic IR plane, fabricated exposure and metering) can then be filed into the real library under ordinary ids. index.json does not mark them; they are distinguishable only by extra.demo and device.description in scan.json. `verify` uses the flag to excuse them rather than report them (library.py:1119-1121). With --rolls pointing at the real rolls folder, a roll commissioned from a sheet opened with --open-roll writes approved.json, roll.json and frames into the real walk folder. That contradicts the safety claim in the comment at gui.py:8868-8874.

**Evidence (from the code):**

```text
gui.py: `ap.add_argument("--library", default=None, help="where scans are filed (default: library, or demo/library with --demo)")` ... `session = ScanSession(root=args.library or str(home / "library"), ... rolls=args.rolls or str(home / "rolls"), ...)`. The only mark on an entry is demo.py `"demo": True, "demo_source": {...}`, which goes to `extra`. entry_id is `<time>_<stock>_<dpi>dpi[_ir]`, the same format as a real entry, and the tags are `("gui",)`. index.json has no demo field. The only reader of the flag is verify: `demo = bool((record.get("extra") or {}).get("demo")) ... if (why != SHADING_SKIPPED_EXPLICIT and not demo ...): problems.append(...)`. `_roll_folder`: `return sheet if inside else roll_dir(rolls, sheet.name)`.
```

**Failure scenario:** The operator runs `uv run python tools/gui.py --demo --library library` to 'see the real library in the demo' and tries a roll. Thirty-eight synthetic RGBI entries with fabricated exposure, metering, a slipping-frame registration and synthetic IR planes are filed into library/ under normal-looking ids. `make verify` stays clean, and later rolls in the demo, and analysis tools, treat them as scans. Delete in that window then removes real library entries, because `_within(entry, session.root)` now holds.

**Fix:** In main(), refuse `--library` or `--rolls` under `--demo` when the path is not inside DEMO_ROOT, or require an explicit `--demo-allow-real-paths`. Tag demo entries with `demo` so the tag appears in index.json. Have `entries()`-based tools (duplicates, prunable, the analysis tools) and the demo's own `_source_for`, `_pool_for` and `_shape_for` skip `extra.demo` records by default.

<details><summary>Second reader's check</summary>

Confirmed: gui.py:8816-8818 and 8885-8890 accept --library and --rolls under --demo with no check. session.root and session.rolls are then the real folders, and DemoScanner entries (extra.demo, extra.demo_source, device description 'DEMO ... (no scanner attached)') are filed with ordinary ids. With --rolls rolls plus --open-roll, _roll_folder (gui.py:2462-2468) finds the sheet folder inside session.rolls and returns the real walk folder. on_scan_chosen then writes approved.json there (gui.py:3164-3166) and submits Roll(out=str(folder)) (gui.py:3210). The code comment at gui.py:8868-8874 claims '--demo pins that under demo/, so nothing the window does afterwards can write back into the walk it is showing'; that is false once --rolls is given. Overstated: the Delete scenario. on_delete (gui.py:4465-4490) asks 'Keep the library entry?' exactly as in non-demo use, so removing a real entry still needs an explicit No. That is not a demo-specific hazard. Also, index.json carries no demo flag (library.py:1048-1060), but scan.json does, and the device description names the demo.

</details>

<a id="demo-parity-demo-03"></a>

### DEMO-03 -- The driver's hold and aim verification prescans record film='negative' whatever the roll's film, and the demo copies it

**Severity** medium · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/direct.py:3377-3378`, `rps7200/direct.py:3509`, `rps7200/direct.py:2053-2058`, `rps7200/direct.py:4031-4036`, `rps7200/direct.py:4063-4067`, `rps7200/session.py:2550-2559`, `tools/scan_roll.py:669`, `rps7200/library.py:330`, `rps7200/library.py:954`

A walk (dry run) with approved positions or with in-walk correction replaces each moved frame's prescan with the hold loop's last verification pass. That pass's meta says `film: negative` for B&W, slide or Kodachrome rolls. It is filed as the frame's walk entry, so the library records the wrong film. That field feeds `library.signature` and the demo's per-film source selection (`_source_for`, `_pool_for`, `_pictures_for`). The demo's `prescan(film="negative")` default matches, so the demo shows the same wrong record and never exposes it.

**Evidence (from the code):**

```text
_hold_to_approved: `image, _ = self.prescan(resolution=prescan_resolution, keep_raw=keep_raw, shading=shading)` passes no `film`. prescan's signature has `film: str = FILM_NEGATIVE`. scan_roll's own prescan passes `film=film` with the comment `The roll's film, so the prescan's entry says what was in the transport; it was recorded as "negative" whatever it was.` After a hold: `prescan_meta = dict(self.last_scan_meta or {})`. The session files it: `self._file(seq, number, rf.prescan, dict(rf.prescan_meta or {...}, roll_membership=...), ...)`. library.save records `"scan": {k: meta.get(k) for k in SCAN_FIELDS}`, which includes `film`.
```

**Failure scenario:** A B&W strip is walked with correction on, or re-walked with approved positions. Each frame the aim or hold loop moved is filed with scan.film='negative', so a later search or signature treats those B&W prescans as colour negatives. The next demo run can pick them as 'negative' pictures, and duplicates grouping by signature splits the same walk across two film types.

**Fix:** Give `_hold_to_approved` (and through it `_aim_frame`) a `film` parameter, pass the roll's film from scan_roll, and forward it to `self.prescan(..., film=film)`. Add a test that a walk with approved offsets on film='bw' files its replacement prescans with film 'bw'.

<details><summary>Second reader's check</summary>

_hold_to_approved calls self.prescan(resolution=..., keep_raw=..., shading=...) at direct.py:3377-3378 without film, so DirectScanner.prescan's default film=FILM_NEGATIVE (direct.py:2058) is passed to scan and recorded in meta. _aim_frame reaches the same helper (direct.py:3509). scan_roll then replaces prescan_meta with `dict(self.last_scan_meta or {})` after a hold (direct.py:4031-4036) and after an aim (direct.py:4063-4067). The session's dry-run walk files that meta as the entry (session.py:2550-2559), and library.save records scan.film from it (library.py:330, SCAN_FIELDS includes film). tools/scan_roll.py:669 files frame.prescan_meta the same way, so the CLI walk is affected too. The comment at direct.py:3971-3972 says this very bug was fixed for the first prescan; the hold/aim prescan was missed. library.signature reads scan.film (library.py:954), and the demo's _source_for, _pool_for and _pictures_for select by scan.film. On a non-dry roll only read_direction and carriage_state of the prescan meta are kept (library.py:381), so the wrong film lands only on walk (dry-run) entries.

</details>

<a id="demo-parity-demo-04"></a>

### DEMO-04 -- `--demo-entry` and the logged 'showing <pair>' are ignored whenever any entry of the requested film has raw bytes

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/demo.py:263-267`, `rps7200/demo.py:326-331`, `rps7200/demo.py:1089-1108`, `tools/gui.py:8823-8826`

`self.pair` is used only as a fallback, when no entry of the requested film has raw bytes. In any ordinary library the demo shows the raw-byte entry of that film nearest 1800 dpi, not the requested entry and not the logged pair. A mistyped or missing `--demo-entry` path is not checked either: open() logs 'showing <name>' for a path that does not exist.

**Evidence (from the code):**

```text
demo.py: `#: The one entry this demo is showing, when it found a good pair. Its prescan answers a prescan and its scan answers a scan` ... open(): `self._log(f"demo mode: showing {self.pair.name}")` ... _source_for: `if best is None or abs(found - 1800) < abs(best_dpi - 1800): best, best_dpi = path, found` ... `self._by_film[film] = best or self.pair`. gui.py help: `a specific library entry for --demo to show; by default the highest-resolution one that has both a prescan and a scan of the same picture`.
```

**Failure scenario:** The operator runs `--demo --demo-entry library/20260911T103600Z_...` to reproduce a report on one specific frame. The log says 'demo mode: showing 20260911T103600Z_...', but every prescan and scan is drawn from a different 1800 dpi entry. The operator then judges the wrong photograph.

**Fix:** When `entry` is given, make `_source_for` return it for every film, or at least for its own film, and check at open() that it exists and has a scan.json. Log the entry actually chosen per film instead of the pair.

<details><summary>Second reader's check</summary>

`self.pair` is set from --demo-entry (demo.py:267). Its only uses are the open() log (demo.py:328-331) and the final fallback in _source_for, `self._by_film[film] = best or self.pair` (demo.py:1107). Any entry of the requested film with raw bytes becomes `best`, chosen nearest 1800 dpi (demo.py:1103-1104), so the named entry is ignored. Inside a roll the strip overrides it anyway (demo.py:1074-1078). The path is never checked for existence. The help at gui.py:8823-8826 and the comment at demo.py:263-266 claim otherwise.

</details>

<a id="demo-parity-demo-05"></a>

### DEMO-05 -- The demo's infrared refusal is a retyped copy of the driver's message and has already drifted

**Severity** low · **Category** demo-divergence · **Verdict** confirmed

**Where:** `rps7200/demo.py:665-674`, `rps7200/direct.py:2912-2930`, `rps7200/demo.py:39-41`

`uncalibrated` and `uncorrectable` are shared with the demo precisely so the demo cannot drift from the driver's words. The infrared refusal is still typed out in demo.py: it compares against the literal "bw" instead of FILM_BW and has lost the chromogenic advice. The tests only match the prefix 'infrared is blind', so the drift is not caught. (The device itself does not refuse; `supports_infrared` says the scanner 'will happily take the pass'. The refusal is the driver's.)

**Evidence (from the code):**

```text
demo.py: `raise ValueError(f"infrared is blind to {film}: its " + ("grain" if film == "bw" else "cyan layer") + " absorbs infrared, so the pass would spend its ~212 s floor and hand back the picture rather than the dust. Scan it RGB.")`. direct.py adds `"(Chromogenic C-41 black and white does clean properly -- scan that as a negative.)"`. The demo docstring claims: `It refuses what the device refuses, in the driver's own words: infrared on black and white or Kodachrome`.
```

**Failure scenario:** An operator trying B&W with IR in the demo is not told that chromogenic C-41 B&W should be scanned as a negative. Any future change to the driver's refusal (wording, or which films it covers) will not reach the demo.

**Fix:** Move the refusal into a DirectScanner static or class method (like `uncalibrated`) that returns the ValueError, and raise it from both `scan` and the demo. Test for string equality, as the uncalibrated test does.

<details><summary>Second reader's check</summary>

demo.py:669-674 retypes the refusal. It compares against the literal "bw" and omits the '(Chromogenic C-41 black and white ...)' sentence that direct.py:2924-2929 carries. tests/test_demo.py:158-160 matches only the 'infrared is blind' prefix. The module docstring (demo.py:39) claims 'in the driver's own words'. The condition itself is supports_infrared, which is shared, so only the wording has drifted.

</details>

<a id="demo-parity-demo-06"></a>

### DEMO-06 -- The ensure_shading stand-in behaves differently from the real one in reuse, caching and outcome

**Severity** low · **Category** demo-divergence · **Verdict** partly

**Where:** `rps7200/demo.py:500-508`, `rps7200/direct.py:835-887`, `rps7200/session.py:2057-2058`, `tools/gui.py:1984-1991`, `tools/gui.py:2083-2090`

The demo's ensure_shading differs from the driver's in three ways. It reports 'loaded' for reuse even when no cache exists, where the driver would calibrate for about 3.5 minutes. It never writes the cached reference or the calibration archive, so the window's cached-reference prompt and the 'Use the cached one' path cannot be exercised in the demo. It never returns a 'reference' key, so the session's 'calibration ended with no usable reference' branch cannot be reached. (Succeeding with no film is not a divergence: the driver does not refuse that either.)

**Evidence (from the code):**

```text
demo: `self._work(210.0 if not reuse else 1.0); self._calibrated = True; return {"action": "loaded" if reuse else "calibrated", ...}`, with no `reference` key and no file written. Direct: `if reuse and path.exists(): ... load` else `result = self.calibrate_shading(keep_data=True)`, archive_calibration, save_shading, and `"reference": result["reference"]`, which may be None. session: `self.calibrated = (summary.get("action") in ("loaded", "calibrated") and summary.get("reference", True) is not None)`. GUI: `return Path(self.session.reference).exists()` decides whether 'Use the cached one' is offered.
```

**Failure scenario:** In `make run-sheet`, the operator must tick 'The film is in the transport', which is false, to calibrate. The demo then calibrates silently. Someone checking the reuse flow in the demo never sees the prompt branch, or the 3-4 minute fallback the hardware takes when the cache has gone.

**Fix:** Make the stand-in follow the driver: reuse loads only when `path.exists()` and otherwise 'calibrates'; write a small placeholder reference or reuse the source entry's; return a `reference` key. Decide deliberately whether no_film refuses a calibration, and make the docstring match.

<details><summary>Second reader's check</summary>

Points 1-3 hold. The demo's ensure_shading (demo.py:500-508) answers 'loaded' in 1/speed s for reuse=True whether or not a cache exists. The real one calibrates when the file is missing (direct.py:835-887). The demo never writes the cache or an archive, so gui.py:1984-1991 and 2083-2090 never see a cached reference under the demo. The demo returns no 'reference' key, so session.py:2057-2058 always sets calibrated=True, and the branch where a calibration yields no usable reference is unreachable in the demo. Point 4 is refuted: the real DirectScanner.ensure_shading and calibrate_shading do not refuse for want of film either. The real scan only logs a note when media_loaded is false (direct.py:3005-3012). A demo calibration that succeeds with no_film therefore matches the driver, and the module docstring's 'every pass and every move' is about transport refusals, not calibration.

</details>

<a id="demo-parity-demo-07"></a>

### DEMO-07 -- Demo nudge still re-implements the driver's distance arithmetic, clamped decision and return shape

**Severity** low · **Category** demo-divergence · **Verdict** confirmed

**Where:** `rps7200/demo.py:432-437`, `rps7200/demo.py:451`, `rps7200/demo.py:467-470`, `rps7200/direct.py:3622-3643`

Only `param_for_mm` and the constants are borrowed. The step and ramp formula, the `clamped` rule and the reply keys are a second copy. CLAUDE.md's rule is 'a number, cap, constant or decision the stand-in needs is taken from DirectScanner, never retyped'. The natural seam is for the demo to implement `slide(action, param, value)` and borrow `DirectScanner.nudge`, just as it borrows scan_roll. The existing test only checks that the keys exist, not that the values match.

**Evidence (from the code):**

```text
demo: `param = self.param_for_mm(millimetres); asked = self.STEP_MM * param + self.OVERHEAD_MM; short = abs(millimetres) - asked; clamped = short > 1e-9 ... return {"param": param, "forward": millimetres >= 0, "asked_mm": round(asked, 3), "requested_mm": ..., "clamped": clamped, "short_mm": ...}`. The driver has the same lines at direct.py:3622-3643, and the demo retypes `2.2 * 0.1057` (MM_PER_UNIT) for backlash.
```

**Failure scenario:** The driver changes nudge, for example to report the delivered distance with the ramp or to change `clamped` rounding. The demo keeps the old answer, and `_hold_to_approved`'s `spent` and `clamped` bookkeeping behave differently in the demo from the scanner, which is how the original param-8 bug happened.

**Fix:** Bind `nudge = DirectScanner.nudge` and give the demo a `slide(action, param, value)` that moves `_film_mm` by the delivered distance. Take the backlash size from a protocol or DirectScanner constant.

<details><summary>Second reader's check</summary>

demo.py:433-437 and 467-470 repeat, line for line, the driver's asked, short and clamped arithmetic and its return dict (direct.py:3622-3643). Only param_for_mm, STEP_MM and OVERHEAD_MM are borrowed (demo.py:805, 811-812). The values agree today, but this is the same 'second home' pattern CLAUDE.md forbids and that demo.py's own docstring (demo.py:425-430) records as having gone stale before.

</details>

<a id="demo-parity-demo-08"></a>

### DEMO-08 -- The demo's progress totals differ from the device's by the channel count and use a retyped lines-per-dpi constant

**Severity** low · **Category** demo-divergence · **Verdict** confirmed

**Where:** `rps7200/demo.py:587`, `rps7200/demo.py:691-694`, `rps7200/demo.py:1047-1057`, `rps7200/direct.py:1824`, `rps7200/direct.py:1872-1874`, `rps7200/session.py:1266`, `tools/gui.py:3822-3823`

The real read reports lines across all planes (3 or 4 × height). The demo reports the height alone and computes it with a literal 0.957 instead of session._LINES_PER_DPI. The window prints these numbers as 'N/M lines'.

**Evidence (from the code):**

```text
demo: `self._work(estimate_seconds(resolution, False), lines=int(resolution * 0.957))` and `_work`: `self.progress_hook(round(total * (i + 1) / steps), total)`. Driver: `total_lines = channels * params.lines` ... `self.progress_hook(got, total_lines)`. session: `_LINES_PER_DPI = 6888 / 7200`. GUI: `self.v_progress.set(f"{done}/{total} lines")`.
```

**Failure scenario:** An 1800 dpi RGBI pass reads '1722 lines' in the demo and '6888 lines' on the scanner. Anyone checking the progress readout, or the per-pass line-rate ETA, against the demo sees numbers the hardware never produces.

**Fix:** Report `channels * lines`, with lines taken from the fitted pass shape or `session._LINES_PER_DPI`, and pass the pass's channel count to `_work`.

<details><summary>Second reader's check</summary>

The driver reports progress_hook(got, channels*params.lines) (direct.py:1824, 1872-1874). The demo passes lines=int(resolution*0.957) (demo.py:587, 693) and reports against that total (demo.py:1056-1057), which is a single plane's height from a retyped constant instead of session._LINES_PER_DPI (session.py:1266). The GUI shows these as 'N/M lines'. A related detail: the demo calls progress_hook and log_hook without the driver's try/except (demo.py:1043-1045, 1056-1057, against direct.py:1873-1876).

</details>

<a id="demo-parity-demo-09"></a>

### DEMO-09 -- The picture-signature cache is written non-atomically, and np.load errors on a damaged cache kill the signing thread silently

**Severity** low · **Category** error-handling · **Verdict** confirmed

**Where:** `rps7200/demo.py:323-325`, `rps7200/demo.py:1257-1262`, `rps7200/demo.py:1275-1281`, `rps7200/demo.py:1244-1245`

`np.savez` writes directly to demo/pictures.npz from a daemon thread. Quitting the window while it writes (daemon threads are killed at exit), or two demo windows at once, leaves a truncated or empty file. On the next launch np.load raises an exception that is not caught, the thread dies with only a traceback on stderr, and `_signatures` stays empty. Every later 'new strip' then falls back to the first strip's pool, and the cache is never rewritten.

**Evidence (from the code):**

```text
`with np.load(self.cache) as data: known = dict(...)` except `(OSError, ValueError, KeyError)` ... `np.savez(self.cache, paths=..., signatures=...)`, run on a daemon thread started in open(). Checked in memory: np.load of b'' raises EOFError, and of a truncated zip raises zipfile.BadZipFile. Neither is caught. Fallback: `if not strip: strip = list(self._pool_for(film))`.
```

**Failure scenario:** Stefan closes the demo seconds after launch, while the first signing pass is still writing pictures.npz. From then on, a second roll in any demo session silently shows the first strip's pictures instead of photographs from the other libraries, until someone deletes demo/pictures.npz.

**Fix:** Write to a temporary file beside the cache and `os.replace` it. Catch `Exception`, or add EOFError and zipfile.BadZipFile, on load and treat the cache as absent. Log the failure through `_log`.

<details><summary>Second reader's check</summary>

_sign_pictures catches only (OSError, ValueError, KeyError) around np.load (demo.py:1258-1262). numpy raises EOFError for an empty file and zipfile.BadZipFile for a truncated npz; neither is caught, so the daemon thread dies before it fills _signatures or rewrites the cache. np.savez writes demo/pictures.npz in place (demo.py:1278-1279) on a daemon thread that is killed at interpreter exit. _pictures_for then finds no signatures, and _next_strip falls back to _pool_for (demo.py:1244-1245) for every later strip.

</details>

<a id="demo-parity-demo-10"></a>

### DEMO-10 -- With an empty library or no usable source, the demo returns shapes and correction states the device never produces, and the prescan and scan can come from different photographs

**Severity** low · **Category** demo-divergence · **Verdict** confirmed

**Where:** `rps7200/demo.py:935-943`, `rps7200/demo.py:1373-1389`, `rps7200/demo.py:886-891`, `rps7200/demo.py:876-880`, `rps7200/demo.py:1105-1108`, `rps7200/demo.py:1355-1357`

(a) In an empty library every pass comes back 574×862 whatever the resolution, which is the 'lying about the resolution' that `_fit`'s own docstring forbids. With shading=True the pass also comes back uncorrected. The driver never returns an uncorrected pass for shading=True; it refuses. (b) When no entry of the requested film has raw bytes and no entry has a prescan.tif, `_stored` goes to `_pixels`, which moves to the next entry on every call. The prescan, each metering probe and the scan then come from different photographs, which `_source_for`'s docstring says must not happen. (c) A 'finished' (already corrected) source asked for shading=False returns corrected pixels, only logged.

**Evidence (from the code):**

```text
_fit: `shape = None if dpi == resolution else self._shape_for(resolution)` ... `else: h, w = height, width`. The test card is `h, w = 574, 862` with `"dpi": None`, and `_shape_for` finds nothing in an empty library. _take: `else: skipped = UNCALIBRATED_SOURCE`. _pixels: `path = wanted[self._next % len(wanted)]; self._next += 1`. `_source_for` memoises `self._by_film[film] = best or self.pair`, which is None when no entry of that film has raw bytes and no entry has a prescan.tif.
```

**Failure scenario:** A fresh checkout (no library/) runs `make run-demo`. The 300 dpi prescan is 862 columns wide, so frame-edge readings, offsets and ETA arithmetic run at twice the real scale. With a library that holds no slide entries, a 'positive' prescan and its scan are two different photographs, and the 'scan replaces its prescan' behaviour looks broken.

**Fix:** Size the test card from the resolution with the device widths as a table, or `session._LINES_PER_DPI`. In `_source_for`, fall back to one fixed entry per film before `_pixels`. Consider refusing a shading=True pass with no calibration behind it (as the driver would) instead of returning it raw.

<details><summary>Second reader's check</summary>

(a) With no entries, _shape_for returns None and the test card has dpi None, so _fit keeps 574x862 at any resolution (demo.py:935-943, 1498). A shading=True pass then returns uncorrected with UNCALIBRATED_SOURCE (demo.py:890-891). (b) When _source_for returns None (no raw-byte entry of that film and no pair), _stored calls _pixels (demo.py:1356-1357), and _pixels advances _next on every call (demo.py:1380-1381), so the prescan, the probes and the scan come from different entries. (c) A finished source asked for shading=False only logs (demo.py:876-880). All are input-side, reached only with an empty or unusual library.

</details>

<a id="demo-parity-demo-11"></a>

### DEMO-11 -- Stale docstrings about the demo seam: seek says each backend has its own scan_roll, and direction.reverse_lines says the demo uses it

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/session.py:257-260`, `rps7200/direction.py:146-153`, `rps7200/demo.py:790`

Both docstrings describe an earlier design. The demo now runs the driver's own roll loop and encodes reversed passes directly, and reverse_lines is dead code in the package.

**Evidence (from the code):**

```text
session.seek: `Above the seam on purpose. It speaks only through wait_warm, position, advance and retreat, so the demo's stand-in runs it unchanged -- ... and the reason it is not inside scan_roll, which each backend has its own copy of.` But demo.py: `_drivers_roll = DirectScanner.scan_roll`. direction.reverse_lines: `What the demo hands over when its carriage starts at the far end`. The demo uses `encode_index(raw, reversed=reversed_now)`; reverse_lines is referenced only in tests/test_decode.py.
```

**Failure scenario:** A reader decides to change the demo's 'own copy' of scan_roll, or keeps reverse_lines in step with the demo, working from these docstrings.

**Fix:** Correct the seek docstring (scan_roll is shared and borrowed by the demo). Remove reverse_lines or reword its docstring as a test helper.

<details><summary>Second reader's check</summary>

session.py:257-260 says scan_roll is something 'each backend has its own copy of'. The demo binds `_drivers_roll = DirectScanner.scan_roll` (demo.py:790). direction.reverse_lines' docstring (direction.py:146-153) says it is what the demo hands over, but the demo uses encode_index(raw, reversed=...) (demo.py:624), and reverse_lines is referenced only in tests/test_decode.py:222,296.

</details>

<a id="demo-parity-demo-12"></a>

### DEMO-12 -- Session docs still describe the move cap as param 8

**Severity** low · **Category** doc-mismatch · **Verdict** confirmed

**Where:** `rps7200/session.py:93-97`, `rps7200/session.py:1207-1224`, `rps7200/direct.py:3596`

FINE_MAX_MM is computed from MAX_CORRECTION_PARAM (87), but the comments still say 8. This is the same stale number whose copy in demo.py caused the documented parity bug.

**Evidence (from the code):**

```text
session.py: `#: What one SLIDE sub-frame command can move, from the calibrated law: distance = STEP_MM x param + OVERHEAD_MM, for param 1 and param 8.` and plan_nudges: `One SLIDE command delivers STEP_MM x param + OVERHEAD_MM for an integer param in 1..8 ... DirectScanner.param_for_mm already clamps silently at param 8`. direct.py: `MAX_CORRECTION_PARAM = 87`.
```

**Failure scenario:** Someone reasoning about the demo's `_move` or `plan_nudges` from these comments expects 8 as the cap and misreads a demo or hardware move log.

**Fix:** Update both comments to say MAX_CORRECTION_PARAM (87) and refer to the constant instead of the number.

<details><summary>Second reader's check</summary>

session.py:93-94 says 'for param 1 and param 8', and plan_nudges' docstring (session.py:1207, 1222-1223) says 'integer param in 1..8' and 'clamps silently at param 8'. The code uses DirectScanner.MAX_CORRECTION_PARAM = 87 (direct.py:3596, session.py:96-97).

</details>

<a id="demo-parity-demo-13"></a>

### DEMO-13 -- Demo entries do not record how the pass was derived from its source, so they cannot be re-derived and synthetic content is not marked

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/demo.py:900-908`, `rps7200/demo.py:944-964`, `rps7200/demo.py:973-974`, `rps7200/demo.py:1016-1019`, `rps7200/library.py:1048-1060`

The owner's rule is that everything can be recomputed later. A demo entry keeps its own pixels and bytes consistently, but not the transform from the named source: the resampling maps, the column shift, the synthesized IR plane and the resampled reference. Its raw record also does not say the bytes were synthesized rather than received. The only markers are `extra.demo` and `extra.demo_source`, and index.json omits them.

**Evidence (from the code):**

```text
meta: `"demo": True, "demo_source": {"entry": source["entry"], "file": source["file"]}`. Not recorded: the fitted (h, w) against the stored shape, `columns = np.roll(..., self._shift(w))` / `_film_mm`, the synthetic `clear = np.full(..., level, ...)` infrared plane, or the fact that `shading.npz` is `_pass_reference(...)` (pixels_per_line = pass width, no mask) and not a device reference.
```

**Failure scenario:** A demo entry with a flat synthetic IR plane or a resampled reference is later used, for example after being copied or merged, to test dust removal or shading. Nothing in its raw, calibration or image records says those parts are invented, and it cannot be regenerated from its source to check.

**Fix:** Add `extra.demo_fit = {source_shape, shape, column_shift, film_mm, infrared_synthesized, reference: 'resampled'|'source'}` and `raw.synthesized: true`, and add a `demo` tag so index.json shows it.

<details><summary>Second reader's check</summary>

_take's meta carries only demo=True and demo_source {entry, file} (demo.py:903-907). The fitted shape, the row and column index maps, the shift from _film_mm (demo.py:947), the synthetic CLEAR_INFRARED plane (demo.py:958-964) and the synthesized _pass_reference (demo.py:973-974) are not recorded. The raw record carries no synthetic marker. Mitigation not mentioned by the reader: device.description is 'DEMO  MF Scanner ... (no scanner attached)' (demo.py:193 via library._describe_inquiry), which also marks the entry. The entry cannot be re-derived from its source, but by default it lives only under demo/library.

</details>

<a id="demo-parity-demo-14"></a>

### DEMO-14 -- _shape_for takes the first entry at a dpi regardless of its scan window, so one partial-frame entry distorts every demo pass at that resolution

**Severity** low · **Category** demo-divergence · **Verdict** confirmed

**Where:** `rps7200/demo.py:1110-1135`

`scan.frame` is not checked. Probe tools and bracket merges can file entries with other windows or trimmed heights (7200 dpi stagger realignment removes 4 rows). The first such entry by name then fixes the 'device shape' for the whole session, and every picture fitted to it is squashed or stretched on its own axis.

**Evidence (from the code):**

```text
`for path in self._entries: ... if int(scan.get("resolution_dpi") or 0) != dpi: continue; h, w = scan.get("height"), scan.get("width"); if h and w: found = (int(h), int(w)); break`. The docstring says: `Any film will do; the frame is the same size whatever is in it.`
```

**Failure scenario:** The library holds a 900 dpi probe with a half-width window. Every demo 900 dpi pass comes back half as wide, with the picture compressed horizontally, and frame-edge and offset readings made in the demo are wrong by that factor.

**Fix:** Consider only entries whose `scan.frame` equals FULL_FRAME and that are not demo entries, and prefer the most common shape over the first one found.

<details><summary>Second reader's check</summary>

_shape_for (demo.py:1122-1133) takes the first entry in sorted order whose resolution_dpi matches and that has a height and width. It never looks at scan.frame, and the result is memoised for the session. An entry with a narrower window, a trimmed height or a probe crop fixes the 'device shape' for every demo pass at that dpi, and _fit then scales each axis independently (demo.py:944-947).

</details>

<a id="demo-parity-demo-16"></a>

### DEMO-16 -- The only end-to-end tests of the demo's hold loop skip without a real library, and the autouse fixture bypasses ensure_shading

**Severity** low · **Category** test-gap · **Verdict** confirmed

**Where:** `tests/test_demo.py:58-65`, `tests/test_demo.py:659-670`, `tests/test_demo.py:692-706`

test_the_demo_converges_on_an_approved_position and test_one_frame_is_made_to_miss_on_purpose depend on the real `library/`, which is gitignored, so in CI they always skip. The module already has synthetic picture builders (`_photograph`, `calibrated_entry`) that could drive them. Because of the autouse fixture no test calibrates through a session, so DEMO-06 is invisible to the suite.

**Evidence (from the code):**

```text
`@pytest.fixture(autouse=True) def calibrated(monkeypatch): monkeypatch.setattr(DemoScanner, "_calibrated", True, raising=False)`; `scanner = DemoScanner("library", speed=1e9) ... if not scanner._entries: ... pytest.skip("no library entries in this checkout to register against")`. This checkout has no library/.
```

**Failure scenario:** A change breaks the demo's film-moving model or the hold loop's convergence in the demo, and CI stays green because both tests skip.

**Fix:** Build a small synthetic library in tmp_path for the hold and slip tests. Add one session-level test that submits Calibrate (measure, reuse with and without a cache) against the demo without the autouse fixture.

<details><summary>Second reader's check</summary>

tests/test_demo.py:659-670 and 692-706 construct DemoScanner('library') and skip when it has no entries. library/ is gitignored, so these skip in CI. The autouse fixture (tests/test_demo.py:58-65) sets _calibrated=True on the class. ensure_shading is exercised only directly (tests/test_demo.py:84), never through ScanSession's Calibrate job with the demo.

</details>

<a id="demo-parity-demo-v01"></a>

### DEMO-V01 -- In `make run-sheet` (--look-only) the sheet's scan path can be reached only by ticking 'The film is in the transport', which is false there

**Severity** low · **Category** user-error · **Verdict** found-by-verifier

**Where:** `tools/gui.py:3097-3100`, `tools/gui.py:2075-2100`, `rps7200/demo.py:500-508`, `tasks.py:184-185`

The documented purpose of --look-only is to let on_scan_chosen run so the backend's 'no film' refusal is exercised. In a fresh demo session the path first stops at the calibration prompt. To get past it, the operator must tick an assertion that film is loaded, which is exactly what --look-only says is untrue. The demo then calibrates happily with no film. The driver's behaviour is matched, because it does not refuse either. What is lost is the one exercise meant to rehearse the correct habit: calibration with the film in, never on an empty transport. In the demo it becomes 'tick the box whatever is true'.

**Evidence (from the code):**

```text
on_scan_chosen: `if self._calibration_missing(self.sheet.top if ...): return` before anything else. The calibration prompt says: "The calibration frame is the lower part of the transport, which the film does not cover ... Calibrating an empty transport is a state the vendor never creates, and once it preceded a wedge." It shows an unticked `loaded = tk.BooleanVar(master=top, value=False)` checkbox. DemoScanner.ensure_shading does not consult self._no_film.
```

**Failure scenario:** Stefan runs `make run-sheet`, ticks frames and presses Scan. He is asked to confirm the film is in the transport and ticks it to continue. The demo calibrates with no film, and only then does the advance refuse. The demo has trained the click-through that on the hardware precedes calibrating an empty transport.

**Fix:** Let DemoScanner.ensure_shading raise the same UsbError via _need_film('calibrate') when no_film is set. The refusal then arrives through the failed-job path, as CLAUDE.md prescribes, and the operator is never asked to assert something false. Alternatively, have the prompt say that under --look-only the tick is a formality.

<a id="demo-parity-demo-v02"></a>

### DEMO-V02 -- Picture-signature cache is keyed by path only, so a re-filed or migrated entry keeps a stale likeness

**Severity** low · **Category** design · **Verdict** found-by-verifier

**Where:** `rps7200/demo.py:1263-1274`

The cache under demo/pictures.npz never invalidates. There is no mtime, checksum or size check. An entry whose prescan.tif or scan.tif is rewritten (migrate-raw, migrate_direction flipping a bottom-up prescan, a deleted-and-reused path) keeps its old signature. _next_strip then groups photographs from the stale value.

**Evidence (from the code):**

```text
`key = entry.as_posix(); signature = known.get(key); if signature is None: signature = picture_signature(entry)`
```

**Failure scenario:** After `tools/library.py migrate-raw --write` rewrites entries, or migrate_direction flips a prescan, a later demo 'new strip' treats two different photographs as one, or one photograph as two. The strip then repeats pictures or shows a frame out of place.

**Fix:** Key the cache on (path, mtime_ns and size of the file signed), or store the sha256 recorded in scan.json alongside each signature.

<a id="demo-parity-demo-15"></a>

### DEMO-15 -- session keeps hand-built substitute prescan metas as fallbacks, the exact shape CLAUDE.md forbids

**Severity** info · **Category** design · **Verdict** confirmed

**Where:** `rps7200/session.py:2072-2074`, `rps7200/session.py:2555-2557`, `rps7200/session.py:2576-2578`

Both DirectScanner and DemoScanner publish last_scan_meta, so these fallbacks are unreachable with either backend today. They exist 'for a stand-in' and would silently file a corrected prescan with no `shading` report (so labelled raw) if a scanner stopped publishing the meta. That is the failure behind the 26 mislabelled prescans.

**Evidence (from the code):**

```text
`meta = dict(getattr(self._scanner, "last_scan_meta", None) or {"resolution_dpi": job.resolution, "film": job.film, "channel_order": ["R", "G", "B"]})` and `dict(rf.prescan_meta or {"resolution_dpi": job.prescan_resolution, "channel_order": ["R", "G", "B"]}, ...)`. CLAUDE.md: `Pass the meta the scan returns, never a substitute.`
```

**Failure scenario:** A future stand-in or refactor leaves last_scan_meta None after a prescan. Each prescan is filed as raw pixels while its image is corrected, and `reconstruct` flags them as changed decodes again.

**Fix:** Raise, or file with `corrections=['shading']` and a note, when the scanner gives no meta, instead of inventing one. Make every stand-in publish last_scan_meta, which DemoScanner already does.

<details><summary>Second reader's check</summary>

The fallbacks exist (session.py:2072-2074, 2555-2557, 2576-2578). Both DirectScanner (sets last_scan_meta in scan) and DemoScanner (demo.py:598, 709) always publish last_scan_meta after a successful pass, so the fallbacks are unreachable with either backend today. This is a latent design risk rather than a defect, so info.

</details>

<a id="demo-parity-demo-17"></a>

### DEMO-17 -- Demo scan() silently drops driver arguments and differs in defaults and hook error handling

**Severity** info · **Category** demo-divergence · **Verdict** confirmed

**Where:** `rps7200/demo.py:652-664`, `rps7200/direct.py:2842-2860`, `rps7200/demo.py:1043-1045`, `rps7200/direct.py:673-683`

No current caller hits these differences: the session, auto_exposure and scan_roll never pass depth, advance or byte14 to scan. But `depth=DEPTH_8` would return 16-bit, `advance=True` would not advance, and a `byte14` override would not reach the carriage model. The default resolution differs (1800 against 300), and a raising log hook aborts a demo pass where it would not abort a real one.

**Evidence (from the code):**

```text
demo: `def scan(self, resolution: int = 1800, ..., fast_infrared: bool = True, **kw: Any)`, where `**kw` absorbs `depth`, `advance`, `require_media`, `skip_shading`, `byte14` and `slide_init_param`. Driver: `def scan(self, resolution: int = 300, infrared: bool = True, depth: int = DEPTH_16, frame=None, advance: bool = False, ... byte14: int | None = None, ...)`. demo `_log`: `if self.log_hook is not None: self.log_hook(message)`. Driver `_log` wraps the hook in try/except: `a broken display must not take down the scan`.
```

**Failure scenario:** A tool or test drives the demo with `scan(depth=DEPTH_8, advance=True)` and gets a 16-bit pass with the film unmoved, while the scanner returns 8-bit and advances.

**Fix:** Give demo.scan the driver's full signature: honour depth and advance, pass byte14 into the carriage model, reject unknown kwargs. Swallow hook exceptions in `_log` and `_work` as the driver does.

<details><summary>Second reader's check</summary>

DemoScanner.scan (demo.py:652-664) takes **kw and silently drops depth, advance, require_media, skip_shading, byte14 and slide_init_param, which DirectScanner.scan honours (direct.py:2842-2860). Its default resolution is 1800 where the driver's is 300. The demo's _log has no try/except (demo.py:1043-1045). No current caller (session._scan at session.py:2101-2111, scan_roll at direct.py:4124-4133, auto_exposure at direct.py:2491-2504) passes the dropped arguments, so this is latent.

</details>

<a id="demo-parity-demo-18"></a>

### DEMO-18 -- Real driver paths the demo cannot reach

**Severity** info · **Category** demo-divergence · **Verdict** confirmed

**Where:** `rps7200/demo.py:816-819`, `rps7200/session.py:2872-2875`, `rps7200/direct.py:1000-1016`, `rps7200/direct.py:3280-3291`, `rps7200/session.py:206-221`

`make run-demo` and `make run-sheet` cannot exercise: debug filing and claiming (the only record of metering probes and hold prescans), DeviceSuspect after an abandoned read, calibration archiving and cache writing, a counter that says nothing or an implausible number (seek's FilmNotPlaced branches), counter jumps in place_on_strip, start_scan refusals and read timeouts. By CLAUDE.md's own rule these are untested when no device is on the bus.

**Evidence (from the code):**

```text
demo: `suspect: str | None = None` with the comment `Nothing here can be`. The demo has no `debug`, `debug_claim`, `_debug_capture` or `archive_calibration`. session: `claim = getattr(self._scanner, "debug_claim", None)` is None under the demo. The demo's `position()` always answers while its transport is open.
```

**Failure scenario:** A regression in debug_claim's array identity, or in seek's silent-counter refusal, passes every demo exercise and first shows up on the hardware.

**Fix:** Add opt-in fault injection to the stand-in's answers (for example `DemoScanner(faults={...})`): a silent counter, a double step, a read that fails part way (setting `suspect`), and a debug flag that spools and claims like the driver.

<details><summary>Second reader's check</summary>

DemoScanner has no debug, debug_claim, _debug_capture, archive_calibration or read_state, and its suspect is a constant None (demo.py:819). session.py:2872-2875 therefore sees claim=None under the demo. With RPS7200_DEBUG=1 the demo files no metering probes or hold prescans, where the driver would. These paths cannot be reached with no device.

</details>

<a id="demo-parity-demo-19"></a>

### DEMO-19 -- Demo metering records probe rounds that cannot respond to exposure

**Severity** info · **Category** demo-divergence · **Verdict** confirmed

**Where:** `rps7200/demo.py:549-565`, `rps7200/direct.py:2489-2565`, `rps7200/demo.py:701-708`

The real auto_exposure runs on stored pictures that ignore exposure_scale. Round 2 therefore always measures the same levels as round 1. A channel clipped in the stored picture is backed off 0.25× each round, down to about 1/64. The resulting fabricated `metering` and `device_settings` are filed in demo entries and logged as if measured.

**Evidence (from the code):**

```text
demo docstring: `The stored pictures carry the exposure they were taken at: the scales this arrives at change the record, not the pixels.` The driver's loop continues after round 2 while any channel is clipped (`growth.append(0.25)` each round).
```

**Failure scenario:** Someone checks a metering change in the demo, sees 'auto-exposure result: [.., 0.016]' for a stored frame with a blown highlight, and takes it as the metering's behaviour.

**Fix:** Scale the fitted picture by exposure_scale / the source entry's recorded scale (clipped at the rail) so that metering converges, or record `metering.simulated: true` in the demo.

<details><summary>Second reader's check</summary>

The driver's metering loop runs up to budget = max(rounds, rounds+1) = 3 rounds (direct.py:2484 area). It continues past `rounds` while any channel is clipped (direct.py:2562-2563) and multiplies a clipped channel by 0.25 each round (direct.py:2578-2579). The demo's stored pictures do not respond to exposure_scale (demo.py:543-547, 556-559), so each round measures the same levels. A clipped channel ends at 0.25^3 ≈ 1/64, clamped at 0.01. Those scales and probe rounds are filed as metering and device_settings in demo entries (demo.py:701-708).

</details>

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Demo scan/prescan library entry pixels | demo/library/<UTC>_<stock\|unknown-film>[_f<frame>]_<dpi>dpi[_ir]/scan.tif (or --library override) | TIFF, uint16 (scan) or uint8 (prescan), HxWxC; uncompressed for single passes (compress=False), then library.compact at session close; compressed for roll frames | Raw for passes drawn from raw bytes (demo last_pixels_raw: stored decode fitted by nearest-neighbour index maps, rolled by _shift, synthetic IR plane added when a 3-plane source is asked for RGBI). Corrected, and labelled corrections_applied=['shading'], when drawn from a stored prescan.tif or a legacy corrected scan.tif | session.FrameWriter._write -> library.save (session.py:1656-1668), from ScanSession._file (session.py:2781-2906) | library.load/corrected/reconstruct/verify, tools/library.py, the GUI's full-resolution view; DemoScanner._decode fallback if --demo-source points at demo/library | Lossless and matches raw.bin.gz exactly (reconstruct 'identical'), but it is not a scan: its derivation from the source entry (shape, shift, IR synthesis) is not recorded |
| Demo raw bytes | demo/library/<id>/raw.bin.gz (raw.bin before compact) | Index-format lines (2-byte doubled tag + little-endian samples), gzip level 6; raw.layout {format:index, bytes_per_line, line_stride, index_header, width, lines, channels, byte_order, lines_received}; sha256 recorded | Raw, but synthesized by direction.encode_index from the fitted pixels (reversed when the demo's carriage is modelled at the far end), not bytes received from a device | library.save from capture_record()['raw'] = DemoScanner.last_raw (demo.py:627-639, 882-884) | library.read_raw/decode_raw/reconstruct/verify; DemoScanner._decode via library.decode_raw when the demo draws from a library | Byte-exact with its own record and checksum; carries no marker that it is synthetic |
| Demo shading reference | demo/library/<id>/shading.npz | np.savez_compressed: ref<c>, mean<c>, dark<c>, darkmean<c> (float64), pixels_per_line, channels, dark_channels | Either the source entry's device reference verbatim (unchanged columns), or a synthesized per-pass reference (_pass_reference: columns re-indexed through the stored mask, pixels_per_line = pass width, unreached columns gain 1, dropped planes left out) | library.save(reference=capture['reference']) (demo.py:882, 973-974, 976-1019) | library.corrected / load, verify (checksum) | Lossless npz; for a resampled pass it describes the demo's column map, not any calibration the device made |
| Demo CCD mask | demo/library/<id>/ccd_mask.bin | bytes, 0x00 used / 0x70 unused per CCD column | The source entry's mask, only when every column is unchanged; absent (None) for resized or shifted passes | library.save(ccd_mask=capture['ccd_mask']) (demo.py:970-972) | library.corrected, verify | Exact copy of the source's mask |
| Demo entry record | demo/library/<id>/scan.json | JSON, written atomically (_write_atomic): scan.* (SCAN_FIELDS; protocol_revision null; carriage_state {far_end, stale:false, modelled:true}; read_direction), extra.demo=true, extra.demo_source {entry, file}, extra.roll_* for roll frames, device {description:'DEMO  MF Scanner  fw 1.70  (no scanner attached)'}, device_settings from DEVICE_SETTINGS scaled, metering (probe rounds on stored pictures), registration (including the deliberate slip at position 2), calibration.report/skipped (UNCALIBRATED_SOURCE or CORRECTED_WHEN_STORED report), provenance, files checksums | Metadata. Exposure, gain, offset and metering are fabricated by the stand-in; mode, commands, filter_offsets and shading_origin are absent | library.save (library.py:306-397) | library.entries/verify (exempts extra.demo from the missing-reference complaint)/reconstruct/signature/duplicates; DemoScanner when drawing from a library | Exact JSON; film is 'negative' on hold/aim replacement prescans whatever the roll's film (DEMO-03) |
| Demo roll-frame prescan | demo/library/<id>/prescan.tif | TIFF uint8 HxWx3 | Corrected (the picture shown), with only read_direction/carriage_state recorded under record.prescan | library.save(prescan=rf.prescan, prescan_meta=rf.prescan_meta) via session._roll -> _file | GUI, migrate_direction, DemoScanner (picture_signature, _stored fallback, _pool_for) when drawing from that library | Lossless for what was shown; no raw bytes of its own |
| Library index | demo/library/index.json | JSON summary per entry (id, created, dpi, channels, film, frame, tags, corrected, notes) | Derived | library.reindex after every save | Humans/tools listing the library | Derived; carries no demo flag |
| Demo roll and walk folders | demo/rolls/<name>/{survey.json, roll.json, roll.json/survey.json kept previous, prescanNN.tif, prescanNN-before.tif, frameNN.tif, approved.json} | JSON manifests (write_manifest, atomic with previous kept) and TIFF deliverables | Deliverables are corrected and oriented; manifests hold settings, per-frame registration, exposure and orientation | ScanSession._roll / RollManifest (session.py:2275-2701), GUI on_scan_chosen (approved.json), carry_walk | GUI open_roll/read_survey, tools/scan_roll.py, session resume (earlier_manifest/renumbered) | Atomic JSON; content partly synthetic (demo film model) |
| Real walk shown by run-sheet / --open-roll | rolls/<name>/{survey.json, roll.json, approved.json, prescanNN.tif} | JSON and TIFF | Corrected prescans and manifests | Real sessions (not the demo, unless --rolls points at rolls/, see DEMO-02) | GUI read_survey / read_approved (read-only), EdgeWatch | Read only under default --demo |
| Picture-signature cache | demo/pictures.npz (DEMO_ROOT/pictures.npz, relative to CWD) | np.savez: paths (posix strings of entry dirs), signatures (float32 24x48 each) | Derived from prescan.tif or scan.tif (<=600 dpi) | DemoScanner._sign_pictures on a daemon thread (demo.py:1254-1281), written non-atomically | DemoScanner._sign_pictures next launch; _pictures_for / _next_strip | Derived cache, keyed by path only (not content or mtime); a truncated file is not recovered (DEMO-09) |
| Demo GUI settings | demo/gui-settings.json | JSON | n/a | ScannerGui._remember (settings.save) | ScannerGui at launch (settings.load) | n/a |
| Demo calibration cache | demo/calibration/shading.npz | npz | n/a | Nobody: DemoScanner.ensure_shading writes nothing | GUI _cached_reference / calibration prompt (existence only) | Never exists under the demo, so the reuse path is unreachable (DEMO-06) |
| Source libraries the demo draws from | <--demo-source, default library>/*/{scan.json, raw.bin.gz\|raw.bin, scan.tif, prescan.tif, shading.npz, ccd_mask.bin}, plus '<source> *' siblings and nested per-dpi folders | As library.save writes them | Raw scan.tif/raw bytes with reference; corrected prescan.tif; legacy corrected scan.tif | Real sessions and tools | DemoScanner: open (glob), best_pair, _source_for, _shape_for, _decode (library.decode_raw, tiff.read, ShadingReference.load), _stored, _pool_for, picture_signature | Read-only; the demo never writes to its source libraries |
| Operator copies | --out <dir>/<roll>_frameNN_<dpi>dpi[_ir].tif\|.jpg, <dir>/prescans/... | TIFF or JPEG (plus infrared companion) | Corrected, oriented, mono where asked | FrameWriter._write via export.write | The operator, NegPy | JPEG is lossy; not the record |

**Second reader's corrections to this table:**

1) "Demo entry record" says film is 'negative' on hold/aim replacement prescans. That holds only for dry-run walk entries, where rf.prescan_meta is filed as the entry (session.py:2550-2559). On a scanning roll only read_direction and carriage_state of the prescan meta are kept (library.py:381). The same bug affects real walks filed by tools/scan_roll.py:669, not only demo ones.
2) The record does carry a second demo marker the table omits: device.description = "DEMO  MF Scanner  fw 1.70  (no scanner attached)" (demo.py:193 via library._describe_inquiry). "carries no marker that it is synthetic" is true of the raw block only, not of the entry as a whole.
3) "Demo roll and walk folders" and "Real walk shown by run-sheet": under --demo with --rolls pointing at the real rolls folder, the real walk is written to, not only read: approved.json (gui.py:3164-3166) and the roll's frames and roll.json via Roll(out=folder). The comment at gui.py:8868-8874 claiming otherwise is wrong in that configuration.
4) "Demo calibration cache": the GUI reads session.reference, which is `--reference` if given. Under --demo with `--reference calibration/shading.npz`, the real cache's existence and age are read, but it is never loaded or written, because the demo's ensure_shading ignores `path`.
5) Demo shading.npz: when the reference is synthesized by _pass_reference, its arrays are float64 and pixels_per_line equals the pass width. When the source reference is passed through unchanged, its original dtype is kept. It is written by ShadingReference.save through np.savez_compressed (shading.py:75-91), as the table states.

## What the operator can do

- Run `make run-demo` (tools/gui.py --demo). It reads library/ plus any 'library *' folders beside it and writes demo/library, demo/rolls, demo/gui-settings.json and demo/pictures.npz, with no scanner on the bus.
- Run `make run-sheet` (--demo --look-only --open-roll rolls/aligned-strip). It shows a real stored walk read-only, recomputes edge proposals and lets the operator tick frames and commission a roll; every pass, move or roll then fails with 'there is no film in the transport'.
- Use `--demo-source <dir>` to choose which library supplies pictures; a second roll started from the Roll button draws other photographs from every library beside it.
- Use `--demo-entry <path>` to ask for a specific entry. It only takes effect when no raw-byte entry of the chosen film exists (DEMO-04).
- Calibrate (measure/reuse/off), prescan, scan RGB or RGBI with auto exposure, walk or scan a roll, set positions on the contact sheet, nudge, step frames, Stop, and Force abort (after typing ABORT). All of these go through the real session and borrowed driver loops.
- Override --library, --rolls, --reference, --settings and --out together with --demo. Nothing refuses any combination (DEMO-02).
- Delete a result. The library entry is removed only when it lies inside the session's library (gui.py on_delete _within check).

## What the operator should not do

- Pass `--library library` or `--rolls rolls` (or any real path) together with `--demo`: synthetic entries and walks are filed as if real.
- Point `--demo-source` at demo/library. The demo then draws from its own synthetic entries, and its shape table (_shape_for) and pools feed on themselves.
- Treat demo outcomes as evidence about the hardware: hold or aim convergence (backlash is not modelled at frame entry, and a deliberate slip at strip position 3), metering scales, progress line counts, timings (divided by 120), or calibration behaviour.
- Use demo/library entries with analysis tools (registration_margin, dpi_analysis, uniformity, duplicates/prune), or merge them into a real library.
- Run two demo windows at once, or quit during the first launch's signature pass. Both share and non-atomically write demo/pictures.npz.
- Launch from another working directory. `demo/`, `library` and `rolls/aligned-strip` are all relative to CWD.
- Tick 'The film is in the transport' under --look-only just to get past the calibration prompt. It is false, and on hardware calibrating with an empty transport preceded a wedge.

## Mistakes nothing guards against

- `--demo --library library` silently files demo entries into the real library. They use normal ids and tags, reconstruct as 'identical', are skipped by verify's missing-reference check, and are later drawn on by the demo itself and by analysis tools.
- `--demo --rolls rolls --open-roll rolls/X` makes `_roll_folder` return the real walk folder, so approved.json, roll.json and frames are written back into the walk that was only meant to be shown.
- With `--library` pointing at the real library under --demo, Delete in the window removes real entries, raw bytes included, after one confirmation.
- A mistyped `--demo-entry` is accepted: open() logs 'demo mode: showing <name>' and pictures come from other entries.
- A mistyped `--demo-source` or a missing library silently gives 574x862 test cards at every resolution, uncorrected even with shading on.
- Calibrate with shading set to 'reuse' and no cache: the demo reports 'shading loaded (demo)' in about 1 s, where the scanner would run a 3-4 minute calibration.
- Quitting the demo while pictures.npz is being written leaves a truncated cache. The signature thread then dies on every later launch and second-roll strips silently stop drawing on other libraries.
- Under --look-only, a roll whose start is not the current frame fails at the seek's advance with a UsbError, not a FilmNotPlaced. The roll folder and approved.json written by on_scan_chosen are left behind in demo/rolls.

## Dataflow notes

Entry. tools/gui.py main() (8845-8906) builds `ScanSession(root=args.library or demo/library, reference=demo/calibration/shading.npz, rolls=demo/rolls)` and, under --demo, sets `session._open_scanner = lambda: DemoScanner(source, entry=..., no_film=args.look_only, libraries=libraries_beside(source), cache=demo/pictures.npz)`. ScanSession._run (session.py:1928-1945) calls the factory, `_listen` attaches log_hook and progress_hook, then open() and inquiry(). DemoScanner.open (demo.py:316-340) globs `root/*/scan.json`, starts the `_sign_pictures` daemon (1254-1281, which reads prescan.tif or scan.tif of every entry in every library and writes pictures.npz), and picks `pair = best_pair(root)`.

Calibrate. session._calibrate (2041-2059) calls demo.ensure_shading (500-508), which only sets `_calibrated`. No reference is kept and no file is written.

Prescan and Scan. session._prescan and session._scan (2061-2132) call demo.prescan (567-602) or demo.scan (652-710). Each runs:
- The IR refusal (a retyped copy of the driver's) and `_refuse` (DirectScanner.correctable_at/uncorrectable/uncalibrated), then `_need_film`.
- For a scan with auto_exposure: `auto_exposure` (549-565), which is DirectScanner.auto_exposure bound to the demo and calls demo.scan for its RGB probes, with `_metering` holding the film.
- `_forget_last_pass`, then `_work` (sleep estimate/120, progress hook), then `_take` (846-909).
- `_take` calls `_stored` (1331-1371), which picks a source with `_source_for` (1059-1108: nearest-1800 raw-byte entry of that film, else pair, else `_pixels` round robin or test card), or the strip entry inside a roll. It then reads via `_decode` (1137-1207: library.decode_raw, else scan.tif, plus reference and mask), or the stored prescan.tif for prescans of uncorrectable entries.
- `_fit` (911-974) sizes the picture with `_shape_for`, shifts it with `_shift` from `_film_mm` via APERTURE_MM/width, sets depth with `_at_depth`, adds a clear IR plane, and builds `_pass_reference` for moved columns.
- `_read_as_carriage` (604-650) encodes index lines with encode_index (reversed if `_carriage_far`), decodes them with DirectScanner.decode_index, keeps `last_raw`/`last_raw_layout` when keep_raw, and updates the carriage with DirectScanner.byte14_for.
- apply_shading runs last, and `_capture`, `last_pixels_raw` and `last_scan_meta` are set.

The session then reads `last_scan_meta`/`last_pixels_raw`, calls `_deliver` (a decimated working copy goes to the UI), and `_file` (2781-2906). `_file` calls `capture_record()`, runs the raw_bytes_disagree and shape guards, skips `debug_claim` (it is absent on the demo), and hands the job to FrameWriter._write (1618-1703), which calls library.save (raw pixels, or corrected pixels labelled corrections=['shading'] when there are no raw pixels). Operator copies go through export.write.

Roll. session._roll (2275-2701) first runs session.seek (229-321) through the demo's wait_warm, position, advance and retreat, then calls demo.scan_roll (712-759). scan_roll checks `_need_film`, records `_starting`/`_rolling`, calls `_new_frame`, and yields from `_drivers_roll`, which is DirectScanner.scan_roll bound to the demo. That loop calls demo.prescan, `_hold_to_approved`/`_aim_frame` (borrowed; they call demo.nudge (417-470) and demo.prescan without film), auto_exposure and demo.scan. `_begin_roll` lays a new strip (`_next_strip`/`_pictures_for`, which joins the signing thread) at the first pass. RollFrames go back to session._roll, which files dry-run prescans or frames with their prescans, and RollManifest writes survey.json/roll.json.

Move. session._move (2203-2273) calls demo.advance or retreat (385-415, with `_new_frame` only while rolling) or demo.nudge per plan_nudges step.

Exits and hazards:
- Stop sets session._stop, which the borrowed loop reads through should_stop.
- force_abort closes demo.t (_FakeTransport), so `_work` raises UsbError and position() returns None. The demo never sets `suspect`.
- Nothing is ever written back to the source libraries.
- Demo output leaves only through library.save into session.root, the roll folders, --out and pictures.npz. Isolation from the real library depends entirely on the defaults (DEMO_ROOT) not being overridden.
