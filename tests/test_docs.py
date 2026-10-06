"""Every function is described: a docstring in Python, a comment above it in JavaScript.

This keeps the standard set for the code base (see docs/DOCUMENTATION.md §11) from slipping when features are added.
"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# BookNLP's own function, kept verbatim (tabs and all) so it can be compared with BookNLP's copy
VERBATIM = {("luna/core/bookfile.py", "_get_syntax"), ("luna/core/bookfile.py", "check_conj"), ("luna/core/bookfile.py", "get_head_in_range")}
DECL = re.compile(r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(|^(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=")


class Python(unittest.TestCase):
    def test_every_class_and_function_has_a_docstring(self):
        missing = []
        for path in sorted((ROOT / "luna").rglob("*.py")):
            rel = str(path.relative_to(ROOT))
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if not ast.get_docstring(node) and (rel, node.name) not in VERBATIM:
                        missing.append(f"{rel}:{node.lineno} {node.name}")
            if path.name != "__init__.py" or path.stat().st_size:
                if not ast.get_docstring(tree):
                    missing.append(f"{rel}: module docstring")
        self.assertEqual(missing, [], "add a docstring saying what it does and how")


class JavaScript(unittest.TestCase):
    def test_every_top_level_function_has_a_comment_above_it(self):
        missing = []
        for path in sorted((ROOT / "luna" / "static" / "js").glob("*.js")):
            lines = path.read_text().split("\n")
            for i, line in enumerate(lines):
                m = DECL.match(line)
                if not m:
                    continue
                j = i - 1
                while j >= 0 and not lines[j].strip():
                    j -= 1
                above = lines[j].strip() if j >= 0 else ""
                described = above.startswith(("/*", "//")) or above.endswith("*/")
                if not described or above.startswith("/* ----"):  # a section heading doesn't describe the function under it
                    missing.append(f"{path.name}:{i + 1} {m.group(1) or m.group(2)}")
        self.assertEqual(missing, [], "add a one-line comment saying what it does")

    def test_every_script_starts_with_a_description(self):
        for path in sorted((ROOT / "luna" / "static" / "js").glob("*.js")):
            head = path.read_text().split("\n")[:3]
            self.assertTrue(any(l.startswith("/*") for l in head), f"{path.name} needs a header comment")


if __name__ == "__main__":
    unittest.main()
