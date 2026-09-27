# Where the documentation and the code disagree

[Back to the summary](README.md)

Every claim in README.md, CLAUDE.md, docs/*.md, TODO.md, docstrings and comments that the code
at `03aacba` contradicts. The docs were brought up to the code once already, in the second fix
round; what remains is mostly where the code moved again, or where a doc states a guarantee the
code only partly keeps. {{DOC_COUNT}} findings are filed as doc mismatches, grouped by the file
that holds the claim, and {{DOC_OTHER_COUNT}} findings in other categories also cite a doc
claim.

The ones that matter most:

- **The debug spool.** The docs promise a spool that survives a crash and files everything; the
  code keeps it in the OS temp directory, deletes a claimed pass before its filing is
  confirmed, and leaves it unfiled after a force-abort (DOC-06, DOC-07 in docs-readme-claude).
- **A real roll frame's `prescan.tif`** is stored corrected, with no bytes, contrary to "the
  library holds raw pixels" (DOC-01).
- **"Nothing is compressed with the device open"** holds for the library entry only; delivered
  copies and the last frames of a roll are compressed with it open and idle (DOC-05, CSA-07).
- **Ctrl-C "finishes the pass in flight"**: `tools/scan.py` ignores the first press on a
  single scan, and `scan_roll` finishes the frame, not the pass (DOC-02).
- **The pre-run estimate** is described as the library medians and is a fit that sits below
  them (DOC-03, CLI-08).
- **The infrared plane**: the docs disagree with each other on whether it is corrected
  (DOC-01 in docs-plans-todo).

{{DOC_TABLES}}

## Findings in other categories that cite a doc

{{DOC_OTHER}}
