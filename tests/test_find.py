"""Find: phrase search, context filters, and creating or deleting spans from the results (core/tools/find.py).

The fixture book (see fixture.py) has 8 sentences. Two quotes ("I am tired," and "You look well, Holmes,"), the
entities Holmes x3, Watson x2, Mrs. Hudson, pronouns and two FAC nouns, and three supersenses.
"""
from __future__ import annotations

import unittest

from luna.core.errors import EditError
from tests.fixture import SENT_START, BookCase


class Phrase(BookCase):
    def test_finds_the_words_in_a_row_ignoring_case(self):
        r = self.st.search("tokens", "word", "phrase", "mrs. HUDSON")
        self.assertEqual([(h["s"], h["e"], h["value"]) for h in r["hits"]], [(SENT_START[3], SENT_START[3] + 1, "Mrs. Hudson")])
        self.assertEqual(r["total"], 1)
        self.assertEqual(self.st.search("tokens", "word", "phrase", "Holmes")["total"], 3)  # one word is a phrase too
        self.assertEqual(self.st.search("tokens", "word", "phrase", "Hudson Mrs.")["total"], 0)  # order matters

    def test_can_match_any_token_field_for_example_a_tag_pattern(self):
        r = self.st.search("tokens", "pos", "phrase", "PRON VERB")  # He sat, You look, she said, She helped
        self.assertEqual(r["total"], 4)
        self.assertEqual(self.st.search("tokens", "lemma", "phrase", "say")["total"], 2)

    def test_matches_never_overlap(self):
        hits = self.st.search("tokens", "pos", "phrase", "PUNCT PUNCT")["hits"]  # `, "` inside quotes, `. "` across sentences
        self.assertEqual(len(hits), 4)
        used = [o for h in hits for o in range(h["s"], h["e"] + 1)]
        self.assertEqual(len(used), len(set(used)))

    def test_needs_words_and_tokens(self):
        for layer, value in (("tokens", "  "), ("entities", "Holmes")):
            with self.assertRaises(EditError):
                self.st.search(layer, "word" if layer == "tokens" else "text", "phrase", value)


class ContextFilters(BookCase):
    def test_inside_quotes_or_in_narration(self):
        find = lambda where: self.st.search("tokens", "word", "eq", "Holmes", where=where)["total"]
        self.assertEqual((find(""), find("quote"), find("narration")), (3, 1, 2))  # one Holmes is addressed inside a quote

    def test_without_an_entity_yet(self):
        nouns = lambda without: [self.st.text_between(h["s"], h["e"])
                                 for h in self.st.search("tokens", "pos", "eq", "NOUN", without=without)["hits"]]
        self.assertEqual(nouns(""), ["tea", "violin", "table"])
        self.assertEqual(nouns("entities"), ["tea"])  # the other two are inside "The violin" and "the table"
        self.assertEqual(nouns("supersenses"), ["tea", "table"])  # "violin" has noun.artifact

    def test_filters_combine_and_reach_bulk_edits(self):
        self.assertEqual(self.st.search("tokens", "pos", "eq", "PUNCT", where="quote", without="entities")["total"], 7)  # 3 in the first quote, 4 in the second
        self.st.bulk_edit("tokens", "word", "eq", "Holmes", None, "lemma", "H.", where="narration")
        self.assertEqual(self.st.search("tokens", "lemma", "eq", "H.")["total"], 2)  # the Holmes in the quote is untouched

    def test_unknown_filter_is_refused(self):
        with self.assertRaises(EditError):
            self.st.search("tokens", "word", "eq", "x", where="nowhere")
        with self.assertRaises(EditError):
            self.st.search("tokens", "word", "eq", "x", without="tokens")


