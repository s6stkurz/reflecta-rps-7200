# The demo against the real scanner

[Back to the summary](README.md)

CLAUDE.md's rule: `--demo` may change what the software is fed, never what it does.
`DemoScanner` stands where `DirectScanner` stands, and everything above that seam runs
unmodified.

## Where it stands at 03aacba

The first audit's demo problems (P26-P28) are fixed or mostly fixed:

- The demo files what it is. A pass drawn from raw bytes is filed raw with its reference; a
  stored prescan that was corrected is labelled so.
- It refuses what the scanner refuses, with the driver's own words: an uncalibrated corrected
  pass, no film in the transport.
- `scan_roll` runs the driver's own loop.
- `--look-only` without `--demo` is refused.

What the second audit found is narrower, and none of it is high:

- **Backlash is not applied** to the first move of a roll frame, so a backward hold converges
  in one command where the scanner needs more (DEMO-01).
- **`--library` and `--rolls` are accepted under `--demo`**, so synthetic entries and walks
  can land in the real library or rolls folder (DEMO-02).
- **Hold and aim prescans record `film = negative`** whatever the roll's film -- the driver's
  own bug, which the demo copies faithfully (DEMO-03, FR-08).

The remaining low findings are in [areas/demo-parity.md](areas/demo-parity.md).

## Every demo divergence

{{DEMO_FINDINGS}}
