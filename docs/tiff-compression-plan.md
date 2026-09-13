# Lossless compression for written TIFFs

## Status: DONE — implemented 2026-09-13, pixels verified identical

Writes go out as deflate + horizontal differencing where `tifffile` is
installed; both readers handle compressed files, which was the mandatory half.
`compress=False` restores the previous output exactly.

### What it actually saves

Measured across five real library entries, written both ways and compared:

```
entry                          shape          plain     zip    saved
300 dpi  prescan               287x428x3       0.4 MB   0.2 MB  54.5%
900 dpi                        860x1292x3      6.7 MB   6.1 MB   8.9%
1800 dpi                      1721x2584x3     26.7 MB  24.1 MB   9.7%
3600 dpi                      3443x5172x3    106.8 MB  98.0 MB   8.3%
3600 dpi RGBI                 3443x5172x4    142.5 MB 116.9 MB  18.0%
                                             -------- --------  -----
total                                         283.0 MB 245.2 MB  13.4%
```

**Pixels identical in every case**, both readers, which is the only thing that
makes the saving worth taking.

**Two corrections to what this plan predicted.** It headlined −16% from one
file, and TODO.md repeated that as "16% on every file"; the real spread is
8–18%, averaging 13.4%. And the expectation that "a shading-corrected scan
should do better" than −16% because raw sensor noise does not compress **did
not hold** — these entries are corrected and the RGB ones give 8–10%. Noise
dominates either way.

The one genuinely good result is **RGBI at 18%**, which is also the largest
file at 142 MB, so the saving lands where it is worth most. The 300 dpi
prescan's 54.5% is real but nearly free in absolute terms.

**Write cost is negligible**: 2.2 s for the 142 MB RGBI frame, against the
217–334 s the scan itself takes.

One expectation to correct before this is picked up, so the result is not a
disappointment: **this will not make NegPy faster.** Compression changes the
size of the file on disk, not the size of the array in memory. A 3600 dpi
4-channel scan is 5172 x 3443 x 4 x 16-bit either way — 142 MB of RAM once
loaded — and every edit still works on that. If anything, loading gets
marginally *slower* on an SSD, because inflating 120 MB costs more CPU than the
22 MB of I/O it saves.

What would actually make editing quicker is working at a lower resolution, or
NegPy's own proxy/preview path. Take this change for the disk space, which is
real and permanent, and leave speed out of the argument for it.

## Context

Scans are written uncompressed. A 1800 dpi frame is 26.7 MB and a 3600 dpi
4-channel one is 142 MB, and with every scan now filed in the library alongside
its raw bytes, that doubles again.

`deflate + predictor` gives the same pixels in less space — measured on
`scans/negatives/state_1800dpi.tif`:

```
16-bit TIFF, uncompressed (today)              26.7 MB
16-bit TIFF, deflate                           24.9 MB   -7%
16-bit TIFF, deflate + predictor               22.5 MB   -16%
```

−16% is modest because raw sensor noise does not compress; a shading-corrected
scan should do better. It is lossless and costs only CPU on write.

The goal is that this changes **file size and nothing else**. Pixels identical,
every existing file still readable, every consumer unaffected.

## The one thing that has to be done first (not a blocker — it is step 1)

`rps7200/tiff.py` has two independent implementations: `tifffile` when
installed, and a hand-written dependency-free path. The built-in reader refuses
anything compressed outright:

```python
compression = one(_COMPRESSION, _COMPRESSION_NONE)
if compression != _COMPRESSION_NONE:
    raise ValueError(
        f"unsupported TIFF compression {compression}; only uncompressed (1) is read"
    )
```

So compressed files must teach that reader to read them. This is not difficult
— `zlib` is in the standard library and the whole addition is around 15 lines —
it simply has to happen *before* anything writes a compressed file, not after.

Scale of the exposure, to be clear about what is actually at stake:
`tifffile` is an **optional** dependency (`pyproject.toml:12-14`) and it is
installed on this machine, so today every read and write goes through it and
the built-in path never runs. Nothing would break here either way. But that
same pyproject comment says *"the built-in one is complete"*, and writing files
it cannot read would make that false for anyone installing without tifffile.

That gap is now closed ahead of this work: `tests/test_tiff.py` forces
`_has_tifffile()` both ways and runs **every write/read pairing** of the two
implementations, which is what turned up the divergences they had already
accumulated (read-only arrays, a channel axis one kept and the other dropped,
big-endian dtypes). Compression therefore lands on a harness rather than
needing one — it adds compressed rows to an existing matrix.

Everything else checks out:

