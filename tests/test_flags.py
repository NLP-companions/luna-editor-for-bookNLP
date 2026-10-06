"""Flags: a "check this later" mark, with an optional note, on a mention, a quote, a group or a sentence."""
from __future__ import annotations

import unittest

from luna.core.errors import EditError
from tests.fixture import BookCase


class Flags(BookCase):
    def test_set_update_and_clear(self):
        i = self.uid_at(0, 0)  # "Holmes"
        self.assertIsNone(self.st.flag_of("entities", i))
        self.st.set_flag("entities", i, "check this name")
        f = self.st.flag_of("entities", i)
        self.assertEqual(f["note"], "check this name")
        self.st.set_flag("entities", i, "changed my mind")  # updates the same flag, doesn't duplicate it
        f2 = self.st.flag_of("entities", i)
        self.assertEqual((f2["uid"], f2["note"]), (f["uid"], "changed my mind"))
        self.assertEqual(len(self.st.flags_list()), 1)
        self.st.clear_flag("entities", i)
        self.assertIsNone(self.st.flag_of("entities", i))
        with self.assertRaises(EditError):
            self.st.clear_flag("entities", i)

    def test_every_kind(self):
        e = self.uid_at(0, 0)
        q = self.uid_at(1, 0, 5, table="quotes")
        self.st.set_flag("entities", e)
        self.st.set_flag("quotes", q)
        self.st.set_flag("groups", 1)  # Watson
        self.st.set_flag("sentence", self.st.uid_of_ord(0))  # sentence 0's first token
        items = self.st.flags_list()
        self.assertEqual({it["kind"] for it in items}, {"entities", "quotes", "groups", "sentence"})
        by_kind = {it["kind"]: it for it in items}
        self.assertEqual(by_kind["entities"]["label"], "Mention “Holmes”")
        self.assertEqual((by_kind["entities"]["s"], by_kind["entities"]["e"]), (0, 0))
        self.assertEqual(by_kind["groups"]["label"], "Group “Watson”")
        self.assertIsNone(by_kind["groups"]["s"])
        self.assertEqual(by_kind["sentence"]["label"], "Sentence 0")
        self.assertIn("Holmes", by_kind["sentence"]["text"])

        with self.assertRaises(EditError):
            self.st.set_flag("nonsense", 0)

    def test_undoable_but_not_in_the_change_log(self):
        i = self.uid_at(0, 0)
        r = self.st.set_flag("entities", i, "later")
        self.assertIn("Flagged", r["undo"])
        kind = self.st.db.execute("SELECT kind FROM batches ORDER BY id DESC LIMIT 1").fetchone()[0]
        self.assertEqual(kind, "review")
        self.st.undo()
        self.assertIsNone(self.st.flag_of("entities", i))

    def test_a_flag_on_something_deleted_drops_out_of_the_list_not_the_table(self):
        i = self.uid_at(7, 0, 1)  # "The violin" (a FAC entity, safe to delete without side effects)
        self.st.set_flag("entities", i, "check this")
        self.st.delete_span("entities", i)
        self.assertEqual(self.st.flags_list(), [])
        self.assertIsNotNone(self.st._flag_row("entities", i))  # the row itself is left behind


if __name__ == "__main__":
    unittest.main()
