"""Host-side work must not need libusb.

Decoding a stored scan, merging a bracket, writing a TIFF and reading the
library all touch no hardware, and the library keeps every scan's raw bytes so
that work can be re-run anywhere. None of it was possible on a machine without
libusb, because the module loaded it at import.

These run with the loader forced to fail, which is what an absent install looks
like.
"""

import subprocess
import sys
import textwrap

import pytest

# Each runs in its own interpreter: the loaders cache, and the modules are
# already imported in this one.
PRELUDE = textwrap.dedent(
    """
    import ctypes.util, os.path, sys
    sys.path.insert(0, {root!r})

    # Simulate an absent install, and do it BEFORE rps7200 is imported: the
    # loader ran at import on the old code, so patching afterwards would let the
    # real library load and prove nothing.
    _exists = os.path.exists

    def exists(path):
        if os.path.basename(str(path)).startswith("libusb-1.0"):
            return False
        return _exists(path)

    os.path.exists = exists
    ctypes.util.find_library = lambda name: None
    """
)


def run(body, root):
    """Run `body` with both loaders unable to find anything."""
    code = PRELUDE.format(root=root) + textwrap.dedent(body)
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )


@pytest.fixture
def root(pytestconfig):
    return str(pytestconfig.rootpath)


def test_the_package_imports_without_libusb(root):
    out = run("import rps7200; print('ok', rps7200.__version__)", root)
    assert out.returncode == 0, out.stderr
    assert "ok" in out.stdout


@pytest.mark.parametrize(
    "module",
    ["rps7200.tiff", "rps7200.library", "rps7200.bracket", "rps7200.shading",
     "rps7200.defects", "rps7200.framing", "rps7200.protocol", "rps7200.direct"],
)
def test_every_host_side_module_imports(root, module):
    out = run(f"import {module}; print('ok')", root)
    assert out.returncode == 0, out.stderr


def test_a_tiff_round_trips_without_libusb(root):
    out = run(
        """
        import numpy as np, tempfile, os
        from rps7200 import tiff
        img = (np.arange(4 * 5 * 3, dtype=np.uint16).reshape(4, 5, 3))
        path = os.path.join(tempfile.mkdtemp(), "x.tif")
        tiff.write(path, img)
        assert np.array_equal(tiff.read(path), img)
        print('ok')
        """,
        root,
    )
    assert out.returncode == 0, out.stderr


def test_a_bracket_merges_without_libusb(root):
    out = run(
        """
        import numpy as np
        from rps7200.bracket import merge_bracket
        a = np.full((8, 8, 3), 1000, np.uint16)
        b = np.full((8, 8, 3), 4000, np.uint16)
        out, stats = merge_bracket([a, b], [1.0, 4.0])
        assert out.shape == (8, 8, 3) and stats.passes == 2
        print('ok')
        """,
        root,
    )
    assert out.returncode == 0, out.stderr


def test_opening_a_transport_still_says_what_to_install(root):
    """Lazy must not mean silent: the failure has to name the missing library."""
    out = run(
        """
        from rps7200.usb_transport import Transport
        try:
            Transport()
        except OSError as exc:
            print("raised:", exc)
        else:
            raise AssertionError("expected an OSError")
        """,
        root,
    )
    assert out.returncode == 0, out.stderr
    assert "libusb" in out.stdout and "LIBUSB_PATH" in out.stdout