- **NegPy** — `negpy/infrastructure/loaders/tiff_loader.py` uses
  `tifffile.imread` and `imageio.imread`. Both handle deflate and predictor 2
  natively. Deflate is a standard TIFF compression; Photoshop, GIMP and Preview
  read it too.
- **`rps7200/library.py`** — `_sha256` is taken after writing, so each entry
  matches its own bytes; and `reconstruct()` compares **arrays**
  (`np.array_equal`), not files, so a format change cannot make an entry look
  like a decode regression.
- **`tools/make_comparison.py`, `rps7200/cli.py`, `tools/scan.py`** — all go
  through `tiff.read` / `tiff.write` and need no change.
- **Existing files** stay uncompressed and stay readable by both paths.

## Implementation

All of it in `rps7200/tiff.py`.

### 1. Teach the built-in reader deflate and predictor

The mandatory half. `zlib` is in the standard library, so the dependency-free
promise holds.

- Accept compression `8` (deflate) and `32946` (legacy zlib) beside `1`; keep
  raising on anything else, naming what it found.
- Decompress each strip with `zlib.decompress` before assembling.
- Add tag `_PREDICTOR = 317`. When it is 2, undo horizontal differencing per
  strip: reshape to `(rows, width, channels)` and `np.cumsum(axis=1)`, cast back
  to the sample dtype so it wraps modulo 2**16 as the format requires. Refuse
  predictor 3 (floating point) — we never write it.
- Strips must be decompressed individually; `_STRIP_BYTE_COUNTS` is the
  *compressed* length, which the existing loop already reads correctly.

### 2. Compress on the tifffile path

Add `compress: bool = True` to `write()`. When set, pass
`compression="zlib", predictor=True` to `tifffile.imwrite`. `compress=False`
restores today's exact output for interop debugging.

### 3. Leave the built-in writer uncompressed

Deliberate asymmetry: reading compressed files is mandatory, writing them is
not. `_write_builtin` stays as it is — the format is valid and universally
readable, and a second compressor is code with no benefit. Both writers produce
identical pixels; only the bytes differ, which nothing depends on.

### 4. Update the module docstring

It currently says "Read and write uncompressed multi-channel TIFFs", which will
no longer be true.

## Verification

The change is only safe if the pixels are provably untouched, so the checks are
about equality, not size.

1. **Existing suite passes unchanged** — `python3 -m pytest tests/ -q`.
   `TestCrossImplementation::test_roundtrip` already covers 6 shape/dtype
   combinations including a strip boundary crossing and the 4-channel RGBI
   case, and now exercises the compressed path for free. Run it a second time
   as `RPS7200_NO_TIFFFILE=1 python3 -m pytest tests/ -q`, which makes the
   import genuinely fail rather than only monkeypatching `_has_tifffile`, so
   the dependency-free promise is tested as a whole package and not per call.

2. **Compressed rows added to the cross-implementation matrix** in
   `tests/test_tiff.py`, which already forces the implementation through its
   `using` fixture. Every combination must round-trip identically:

   | written by | read by | why it matters |
   |---|---|---|
   | tifffile (compressed) | built-in | **the regression this prevents** |
   | tifffile (compressed) | tifffile | the normal path |
   | built-in | built-in | already covered, uncompressed |
   | built-in | tifffile | already covered, uncompressed |
   | tifffile, `compress=False` | both | the escape hatch still works |

   Parametrised over the same `SHAPES` as `TestCrossImplementation`, so the
   strip boundary and the 4-channel extrasamples case are covered compressed.

3. **Old files still read.** Assert `tiff.read` returns identical arrays for
   `scans/negatives/state_1800dpi.tif` and `scans/flat/flat_clearfilm_3600dpi.tif`
   (4-channel, 142 MB) through both implementations. These predate the change
   and must not be disturbed.

4. **Pixel-identity against the current outputs.** Re-run
   `tools/make_comparison.py` and assert the three regenerated files are
   `np.array_equal` to the ones in the repo root now. Same pixels, smaller
   files — this is the check that the change is invisible.

5. **The library is unaffected.** `tools/library.py verify` and
   `tools/library.py reconstruct` on a fixture library written with compression:
   checksums valid, and every entry still reports "identical to the stored
   image".

6. **Report the real numbers** — measured size before and after at 1800 and
   3600 dpi, and the write-time cost, so the trade is visible rather than
   assumed.

7. **Send the three TIFFs** (`1_nothing_done.tif`, `2_corrected.tif`,
   `3_corrected_inverted.tif`) per the standing convention, though this change
   should not alter them by a single pixel — which is the point.

Nothing here needs the scanner.
