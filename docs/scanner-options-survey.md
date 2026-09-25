# Scanner options: a second survey

## Status: the scanning side is exhausted. **One run is still wanted: a 7200 dpi shading reference from an ordinary pass over `CALIBRATION_FRAME`, about 6 minutes, when the scanner is back.**

Everything else SilverFast and VueScan offer on the *scanning* side is either
built here, measured and refused with a number, or absent from this hardware
altogether. This document exists so nobody explores it a third time.

The yardstick below is the reference backend's own option list, because it is
the one enumeration of this scanner family's controls written by somebody who
had the firmware notes. Four of its options are not covered here, and each one
has a reason rather than a gap: `light` is measured dead upstream, 8-bit depth
is deliberate, `sharpen` is out of scope by Stefan's framing, and the scan
window is wired end to end with nothing setting it -- because, as the third
section shows, **a marquee saves no routine time on this device**. That last
one is the correction to an assumption worth writing down.

## The yardstick: every option the reference backend exposes

`pieusb/option.py` -- the Python reference driver for this scanner family, in
the uv cache -- exposes 21 named options plus seven exposure fields. Every one
of them, and what this driver does about it:

| option | what it is | here | status |
|---|---|---|---|
| `mode` | `gray` / `rgb` / `rgbi` | RGB and RGBI, `scan(infrared=)`. B&W is delivered host-side by `rps7200/mono.py` | **covered**; `gray` refused, see below |
| `color_depth` | 8 or 16 bit | 16-bit for every delivered scan; `DEPTH_8` only for prescans and the calibration pass | **not covered, deliberately** |
| `resolution` | dpi | `--dpi`; 3600 for quality, 1800 when time matters (`docs/dpi-tradeoff-plan.md`) | **covered** |
| `sharpen` | MODE SELECT quality bit `0x02` | a `set_mode` parameter with no caller; `scan()` does not expose it | **not covered**; out of scope |
| `reuse_calibration` | reuse a shading reference | `--reuse`; one reference per session, as the vendor does at power-on | **covered** |
| `fast_infrared` | quality bit `0x80` | **adopted as the default 2026-09-16**, `PROTOCOL_REVISION` 3 | **covered** |
| `auto_exp` | meter a preview pass | on by default in the window's Scan and in every roll (`--meter each`); `scan()` and `tools/scan.py` meter only when asked (`--auto-exposure`). RGB only, two rounds, `EXPOSURE_TARGET` 0.80 | **covered, and further** |
| `advance` | advance the slide | `SLIDE` (0xD1) whole-frame, plus calibrated sub-frame moves; the whole roll path | **covered, and further** |
| `tl_x` `tl_y` `br_x` `br_y` | the scan window | `set_scan_frame`, sub-command `SUB_SCAN_FRAME` 0x12, `scan(frame=...)`, `Scan.frame` | **wired, nothing sets it** |
| `gain_r/g/b/i` | per-channel gain | read from the device and written back unchanged | **covered**; measured worthless |
| `offset_r/g/b/i` | per-channel offset | read from the device and written back unchanged | **covered**; never chosen here |
| `light` | lamp level, byte 75 | read from the device and written back; `Settings.light` defaults to 4 | **not covered**; measured dead |
| *(exposure)* `exp_time_r/g/b/i`, `exp_rel_r/g/b` | absolute timer counts, and a relative percentage | this driver pins `exp_rel_*` at 100 and moves exposure through the absolute fields | **covered**, and see the note below |

Two of those rows are worth a sentence each before the sections that follow.

**`gray` is refused at both ends, for the same reason.** `passes = 0x04` is the
green filter alone and returns `colorFormat = 0x01` (PIXEL), whose lines carry
no `RR`/`GG`/`BB` tag, so neither backend's deinterleave can read it --
`pieusb` lists it in `UNSUPPORTED_MODES`, and `docs/protocol.md` records that
**it costs the same time and the same bandwidth as a colour pass and returns a
third as much**. CyberView agrees by practice: `captures/bw.pcapng` is a whole
black and white session, eight passes, every one `0x80` RGB.

