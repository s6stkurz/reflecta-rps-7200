# The analog gain register: is there anything in it?

## Status: designed, not run. Needs Stefan at the scanner.

Written down before the hardware is touched so the scanner time is spent on a
decided question rather than on working out what to ask. **Do not drive this
without his go-ahead.**

## The question

Blue cannot reach its target in plain RGB. Metering asks for ×10.005 of the
6506 base and the 16-bit timer holds ×10.07, so blue arrives pinned at the rail
and still lands about 30% below red and green. Three of the four metered RGB
entries in the library are in that state. Exposure has nothing left to give.

`SET GAIN OFFSET` also carries a **gain** field, R/G/B/I, which this driver has
never written. The device reports 39 / 33 / 21 / 21, and blue's is the lowest of
the three visible channels. If that gain is analog — applied before the ADC —
then raising blue's from 21 toward 33 would buy the range exposure cannot.

If it is a digital multiplier applied after conversion, it buys nothing at all:
the noise scales with the signal and the result is a brighter picture with the
same information in it. **Which of the two it is, is the whole question.**

## What the other drivers say

Not decisive either way, but worth having straight:

- **pyopticfilm does this.** `fix/gl845-afe-gain-width` is a fix to its
  per-channel AFE gain/offset dichotomy search, clamping the codes to the 8-bit
  front-end registers. So a per-channel analog gain search is ordinary practice
  on that hardware.
- **nkscan cannot.** Its `AnalogControl` bitflags define `ANALOG_GAIN` and
  `DIGITAL_GAIN`, but the LS-9000's capability page advertises byte 14 = `0x40`
  — `EXPOSURE_VALUE` only. Nikon folds gain and integration time together
  behind one exposure number and decides the split itself.
- **pieusb never touches it.** Its metering moves `exp_rel_*` and nothing else.
- **CyberView never touches it either**: gain is identical in 36 of 36
  `WRITE GAIN/OFFSET` payloads across every capture.

So nobody has driven this register on this scanner, and the vendor does not.

## Why it is not the `SET_SCAN_HEAD` situation

Worth being explicit, because the instinct after that one is to refuse.

`SET_SCAN_HEAD` drives a **mechanism** with an unknown step unit and reports
nothing back, so a bad value moves the carriage somewhere with no feedback and
no way to calibrate by escalation. That is why it is banned.

Gain is a register in a payload this driver already sends before every single
scan. The failure mode is a pass that comes back wrong-looking, which costs one
pass. Nothing moves.

That is a reason to run the experiment carefully, not a reason to refuse it —
but it is also not a reason to be casual, because `set_gain_offset` writes gain
and exposure in one payload, so a malformed value lands on every channel at
once.

## The design

Five passes, about two minutes of scanner time, plus one shading calibration if
the session does not already have one.

- **Blue only.** Red and green are comfortable; there is no question about them
  and changing them only adds variables.
- **Ladder: 21 → 25 → 29 → 33 → 39.** The top is red's own value, so every rung
  is a number this device is already known to accept in that field. Nothing on
  the ladder is invented.
- **Fixed exposure throughout**, at whatever metering settled on for the frame.
  Gain is the only variable; that is the point.
- **300 dpi, RGB, a short frame.** Scan time follows line count, so each pass is
  seconds. No infrared: it would add the ~212 s floor per pass for nothing.
- **`RPS7200_DEBUG=1`**, so every pass is filed with its raw bytes and the whole
  thing is re-analysable without a second run.

### Guards, in order

1. **Read back after every write.** `get_gain_offset()` returns a fixed
   reference on this device, not a readback, so it cannot confirm the write —
   which means the confirmation has to come from the image instead. That is what
   makes step 2 the abort condition rather than a comparison of registers.
2. **Abort the ladder the moment a pass does not change as the one before it
   did.** A pass that comes back identical to its predecessor says the field is
   being ignored; a pass that comes back darker says something wrapped. Either
   ends the run.
3. **Stop at the first pass that clips.** A clipped blue measures nothing.
4. **Restore 39 / 33 / 21 / 21 at the end**, and on any exit path.

### The measurement, and what decides it

For each rung, from the stored raw bytes and offline:

- blue's median DN, and
- blue's **random** noise, via `noise_split` from the `measure-scan-quality`
  skill — which needs the two repeats at one setting the ladder should therefore
  include at its first rung.

Then:

- **If DN rises faster than noise**, the gain is analog and it is worth having.
  Blue gets a second lever and the RGB rail stops being the end of the argument.
- **If DN and noise rise together**, it is a digital multiplier. Record that,
  restore the register, and never touch it again — the finding is the value.

Both outcomes are worth the two minutes, which is the argument for running it at
all. Only the first leads to any code change.

## What to do with a positive result

Nothing automatic, at first. Gain would become an explicit argument with the
measured range documented, not something `auto_exposure` reaches for on its own
— metering is hard enough to reason about with one lever. Whether it should
eventually be metered is a separate question, and one this experiment does not
answer.

## The cheaper answer that already exists

Worth saying plainly, because it may make the whole thing unnecessary: **RGBI
mode already gives blue about 5× the sensitivity of RGB** (`BLUE_RGBI_HEADROOM`).
Blue is rail-limited in RGB and has room to spare in RGBI. If a frame's blue
record matters, scanning it RGBI is the answer available today, at the cost of
the ~212 s infrared floor.

The gain experiment is worth running for what it tells us about the hardware.
It is not the only route to a usable blue.
