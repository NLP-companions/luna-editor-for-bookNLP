"""The example book in examples/ imports cleanly: its text matches BookNLP's offsets, and it can be exported."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from luna.core.store import Store, create_project

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "three-gables"


class Example(unittest.TestCase):
    def test_the_example_book_imports_and_exports(self):
        home = Path(tempfile.mkdtemp(prefix="bne-example-"))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        db = home / "example.sqlite"
        self.assertEqual(create_project(db, EXAMPLE, EXAMPLE / "three-gables.txt"), "three-gables")  # offsets are checked on import
        st = Store(db, "no-such-model")
        self.addCleanup(st.db.close)
        self.assertGreater(len(st.clusters()), 5)
        out = Path(st.export(home / "exports")["path"])
        self.assertTrue((out / "three-gables.tokens").exists())


if __name__ == "__main__":
    unittest.main()