**The two backends drive exposure through opposite fields, and it does not
matter.** pieusb measured `exp_time_*` inert on a ProScan 10T -- 500 through
10000 all within 0.2% -- and uses `exp_rel_*`; this driver pins `exp_rel_*` at
100 (`set_exposure_time()`'s default) and moves the absolute fields, where
exposure demonstrably works and wraps past 65535. It is tempting to read the
unused field as spare headroom. It is not: pieusb's own documented model is
`timer = exposure_time * exp_rel / 100`, so both routes land in the same 16-bit
Timer 1 and hit the same rail. Nothing has re-measured `exp_rel_*` on *this*
device, and there is no reason to spend a pass on it -- the ceiling is the
register, not the route to it.

## `light` is already measured dead, and the measurement is upstream

This is the cheapest of the four gaps to close, because somebody else closed
it. `pieusb/option.py` quotes the firmware note and then records its own
measurement:

> "Current light level. The stability of the light source is tested during
> warming up. The check starts with a light value 7 or 6, and decrements it
> when the light warms up. At a light value of 4, the scanner produces stable
> scans (i.e. successive 'white' scan values don't differ more than 0x200)."
> (`pieusb_scancmd.h:208-213`)
>
> So the operating band is 4..7, with 4 the warmed-up value [...]
>
> **No measurable effect on a ProScan 10T: 4, 5, 6 and 7 produce the same
> image to within 0.1%.** Sent anyway, since 0 is outside the band and no other
> model has been tested. Use `exp_rel_*` to change exposure.

So the field is a warm-up state readout that the firmware drives itself, not an
exposure control the host gets to use. This driver reads it from byte 75 of
READ GAIN OFFSET and writes back whatever it read; `Settings.light` in
`rps7200/protocol.py` defaults to 4, and the three-stock comparison in
`docs/protocol.md` found CyberView sending 6 throughout, unchanged by film.

**Do not spend scanner time on it.** The measurement is on a sibling model
rather than this one, which is the honest caveat -- but 0.1% across the entire
documented band, on a field whose own documentation describes it as a warm-up
indicator, is not a lead. If anyone ever wants it anyway, it is a five-pass
ladder 4-5-6-7 at 300 dpi RGB, two minutes, and the prediction is nothing.

## The scan window saves no routine time, and that is the interesting part

Both products put a marquee in front of the operator and both are sold partly
on speed, so the natural assumption is that a tighter window is a faster scan.
**On this transport it is not**, and this is the one finding in this document
that overturns something rather than confirming it.

The capability is not the problem. It is built and reachable today:
`DirectScanner.scan(frame=...)`, `set_scan_frame`, sub-command
`SUB_SCAN_FRAME` 0x12, and `Scan.frame` in `rps7200/session.py` is threaded
through to it. What is missing is any caller: nothing in `rps7200/`, `tools/`
or `tests/` ever sets it. (`tools/scan.py --frame` is a *metadata* label for
the frame's position on the roll, not a window. They are easy to confuse.)

**There is almost nothing to crop.** `FULL_FRAME = (0, 0, 10343, 6887)` in
units of 1/7200 inch is 10344 x 6888 units:

```
    transport window   36.49 x 24.30 mm
    a 35 mm frame      36.00 x 24.00 mm
    slack              0.49 x 0.30 mm       1.4% of x, 1.2% of y
```

The vendor's own detected picture windows are the same story from the other
direction: (96,71) to (10175,6815) on a strip it had registered correctly, so
10080 of 10344 columns and 6745 of 6888 lines -- **97.4% of the width and 97.9%
of the lines**. The aperture is barely larger than the picture, which is
`rps7200/framing.py`'s opening paragraph and the reason a drifted frame shows
up as a *narrower* picture rather than a displaced one. There is no margin to
discard.

**And scan time tracks the line count, so half of even that is unavailable.**
The measured law for a pass whose infrared plane follows the resolution is

```
    time = 7.46 s + 59.88 ms/line        r^2 = 1.000, docs/fast-infrared-plan.md
```

-- fitted across five resolutions to within 0.16 s, and checked against a pass
it was not fitted to, to 0.24 s. Lines, not pixels. Cropping the full 2.1% of
lines off an 1800 dpi infrared pass means 36 fewer lines of 1721, which is
**2.2 seconds off 110.5**. Narrowing x buys nothing at all: the line rate is
set by the per-line integration period, so fewer columns per line is the same
line. (That last part is a prediction, not a measurement -- every timing figure
in this repo was taken at full width. It is not worth a pass to confirm, given
the 2.2 s the measured half is worth.)

**So a marquee is for deliberately scanning a detail at high dpi, not for
speed.** That is a real use and the mechanism is there for it: ask for a
quarter of the frame at 3600 dpi and it costs a quarter of the lines, which is
how a small region gets the resolution the whole frame cannot afford. Two
things to know before building it:

- **The window is already load-bearing once.** `CALIBRATION_FRAME` is the only
  sub-rectangle this driver has ever scanned, and the run in the last section
  is a second.
- **A narrowed 7200 dpi window slips past the shading guard, and nobody has
  checked whether it should.** `_shading_columns_needed` is
  `(x1 - x0 + 1) * dpi / 7200`, so a 7200 dpi pass narrowed to 5172 units --
  18.25 mm, half the frame -- needs 5172 columns and satisfies
  `MAX_SHADING_COLUMNS` exactly. Whether the reference then lands on the right
  columns is a different question: the mapping goes through the per-pass CCD
  mask (`build_width_to_loc`, `apply_shading`), and no pass outside
  `CALIBRATION_FRAME` has ever exercised it. Treat a half-width 7200 dpi scan
  as unverified rather than supported.

## Already settled, and by what

Everything below is closed. The file named is where the numbers live.

| what the products call it | the answer here | where |
|---|---|---|
| Multi-exposure / HDR | **impossible on this hardware** | `docs/multi-exposure-plan.md` |
| Multi-sampling / averaging | **ceiling -1.8% to -3.5%**, grain-limited | `docs/multi-exposure-plan.md` |
| Infrared dust removal (iSRD / VueScan IR) | deliberately **not ours** | TODO.md, "Decided against" |
| Greyscale / monochrome scan mode | hardware mode **not worth using** | `docs/protocol.md` |
| Analog gain | a **digital multiplier**, worth 0.53% | `docs/analog-gain-plan.md` |
| Faster infrared pass | **adopted**, removes the floor | `docs/fast-infrared-plan.md` |
| Autofocus | **no such command exists** | CLAUDE.md, `SET_SCAN_HEAD` |

**Multi-exposure is built, measured and refused.** Two failures compound.
There is very little to win: in the darkest tenth of a slide the random
per-pass component is only 21% of high-frequency noise at 300 dpi and 27% at
1800, the rest being film grain identical in every pass, which caps *any*
multi-pass method at **-1.8% to -3.5%**. And the passes stop agreeing as the
bracket widens -- two repeats at one exposure give median |z| = 1.03, and a
nine-pass ladder gives 1.03 at x1.40, 1.48 at x2.33 and 5.60 at x3.66.
**Agreement holds to about x1.7, degrades to x2.7 and collapses beyond**, and
no linear, quadratic or cubic transfer function removes it. The measured
outcome at 3600 dpi: a 2-pass bracket reads **+68.2%** noise against a single
pass and a 9-pass **+75.4%**, while the narrow x1.4 bracket that the passes can
actually support reads +0.9% -- neutral, which acquits the merge and condemns
the feature. `rps7200/bracket.py` and `scan_bracket()` stay, tested and
re-runnable offline; do not ship them as a default.

**Multi-sampling is the same ceiling without the bracket.** The -3.5% above
*is* the averaging ceiling, and it was checked the honest way: -1.8% predicted
from averaging five passes at 300 dpi against -1.6% achieved, which is how we
know the model is right rather than the merge broken. Higher resolution does
not help -- more resolution resolves grain rather than adding noise.

**Infrared dust removal is a boundary, not a gap.** TODO.md's "Decided
against": *"NegPy does it, and does it well. The point of this driver is
handing the infrared plane over untouched."*

**Fast infrared is the one place a product feature was adopted.** Quality bit
`0x80` does not speed the infrared acquisition up, it **removes the ~220 s
floor**: a tied pass costs `7.46 s + 59.88 ms/line`, which is -88.8% at 300 dpi,
-49.8% at 1800 and nothing above about 3709 dpi where the line count has
already overtaken the floor. Quality is clean over eleven passes at 1800 dpi
(colour negative) and 3600 (slide) -- picture, infrared plane and speck depth
all unchanged -- and NegPy's dust removal worked on a tied scan, which is
better evidence than the ladder. Default since 2026-09-16,
`PROTOCOL_REVISION` 3.

**The gain register was the last plausible free lever, and it is not one.**
Blue gain 21 -> 39 at one exposure gives signal x1.4842 and random noise
x1.4763: a shortfall of **0.53%** where an analog gain on this sensor's numbers
would have given ~4%. Safe to write, pointless to. Blue's rail limit in plain
RGB cannot be lifted this way; scan RGBI, where blue is about 5x more sensitive
on colour negative and ~9.6x on black and white.

### Scanner focus does not exist on this device

Both products offer autofocus, manual focus or both. There is no counterpart
here, and this is the one row where the absence is a documented hazard rather
than a decision.

The only head-positioning command is **`SET_SCAN_HEAD` (0xD2)**, and CLAUDE.md
bans it outright. It drives a mechanism with a step count whose unit is unknown
and **it reports nothing**: no error, no sense condition, and not one byte of
`READ_STATE` changes, at any step count. 10 and 100 steps looked like a clean
no-op; **1000 turned the gears audibly and needed a power cycle**. The silence
is the trap -- there is no feedback that says stop, so the step count cannot be
calibrated by escalating it. The SANE `pieusb` backend defines the forward and
backward modes and never calls them, refuses mode 2 as "unreliable, possibly
dangerous", and CyberView sends the command **zero times in 3,955 commands
across all six captures**. Nothing has ever driven it successfully.

Do not confuse it with the film transport: `SLIDE` (0xD1), whole-frame
`SLIDE_NEXT`/`SLIDE_PREV` and the calibrated sub-frame moves are measured and
safe, and are unrelated to focus.

## The one thing left to run

**A 7200 dpi shading reference from an ordinary scan over
`CALIBRATION_FRAME`.** Specced in TODO.md under "Specced but not built" and in
`docs/7200dpi-plan.md`; it wants Stefan's agreement because it is a design
change, not a fix.

The problem it addresses, measured on the hardware 2026-09-13: the device
declares the same shading descriptor at 7200 dpi as at 3600 --
`pixels_per_line=10344` *bytes*, so **5172 columns** -- and calibrates 5172
columns however it is asked. `MAX_SHADING_COLUMNS` records that ceiling. A
7200 dpi pass is 10344 columns wide, so it cannot be corrected from the
scanner's own reference at all, and `scan()` refuses such a pass **before**
running it, so the refusal costs no scanner time. Mapping output column *j* to
calibration column *j // 2* was the obvious rescue, was measured offline
against both stored 7200 dpi entries, and does nothing -- the artefact is a
difference *between* the column parities, and that mapping gives both the same
correction.

The idea is to get the same measurement through a different command. An
ordinary **scan** at 7200 dpi over `CALIBRATION_FRAME = (0, 3431, 10343,
6888)` -- the lower part of the transport, where the light path is clear and
the film does not reach -- would return **10344 columns of clear-path
response**, which is what the calibration measures, taken through a command the
device is willing to run at full width.

It is *not* the flat-through-film idea `rps7200/shading.py` warns against. That
one fails because it is measured through film and at the wrong exposure, and
this would be neither.

Two open questions, both answerable in **one ~6 minute pass**:

1. whether the clear-path level at the scan's own exposure is usable as a
   reference at all, and
2. how it interacts with `_realign_native_column_stagger` -- the even/odd
   column offset of 4 scan lines, confirmed by cross-correlating two unrelated
   7200 dpi entries per channel, correlation peaking cleanly at lag 4.

**The counter-argument is already on record and does not go away if this
works.** `docs/dpi-tradeoff-plan.md` found 7200 dpi edges come out *softer*
than 3600 in matched 100% crops -- oversampling an optical blur -- for 314.4 s
against 165.4 s and 427.4 MB against 106.8. CyberView never uses it either:
7200 dpi appears in none of the nine captures, whose highest resolution is
3600. So this run would make 7200 dpi **correctable**, not worth using. Its
value is closing the last hole in the shading path and settling whether a
clear-path scan can serve as a reference -- which is a question about the
correction machinery, useful beyond 7200 dpi.

It also needs the film loaded, like every other calibration here: the
calibration frame is the lower transport, which the film does not cover, so the
sensor is measured with the strip still in. Calibrating an empty transport is a
state the vendor never creates and doing it once preceded a wedge.

## What this deliberately does not cover

Colour correction, inversion, dust removal and sharpening. They are out of
scope by Stefan's framing of the question, and they are **NegPy's job** by this
project's design: this driver hands over linear raw pixels, the per-session
shading reference, the per-pass CCD mask and the infrared plane untouched, and
a consumer that has all four can do better than a scanner driver guessing.

`sharpen` appears in the table above for completeness because the reference
backend exposes it -- MODE SELECT quality bit `0x02`, reachable through
`set_mode` and called by nothing. Two further reasons not to reach for it, on
top of being out of scope: the vendor's own header says it is *"only effective
with fastInfrared off"*, and fast infrared is now this driver's default on
every infrared pass; and CyberView sends neither bit in any capture.

The same boundary is why 8-bit delivery is absent. The library holds raw 16-bit
pixels precisely so that every later improvement applies to every scan ever
taken, and 8 bits is a delivery decision a consumer can make from 16 while the
reverse is impossible. `DEPTH_8` is used where the depth is genuinely all that
is needed -- prescans and the calibration pass -- and nowhere else.
