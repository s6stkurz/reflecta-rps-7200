# P16 -- Read timeouts and the 10-minute budget are not enforced where they matter

**Severity** medium · **Group** B: Scanner safety -- wedges, abandoned reads, calibration · **Reported independently by** 5 findings in 4 areas

[Back to the summary](../README.md)

## The problem

`INFRARED_FLOOR_S` (212 s), described as guarding a timeout, is not used by any timeout;
the actual read guards are 120 s, below the ~220 s an untied infrared pass (`--no-fast-ir`)
holds the device -- the exact situation that wedged the scanner once. Neither CLI tool
estimates its runtime or warns before a run that will outlive the harness's 10-minute
foreground kill, and arguments are validated only after the device has been opened,
calibrated and metered (any integer `--dpi` is accepted).

## Fix plan

1. Derive the read timeout from the pass: `max(120, INFRARED_FLOOR_S + margin)` for
   untied IR, and from the library medians otherwise.
2. Validate arguments before opening the device.
3. Print an estimate from the medians (CLAUDE.md lists them) and refuse to run in the
   foreground past ~8 minutes unless `--background-ok`.

## Evidence (from [TP-09](../areas/transport-protocol.md#transport-protocol-tp-09))

**Where:** `rps7200/session.py:55-63`, `rps7200/direct.py:1398-1418`, `rps7200/direct.py:1440-1500`, `rps7200/usb_transport.py:75`, `CLAUDE.md:363-367`, `README.md:458-459`, `tools/uniformity.py:683-688`

```text
`INFRARED_FLOOR_S = 212.0` (session.py:63) is referenced only by tests/test_session.py:667,674. The actual guards: read_planes `idle_timeout: float = 120.0` (direct.py:1447, NoDataYet idle -> `raise ScanReadError(f"no data for {idle_timeout:.0f}s ...")`), `PARTIAL_READ_TIMEOUT_S = 120.0` (usb_transport.py:75), read_lines `timeout_ms: int = 120_000` (1403). scan() does not expose idle_timeout. uniformity.py:686: 'if it stalls, plumb idle_timeout through scan() rather than retrying'.
```

**Failure scenario:** On a --no-fast-ir 300 dpi RGBI pass, the device answers READs with ZLPs while it spends its ~220 s floor. After 120 s, read_planes raises ScanReadError mid-scan: the read is abandoned, the device wedges and needs a power cycle. This is the incident CLAUDE.md describes with a 60 s timeout, now with a 120 s one.

**Second reader's check:** `INFRARED_FLOOR_S = 212.0` (session.py:63) is referenced only by tests/test_session.py:667,674. No timeout uses it. The real guards are read_planes `idle_timeout: float = 120.0` (direct.py:1447, 1494-1498), `PARTIAL_READ_TIMEOUT_S = 120.0` (usb_transport.py:75) and read_lines `timeout_ms=120_000` (direct.py:1403). scan() does not expose idle_timeout. CLAUDE.md:365-367 ('212 s survives in the code as INFRARED_FLOOR_S because it guards a timeout') and README.md:458-459 are contradicted by the code. Whether an untied IR pass is actually silent for more than 120 s is not established in code, so medium is right.

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [TP-09](../areas/transport-protocol.md#transport-protocol-tp-09) | transport-protocol | medium | confirmed | INFRARED_FLOOR_S guards no timeout; the real read guards are 120 s, below the documented 212-227 s untied-IR floor | rps7200/session.py:55-63, rps7200/direct.py:1398-1418, rps7200/direct.py:1440-1500 |
| [D13](../areas/docs-readme-claude.md#docs-readme-claude-d13) | docs-readme-claude | medium | confirmed | INFRARED_FLOOR_S 'guards a timeout', but no timeout in the code uses it | rps7200/session.py:55-63, tests/test_session.py:667-674, rps7200/direct.py:1398-1405 |
| [CLI-A2](../areas/cli-operator-tools.md#cli-operator-tools-cli-a2) | cli-operator-tools | medium | found-by-verifier | Neither CLI tool estimates its runtime or warns before a run that will outlive the 10-minute foreground kill | tools/scan.py:196-235, tools/scan_roll.py:455-477, rps7200/session.py:743 |
| [CLI-14](../areas/cli-operator-tools.md#cli-operator-tools-cli-14) | cli-operator-tools | medium | confirmed | Arguments are validated only after the device is opened, calibrated and metered; any integer --dpi is accepted | tools/scan.py:39, tools/scan.py:70-73, tools/scan.py:143-146 |
| [PA-22](../areas/probe-and-analysis-tools.md#probe-and-analysis-tools-pa-22) | probe-and-analysis-tools | low | confirmed | fast_ir_probe accepts bw/kodachrome and refuses only after metering at 1800 dpi; its docstring says it meters in RGBI | tools/fast_ir_probe.py:148-152, tools/fast_ir_probe.py:199-200, tools/fast_ir_probe.py:29 |
