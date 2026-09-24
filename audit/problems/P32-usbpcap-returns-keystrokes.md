# P32 -- The pcap reader hands back keystroke payloads, and the test asserts it does

**Severity** medium · **Group** E: Outputs, measurement and framing · **Reported independently by** 2 findings in 2 areas

[Back to the summary](../README.md)

## The problem

CLAUDE.md: `rps7200/usbpcap.py` "only ever returns control setup packets and payloads for
a device the caller named; it never returns an interrupt payload, which is where a
keystroke is", and `tests/test_usbpcap.py` "asserts it does not come back". In the code,
the public `packets()` returns every record's payload, keyboard interrupt payloads
included, and the test asserts they *are* returned (filtering happens only in
higher-level helpers). The `captures/*.pcapng` hold keyboard HID traffic from the
capture machine, so this is the privacy guard the docs rely on.

## Fix plan

1. Make `packets()` private (`_packets`) or give it the same device/transfer-type filter as
   the helpers; strip interrupt payloads at the lowest level.
2. Rewrite the test to assert the keystroke never comes back from any public function.

## Evidence (from [TP-20](../areas/transport-protocol.md#transport-protocol-tp-20))

**Where:** `rps7200/usbpcap.py:8-18`, `rps7200/usbpcap.py:103-127`, `tests/test_usbpcap.py:137-141`, `CLAUDE.md:488-492`

```text
packets() yields `Packet(device=..., transfer=data[22], ..., payload=data[header_len:])` for every EPB, with no filtering by transfer type or device. The test asserts `[p.transfer for p in got].count(INTERRUPT) == 2`, meaning keystrokes are returned. The module docstring says 'It never returns an interrupt-endpoint payload ... That is not a comment, it is what `setups` and `scanner_devices` actually do', and CLAUDE.md:490 says 'That reader only ever returns control setup packets and payloads for a device the caller named; it never returns an interrupt payload.'
```

**Failure scenario:** Someone writes a quick 'dump all payloads' diagnostic with usbpcap.packets() and pastes the output into an issue or commit. The keystrokes from the capture machine (possibly passwords) are published.

**Second reader's check:** packets() (usbpcap.py:103-127) yields every EPB record's payload with no filter on transfer type or device. tests/test_usbpcap.py:138-141 asserts it returns two INTERRUPT (keystroke) records. The module docstring (usbpcap.py:8-18) says 'It never returns an interrupt-endpoint payload' (scoped to setups/scanner_devices, which is accurate for those two). CLAUDE.md:488-492 says 'That reader only ever returns control setup packets and payloads for a device the caller named; it never returns an interrupt payload', and packets(), a public import used by parse_capture, contradicts that.

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [TP-20](../areas/transport-protocol.md#transport-protocol-tp-20) | transport-protocol | medium | confirmed | usbpcap.packets() returns every record's payload, keyboard interrupt (keystroke) payloads included, although the docs say it never does | rps7200/usbpcap.py:8-18, rps7200/usbpcap.py:103-127, tests/test_usbpcap.py:137-141 |
| [D14](../areas/docs-readme-claude.md#docs-readme-claude-d14) | docs-readme-claude | medium | confirmed | usbpcap's public packets() hands back interrupt (keystroke) payloads; the test asserts they are returned | rps7200/usbpcap.py:103-127, rps7200/usbpcap.py:8-18, tests/test_usbpcap.py:171-189 |
