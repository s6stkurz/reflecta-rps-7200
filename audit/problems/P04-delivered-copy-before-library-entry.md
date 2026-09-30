# P04 -- A failed delivered copy (output folder, rolls/) costs the library entry

**Severity** critical · **Group** A: Library exactness -- the central requirement · **Reported independently by** 6 findings in 6 areas

[Back to the summary](../README.md)

## The problem

`FrameWriter._write` writes every delivered copy (`export.write` for the output folder
and `rolls/<name>/frameNN.tif`) **before** `library.save`. Any exception there -- an
unplugged external drive, a read-only or deleted output folder, a full disk, a Windows
path problem -- propagates to `_run`, which logs it and moves on. The library entry, and
with it the raw bytes, is never written. The operator's copy is re-creatable from the
library; the library entry is not re-creatable from anything. The order is backwards.
Every failure test in the suite passes `library=None`, so none can see it.

## Fix plan

1. In `FrameWriter._write`, file the library entry first, then write delivered copies,
   each in its own `try` that appends to `notes` on failure.
2. Surface a failed delivered write as a visible warning (not only a log line), and say
   the library entry exists and can be exported again.
3. Test with a library root *and* an unwritable output path: the entry must exist.

## Evidence (from [LIB-03](../areas/library.md#library-lib-03))

**Where:** `rps7200/session.py:1060-1103`, `rps7200/session.py:1046-1058`, `rps7200/session.py:2067-2071`, `tools/gui.py:644-645`, `tools/scan_roll.py:542-580`

```text
`for path in job.get("paths") or (): Path(path).parent.mkdir(parents=True, exist_ok=True); note = export.write(str(path), delivered, ...)` precedes `entry = None / if job["library"]: ... entry = library.save(...)`. _run: `except Exception as exc: self.errors.append(f"picture {job['number']}: {exc}")` and the job is dropped. The output folder is remembered across launches (gui.py:644 `if self.remembered["output"] and self.session.out_dir is None: self._set_outdir(...)`).
```

**Failure scenario:** The GUI remembers an output folder on E:\ (a USB disk). Stefan scans a 38-frame roll with the disk unplugged. Every frame fails with a mkdir/OSError. The log says 'picture N could not be filed' and not one library entry exists, although every pass completed.

**Second reader's check:** session.py:1066-1082 runs preview.orient, to_monochrome and, for each path, mkdir plus export.write before `if job['library']: ... library.save` at 1085. Any exception in those steps propagates to _run (1049-1058), which records the error and drops the job. The capture dict (raw bytes, reference, mask) is lost with it. For a roll, paths includes the roll directory's frameNN.tif and the output folder copy (_file 2082-2086). A full disk, a permission error or a missing drive on the remembered output folder therefore costs the library entry of every frame.

## Every report of this problem

| Finding | Area | Severity | Verdict | Title | Where |
|---|---|---|---|---|---|
| [LIB-03](../areas/library.md#library-lib-03) | library | high | confirmed | FrameWriter writes delivered copies before the library entry; any export failure loses the raw bytes | rps7200/session.py:1060-1103, rps7200/session.py:1046-1058, rps7200/session.py:2067-2071 |
| [SR-02](../areas/session-roll.md#session-roll-sr-02) | session-roll | critical | confirmed | A failed write of the delivered TIFF/JPEG (output folder or rolls/) aborts the library entry: raw bytes of the scan are lost | rps7200/session.py:1060-1103, rps7200/session.py:1046-1058, rps7200/session.py:2067-2071 |
| [GUI1-02](../areas/gui-part1.md#gui-part1-gui1-02) | gui-part1 | critical | confirmed | An unwritable or missing output folder (or roll folder) silently costs the library entry, raw bytes included | rps7200/session.py:1074-1100, rps7200/session.py:1046-1058, tools/gui.py:643-647 |
| [OUT-02](../areas/outputs.md#outputs-out-02) | outputs | critical | partly | FrameWriter writes delivered files before the library entry, so any delivery failure loses the raw bytes | rps7200/session.py:1066-1095, rps7200/session.py:1045-1056, rps7200/export.py:181-193 |
| [T03](../areas/tests.md#tests-t03) | tests | high | confirmed | FrameWriter writes the operator's copy before the library entry, so a failing output path loses the raw bytes; the failure tests use library=None | rps7200/session.py:1060-1103, rps7200/session.py:1046-1058, tests/test_session.py:727-744 |
| [UT-07](../areas/uncited-tests-vs-findings.md#uncited-tests-vs-findings-ut-07) | uncited-tests-vs-findings | medium | partly | Every FrameWriter test that exercises a failed delivered write passes library=None, so losing the raw bytes when that write fails is never tested | rps7200/session.py:1048-1058, rps7200/session.py:1074-1097, tests/test_roll_writer.py:67-82 |
