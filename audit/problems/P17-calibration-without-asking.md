# P17 -- Calibration starts without asking what is in the transport

**Severity** medium · **Group** B: Scanner safety -- wedges, abandoned reads, calibration · **Reported independently by** 3 findings in 3 areas

[Back to the summary](../README.md)

## The problem

CLAUDE.md: calibrate with the film loaded, and only Stefan can see the transport. The
window's Calibrate button starts at once; a bare Return in the "Calibrate first" prompt
starts a calibration; `tools/scan.py` calibrates straight after INQUIRY;
`tools/uniformity.py` tells the operator to empty the transport and then calibrates --
the state the vendor never creates and that preceded a wedge. The measured media flag
is never checked.

## Fix plan

1. One confirmation ("Film in the transport?") before any calibration, in the window and
   the CLIs (`--film-loaded` to skip).
2. Log the media bit at calibration time and record it in the calibration entry.
3. `tools/uniformity.py`: remove the "empty the transport" instruction or gate it behind
   an explicit flag with a warning.

## Evidence (from [TP-18](../areas/transport-protocol.md#transport-protocol-tp-18))

**Where:** `rps7200/direct.py:1805-1813`, `rps7200/protocol.py:555-575`, `tools/uniformity.py:700-743`, `CLAUDE.md:272-280`

```text
calibrate_shading reads state only for `if not self.read_state().warming_up: break`. State.no_media (byte 8, 'measured, confirmed' 1 = empty) is never consulted before calibrating. uniformity.py:700-702 prints 'Metering on the EMPTY transport' and asks `confirm("    press Enter with the transport empty: ")`, and then at 729-735 runs 'Calibrating shading (3-4 minutes) ...' with `result = scanner.calibrate_shading()` and no prompt to load film. CLAUDE.md:279-280: 'Calibrating an empty transport is a state the vendor never creates, and doing it once preceded a wedge.'
```

**Failure scenario:** The operator follows uniformity.py's prompts: empty transport for metering, Enter, and the tool calibrates the empty transport straight away. This is the documented pre-wedge state.

**Second reader's check:** calibrate_shading reads state only for warming_up (direct.py:1805-1813) and never consults State.no_media (protocol.py:571-574). uniformity.py:700-706 asks for an EMPTY transport and then runs auto_exposure, whose shaded probe scans trigger scan()'s lazy calibration (direct.py:2579-2592) on that empty transport when the session has no reference. The explicit calibrate_shading at 729-735 follows with no prompt to load film. So the documented wedge precursor is reached twice, once through the lazy path before the explicit calibration.

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [TP-18](../areas/transport-protocol.md#transport-protocol-tp-18) | transport-protocol | medium | confirmed | Calibration never checks the measured media flag, and tools/uniformity.py calibrates right after telling the operator to empty the transport | rps7200/direct.py:1805-1813, rps7200/protocol.py:555-575, tools/uniformity.py:700-743 |
| [CLI-15](../areas/cli-operator-tools.md#cli-operator-tools-cli-15) | cli-operator-tools | medium | confirmed | scan.py calibrates straight after INQUIRY, with no check or question about film in the transport | tools/scan.py:160-168, rps7200/direct.py:2543-2553 |
| [D17](../areas/docs-readme-claude.md#docs-readme-claude-d17) | docs-readme-claude | medium | confirmed | Bare Return in the 'Calibrate first' prompt starts a calibration: calibrating does have a key | tools/gui.py:815-839, tools/gui.py:1946-1958, tools/gui.py:2021-2030 |
