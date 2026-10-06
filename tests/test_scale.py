"""A book big enough to show how things scale. Two parts:

* always run: a synthetic book of 3,000 sentences (28,000 tokens, 300 groups, 1,300 quotes): big edits stay correct and
  undoable, the export re-imports, nothing is slow;
* only with BNE_SCALE=1 (about 30 s): a book of 20,000 sentences (190,000 tokens, a long tail of 3,000 groups) with time
  limits on the things that used to be slow (each limit is about ten times what a laptop needs, so only a return of the
  old quadratic code trips it). `python -m tests.scale` prints all the timings.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from luna.core.store import Store, create_project
from tests.synthetic import write_book


def open_book(sentences, groups, distinct, seed):
    """Write a synthetic book into a temp folder, import it and open it: (folder, Store)."""
    tmp = Path(tempfile.mkdtemp(prefix="bne-scale-"))
    text = write_book(tmp, "s", sentences, seed=seed, groups=groups, zipf=1.05 if distinct else 0.9, distinct_names=distinct)
    create_project(tmp / "s.sqlite", tmp / "s", text)
    return tmp, Store(tmp / "s.sqlite", "no-such-model")


class MidSizeBook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.st = open_book(3000, 300, False, 3)

    @classmethod
    def tearDownClass(cls):
        cls.st.db.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def snapshot(self):
        """Every table that edits change, as plain data."""
        return {t: [tuple(r) for r in self.st.db.execute(f"SELECT * FROM {t} ORDER BY uid")]
                for t in ("tokens", "entities", "supersenses", "quotes", "groups")}

    def test_the_import_is_faithful(self):
        self.assertEqual(self.st.meta["n_offset_mismatches"], 0)
        self.assertEqual(self.st.meta["join_style"], {"entities": True, "supersenses": True, "quotes": True})
        self.assertGreater(self.st.info()["counts"]["quotes"], 1000)

    def test_big_edits_undo_and_redo_exactly(self):
        st = self.st
        top = sorted(st.clusters().values(), key=lambda c: -c["count"])
        quotes = [r[0] for r in st.db.execute("SELECT uid FROM quotes ORDER BY uid LIMIT 800")]
        for step in (lambda: st.set_speakers(quotes, top[0]["id"], True),
                     lambda: st.merge_groups(top[1]["id"], [c["id"] for c in top[2:30]]),
                     lambda: st.bulk_edit("tokens", "pos", "eq", "NOUN", None, "pos", "ADJ"),
                     lambda: st.bulk_create("tokens", "word", "eq", "said", None, "entities", {"cat": "VAR", "group_mode": "text"}),
                     lambda: st.bulk_delete("supersenses", "cat", "eq", "verb.social", None)):
            before = self.snapshot()
            step()
            after = self.snapshot()
            self.assertTrue(before != after, "the step changed nothing")
            st.undo()
            self.assertTrue(self.snapshot() == before, "undo didn't restore the book")
            st.redo()
            self.assertTrue(self.snapshot() == after, "redo didn't restore the step")
            st.undo()

    def test_every_speaker_link_is_the_nearest_mention_of_that_group(self):
        """Linking thousands of speakers uses a lookup instead of a scan; it must pick what the scan would."""
        st = self.st
        top = sorted(st.clusters().values(), key=lambda c: -c["count"])[0]["id"]
        quotes = [r[0] for r in st.db.execute("SELECT uid FROM quotes ORDER BY uid LIMIT 300")]
        st.set_speakers(quotes, top)
        try:
            mentions = [(r["s"], r["e"]) for r in st._ent_rows("WHERE x.coref=?", (top,))]
            checked = 0
            for q in st._quote_rows():
                row = st._row("quotes", q["uid"])
                if q["uid"] not in quotes or row["m_start"] is None:
                    continue
                s, e = st.ord_of(row["m_start"]), st.ord_of(row["m_end"])
                got = q["s"] - e if e < q["s"] else s - q["e"]
                want = min(q["s"] - b if b < q["s"] else a - q["e"] for a, b in mentions if not (a <= q["e"] and b >= q["s"]))
                self.assertEqual(got, want)
                checked += 1
            self.assertGreater(checked, 100)
        finally:
            st.undo()

    def test_export_and_reimport_keep_every_count(self):
        out = self.tmp / "exported"
        self.st.export(out)
        db = self.tmp / "again.sqlite"
        create_project(db, out, self.tmp / "s.txt", "s")
        again = Store(db, "no-such-model")
        self.addCleanup(again.db.close)
        self.assertEqual(again.info()["counts"], self.st.info()["counts"])
        self.assertEqual({c["id"]: c["count"] for c in again.clusters().values()}, {c["id"]: c["count"] for c in self.st.clusters().values()})

    def test_every_view_answers(self):
        st = self.st
        for call in (st.merge_suggestions, st.fragments, st.quote_suggestions, st.checks, st.narrator_info, st.quote_rule,
                     lambda: st.pass_step(-1, "next"), lambda: st.qpass_step(-1, "next"), st.groups_list, st.quotes_list):
            call()


@unittest.skipUnless(os.environ.get("BNE_SCALE"), "set BNE_SCALE=1 for the 190,000-token book")
class LongBook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.st = open_book(20000, 3000, True, 2)
        cls.top = sorted(cls.st.clusters().values(), key=lambda c: -c["count"])

    @classmethod
    def tearDownClass(cls):
        cls.st.db.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def within(self, seconds, fn, *args):
        """Run fn and fail when it takes longer than `seconds`."""
        t = time.perf_counter()
        fn(*args)
        took = time.perf_counter() - t
        self.assertLess(took, seconds, f"{getattr(fn, '__name__', fn)} took {took:.1f}s")

    def test_the_edits_that_used_to_scan_the_whole_book_per_item(self):
        st, top = self.st, self.top
        quotes = [r[0] for r in st.db.execute("SELECT uid FROM quotes ORDER BY uid LIMIT 3000")]
        self.within(5, st.set_speakers, quotes, top[0]["id"])                   # was 21 s
        self.within(5, st.set_speakers, quotes, top[1]["id"], True)             # was 13 s
        self.within(5, st.bulk_create, "tokens", "word", "eq", "said", None, "entities",
                    {"cat": "VAR", "prop": "PROP", "group_mode": "text"})       # was 14 s
        self.within(5, st.regroup, [r[0] for r in st.db.execute("SELECT uid FROM entities WHERE coref=? LIMIT 3000", (top[2]["id"],))], top[3]["id"])

    def test_the_views_and_suggestions(self):
        st = self.st
        self.within(10, st.merge_suggestions)                                  # was 3.5 s on a book with a long tail; ~1 s now
        self.within(10, st.quote_suggestions)
        self.within(5, st.window, 1000, 200)
        self.within(5, st.search, "tokens", "pos", "eq", "NOUN")
        self.within(10, st.export, self.tmp / "exported")


if __name__ == "__main__":
    unittest.main()
