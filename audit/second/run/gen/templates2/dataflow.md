# Dataflow at 03aacba

[Back to the summary](README.md)

How data moves through the driver after the fixes, traced hop by hop from the code by one
reader and then checked, hop by hop, by a second reader who corrected wrong steps, added
missing ones and dropped invented ones. Compare the [first audit's dataflow](../dataflow.md),
at `83dbb22`.

What changed in the flows since the first audit, in short:

- A pass is decoded into **raw pixels**, which are what the library files; the corrected
  picture is computed from them for the window, for delivered files and for Export
  (`library.corrected`, with today's code). A pass with no raw pixels beside it is labelled
  corrected when filed.
- Every library entry is **reserved, written and then committed** (`INCOMPLETE` marker,
  record renamed over last, checksums for every file). The window files single scans and
  prescans **uncompressed** while its device is open and compacts them after it closes; a
  roll's frames still gzip on the writer thread while the next frame scans.
- Each **calibration** is archived byte for byte under `calibration/<UTC time>/` beside the
  cached reference, and the entries it corrects name that archive.
- Each pass records **every command it sent** with what came back (`extra.commands`), when it
  began, where its reference came from, and for a roll entry its `roll_membership`.
- A pass or calibration that stops before its last line marks the device **suspect**, and the
  driving commands refuse from then on.

Still true, and the subject of the high findings: a real roll frame's prescans reach the
library only as a corrected `prescan.tif`; metering probes and hold/aim prescans reach it only
through debug filing; and debug filing's spool lives in the OS temp directory until the device
closes.

## The flows

{{DF_FLOWS}}

## Module dependencies

{{DF_DEPS}}

## Findings the dataflow readers made along the way

{{DF_FINDINGS}}
