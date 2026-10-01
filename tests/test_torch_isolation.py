"""The driver must never need torch.

torch is in the environment for research/frame-position-net only (the `nn`
dependency group). The driver, the window's modules and the edge reader have
to import and run without it: torch is hundreds of megabytes, and on an Intel
Mac it is pinned to a release from 2024. A stray `import torch` in rps7200
would make all of that a requirement of scanning.

Each check runs in its own interpreter, because this one may already hold
torch from another test.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_driver_and_the_edge_reader_import_without_torch():
    code = textwrap.dedent(
        """
        import importlib, pkgutil, sys
        sys.path.insert(0, {root!r})
        import rps7200
        for info in pkgutil.iter_modules(rps7200.__path__):
            importlib.import_module("rps7200." + info.name)
        import tools.frame_edges
        for info in pkgutil.iter_modules(tools.frame_edges.__path__):
            importlib.import_module("tools.frame_edges." + info.name)
        loaded = sorted(m for m in sys.modules if m == "torch" or m.startswith("torch."))
        print(len(loaded), loaded[:5])
        """
    ).format(root=str(ROOT))
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          cwd=ROOT, timeout=300)
    assert done.returncode == 0, done.stderr
    assert done.stdout.split()[0] == "0", done.stdout
