# Can each kind of pass be re-derived? (gap pass)

Area key `re-derivation-matrix-per-pass-kind`. 9 findings: 2 medium, 5 low, 2 info.

Every finding below was produced by one reader and then re-checked against the code by a second, adversarial reader. `verdict` is that second reader's: `confirmed`, `partly` (real, description corrected -- the corrected text is shown), or `found-by-verifier` (added by the second reader).

[Back to the summary](../README.md)

## What this area is

A read-only check of the owner's central requirement, one pass kind at a time, against the executable code at HEAD cd4e083 (branch claude/clever-mayer-dy1j3o).


**Every pass that goes through DirectScanner.scan() is well described.** That covers window scans and prescans, bracket passes, roll frames, dry-run walk prescans, and metering probes or hold/aim prescans when debug filing is on. For each of these the entry holds:
- the exact INDEX bytes (read_planes keeps them; _read_payload returns exactly read_size or raises) and a raw_layout that is sufficient to decode them;
- every command sent between the start of the log and START SCAN/READ/settle, in extra.commands. That includes the MODE SELECT payload (byte 14, fast-IR bit), SET SCAN FRAME, the READ GAIN/OFFSET reply, the SET GAIN/OFFSET payload, SLIDE INIT, the COPY (mask) reply and the PARAM reply (filter offsets, available lines);
- fast_infrared, per-channel exposure including exposure[3] (the IR timer), read_direction, stagger_realigned, the correction report or skip reason, the session reference and this pass's CCD mask.

reconstruct replays the decode, the upright turn and the 7200 dpi stagger.


**What breaks the requirement lies outside scan():**
- **Prescans inside roll-frame entries.** Stored corrected, with no raw bytes, mask or commands; prescan_before is discarded.
- **Metering evidence for rolls and brackets.** Their frames are recorded as exposure_metered=False with no metering record.
- **Sub-frame SLIDE moves.** Param bytes are never recorded; the hold loop keeps only a total, spent_mm.
- **The calibration archive.** Outside the library, and linked only by a CWD-relative string, and only when calibrated this session.
- **Anything filed only by debug.** Metering probes and hold/aim prescans are filed only with RPS7200_DEBUG on, unlinked from the frame they served.
- **Decisions drawn from other passes.** Their inputs are not recorded: the metering region, the reversal evidence and the "check this frame" warning, and the hold's reference entry.

The serialisation concern does not fire today. Every value placed in meta on the traced paths is cast to a Python int, float, bool, str, list or dict before it reaches json.dumps(default=str). The only type change on a round trip is tuple to list.


Test proof that a row can be re-derived exists for:
- hand-built entries (plain, bottom-up, 7200 dpi stagger, labelled-corrected);
- demo passes that go through ScanSession.

No test covers a real DirectScanner pass, a roll frame from a real scanner, a debug-filed probe or a re-reduction of a calibration.

## Findings at a glance

