#!/usr/bin/env python3
"""Can this machine talk to the scanner at all? One command, one ladder.

    uv run python tools/check_scanner.py

Written for the first minutes after Zadig binds WinUSB to the scanner on
Windows. Until that happens libusb enumerates the device and still cannot open
it -- the stock Image/WIA driver holds it -- so every rung below the open is
unexercised there: the interface claim, endpoint discovery, the IEEE1284
preamble, a command round trip and a short windowed read. On Windows all of
that goes through WinUSB rather than IOKit, and nothing has ever driven it.

The ladder climbs until something fails, then stops and says what the failure
means rather than what the exception was.

It does **not** calibrate, scan, meter or move the transport. The only commands
it sends are INQUIRY and READ STATE, and both only read. Anything that moves
the mechanism costs minutes of the owner's hardware, and a wedge costs a power
cycle, so it is asked for each time -- see CLAUDE.md.

Exit status is 0 when the ladder completes, and 0 as well when no scanner is on
the bus: running this on a machine with nothing attached is a supported thing
to do, not a failure. A rung that fails with the scanner present exits 1.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rps7200.console import use_utf8_stdout
from rps7200.direct import MEDIA_PRESENT, DirectScanner, ScanReadError
from rps7200.usb_transport import (
    PRODUCT_ID,
    VENDOR_ID,
    ScannerNotFound,
    Transport,
    UsbError,
)

TOTAL = 5

EXPECTED_VENDOR = "PIE"
EXPECTED_PRODUCT = "MF Scanner"

#: What INQUIRY reports on the scanner this driver was written against. The
#: field offsets follow `sanei_pieusb_cmd_inquiry` and were confirmed against
#: this model; another model answers at other offsets, so every number printed
#: below would be read out of the wrong place.
EXPECTED_MODEL = 0x31
EXPECTED_FIRMWARE = "1.70"

#: 10344 x 6888 -- the CCD `framing.FULL_FRAME` is hard-coded to. A scan never
#: issues INQUIRY (neither CyberView nor any successful run here does, and
#: asking from inside the scan flow has broken reads), so this is the only
#: place that hard-coded frame is ever checked against the device itself.
EXPECTED_CCD = (10344, 6888)

#: READ STATE byte 6, bit 7. The vendor's power-on capture reads 0x0d, 0x15,
#: 0x1d or 0x35 with the device idle and 0x8d, 0x95 or 0x9d while a pass runs.
STATE_SCANNING = 0x80


def rung(number: int, what: str) -> None:
    print(f"{number}/{TOTAL}  {what} ...", flush=True)


def note(detail: str) -> None:
    print(f"       {detail}")


def failed(what: str, *hints: str) -> int:
    """Stop the ladder. Returns the process exit status, for `return`."""
    print(f"\nFAILED: {what}")
    for hint in hints:
        print(f"  {hint}")
    print("\nStopped here. Nothing further was sent to the device.")
    return 1


def check(verbose: bool = False) -> int:
    print("Reflecta RPS 7200 -- connection check")
    print("Reads only: INQUIRY and READ STATE. Nothing here calibrates,")
    print("scans, or moves the transport.\n")

    # -- 1. libusb ---------------------------------------------------------
    # Constructing the transport is what loads the library and calls
    # libusb_init; the loader is lazy on purpose, so this is the first line
    # that can fail for a missing DLL.
    rung(1, "loading libusb")
    try:
        transport = Transport(verbose=verbose)
    except (OSError, UsbError) as exc:
        return failed(
            f"libusb would not load or initialise: {exc}",
            "Everything host-side still works without it -- decoding a",
            "stored scan touches no device. Only the bus needs this.",
        )
    note("loaded, libusb_init succeeded")

    try:
        return _ladder(transport)
    finally:
        # Releases the interface and closes the context whatever happened,
        # including on a rung that raised: a handle left open is one the next
        # run cannot claim.
        transport.close()


def _ladder(transport: Transport) -> int:
    # -- 2. on the bus -----------------------------------------------------
    # "Not plugged in" and "plugged in and held by another driver" are the same
    # NULL handle from `open`, and they need opposite answers here -- one is
    # fine and exits 0, the other is the whole reason this file exists. The
    # private call is what separates them; the public path folds them together.
    rung(2, f"looking for {VENDOR_ID:#06x}:{PRODUCT_ID:#06x} on the bus")
    if not transport._on_the_bus():
        print(f"       no device {VENDOR_ID:#06x}:{PRODUCT_ID:#06x} found\n")
        print("No scanner attached, so there is nothing to check. That is not")
        print("a failure -- this command is safe to run on a bare machine.")
        print("If the scanner should be here: check it is powered on and the")
        print("cable is in, then run this again.")
        return 0
    note("enumerated")

    # -- 3. open, claim, endpoints -----------------------------------------
    rung(3, "opening the device and claiming interface 0")
    try:
        transport.open()
    except ScannerNotFound as exc:
        # It is on the bus -- rung 2 just said so -- so this is the driver
        # binding, which is exactly what Zadig changes. The transport's own
        # message names this platform's version of the problem.
        return failed(str(exc))
    except UsbError as exc:
        return failed(
            f"the device opened but interface 0 could not be claimed: {exc}",
            "Something else holds it. Close CyberView, VueScan, any other",
            "copy of this driver and any SANE frontend, then try again.",
        )
    note(
        f"claimed, bulk-in endpoint {transport.bulk_in_ep:#04x}, "
        f"max packet {transport.max_packet_size}"
    )

    # `debug=False` deliberately: CLAUDE.md's rule is that every *scan* is
    # filed, and nothing here scans, so there is nothing to file.
    scanner = DirectScanner(transport=transport, debug=False)

    # -- 4. INQUIRY --------------------------------------------------------
    # The first real round trip: IEEE1284 preamble, six command bytes, a status
    # read, and a short windowed payload read. A stall in the windowed reader
    # shows up here, before anything has cost scanner time.
    rung(4, "INQUIRY")
    try:
        info = scanner.inquiry()
    except (UsbError, ScanReadError, IndexError) as exc:
        return failed(
            f"the device is open but would not answer INQUIRY: {exc}",
            "The command path itself is broken -- the preamble, the status",
            "handshake, or the windowed read. This is the rung that has never",
            "run on Windows; --verbose prints every transfer.",
            "One exception: a CHECK CONDITION left queued by an earlier",
            "session is one-shot and lands on whatever command comes next, so",
            "a single failure here is worth exactly one re-run.",
        )
    print("       " + info.describe().replace("\n", "\n       "))

    warnings: list[str] = []
    if info.model != EXPECTED_MODEL:
        return failed(
            f"model is {info.model:#06x}, expected {EXPECTED_MODEL:#06x}",
            "Every offset INQUIRY is decoded at was confirmed on model 0x31.",
            "On another model the fields above are read from the wrong place,",
            "so nothing printed can be believed -- this line included.",
        )
    if (info.ccd_width, info.ccd_length) != EXPECTED_CCD:
        return failed(
            f"CCD is {info.ccd_width} x {info.ccd_length}, expected "
            f"{EXPECTED_CCD[0]} x {EXPECTED_CCD[1]}",
            "framing.FULL_FRAME is hard-coded to that size and a scan never",
            "issues INQUIRY, so a scan would set a window off the sensor and",
            "nothing would report that it had.",
        )
    if info.firmware != EXPECTED_FIRMWARE:
        # Not fatal: the offsets are a property of the model, and the ladder
        # can still finish. Worth saying, because every protocol answer in
        # docs/ was measured on 1.70 and none of it has been re-checked.
        warnings.append(
            f"firmware is {info.firmware!r}, not {EXPECTED_FIRMWARE!r}. "
            "Everything measured in docs/ came off 1.70."
        )
    if (info.vendor, info.product) != (EXPECTED_VENDOR, EXPECTED_PRODUCT):
        warnings.append(
            f"identifies as {info.vendor!r} {info.product!r}, not "
            f"{EXPECTED_VENDOR!r} {EXPECTED_PRODUCT!r}."
        )
    note("model and CCD are the ones this driver was written against")

    # -- 5. READ STATE -----------------------------------------------------
    rung(5, "READ STATE")
    try:
        state = scanner.read_state()
    except ScanReadError as exc:
        return failed(
            f"READ STATE did not answer: {exc}",
            "While the lamp warms -- about 80 s from cold -- the scanner",
            "answers NOT READY to every command, this one included. If it was",
            "just switched on, wait and run this again.",
            "A reply that is merely short means the 13-byte length is wrong",
            "for this firmware: pieusb asks for 12, CyberView for 13.",
        )
    except UsbError as exc:
        return failed(f"READ STATE failed: {exc}")

    scanning = "set" if state.scanning & STATE_SCANNING else "clear"
    media = "set" if state.scanning & MEDIA_PRESENT else "clear"
    note(f"byte 2  position    {state.position}")
    note(f"byte 5  warming up  {state.warming_up}")
    note(f"byte 6  flags       {state.scanning:#04x}"
         f"   0x80 scanning {scanning}, 0x40 media {media}")
    note(f"byte 8  media flag  {state.busy}   1 = empty transport")
    print()
    print("       What those media bits are worth: byte 8 is the one measured")
    print("       here, 1 empty and 0 loaded, with one variable changed. Byte")
    print("       6's 0x40 tracked the film once, and is clear throughout the")
    print("       vendor's power-on capture, so a set bit is evidence and a")
    print("       clear one is not. Only you can see the transport; neither")
    print("       bit decides anything on its own.")

    if state.scanning & STATE_SCANNING:
        # Tell a pass that is still running from a flag left behind by one
        # that is not. A real scan changes something -- it finishes, or the
        # position moves. A stale one sits exactly where it is.
        settled = True
        for _ in range(4):
            time.sleep(2.0)
            again = scanner.read_state()
            if (again.scanning, again.position) != (state.scanning,
                                                    state.position):
                settled = False
                state = again
                break
        if not settled:
            return failed(
                "a scan really is running: the state changed while this "
                "looked at it",
                f"now {state.scanning:#04x} at position {state.position}.",
                "Wait for it to finish rather than starting anything else --",
                "an abandoned read is what wedges this device.",
            )
        print()
        print(f"NOTE: the scanning bit is set ({state.scanning:#04x}) and "
              "nothing here started a scan.")
        print("      It did not change over eight seconds, so this is a flag")
        print("      left behind rather than a pass still running. Measured")
        print("      2026-09-21: a state that survived a power cycle cleared")
        print("      the moment the next session started, and a full walk ran")
        print("      normally through it. The vendor's own capture reads 0x1d")
        print("      idle and 0x9d scanning, so the bit means what it says --")
        print("      it is the staleness that is not dangerous.")
        print("      If a scan does start misbehaving after this, power-cycle")
        print("      at the unit's own switch before trying again.")

    print()
    for warning in warnings:
        print(f"NOTE: {warning}")
    if warnings:
        print()
    print("The device path works on this machine: open, claim, endpoints,")
    print("a command round trip and a payload read all succeeded.")
    print()
    print("Next, and only with the owner's agreement: one 300 dpi RGB frame,")
    print("about 22 s at the median, filed with RPS7200_DEBUG=1. That is the")
    print("smallest run that exercises a real windowed image read. Budget")
    print("above the median -- scan time tracks the exposure sum as well as")
    print("the line count, so a dense frame runs longer than a thin one.")
    return 0


def main() -> int:
    use_utf8_stdout()
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--verbose", action="store_true",
                    help="log every USB transfer, for a rung that fails "
                         "obscurely")
    args = ap.parse_args()
    return check(verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
