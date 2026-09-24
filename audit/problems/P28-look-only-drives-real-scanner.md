# P28 -- `--look-only` without `--demo` drives the real scanner

**Severity** medium · **Group** D: The demo is not yet the real software with different inputs · **Reported independently by** 4 findings in 3 areas

[Back to the summary](../README.md)

## The problem

`--look-only` is honoured only by the demo. Given without `--demo`, the window says
scanning will refuse for lack of film, while the real scanner is driven normally --
including an empty-transport calibration it offers. Under `--look-only --demo` the
demo's `prescan()` does not refuse either: it returns a stored photograph of film that is
not there.

## Fix plan

1. Refuse `--look-only` without `--demo` at argument parsing.
2. Under `--look-only`, the demo's `prescan`/`scan`/`calibrate` raise the same exception
   the real scanner raises with no film (a backend refusal, not a greyed button).

## Evidence (from [DP-13](../areas/demo-parity.md#demo-parity-dp-13))

**Where:** `tools/gui.py:8145-8147`, `tools/gui.py:8187-8203`, `tools/gui.py:7266-7271`, `rps7200/direct.py:2543-2552`, `rps7200/demo.py:826-844`

```text
`ap.add_argument("--look-only", ... help="there is no film in the transport. Every control still works; anything that reaches for film says so")`; `no_film=args.look_only` is passed only inside `if args.demo:`, while `ScannerGui(..., look_only=args.look_only, ...)` is unconditional. Sheet text: 'There is no film in the transport ... Scanning is offered as it always is, and will say there is no film when it reaches for it.' DirectScanner.scan: 'Reported, not enforced ... continuing'; NoMediaLoaded is imported but never raised anywhere.
```

**Failure scenario:** Someone runs `uv run python tools/gui.py --look-only --open-roll rolls/X` (forgetting --demo) to browse a walk with the scanner plugged in. Pressing 'Scan chosen frames' as the text invites seeks, moves the transport, calibrates an empty transport and scans.

**Second reader's check:** no_film=args.look_only is passed only inside `if args.demo:` (gui.py:8187-8199), while look_only=args.look_only reaches ScannerGui unconditionally (8202-8203) and changes the sheet's text to promise that scanning 'will say there is no film' (7266-7271). NoMediaLoaded is defined and exported but never raised (grep of rps7200/ and tools/). DirectScanner.scan only logs the media bit ('Reported, not enforced', direct.py:2543-2552). An askokcancel dialog does stand before 'Scan chosen frames' (gui.py:2817-2829), but it does not mention the missing film.

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [DP-13](../areas/demo-parity.md#demo-parity-dp-13) | demo-parity | medium | confirmed | --look-only without --demo drives the real scanner while the window says scanning will refuse for lack of film | tools/gui.py:8145-8147, tools/gui.py:8187-8203, tools/gui.py:7266-7271 |
| [GUI2-13](../areas/gui-part2.md#gui-part2-gui2-13) | gui-part2 | medium | confirmed | --look-only without --demo promises refusals the real scanner never makes, and offers an empty-transport calibration | tools/gui.py:8145-8147, tools/gui.py:8187-8199, tools/gui.py:7266-7272 |
| [GUI1-20](../areas/gui-part1.md#gui-part1-gui1-20) | gui-part1 | medium | confirmed | --look-only without --demo drives the real scanner while the window promises a refusal | tools/gui.py:8145-8147, tools/gui.py:8187-8203, tools/gui.py:7266-7272 |
| [DP-A1](../areas/demo-parity.md#demo-parity-dp-a1) | demo-parity | low | found-by-verifier | Under --look-only the demo's prescan() does not refuse: it returns a stored photograph of film that is not there | rps7200/demo.py:455-477, rps7200/demo.py:826-844, tools/gui.py:7266-7271 |