| ID | Severity | Category | Verdict | Title |
|---|---|---|---|---|
| [RDM-01](#re-derivation-matrix-per-pass-kind-rdm-01) | medium | data-integrity | partly | Decisions drawn from other passes are recorded by outcome only: no metering region, no reversal evidence, no 'check this frame' flag, no hold reference identity |
| [RDM-A1](#re-derivation-matrix-per-pass-kind-rdm-a1) | medium | data-integrity | found-by-verifier | Every metered roll frame, and every auto-exposed bracket pass, is filed as 'not metered' with no metering evidence |
| [RDM-02](#re-derivation-matrix-per-pass-kind-rdm-02) | low | bug | confirmed | The same roll scanned from tools/scan_roll.py never gets the reversal check the window applies, and its entries lack the `reversal` field |
| [RDM-03](#re-derivation-matrix-per-pass-kind-rdm-03) | low | data-integrity | confirmed | No record says which code reduced a shading reference: calibration.json has no provenance or reduction parameters, and a loaded reference records only a path and an mtime |
| [RDM-A2](#re-derivation-matrix-per-pass-kind-rdm-a2) | low | data-integrity | found-by-verifier | Hold and aim verification prescans are taken as film='negative' whatever the roll is, and that meta becomes the filed prescan's record |
| [RDM-A3](#re-derivation-matrix-per-pass-kind-rdm-a3) | low | data-integrity | found-by-verifier | Passes kept only by debug filing (metering probes, hold/aim prescans) go to ./library or RPS7200_DEBUG_ROOT rather than the caller's library, carry no role, and are not linked to the frame they served |
| [RDM-A5](#re-derivation-matrix-per-pass-kind-rdm-a5) | low | data-integrity | found-by-verifier | A deliberately uncorrected pass (shading=False) is filed with the session's shading.npz but no shading_origin |
| [RDM-04](#re-derivation-matrix-per-pass-kind-rdm-04) | info | data-integrity | confirmed | Serialisation check: no traced meta field reaches json.dumps(default=str) as numpy, bytes or Path; the risk is latent |
| [RDM-A4](#re-derivation-matrix-per-pass-kind-rdm-a4) | info | test-gap | found-by-verifier | Nothing proves re-derivability beyond the decode: no check that data.bin re-reduces to the stored shading.npz, and reconstruct never exercises correction, bracket merge, reversal or hold |

## Findings in full

<a id="re-derivation-matrix-per-pass-kind-rdm-01"></a>

### RDM-01 -- Decisions drawn from other passes are recorded by outcome only: no metering region, no reversal evidence, no 'check this frame' flag, no hold reference identity

**Severity** medium · **Category** data-integrity · **Verdict** partly

**Where:** `rps7200/direct.py:2517-2519`, `rps7200/direct.py:2631-2643`, `rps7200/session.py:2175-2201`, `rps7200/session.py:2134-2146`, `rps7200/direct.py:3345-3350`, `rps7200/direct.py:4029-4030`, `rps7200/direct.py:3509-3515`, `rps7200/session.py:2562-2582`

**Doc claim:** rps7200/library.py:350-359 claims the registration record exists so the evidence behind a frame's placement survives. rps7200/direct.py:2442-2444 claims last_metering makes metering "checkable from ordinary scans".

Three decisions are recorded by outcome only.
- Metering does not record its crop region. This matters only when debug filing kept the round-1 probe, which is then unlinked.
- Reversal keeps only the applied turn, not the scores or the margin. The 'check this frame' disagreement for known-direction passes exists only in the session log. For a window single scan, nothing names the prescan entry it was compared with. For a roll frame, the compared prescan is the entry's own prescan.tif.
- The hold/aim record carries no reference identity. For operator holds the only link is approved.json's reference_entry. For aimed frames of a real roll, the pre-aim reference is not kept at all unless debug filing is on, and then it is unlinked.

**Evidence (from the code):**

```text
Metering picks its region on the first probe and uses it for every round, but never records it: `if region is None:\n    region = metering_slice(image)\ncrop = image[region]` (direct.py:2517-2519). `self.last_metering = {"target":..., "percentile":..., "film":..., "locked":..., "infrared":..., "blue_headroom":..., "resolution_dpi":..., "base_exposure":..., "rounds": probes, "scales":..., "limited":...}` (2631-2643) has no region key.

Reversal. When a pass whose direction is known disagrees with its prescan, the only trace is a log line: `if _read_known(meta):\n    extra, detail = reversal_against(reference, image)\n    if extra != (0, False):\n        self._emit("log", text=(... "is left as it came -- check this frame"))\n    return meta` (session.py:2177-2186). When the pass is turned: `return dict(meta, reversal=[extra[0], bool(extra[1])])` (2201). `detail` (the scores and the margin) is dropped, and the prescan it was judged against, `self._last_prescan` (2134-2146), is not identified.

Hold. `out` holds `"target_mm"`, `"outcome"`, `"moves"`, `"spent_mm"`, `"history"`, `"source"`… (direct.py:3345-3350). It has no `approved.reference_entry`, although `Approved` carries one ("`reference_entry` is the library path to the same pass, for the case where the array did not survive", session.py:1332-1333).
```

**Failure scenario:** A roll of 20 frames is scanned from the contact sheet. Frame 7's scan disagrees with its prescan by margin 0.9 and the log says "check this frame". Frame 12's direction was unknown and it was turned 180°. A month later the reversal threshold is suspected wrong. From the library, nobody can list the flagged frames, recover the margins, or tell which prescan entry frame 12 was compared with. Nor can the confidences in frame 7's hold history be recomputed, because the entry does not say which walk prescan was the reference.

**Fix:** Add these to the record:
- `metering.region` (the row and column slice bounds);
- `reversal_detail` (the scores, margin and reason from `reversal_against`), plus a `reversal_disagreement` field for the known-direction branch;
- the judged prescan's `started_utc` or entry id;
- `reference_entry` (and the reference's shape and `started_utc`) in the hold record `out`.

These are small values that are already in hand where each decision is made.

<details><summary>Second reader's check</summary>

Most of the finding holds up in the code.
- Metering: `region = metering_slice(image)` (direct.py:2517-2519) is never put into `last_metering` (direct.py:2631-2643).
- Reversal: the known-direction disagreement only reaches `self._emit("log", ...)` and `return meta` (session.py:2177-2186). The turned branch keeps only `reversal=[deg, mirrored]` and drops `detail` (session.py:2194-2201).
- Hold: the record `out` (direct.py:3345-3350) and `marks["approved"]` (direct.py:4029-4030) carry no reference identity. `Approved.reference_entry` (session.py:1348) is only written to approved.json by the GUI (tools/gui.py:3317).
- Aim: `_Aim(offset_mm=decision, reference=image)` (direct.py:3509-3515) measures against the pre-aim prescan. On a real roll, `prescan_before` is written only on the dry-run branch (session.py:2562-2582). With debug off it is kept nowhere. With debug on it is filed as an unlinked 'debug' entry.

One point overreaches. For window roll frames, the prescan `_note_reversal` compares against is `rf.prescan` (session.py:2604-2608), and that same array is stored as the entry's own prescan.tif (session.py:2632). The 'judged prescan' is therefore identified, though it is stored corrected. The unlinked-reference problem applies to window single scans (`_prescan_here`, session.py:2134-2146), where the prescan was filed as a separate entry. The metering region matters less than stated: the round-1 probe is itself filed only under RPS7200_DEBUG, so without debug the levels cannot be re-derived whether or not the region is recorded.

</details>

<a id="re-derivation-matrix-per-pass-kind-rdm-a1"></a>

### RDM-A1 -- Every metered roll frame, and every auto-exposed bracket pass, is filed as 'not metered' with no metering evidence

**Severity** medium · **Category** data-integrity · **Verdict** found-by-verifier

**Where:** `rps7200/direct.py:4105-4112`, `rps7200/direct.py:4124-4133`, `rps7200/direct.py:2791-2795`, `rps7200/direct.py:2811-2820`, `rps7200/direct.py:3186`, `rps7200/direct.py:3219-3220`, `rps7200/library.py:344-349`, `rps7200/direct.py:2442-2444`, `rps7200/session.py:2659-2665`

**Doc claim:** rps7200/library.py:344-349 ('What the metering probe measured, when this scan did its own ... Absent on a scan given its exposure rather than metering one'); rps7200/direct.py:2442-2444 (':meth:`scan` files it with the entry ... checkable from ordinary scans'); rps7200/direct.py:363-364 ('last_metering records blue's achieved level on every metered scan')

By default a roll meters every frame. The rounds, levels, targets, `blue_headroom`, `base_exposure` and `limited` flags are computed and left in `last_metering`, but the frame scan is told nothing about them. Every roll frame entry therefore says `scan.exposure_metered: false` and `metering: null`. That is the exact state library.py:344-349 describes as 'a scan given its exposure rather than metering one', and it is false. direct.py:2442-2444 and 363-364 claim `scan` files the metering record 'with the entry' and that blue's RGBI headroom becomes 'checkable from ordinary scans'. Rolls are where RGBI scans are actually made, and none of them carry it. Auto-exposed brackets from tools/scan.py lose it the same way. The probes themselves are filed only under RPS7200_DEBUG, and nothing links them to the frame. A side effect: `signature()` counts these metered exposures as commanded (library.py:938-943), so repeat scans of the same roll frame never reduce to each other. That direction is conservative.

**Evidence (from the code):**

```text
Roll loop: `scales = self.auto_exposure(target=exposure_target, infrared=infrared, film=film, shading=shading,)` (direct.py:4108-4111), then `image, meta = self.scan(resolution=resolution, infrared=infrared, frame=window, exposure_scale=scales, film=film, ...)` (4124-4133). No `auto_exposure=True` is passed, and scan()'s default is False (direct.py:2851). scan() then writes `"exposure_metered": bool(auto_exposure)` (3186) and attaches the evidence only `if auto_exposure and self.last_metering is not None: meta["metering"] = self.last_metering` (3219-3220). scan_bracket does the same: `scales = self.auto_exposure(film=film, infrared=infrared, shading=shading)` then `self.scan(..., exposure_scale=pass_scale, ...)` (2791-2820). The roll manifest keeps only `for key in ("exposure", "gain", "offset")` (session.py:2662-2664). The default is `meter: str = METER_EACH` (session.py:1425; tools/scan_roll.py:152).
```

**Failure scenario:** A 30-frame RGBI colour-negative roll is scanned from the window with the default METER_EACH. Later someone wants to check BLUE_RGBI_HEADROOM against the blue levels metering reached, or to find why frame 12 came out 20% under target (held by the timer ceiling, `limited`). All 30 entries have `metering: null` and `exposure_metered: false`, and roll.json has only the final exposure. The evidence is gone and the records claim the exposures were commanded.

**Fix:** Have scan() accept the metering record from a caller that metered separately, for example `scan(..., metering=self.last_metering)`, or set `meta['metering']` and `exposure_metered=True` in scan_roll right after scan(). For METER_ONCE, record that the scales were reused from frame N. For brackets, record the metered base scales and the metering block on each pass. Keep `exposure_metered` False there so `signature` still separates members, or add an explicit `bracket` key to the signature.

<a id="re-derivation-matrix-per-pass-kind-rdm-02"></a>

### RDM-02 -- The same roll scanned from tools/scan_roll.py never gets the reversal check the window applies, and its entries lack the `reversal` field

**Severity** low · **Category** bug · **Verdict** confirmed

**Where:** `tools/scan_roll.py:731-760`, `rps7200/session.py:2596-2609`, `rps7200/session.py:2193-2201`, `rps7200/session.py:2853-2858`

**Doc claim:** rps7200/session.py:2150-2173 (the _note_reversal docstring) says every file that leaves is turned by it, TIFF and JPEG alike, including a roll's own frameNN.tif. That does not hold for rolls scanned by tools/scan_roll.py.

When a frame's direction is UNKNOWN, `_note_reversal` compares the scan with the frame's own prescan and turns the delivered file if it reads 180° against it, recording `reversal` in the entry. The CLI roll path shares the driver loop (`DirectScanner.scan_roll`) but skips this session-level step. The same unknown-direction frame is therefore delivered turned by the window and untouched by the CLI, and the two entries disagree about the delivered arrangement. This differs from the missing rotation and mono handling already noted for this tool: the check exists to catch a carriage reversal, not an operator choice.

**Evidence (from the code):**

```text
Window roll: `frame_meta = self._note_reversal(rf.meta, rf.image, rf.prescan, rf.prescan_meta, prescan_reversed=...)` (session.py:2604-2608). `_file` then composes `reversal` into the delivered arrangement: `reversal = meta.get("reversal") ... turn, flip = preview.compose((int(reversal[0]), bool(reversal[1])), (turn, flip))` (2853-2857).

CLI roll: `writer.submit(... image=frame.image, raw_image=frame.raw_image, meta=dict(frame.meta, roll_membership=roll_membership(roll_name, number, "frame", out)), prescan=frame.prescan, ...)` (tools/scan_roll.py:731-756). No `_note_reversal`, `reversal_against` or `rotate` appears anywhere in tools/scan_roll.py (grep finds none).
```

**Failure scenario:** A 600 dpi RGBI roll is scanned with `tools/scan_roll.py`. A frame's line tags are inconclusive (a complete read whose first and last lines disagree, so direction UNKNOWN) and it came back rows-reversed. frameNN.tif is delivered upside down with no warning. Its library entry has no `reversal`, so an Export from the window reproduces the upside-down file as well.

**Fix:** Move the reversal judgment below the seam that both front ends share (for example, have `scan_roll` return the decision in `RollFrame.meta`), or call the same helper from tools/scan_roll.py before submitting. Record the evidence per RDM-01.

<details><summary>Second reader's check</summary>

tools/scan_roll.py never calls `_note_reversal` or `reversal_against`; a grep for reversal/rotate/match_prescan finds nothing in it. It submits `meta=dict(frame.meta, roll_membership=...)` directly (tools/scan_roll.py:731-756). The window roll calls `_note_reversal(rf.meta, rf.image, rf.prescan, ...)` (session.py:2604-2608), and `_file` composes `meta['reversal']` into the delivered arrangement (session.py:2853-2857). `match_prescan` defaults to True (session.py:1782).

The case is narrow. `read_direction` returns UNKNOWN only for a complete read whose two ends disagree, or for a pass with no R or no B tag (direction.py:103-117). A short read takes the direction from its first lines and counts as known. Low is right. The _note_reversal docstring overclaims ('every file that leaves is turned by it', session.py:2155-2158) for the CLI path.

</details>

<a id="re-derivation-matrix-per-pass-kind-rdm-03"></a>

### RDM-03 -- No record says which code reduced a shading reference: calibration.json has no provenance or reduction parameters, and a loaded reference records only a path and an mtime

**Severity** low · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/direct.py:788-803`, `rps7200/direct.py:720-729`, `rps7200/direct.py:2351-2356`, `rps7200/shading.py:109-111`, `rps7200/shading.py:75-91`, `rps7200/library.py:385`

**Doc claim:** rps7200/direct.py:850-853 claims the reference is a reduction of the calibration bytes and that "a reduction cannot be redone with better code once its input is gone". It is equally impossible to tell which references need redoing.

Every entry's shading.npz is a reduction (a dark/light split by level) of calibration bytes. When the reference was measured in the same process, the entry's `provenance.driver_commit_at_import` identifies the reducing code. When it was loaded from the cache (the window's "Use the cached one", or `--reuse`), the reduction was done by whatever code wrote the cache, possibly weeks and many commits earlier. Nothing records that code: not the npz, not calibration.json, not shading_origin. Such entries also carry no archive link (already reported), so neither the reducing code nor the input bytes can be named. After a change to the reduction (a split fix, or the partial-calibration bug already reported), there is no way to tell which entries carry references from the old code.

**Evidence (from the code):**

```text
The archive record is `{"measured_utc", "resolution", "pixels_per_line", "bytes_per_line", "index_header", "bytes", "sha256", "duration_s", "commands", "reference", "ccd_mask", "protocol_revision"}` (direct.py:788-801): no driver commit and no reduction parameters.

The reduction takes a tunable: `def calculate_shading(data: bytes, pixels_per_line: int, split_ratio: float = 5.0)` (shading.py:109-111).

`ShadingReference.save` writes arrays only (shading.py:80-91).

A loaded reference's origin is `{"action": "loaded", "path": str(path), "file_modified_utc": ..., "loaded_utc": ...}` (direct.py:724-729). Each entry's `provenance()` (library.py:385) describes the process that filed it, not the one that reduced its shading.npz.
```

**Failure scenario:** A bug in `calculate_shading`'s split is fixed at commit X. The operator has used "Use the cached one" for three weeks. Every entry filed in that period records provenance X or later and `shading_origin.action = loaded`, yet its shading.npz came from the pre-X reduction. `verify` and `reconstruct` cannot flag them, and re-reducing is impossible without the (unlinked) data.bin.

**Fix:** Write `provenance()` and the reduction parameters (`split_ratio`, the width source) into calibration.json. Store the same identity inside shading.npz, for example as a JSON string array, and have `load_shading` copy it, together with the archive folder when it can be found, into `_shading_origin`.

<details><summary>Second reader's check</summary>

The calibration.json record (direct.py:788-801) has no provenance or driver commit and no split_ratio. `ShadingReference.save` writes arrays only (shading.py:74-91). `calculate_shading(data, width)` is called with the default `split_ratio=5.0` (direct.py:2351, shading.py:109-111). A loaded reference's origin is `{action: loaded, path, file_modified_utc, loaded_utc}` (direct.py:724-729), and `provenance()` (library.py:385) is the filing process. So an entry whose reference came from the cache records no identity for the code that reduced it. The loaded case also has no `archive` link: only ensure_shading's measuring branch sets it (direct.py:858-860). The severity is low because re-reducing needs data.bin anyway, and nothing in code reads data.bin today.

</details>

<a id="re-derivation-matrix-per-pass-kind-rdm-a2"></a>

### RDM-A2 -- Hold and aim verification prescans are taken as film='negative' whatever the roll is, and that meta becomes the filed prescan's record

**Severity** low · **Category** data-integrity · **Verdict** found-by-verifier

**Where:** `rps7200/direct.py:3377-3378`, `rps7200/direct.py:3968-3974`, `rps7200/direct.py:4031-4036`, `rps7200/direct.py:4056-4063`, `rps7200/session.py:2545-2552`, `rps7200/demo.py:1096`, `rps7200/demo.py:1297`, `rps7200/demo.py:1326`

**Doc claim:** rps7200/direct.py:3970-3972 (comment claims prescans now record the roll's film)

The first prescan fix was not carried into `_hold_to_approved`. Any walk with approved positions or `correct` on a black-and-white roll (which frame_edges does read) or on a positive gets its replaced prescans filed with `scan.film = "negative"`. So do the frame's `prescan_meta`, and under debug every verification prescan. The record then misstates what was in the transport. `signature()` includes `scan.film`, and the demo's pools classify stored passes by it, so a B&W walk's prescans would be offered as colour-negative film.

**Evidence (from the code):**

```text
The frame's first prescan passes the roll's film, with the comment 'The roll's film, so the prescan's entry says what was in the transport; it was recorded as "negative" whatever it was.' (direct.py:3968-3974). The hold loop's verification pass does not: `image, _ = self.prescan(resolution=prescan_resolution, keep_raw=keep_raw, shading=shading)` (direct.py:3377-3378). `prescan()` defaults to `film: str = FILM_NEGATIVE` (direct.py:2058), and scan() records `"film": film`. When the replacement is kept, `prescan_meta = dict(self.last_scan_meta or {})` (direct.py:4034-4035, 4060-4061). On a walk the window then files `dict(rf.prescan_meta ...)` as the prescan entry's meta (session.py:2545-2552). The demo selects stored passes by `(record.get("scan") or {}).get("film")` (demo.py:1096, 1297, 1326).
```

**Failure scenario:** A B&W strip is walked in the window with 'correct' on. Frames 3, 7 and 9 are aimed, so their filed prescan entries come from the hold loop's last pass and record `scan.film: negative`. A later `--demo` session on film 'negative' draws those B&W prescans into its colour-negative pool, and library queries by film miscount them.

**Fix:** Thread `film` through `_hold_to_approved` and `_aim_frame` into `self.prescan(..., film=film)`, and add a test that a replaced prescan's meta carries the roll's film.

<a id="re-derivation-matrix-per-pass-kind-rdm-a3"></a>

### RDM-A3 -- Passes kept only by debug filing (metering probes, hold/aim prescans) go to ./library or RPS7200_DEBUG_ROOT rather than the caller's library, carry no role, and are not linked to the frame they served

**Severity** low · **Category** data-integrity · **Verdict** found-by-verifier

**Where:** `rps7200/direct.py:1049`, `rps7200/direct.py:1064-1074`, `rps7200/library.py:53`, `tools/scan_roll.py:475`, `rps7200/session.py:2872-2875`

**Doc claim:** CLAUDE.md 'Claude: always scan with debug filing on' section (claims debug filing adds 'the passes they do not keep -- metering probes, hold and aim prescans' to the library)

CLAUDE.md makes debug filing the mechanism that keeps 'the passes they do not keep -- metering probes, hold and aim prescans'. Those are exactly the passes whose evidence RDM-01 and RDM-A1 find missing from the frame entries. When they are filed, it is into a different library whenever the operator passed `--library` or chose another root, or when the process runs from a different directory. They arrive with no roll membership, no frame label and no role, so the only way to tie a probe to the frame it metered, or a verification prescan to the hold history entry it produced, is to compare timestamps.

**Evidence (from the code):**

```text
`root = os.environ.get(self.DEBUG_ROOT_ENV) or library.DEFAULT_ROOT` (direct.py:1049), and `DEFAULT_ROOT = Path("library")` (library.py:53) is relative to the CWD. Filing uses `film=FilmNotes(notes="captured with RPS7200_DEBUG on"), tags=["debug"]` (direct.py:1067-1068). Nothing anywhere sets RPS7200_DEBUG_ROOT (grep). tools/scan_roll.py opens `DirectScanner(verbose=args.verbose, debug=None)` (475) and files its frames to `args.library`. The session files to `self.root`. The probe's own meta has no field saying it was a metering round or a hold verification: scan() is called with ordinary arguments (direct.py:2491-2503, 3377).
```

**Failure scenario:** `RPS7200_DEBUG=1 uv run python tools/scan_roll.py --library /data/scans/library ...` from the repo root. The frames land in /data/scans/library. The 60 metering probes and 25 hold prescans land in ./library, tagged only 'debug'. Asked later which probe metered frame 14, nobody can answer from either library.

**Fix:** Give DirectScanner a `debug_root` that the tools and the session set to their library root. Tag each spooled pass with its role (`metering_round`, `hold_verify`, `aim_verify`, frame index, roll name) at the call site. Record the probe entries' `started_utc` in the frame's metering block and hold history so the link is explicit.

<a id="re-derivation-matrix-per-pass-kind-rdm-a5"></a>

### RDM-A5 -- A deliberately uncorrected pass (shading=False) is filed with the session's shading.npz but no shading_origin

**Severity** low · **Category** data-integrity · **Verdict** found-by-verifier

**Where:** `rps7200/direct.py:3207-3209`, `rps7200/direct.py:895-903`, `rps7200/library.py:262-263`

A window Scan with correction off (`job.shading=False`) in a session that has a reference produces an entry holding shading.npz with `calibration.skipped='explicit'` and `extra.shading_origin: null`. The stored reference, which is what `library.corrected()` would use on export, then has no record of whether it was measured that session or loaded from a cache of another day. For every other pass that distinction is recorded.

**Evidence (from the code):**

```text
`"shading_origin": (dict(origin) if shading and (origin := self._shading_origin) else None)` (direct.py:3207-3209). `capture_record()` hands over `"reference": self._shading` whether or not this pass used it (direct.py:~900). library.save writes `reference.save(path / "shading.npz")` whenever a reference is passed (library.py:262-263).
```

**Failure scenario:** An operator scans a frame raw on purpose for comparison, after 'Use the cached one'. The entry's shading.npz is a weeks-old cached reference, and nothing in scan.json says so. A later analysis that corrects it treats the reference as same-session.

**Fix:** Record `shading_origin` whenever a reference is stored with the entry, not only when the pass applied it.

<a id="re-derivation-matrix-per-pass-kind-rdm-04"></a>

### RDM-04 -- Serialisation check: no traced meta field reaches json.dumps(default=str) as numpy, bytes or Path; the risk is latent

**Severity** info · **Category** data-integrity · **Verdict** confirmed

**Where:** `rps7200/library.py:397`, `rps7200/direct.py:802-803`, `rps7200/direct.py:973-976`, `rps7200/session.py:907`, `rps7200/direct.py:3155-3215`, `rps7200/direct.py:1886-1896`, `rps7200/framing.py:263-273`, `rps7200/framing.py:1100-1102`, `tools/frame_edges/vote.py:65-80`, `tools/frame_edges/stepline.py:407-413`

The critic's concern was that `default=str` in library.save, calibration.json, the debug sidecar and roll manifests silently turns np.int64, np.float32, bytes or Path values into strings. Following every producer of meta for each pass kind (scan, prescan, metering, bracket, roll marks, hold/aim records, the frame_edges reader, the demo's `_take`/`_settings_meta`, session additions, the Inquiry dataclass) found no such value in the current code. The only lossy round trip is tuple to list (`frame`, the Inquiry `frame`), and every reader tolerates it (`signature` re-tuples). JSON float repr round-trips exactly. The protection is convention rather than enforcement: one uncast `np.argmax` added to a registration or hold detail would be stringified with no error.

**Evidence (from the code):**

```text
Values that could arrive as numpy types are cast before they enter meta:
- scan meta: `"width": int(params.width), "height": int(image.shape[0]), ... "filter_offsets": [int(params.filter_offset1), int(params.filter_offset2)]` (direct.py:3165-3172).
- `Settings.scaled`: `exposure=[int(max(100, min(65535, round(e * f)))) ...]` (protocol.py:619-622).
- raw_layout: all `int(...)` (direct.py:1886-1896).
- `registration()`: `"x0": int(x0), ... "offset": int(round(offset))` (framing.py:263-273).
- `measure_shift_mm`: `confidence=round(float(confidence), 2), dy=int(dy), dx=int(dx), ... row_reversed=bool(reversed_wins)` (framing.py:1100-1102).
- The edge vote: `Side(EDGE, x=x, lo=float(...), hi=float(...))` (vote.py:65-80); members cast with `float(...)` (stepline.py:407-413, 425; chroma.py:541).
- Paths are cast too: `"path": str(path)` (direct.py:725), `"archive"` via `str(archive)` (860), `"folder": str(folder)` (session.py:786).

Bracket `exposure_scale` elements are numpy float64 (`s * k` with k from `np.geomspace`), a float subclass that json writes as a number.
```

**Failure scenario:** There is none today. A future detector member that returns `outer=np.argmax(...)` without `float()` would reach `registration.correction.ensemble.edges.*.outer`. That value would be written as the string "123" in scan.json and roll.json, and later numeric readers (tools/registration_margin.py) would silently skip it or fail.

**Fix:** Replace `default=str` with a converter that maps `np.generic` via `.item()`, converts ndarray via `.tolist()`, and raises TypeError on anything else (bytes, Path, objects). `tools/frame_edges/sides.py:_jsonable` is already that function. Add a test that round-trips a real `scan()` meta.

<details><summary>Second reader's check</summary>

I spot-checked the producers.
- scan meta casts width, height, bytes_per_line and filter_offsets to int (direct.py:3165-3172).
- `Settings.scaled` produces ints (protocol.py:619-622).
- `apply_shading`'s report is Python int (shading.py:250-279).
- `carriage_record` stores hex strings and bools (direct.py:1249-1250).
- `_CommandLog` stores cdb, out and in as hex (direct.py:480-494).
- `film_base_from` casts (framing.py:554).
- `vote()` casts every Side field to float (vote.py:65-80).
- `lone_gap` passes member values through, but those members also produce Python floats or float subclasses: changepoint `float(...)` (changepoint.py:819-854), chroma `float(a)` / `round(float(...))` (chroma.py:675-690), stepline `_plateau_start` float and `_fit` floats (stepline.py:398-425).
- Bracket `exposure_scale` elements are np.float64, a float subclass, which json encodes natively.

No np.int64, np.float32, bytes or Path reaches `default=str` on the traced paths. The protection is convention only.

</details>

<a id="re-derivation-matrix-per-pass-kind-rdm-a4"></a>

### RDM-A4 -- Nothing proves re-derivability beyond the decode: no check that data.bin re-reduces to the stored shading.npz, and reconstruct never exercises correction, bracket merge, reversal or hold

**Severity** info · **Category** test-gap · **Verdict** found-by-verifier

**Where:** `rps7200/library.py:675-766`, `tests/test_scanner_api.py:271-314`, `rps7200/direct.py:781-803`

**Doc claim:** rps7200/direct.py:850-853 ('a reduction cannot be redone with better code once its input is gone. Without this no correction in the library could ever be recomputed from scratch'): the input is kept, but recomputation is never exercised

Checked against the pass-kind matrix, only one column is proven re-derivable end to end: raw bytes to scan.tif, including the 7200 dpi stagger and bottom-up turn, for every kind that has raw bytes. The shading reduction, the correction (`corrected()` is recomputed but never compared with anything), the bracket merge (deterministic from entries, but untested and with no bracket group id), the reversal decision and the hold confidences are recorded but never re-derived by any check. A regression in `calculate_shading`, or in the archive's line stride, would go unseen until someone tries to recompute.

**Evidence (from the code):**

```text
`reconstruct` decodes the raw bytes and compares them with scan.tif (`if np.array_equal(image, stored): return image, "identical to the stored image"`, library.py:~755). It applies the stored reference only in the legacy `if "shading" in applied:` branch. The only reads of data.bin and calibration.json anywhere are tests/test_scanner_api.py:271-314, with synthetic bytes (`b"\x01\x02" * 100`) that check the archive was written. No code or test runs `calculate_shading(data.bin, pixels_per_line)` and compares the result with the archived or entry shading.npz. There is no test that re-merges a bracket from its entries, and none that recomputes a hold confidence from stored prescans.
```

**Failure scenario:** A change to how calibration lines are collected makes data.bin drop the final partial block. Entries keep verifying, and reconstruct reports 'identical' on every entry. Only a person re-reducing data.bin months later finds the references cannot be reproduced.

**Fix:** Add `tools/library.py reconstruct --calibration`, which walks calibration/<UTC>/ folders, re-reduces data.bin with `calculate_shading(data, pixels_per_line)` and compares it with shading.npz. Add a test on a stored real calibration if one is committed as a fixture, and a bracket re-merge test from filed entries.

## What this area persists

| What | Path | Format | Raw or corrected | Written by | Read by | Exact? |
|---|---|---|---|---|---|---|
| Entry record: image, raw (file, bytes, sha256, layout), scan (SCAN_FIELDS), extra (mode, commands, shading_origin, started_utc, bracket_*, roll_index, roll_position, roll_membership, demo, demo_source), device, device_settings, metering, registration, calibration, prescan (read_direction, carriage_state), film, tags, provenance, files | library/<UTC>_<stock>[_f<frame>]_<dpi>dpi[_ir][-N]/scan.json | JSON via json.dumps(indent=2, default=str), written by _write_atomic (temp, fsync, os.replace) | describes raw pixels; calibration.report is the correction computed for the pass, not applied; corrections_applied names anything baked in | library.save (library.py:216-400); compact (414-453); add_tags; migrate_direction; tools/library.py migrate-raw | library.load/corrected/reconstruct/decode_raw/verify/entries/signature/prunable, tools/registration_margin.py, demo (_stored, best_pair), GUI export | exact for every value traced (all cast to Python types); tuples become lists; hold/registration mm values rounded to 2-4 places by their producers |
| Raw INDEX bytes of the pass, exactly as read (2-byte tag per line, in read order) | library/<id>/raw.bin.gz (gzip level 6) or raw.bin (plain, window single passes before compact) | gzip or plain bytes; sha256 of the uncompressed stream recorded in raw.sha256 | raw | library.save (in place, no fsync); compact gzips raw.bin after close | library.read_raw -> reconstruct, decode_raw, verify, migrate_direction | byte-exact; decoding needs raw.layout (width, lines, bytes_per_line, channels, lines_received) |
| Decoded pass: upright, with the 7200 dpi stagger realignment, no flat-fielding | library/<id>/scan.tif | TIFF uint16 (or uint8) H x W x C; deflate when compressed | raw (except legacy or labelled entries with corrections_applied=['shading']) | library.save, compact, migrate_direction, migrate-raw | library.load/corrected/reconstruct/verify, GUI, make_comparison | bit-exact; re-derivable from raw bytes via decode_index plus _replay |
| Session shading reference in force at filing time (light/dark per channel, means, pixels_per_line) | library/<id>/shading.npz; calibration/shading.npz (cache); calibration/<UTC>/shading.npz (archive) | np.savez_compressed float64 arrays; no metadata on who reduced it or how | a reduction of calibration data.bin | library.save via reference.save; DirectScanner.save_shading (atomic); archive_calibration (in place) | library.load/corrected; DirectScanner.load_shading | exact floats; the code version that reduced it is not recorded (RDM-03) |
| CCD mask of this pass (COPY reply, sized to the reference width or to 5172) | library/<id>/ccd_mask.bin; calibration/<UTC>/ccd_mask.bin | raw bytes | raw | library.save; archive_calibration | library.load/corrected/reconstruct (legacy branch) | byte-exact; also duplicated as hex in extra.commands |
| Framing prescan stored beside a roll frame | library/<id>/prescan.tif | TIFF uint8 | CORRECTED and unlabelled on real-roll frames; only read_direction and carriage_state of its meta are kept | library.save (prescan=rf.prescan) | migrate_direction, the demo's pools, GUI | not re-derivable: no raw bytes, mask or commands (reported elsewhere) |
| Calibration archive: every calibration line as read, plus a record | <dir of --reference or calibration>/<UTC>[-N]/data.bin + calibration.json + ccd_mask.bin + shading.npz | plain bytes; JSON default=str (measured_utc, resolution, pixels_per_line, bytes_per_line, index_header, bytes, sha256, duration_s, commands, reference, ccd_mask, protocol_revision) | raw calibration bytes | DirectScanner.archive_calibration, called only from ensure_shading when it measures | nothing in code; linked from entries only by extra.shading_origin.archive (CWD-relative string) | byte-exact data.bin; no provenance or reduction parameters recorded |
| Debug spool, one set per pass captured with RPS7200_DEBUG on | <system tmp>/rps7200-debug-*/NNN-image.npy, NNN-raw.bin, NNN-meta.json, NNN-shading.npz, NNN-ccd_mask.bin | npy raw pixels; plain bytes; JSON default=str (meta, raw_layout, captured) | raw | DirectScanner._debug_capture (direct.py:908-998) | DirectScanner._debug_flush -> library.save after close(); nothing else | exact; the meta is copied before callers add bracket, roll or registration fields |
| Roll and walk manifests and decisions | rolls/<name>/roll.json, survey.json, approved.json (+ .bak) | JSON via write_manifest (default=str, temp, fsync, replace) | metadata; registration marks, hold and aim records, per-frame exposure/gain/offset; approved offset_mm snapped and rounded to 4 places, reference_entry, source | ScanSession._roll via RollManifest; tools/scan_roll.py; GUI _write_approved | GUI read_survey/read_approved/export, scan_roll --approved, resume | exact JSON, but the only home of walk evidence and of the approved.json-to-reference link |
| Roll-folder pictures | rolls/<name>/prescanNN.tif, prescanNN-before.tif, frameNN.tif | TIFF (deflate) | corrected; prescans arranged with the session orientation | FrameWriter._write (window), tools/scan_roll.py tiff.write (dry run) | GUI sheet (hold references on reopen), registration studies | prescanNN-before.tif has no library entry and is written with the final prescan's meta |
| Delivered file and its sidecar from tools/scan.py | --out (e.g. scan.tif or .jpg) and <out>.json | TIFF or JPEG; JSON default=str (for a bracket, the last pass's meta plus a `bracket` summary) | corrected (a merged bracket, mono when chosen) | tools/scan.py:402-416 | operator and NegPy | the merge is not re-derivable from it: fitted parameters exist only as prose in stats.describe() |

**Second reader's corrections to this table:**

Corrections to the claimed table:

1. **Entry record (scan.json).**
   - On every metered roll frame (default METER_EACH) and every auto-exposed bracket pass, `metering` is null and `scan.exposure_metered` is false, although metering ran (RDM-A1). "metering: absent on a scan given its exposure" is therefore not what distinguishes these entries.
   - `extra.shading_origin` is null on a shading=False pass even when shading.npz is stored (RDM-A5).
   - `extra.reversal` exists only for window-roll or window-scan passes of unknown direction. CLI roll frames never get one (RDM-02).
   - `extra.rotation` and `extra.flipped` come from session `_file`.

2. **Framing prescan (prescan.tif).** Correct as claimed. In addition, on walk entries whose prescan was replaced by the hold or aim loop, the entry's `scan.film` is "negative" whatever the roll is (RDM-A2).

3. **Debug spool.** It is filed to `$RPS7200_DEBUG_ROOT` or the CWD-relative `./library` (direct.py:1049), not the caller's `--library` or session root. Entries carry `film.notes='captured with RPS7200_DEBUG on'` and `tags=['debug']`, with no roll membership, frame label or role (RDM-A3).

4. **Calibration archive.**
   - It is written only on ensure_shading's measuring branch.
   - `extra.shading_origin.archive` links to it only for action 'calibrated' and only on passes with shading=True.
   - It is read by nothing but tests with synthetic bytes, so the reduction is never re-checked (RDM-A4).

5. **ccd_mask.** "Duplicated as hex in extra.commands" is correct: the COPY reply is not a SCSI_READ, so `_CommandLog` keeps it as `in` (direct.py:490-494).

6. **tools/scan.py delivered sidecar.**
   - The merge is not re-derivable from the sidecar.
   - It is re-derivable in principle from the bracket's entries: raw pixels, reference, `bracket_index`, `bracket_ratio`, `bracket_passes` and `bracket_stops` are all in extra, and `merge_bracket` is deterministic.
   - There is no bracket group id, so grouping relies on the film label and started_utc.
   - The metered base scale is recoverable as exposure_scale / bracket_ratio. The metering evidence is not (RDM-A1).

**Per-pass-kind matrix, as checked in code.**

- **Window single scan:** raw, layout, commands, exposure/gain/offset, fast_ir, frame, read_direction, stagger, mask, reference and metering all recorded.
- **Window prescan:** full entry via `_file` with its own meta.
- **Bracket pass:** full entry plus the bracket_* fields; `metering` is lost.
- **Roll frame:** full entry, with registration marks, hold/aim record and roll_membership. `metering` is lost. The prescan is stored corrected, keeping only read_direction and carriage_state.
- **Roll prescan / prescan_before:**
  - On a real roll there is no entry of its own.
  - On a walk the prescan gets its own entry with raw bytes.
  - prescan_before is a corrected TIFF only, with no entry.
- **Metering probes and hold/aim verification prescans:** filed only under debug, unlinked and without a role.
- **Dry-run walk prescan:** entry with raw bytes; its film is wrong if the prescan was replaced.
- **Calibration:** data.bin plus calibration.json; no provenance, no split_ratio, never re-reduced.
- **IR tied/untied:** fast_infrared recorded; the IR timer is in exposure[3] and in the WRITE GAIN OFFSET bytes in commands.
- **7200 dpi:** `stagger_realigned` recorded and replayed by reconstruct.
- **Bottom-up:** read_direction recorded and checked by reconstruct.
- **Demo:** same filing path, flagged by extra.demo.

**Moves.** SLIDE moves between passes (nudges) fall outside every pass's command window, because recording starts in scan() after metering. The hold record keeps only `moves` and `spent_mm`; nudge's returned param/forward is discarded apart from `clamped` (direct.py:3368-3374).

**Re-derivation.** Only raw → scan.tif is proven re-derivable by reconstruct.

## What the operator can do

- Run `uv run python tools/library.py reconstruct` to re-decode every stored pass (turned upright, with the 7200 dpi stagger replayed) with current code, and `tools/library.py verify` to check each file's checksum.
- Recompute any entry's corrected picture with today's correction code through `library.corrected(entry)`, from its raw scan.tif, shading.npz and ccd_mask.bin.
- Set RPS7200_DEBUG=1 before a window session, `tools/scan.py` or `tools/scan_roll.py`, so that metering probes and hold/aim verification prescans are filed with their raw bytes and command logs.
- Re-reduce a calibration by hand: `calculate_shading(open('calibration/<UTC>/data.bin','rb').read(), pixels_per_line)` with pixels_per_line from calibration.json (no tool does it).
- Read what the scanner was sent for any scan() pass in scan.json `extra.commands`: MODE SELECT, frame, gain read-back and write, SLIDE INIT, mask, and PARAM replies.

## What the operator should not do

- Do not treat the prescan.tif inside a roll-frame entry as raw data; it is the corrected 8-bit picture with no bytes behind it.
- Do not delete, move or rename the calibration/ folder or run the tools from a different working directory: `extra.shading_origin.archive` is a relative string, and verify never checks it.
- Do not delete a walked roll folder: survey.json, approved.json (including reference_entry) and prescanNN-before.tif are the only record of walk and aim decisions.
- Do not use 'Use the cached one' or `--reuse` when the reference's origin matters: such entries carry no archive link and no record of the reducing code.
- Do not scan rolls or brackets for metering evidence: their entries carry no metering record.

## Mistakes nothing guards against

- Metering each frame of a roll, or metering a bracket, files every frame with `exposure_metered: false` and `metering: null`, and nothing warns that the probe evidence was dropped.
- Scanning a roll with tools/scan_roll.py instead of the window silently skips the reversal check for frames whose read direction is unknown (RDM-02).
- A 'check this frame' warning (a scan that disagrees with its own prescan) is only logged. Closing the window loses it, and no entry records it (RDM-01).
- Scanning at 7200 dpi requires `--no-shading`, which also skips calibration. The entry is filed with no shading reference at all and a CCD mask read at the 5172-byte default, and verify reports nothing because the skip is explicit.
- Stopping a bracket with Ctrl-C between passes files the passes taken so far. Nothing marks the bracket incomplete, and no bracket id groups them (the missing id is reported elsewhere).
- Nudging the film in the window before a Scan leaves no trace in the scan entry. Only READ STATE byte 2, inside `carriage_state.read_state`, shows which whole frame the film was on.

## Dataflow notes

Legend: Y = held in the entry and re-derivable; N = lost or not held; P = partial. Filing chain for every row that goes through scan():
- DirectScanner.scan (direct.py:2842-3235) → _read_pass (3237-3294) → read_planes (1799-1913, which sets last_raw and last_raw_layout) → decode_index (1922-1971) → stagger realignment (3089-3097) → apply_shading (3128-3144).
- meta is built at 3155-3215, with the command log from _CommandLog.start/stop at 2990-2992 and 3074-3076.
- capture_record (889-904) is read by the caller immediately; library.save (library.py:216-400) writes scan.tif, prescan.tif, shading.npz, ccd_mask.bin, raw.bin(.gz) and then scan.json atomically.

MATRIX. Columns in order:
1. bytes
2. layout
3. MODE SELECT/byte14/filter_offsets
4. exposure incl. IR timer
5. gain/offset written and read back
6. fast_ir
7. frame
8. SLIDE moves before the pass
9. reference and link to data.bin
10. mask
11. read_direction/stagger
12. correction state
13. merge params
14. approved/offset decision
15. test proving re-derivation

Rows:
(1) **Window scan** (session.py:2100-2132 → _file 2781-2906 → FrameWriter 1618-1668, compress=False, compacted at close)
1-7 Y. 8 N (nudges; position only via carriage_state.read_state byte 2). 9 Y reference; link only if calibrated this session, CWD-relative. 10 Y. 11 Y. 12 Y. 13 n/a. 14 reversal outcome only (RDM-01). Metering Y, except the region (RDM-01). 15 demo only (test_demo.py:1260-1276); the real path has none.

(2) **Window prescan** (session.py:2061-2098): same as (1); no metering. 15 demo only.

(3) **Bracket pass** (tools/scan.py:280-375, scan_bracket direct.py:2736-2840, on_pass capture after each pass): 1-12 Y per pass; bracket_index, ratio, passes and stops in extra. Metering N (auto_exposure runs outside scan(), exposure_metered False). 13 N (known). Bracket id N (known). 15 N (fakes).

(4) **Roll frame** (direct.py:4094-4141 → session.py:2596-2637 or tools/scan_roll.py:731-760)
- 1-7 Y; roll_index, roll_position and roll_membership in extra; registration = marks at top level.
- Metering N (direct.py:4108-4133 calls scan() with auto_exposure False).
- 8 P: hold/aim keeps moves, spent_mm, clamped and history, but no param bytes and no per-move sign.
- 9 as (1). 10 Y. 11 Y.
- Reversal is judged only by the window (RDM-02).
- 14 P: target_mm and source are kept; the reference entry and the detector proposal are not.
- 15 N on the real path.

(5) **Roll frame's prescan and prescan_before**
- prescan.tif is the corrected rf.prescan. From prescan_meta only read_direction and carriage_state are kept (library.py:376-382). Raw pixels (rf.raw_prescan), bytes, mask, commands and exposure: N.
- prescan_before: N on a real roll. On a walk it is only a roll-folder TIFF (file_entry=False), carrying the final prescan's meta.

(6) **Metering probe rounds** (direct.py:2489-2565): each is a scan(keep_raw=True). Filed only by the debug flush (1031-1125), with 1-12 Y, but film='negative' (no film passed at 2492), no purpose or round marker, and no link to the scan it served. The region is not recorded (RDM-01). 15 N.

(7) **Hold/aim verification prescans** (direct.py:3377-3378, via prescan()): debug-only, same as (6).

(8) **Dry-run walk prescan** (session.py:2536-2566; tools/scan_roll.py:664-694): filed with the final pass's capture. 1-12 Y. 14 N: registration and aim decisions go only to survey.json. 15 demo only (test_demo.py:1360-1380).

(9) **Calibration** (calibrate_shading 2128-2381 → ensure_shading 806-887 → archive_calibration 749-804)
- data.bin, commands (frame, MODE SELECT, SLIDE INIT 10 01, per-poll gain read and write, mask) and the mask: Y.
- Archived only on the ensure_shading measure path.
- No provenance or split_ratio (RDM-03).
- Entries link to it only through shading_origin.archive; loaded references have no link.
- 15 N.

(10) **IR pass, tied or untied**: fast_infrared is in scan fields and in the quality bits inside the MODE SELECT payload. exposure[3] is the IR timer: a 3-value scale leaves it at the device value, a scalar scale scales it. The IR plane is reported `uncorrected: 1`.

(11) **7200 dpi pass**: reachable only with shading=False (the tools refuse otherwise), which skips calibration, so the entry has no reference. The mask is read at CCD_MASK_SIZE. stagger_realigned=4 is replayed by reconstruct. 15 hand-built only (test_library.py:690-735).

(12) **Bottom-up pass**: bytes are kept in read order and read_direction is recorded; reconstruct compares the recorded direction with the one read today. 15 hand-built only.

(13) **Demo pass** (demo.py:846-909): bytes are synthesised by encode_index and reconstruct to the pixels; demo_source is recorded. There is no commands, mode, filter_offsets, protocol_revision or shading_origin, and the fit/shift is not recorded (known). 15 Y via the session.

Serialisation: every meta value is cast at its producer (RDM-04). default=str never fires on the traced paths; tuples become lists. Rounded values are only derived ones (the metering record's scales/levels/targets, hold mm, registration mm); the written exposure ints and the requested exposure_scale are exact.

reconstruct (library.py:687-774) proves only the decode, the upright turn and the stagger. It never proves the correction, a prescan.tif, a metering decision, a hold/aim or reversal decision, a bracket merge or a calibration reduction.
