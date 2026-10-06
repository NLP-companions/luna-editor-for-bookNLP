"""Checks on the front end that don't need a browser: scripts parse, and the API and the front end agree."""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from luna import app as editor_app

STATIC = Path(__file__).resolve().parent.parent / "luna" / "static"
SCRIPTS = sorted((STATIC / "js").glob("*.js"))


class Scripts(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node isn't installed")
    def test_every_script_parses(self):
        for js in SCRIPTS:
            r = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{js.name}: {r.stderr}")

    def test_pages_load_the_scripts_that_exist(self):
        pages = ("index.html", "library.html", "settings.html")
        for page in pages:
            html = (STATIC / page).read_text()
            for src in re.findall(r'(?:src|href)="/static/([^"]+)"', html):
                self.assertTrue((STATIC / src).exists(), f"{page} refers to /static/{src}")
        loaded = set(re.findall(r'src="/static/([^"]+\.js)"', "".join((STATIC / page).read_text() for page in pages)))
        self.assertEqual(loaded, {f"js/{p.name}" for p in SCRIPTS}, "every script is used by a page")


class ApiContract(unittest.TestCase):
    """The front end calls api(METHOD, '/api/...') or links to a download (href="/api/..."); each call must match a route that
    allows that method, and vice versa."""

    @classmethod
    def setUpClass(cls):
        home = Path(tempfile.mkdtemp(prefix="bne-static-"))
        cls.addClassCleanup(shutil.rmtree, home, ignore_errors=True)
        app = editor_app.build_app(editor_app.Holder(home=home))
        cls.routes = [(re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", r.path) + "$"), r.methods) for r in app.routes
                      if r.path.startswith("/api/")]

    def calls(self):
        pat = re.compile(r"""(?:api|structural)\(\s*'(GET|POST|PUT|PATCH|DELETE)'\s*,\s*([`'])(/api/[^`'?]*)""")
        link = re.compile(r'href="(/api/[^"?]*)')
        for js in SCRIPTS:
            text = js.read_text()
            for m in pat.finditer(text):
                yield js.name, m.group(1), re.sub(r"\$\{[^}]*\}", "X", m.group(3))
            for m in link.finditer(text):
                yield js.name, "GET", re.sub(r"\$\{[^}]*\}", "X", m.group(1))

    def test_every_call_has_a_route(self):
        seen = 0
        for name, method, url in self.calls():
            seen += 1
            allowed = [ms for rx, ms in self.routes if rx.match(url)]
            self.assertTrue(allowed, f"{name}: {method} {url} matches no route")
            self.assertTrue(any(method in ms for ms in allowed), f"{name}: {url} doesn't allow {method}")
        self.assertGreater(seen, 100)

    def test_no_route_is_left_without_a_caller(self):
        used = {(m, u) for _, m, u in self.calls()}
        orphans = [rx.pattern for rx, methods in self.routes if not any(rx.match(u) and m in methods for m, u in used)]
        self.assertEqual(orphans, [], "routes nothing in the front end calls")


if __name__ == "__main__":
    unittest.main()