class CreateEntities(BookCase):
    def create(self, group_mode, coref=None, **kw):
        # both spoken "said" tokens: two results with the same text
        return self.st.bulk_create("tokens", "lemma", "eq", "say", None, "entities",
                                   {"cat": "VAR", "prop": "NOM", "group_mode": group_mode, "coref": coref}, **kw)

    def new_ones(self):
        return [self.ent(u) for u in [r[0] for r in self.st.db.execute("SELECT uid FROM entities WHERE cat='VAR' ORDER BY uid")]]

    def test_all_results_into_one_new_group(self):
        r = self.create("one")
        self.assertEqual((r["created"], r["skipped"]), (2, 0))
        cats = self.new_ones()
        self.assertEqual({(e["cat"], e["prop"], e["text"]) for e in cats}, {("VAR", "NOM", "said")})
        self.assertEqual(len({e["coref"] for e in cats}), 1)
        self.assertNotIn(cats[0]["coref"], {0, 1, 2, 3, 4})  # a group of its own
        self.assertEqual(r["history"]["undo"], "Created 2 VAR NOM entities “said”")

    def test_all_results_into_an_existing_group(self):
        self.create("one", coref=1)
        self.assertEqual({e["coref"] for e in self.new_ones()}, {1})
        with self.assertRaises(EditError):
            self.create("one", coref=999)

    def test_one_group_per_distinct_text_or_per_result(self):
        self.st.bulk_create("tokens", "pos", "eq", "PUNCT", None, "entities", {"cat": "VAR", "group_mode": "text"},
                            where="narration")  # `.` `.` … and `,` — texts differ, equal texts share a group
        groups = {}
        for e in self.new_ones():
            groups.setdefault(e["text"], set()).add(e["coref"])
        self.assertTrue(all(len(g) == 1 for g in groups.values()))
        self.assertEqual(len({next(iter(g)) for g in groups.values()}), len(groups))
        self.st.undo()
        self.create("each")
        self.assertEqual(len({e["coref"] for e in self.new_ones()}), 2)

    def test_results_with_an_entity_of_the_same_bounds_are_skipped(self):
        # "Mrs." and "Hudson" alone are new (the entity is "Mrs. Hudson"); the five Holmes and Watson tokens already are entities
        r = self.st.bulk_create("tokens", "pos", "eq", "PROPN", None, "entities", {"cat": "VAR", "prop": "NOM"})
        self.assertEqual((r["created"], r["skipped"]), (2, 5))
        with self.assertRaises(EditError):  # now every PROPN token is one
            self.st.bulk_create("tokens", "pos", "eq", "PROPN", None, "entities", {"cat": "VAR"})

    def test_a_multi_word_phrase_becomes_one_entity_and_undoes_in_one_step(self):
        before = self.snapshot()
        r = self.st.bulk_create("tokens", "word", "phrase", "tired , \"", None, "entities", {"cat": "VAR", "prop": "NOM"})
        self.assertEqual(r["created"], 1)
        e = self.new_ones()[0]
        self.assertEqual(e["text"], "tired , \"")
        self.assertEqual(self.st.history()["items"][0]["label"], r["history"]["undo"])
        self.st.undo()
        self.assertEqual(self.snapshot(), before)
        self.st.redo()
        self.assertEqual(len(self.new_ones()), 1)

    def test_only_the_ticked_results(self):
        hits = self.st.search("tokens", "lemma", "eq", "say")["hits"]
        r = self.st.bulk_create("tokens", "lemma", "eq", "say", [hits[1]["uid"]], "entities", {"cat": "VAR"})
        self.assertEqual(r["created"], 1)
        self.assertEqual(self.new_ones()[0]["tok_start"], self.st.uid_of_ord(hits[1]["s"]))

    def test_the_type_is_needed_and_the_source_can_be_any_layer(self):
        with self.assertRaises(EditError):
            self.st.bulk_create("tokens", "lemma", "eq", "say", None, "entities", {"cat": ""})
        # supersense results become entities over the same words ("violin", inside the entity "The violin")
        r = self.st.bulk_create("supersenses", "cat", "eq", "noun.artifact", None, "entities", {"cat": "VAR"})
        self.assertEqual((r["created"], r["skipped"], self.new_ones()[0]["text"]), (1, 0, "violin"))


