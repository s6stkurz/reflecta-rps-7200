"""Shared test setup.

``RPS7200_NO_TIFFFILE=1`` makes ``import tifffile`` fail for the whole run, so
the suite can be executed as it would be on a bare install::

    python3 -m pytest tests/ -q                       # tifffile present
    RPS7200_NO_TIFFFILE=1 python3 -m pytest tests/ -q # tifffile absent

Both must pass. ``tests/test_tiff.py`` monkeypatches ``_has_tifffile`` per test,
which is what makes the cross-implementation matrix possible, but a monkeypatch
only proves each call site picks the right branch. This proves the *package*
works with the dependency genuinely missing -- that no other module imports
tifffile behind the driver's back, and that the optional dependency really is
optional.
"""

import os
import sys
from importlib.abc import MetaPathFinder


class _BlockTifffile(MetaPathFinder):
    """Refuse tifffile the way an absent install does.

    ModuleNotFoundError rather than a bare ImportError, because that is what a
    missing package raises -- and ``pytest.importorskip`` re-raises anything
    else rather than skipping, on the grounds that a broken install should not
    look like an absent one.
    """

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "tifffile" or fullname.startswith("tifffile."):
            raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
        return None


if os.environ.get("RPS7200_NO_TIFFFILE"):
    sys.modules.pop("tifffile", None)
    sys.meta_path.insert(0, _BlockTifffile())
