"""Scanning the Reflecta RPS 7200 over USB, with RGB and raw infrared together.

The scanner is driven directly, the way the vendor software does it -- no SANE.
The infrared plane comes back untouched rather than being spent internally on
dust removal, and the shading correction the scanner measures but never applies
is applied here.

    from rps7200.direct import DirectScanner

    with DirectScanner() as s:
        s.ensure_shading("calibration/shading.npz")   # once per power-on
        image, meta = s.scan(resolution=1800, infrared=True)

Nothing above imports libusb until it is used, so decoding a stored scan,
merging a bracket or writing a TIFF works with no scanner drivers installed.
"""

__version__ = "0.1.0"
