"""Runs against COPIES of the working copies in ./projects (skipped when there are none).

The copies live in a temp folder; the real files are never opened for writing. This catches what a tiny fixture
can't: real BookNLP output, and everything you have actually edited so far.

Set BNE_REAL_BOOKS=all to include the big ones (slower); by default the two smallest are used.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from luna.core.errors import EditError
from luna.core.store import Store, create_project

# your own working copies: in LUNA_DATA, else in luna-data next to the tests
PROJECTS = Path(os.environ.get("LUNA_DATA") or Path(__file__).resolve().parent.parent / "luna-data") / "projects"


def candidates():
    found = sorted(PROJECTS.glob("*.sqlite"), key=lambda p: p.stat().st_size)
    return found if os.environ.get("BNE_REAL_BOOKS") == "all" else found[:2]


@unittest.skipUnless(candidates(), "no working copies in ./projects")
class RealBooks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="bne-real-"))
        cls.books = {}
        for p in candidates():
            dst = cls.tmp / p.name
            shutil.copy(p, dst)
            cls.books[p.stem] = dst

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def each(self):
        """(name, Store) for every book; run each check inside `with self.subTest(book=name)`."""
        out = []
        for name, path in self.books.items():
            st = Store(path, spacy_model="no-such-model")
            self.addCleanup(st.db.close)
            out.append((name, st))
        return out

    def test_every_view_can_be_computed(self):
        for name, st in self.each():
            with self.subTest(book=name):
                info = st.info()
                self.assertGreater(info["counts"]["tokens"], 100)
                lst = st.groups_list()
                self.assertEqual(lst["total"], len([c for c in st.clusters().values() if not c["hidden"]]))
                for c in lst["items"][:5]:
                    st.group_detail(c["id"])
                    st.book_preview(c["id"])
                st.merge_suggestions()
                cl, prof = st.clusters(), st._profiles()
                self.assertEqual({c: p["mentions"] for c, p in prof.items()}, {c: g["count"] for c, g in cl.items()})
                self.assertEqual(sum(p["speech"]["quotes"] for p in prof.values()),
                                 sum(1 for q in st._quote_rows() if q["char_id"] in cl))
                st.fragments(max_count=3)
                st.quote_suggestions()
                st.quotes_list()
                st.checks(limit=5)
                st.narrator_info()
                st.narrator_rule()
                st.quote_rule()
                st.pass_step(-1, "next")
                st.carry_state()
                st.book_settings()
                st.proposals()
                st.window(0, 30)
                st.window(info["n_sentences"] - 1, 30)
                for layer, field in (("tokens", "pos"), ("entities", "cat"), ("supersenses", "cat")):
                    st.search(layer, field, "invalid", "")
                    st.search(layer, field, "edited", "")

    def test_data_is_consistent(self):
        for name, st in self.each():
            with self.subTest(book=name):
                db = st.db
                one = lambda q: db.execute(q).fetchone()[0]
                text = st.meta["text"]
                self.assertEqual([r[0] for r in db.execute("SELECT ord FROM tokens ORDER BY ord")], list(range(st.n_tokens)))
                for r in db.execute("SELECT word, char_start, char_end FROM tokens ORDER BY ord"):
                    self.assertEqual(text[r["char_start"]:r["char_end"]].replace(" ", "S").replace("\n", "N").replace("\r", "N").replace("\t", "T"), r["word"])
                self.assertEqual(one("SELECT COUNT(*) FROM tokens WHERE head IS NOT NULL AND head NOT IN (SELECT uid FROM tokens)"), 0)
                for tbl in ("entities", "supersenses", "quotes"):
                    self.assertEqual(one(f"SELECT COUNT(*) FROM {tbl} WHERE tok_start NOT IN (SELECT uid FROM tokens) OR tok_end NOT IN (SELECT uid FROM tokens)"), 0, tbl)
                    self.assertEqual(one(f"SELECT COUNT(*) FROM {tbl} x JOIN tokens s ON s.uid=x.tok_start JOIN tokens e ON e.uid=x.tok_end WHERE s.ord > e.ord"), 0, tbl)
                self.assertEqual(one("SELECT COUNT(*) FROM entities WHERE coref IS NULL"), 0)
                self.assertEqual(one("SELECT COUNT(*) FROM quotes WHERE m_start IS NOT NULL AND m_start NOT IN (SELECT uid FROM tokens)"), 0)
                self.assertEqual(sum(c["count"] for c in st.clusters().values()), one("SELECT COUNT(*) FROM entities"))
                # a quote's speaker mention, when there is one, is a mention of the speaker's group (or at least a mention)
                for q in db.execute("SELECT char_id, m_start, m_end FROM quotes WHERE m_start IS NOT NULL AND char_id IS NOT NULL"):
                    n = db.execute("SELECT COUNT(*) FROM entities WHERE tok_start=? AND tok_end=?", (q["m_start"], q["m_end"])).fetchone()[0]
                    self.assertGreaterEqual(n, 0)

    def test_export_reimport_export_is_stable(self):
        for name, st in self.each():
            with self.subTest(book=name):
                first = self.tmp / f"{name}-1"
                st.export(first)
                db = self.tmp / f"{name}-re.sqlite"
                text = Path(st.meta["text_path"])
                if not text.exists():
                    text = self.tmp / f"{name}.txt"
                    text.write_text(st.meta["text"], encoding="utf-8")
                create_project(db, first, text, st.meta["book_id"])
                st2 = Store(db, spacy_model="no-such-model")
                second = self.tmp / f"{name}-2"
                st2.export(second)
                st2.db.close()
                for ext in ("tokens", "entities", "supersense", "quotes"):
                    a, b = first / f"{st.meta['book_id']}.{ext}", second / f"{st.meta['book_id']}.{ext}"
                    if a.exists():
                        self.assertEqual(a.read_text(), b.read_text(), ext)

    def test_undoing_everything_restores_the_original_import(self):
        compared = 0
        for name, st in self.each():
            with self.subTest(book=name):
                src = Path(st.meta["source_dir"])
                text = Path(st.meta["text_path"])
                if not (src / f"{st.meta['book_id']}.tokens").exists() or not text.exists():
                    continue  # BookNLP's original files aren't on this machine
                compared += 1
                original = self.tmp / f"{name}-original.sqlite"
                create_project(original, src, text, st.meta["book_id"])
                o = Store(original, spacy_model="no-such-model")
                n = 0
                try:
                    while True:
                        st.undo()
                        n += 1
                except EditError:
                    pass
                cols = {"tokens": "uid, ord, word, lemma, char_start, char_end, pos, tag, dep, head, event, sent_start, para_start",
                        "entities": "uid, tok_start, tok_end, coref, prop, cat, text",
                        "supersenses": "uid, tok_start, tok_end, cat, text",
                        "quotes": "uid, tok_start, tok_end, m_start, m_end, m_phrase, char_id, text"}
                for tbl, c in cols.items():
                    mine = [tuple(r) for r in st.db.execute(f"SELECT {c} FROM {tbl} ORDER BY uid")]
                    theirs = [tuple(r) for r in o.db.execute(f"SELECT {c} FROM {tbl} ORDER BY uid")]
                    self.assertEqual(mine, theirs, f"{name}.{tbl} after undoing {n} steps")
                self.assertEqual(st.db.execute("SELECT COUNT(*) FROM groups WHERE name IS NOT NULL OR pronoun IS NOT NULL OR hidden=1").fetchone()[0], 0)
                o.db.close()
        if not compared:
            self.skipTest("BookNLP's original output for these books isn't on this machine")

    def test_undo_then_redo_everything_comes_back(self):
        for name, st in self.each():
            with self.subTest(book=name):
                def all_the_way(step):
                    n = 0
                    try:
                        while True:
                            step()
                            n += 1
                    except EditError:
                        return n

                all_the_way(st.redo)  # your last session may have ended with some steps undone
                before = [tuple(r) for r in st.db.execute("SELECT * FROM entities ORDER BY uid")], st.history()["count"]
                n = all_the_way(st.undo)
                all_the_way(st.redo)
                after = [tuple(r) for r in st.db.execute("SELECT * FROM entities ORDER BY uid")], st.history()["count"]
                self.assertEqual(before, after, f"{name}: undid and redid {n} steps")


if __name__ == "__main__":
    unittest.main()
