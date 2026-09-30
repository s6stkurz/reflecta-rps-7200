# What is written to disk, and in what state

[Back to the summary](README.md)

Every file the driver writes or reads, as each area's reader found it in the code at
`03aacba`, with the format, whether it holds raw or corrected pixels, who writes and who reads
it, and whether it is exact -- bit for bit what the scanner sent, or losslessly derived from
it. The areas overlap on purpose: the same file seen from its writer and from its reader.
Where a second reader corrected a row, the correction is in that area's file under
[areas/](areas/).

The first audit's version is [../persisted-state.md](../persisted-state.md). The main changes:

- `scan.tif` is the raw decode on every path the window and the tools take; a pass filed
  without raw pixels is labelled corrected (`image.corrections_applied`).
- `raw.bin` (uncompressed, while the window's device is open) or `raw.bin.gz`; the record
  holds a checksum of every file and an `INCOMPLETE` marker stands until it is complete.
- `calibration/<UTC time>/{data.bin, calibration.json, shading.npz, ccd_mask.bin}` beside
  the cached `shading.npz`.
- Roll manifests (`roll.json`, `survey.json`, `approved.json`) are written beside and renamed
  over, keep the version they replaced as `.bak`, and set an unreadable one aside.

What is still not exact, from the rows below: a roll frame's `prescan.tif` (corrected 8-bit,
no bytes); the roll folder's `frameNN.tif` and `prescanNN.tif` (corrected by that day's code,
by design); delivered copies; and anything held only in the debug spool in the OS temp
directory until the device closes.

## Every row, from every area

{{PS_APPENDIX}}
