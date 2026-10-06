"""Every core module imports on its own, in a fresh interpreter: a circular import between modules only shows up when the
wrong one happens to be imported first (as core/tools/coref.py and core/matching.py once did)."""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Imports(unittest.TestCase):
    def test_each_core_module_imports_first(self):
        mods = [".".join(p.relative_to(ROOT).with_suffix("").parts) for p in sorted((ROOT / "luna" / "core").rglob("*.py"))]
        code = "import importlib, sys\nfor m in sys.argv[1:]:\n    importlib.import_module(m)"
        failed = []
        for m in mods:
            r = subprocess.run([sys.executable, "-c", code, m], cwd=ROOT, capture_output=True, text=True)
            if r.returncode:
                failed.append(f"{m}: {r.stderr.strip().splitlines()[-1]}")
        self.assertEqual(failed, [])


if __name__ == "__main__":
    unittest.main()