class CreateSupersensesAndQuotes(BookCase):
    def test_supersenses_where_there_is_none_yet(self):
        r = self.st.bulk_create("tokens", "pos", "eq", "NOUN", None, "supersenses", {"cat": "noun.food"}, without="supersenses")
        self.assertEqual(r["created"], 2)
        self.assertEqual(self.st.search("supersenses", "cat", "eq", "noun.food")["total"], 2)
        with self.assertRaises(EditError):
            self.st.bulk_create("tokens", "pos", "eq", "NOUN", None, "supersenses", {"cat": ""})

    def test_quotes_with_a_speaker(self):
        r = self.st.bulk_create("tokens", "word", "phrase", "Mrs. Hudson smiled", None, "quotes", {"char_id": 2})
        self.assertEqual(r["created"], 1)
        q = self.st.search("quotes", "text", "contains", "Mrs. Hudson smiled")["hits"][0]
        self.assertEqual(self.st._row("quotes", q["uid"])["char_id"], 2)
        self.assertIn("spoken by", r["history"]["undo"])

    def test_quotes_never_overlap_an_existing_quote(self):
        with self.assertRaises(EditError):  # "tired" is inside the first quote
            self.st.bulk_create("tokens", "word", "eq", "tired", None, "quotes", {})
        r = self.st.bulk_create("tokens", "lemma", "eq", "say", None, "quotes", {})  # "said" is outside both
        self.assertEqual((r["created"], r["skipped"]), (2, 0))
        self.assertEqual(self.st.search("quotes", "text", "eq", "said")["total"], 2)

    def test_unknown_targets_and_speakers_are_refused(self):
        with self.assertRaises(EditError):
            self.st.bulk_create("tokens", "lemma", "eq", "say", None, "tokens", {})
        with self.assertRaises(EditError):
            self.st.bulk_create("tokens", "lemma", "eq", "say", None, "quotes", {"char_id": 999})


class Delete(BookCase):
    def test_delete_all_results_and_undo_in_one_step(self):
        before = self.snapshot()
        r = self.st.bulk_delete("entities", "cat", "eq", "FAC", None)
        self.assertEqual(r["deleted"], 2)
        self.assertEqual(self.st.search("entities", "cat", "eq", "FAC")["total"], 0)
        self.assertEqual(r["history"]["undo"], "Deleted 2 entities")
        self.st.undo()
        self.assertEqual(self.snapshot(), before)

    def test_only_the_ticked_results(self):
        hits = self.st.search("supersenses", "cat", "contains", "verb")["hits"]
        self.st.bulk_delete("supersenses", "cat", "contains", "verb", [hits[0]["uid"]])
        self.assertEqual(self.st.search("supersenses", "cat", "contains", "verb")["total"], 1)

    def test_a_deleted_speaker_mention_is_unlinked_from_its_quote(self):
        q = self.uid_at(1, 0, 5, table="quotes")
        self.assertIsNotNone(self.st._row("quotes", q)["m_start"])
        self.st.bulk_delete("entities", "text", "eq", "Watson", [self.uid_at(1, 7)])
        row = self.st._row("quotes", q)
        self.assertEqual((row["m_start"], row["char_id"]), (None, 1))  # the speaker stays, the link to the mention goes

    def test_quotes_can_be_deleted_by_a_search_and_tokens_cannot(self):
        r = self.st.bulk_delete("quotes", "text", "contains", "look", None)
        self.assertEqual(r["deleted"], 1)
        with self.assertRaises(EditError):
            self.st.bulk_delete("tokens", "word", "eq", "Holmes", None)
        with self.assertRaises(EditError):
            self.st.bulk_delete("quotes", "text", "contains", "no such quote", None)


if __name__ == "__main__":
    unittest.main()
