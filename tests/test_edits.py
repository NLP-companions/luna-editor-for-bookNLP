"""Editing: tokens, spans, groups, undo/redo, structure changes."""
from __future__ import annotations

import unittest

from luna.core.errors import EditError
from tests.fixture import BookCase, N_TOKENS, SENT_START, ords


class TokenEdits(BookCase):
    def test_edit_marks_manual_and_undoes(self):
        before = self.snapshot()
        h = self.st.edit_token(0, {"lemma": "holm"})
        self.assertIn("lemma", h["undo"])
        row = self.st._row("tokens", 0)
        self.assertEqual(row["lemma"], "holm")
        self.assertIn("lemma", row["manual"])
        self.st.undo()
        self.assertEqual(self.snapshot(), before)
        self.st.redo()
        self.assertEqual(self.st._row("tokens", 0)["lemma"], "holm")

    def test_head_edit_uses_positions(self):
        self.st.edit_token(0, {"head_ord": 2})
        self.assertEqual(self.st._row("tokens", 0)["head"], self.st.uid_at[2])
        with self.assertRaises(EditError):
            self.st.edit_token(0, {"head_ord": 999})

    def test_rejects_unknown_and_empty_values(self):
        with self.assertRaises(EditError):
            self.st.edit_token(0, {"word": "x"})
        with self.assertRaises(EditError):
            self.st.edit_token(0, {"pos": "  "})

    def test_no_op_edit_records_nothing(self):
        self.st.edit_token(0, {"lemma": self.st._row("tokens", 0)["lemma"]})
        self.assertEqual(self.st.history()["count"], 0)

    def test_new_edit_discards_redo(self):
        self.st.edit_token(0, {"lemma": "a"})
        self.st.undo()
        self.assertIsNotNone(self.st.history()["redo"])
        self.st.edit_token(1, {"lemma": "b"})
        self.assertIsNone(self.st.history()["redo"])
        with self.assertRaises(EditError):
            self.st.redo()

    def test_undo_with_nothing_to_undo(self):
        with self.assertRaises(EditError):
            self.st.undo()


class SpanEdits(BookCase):
    def test_create_entity_defaults_and_new_group(self):
        a, b = ords(7, 3, 3)  # "on"
        r = self.st.create_span("entities", {"s": a, "e": b})
        e = self.ent(r["uid"])
        self.assertEqual((e["cat"], e["prop"], e["text"]), ("PER", "PROP", "on"))
        self.assertEqual(e["coref"], 5)  # the next free group number
        self.assertIn(5, self.st.clusters())

    def test_create_entity_in_existing_group_and_reversed_bounds(self):
        a, b = ords(6, 1, 1)
        r = self.st.create_span("entities", {"s": b + 2, "e": a, "coref": 0, "cat": "VAR", "prop": "NOM"})
        e = self.ent(r["uid"])
        self.assertEqual((e["coref"], e["cat"], e["text"]), (0, "VAR", "thanked her ."))

    def test_new_group_number_skips_groups_that_only_have_settings(self):
        # a group with no mentions left but a saved name must not be reused for a brand-new group
        with self.st.batch("leftover"):
            self.st._group_upsert(9, {"name": "Ghost", "hidden": 1})
        a, b = ords(7, 3, 3)
        r = self.st.create_span("entities", {"s": a, "e": b})
        self.assertNotEqual(self.ent(r["uid"])["coref"], 9)
        self.assertGreater(self.ent(r["uid"])["coref"], 9)
        r2 = self.st.edit_span("entities", r["uid"], {"coref": None})
        self.assertGreater(self.ent(r["uid"])["coref"], 9)
        self.assertNotIn("Ghost", [c["name"] for c in self.st.clusters().values()])
        del r2

    def test_quote_cannot_overlap(self):
        a, b = ords(1, 2, 4)
        with self.assertRaises(EditError):
            self.st.create_span("quotes", {"s": a, "e": b})
        a, b = ords(2, 0, 1)
        r = self.st.create_span("quotes", {"s": a, "e": b, "char_id": 0})
        q = self.st._row("quotes", r["uid"])
        self.assertEqual(q["char_id"], 0)
        self.assertIsNotNone(q["m_start"])  # the nearest mention of that group outside the quote

    def test_edit_entity_type_group_and_bounds(self):
        uid = self.uid_at(7, 0, 1)
        self.st.edit_span("entities", uid, {"cat": "VAR"})
        self.assertEqual(self.ent(uid)["cat"], "VAR")
        self.assertIn("cat", self.ent(uid)["manual"])
        self.st.edit_span("entities", uid, {"coref": 4})
        self.assertEqual(self.ent(uid)["coref"], 4)
        a, b = ords(7, 0, 2)
        self.st.edit_span("entities", uid, {"s": a, "e": b})
        self.assertEqual(self.ent(uid)["text"], "The violin lay")
        with self.assertRaises(EditError):
            self.st.edit_span("entities", uid, {"s": b, "e": a})

    def test_speaker_mention_follows_entity_changes(self):
        mention = self.uid_at(1, 7)
        quote = self.uid_at(1, 0, 5, table="quotes")
        self.st.regroup([mention], 0)  # Watson's mention moves to Holmes' group
        self.assertEqual(self.st._row("quotes", quote)["char_id"], 0)
        a, b = ords(1, 7, 8)
        self.st.edit_span("entities", mention, {"s": a, "e": b})
        q = self.st._row("quotes", quote)
        self.assertEqual(q["m_phrase"], "Watson .")
        self.assertEqual(q["m_end"], self.st.uid_at[b])

    def test_delete_entity_unlinks_quote_mention(self):
        quote = self.uid_at(1, 0, 5, table="quotes")
        self.st.delete_span("entities", self.uid_at(1, 7))
        q = self.st._row("quotes", quote)
        self.assertIsNone(q["m_start"])
        self.assertEqual(q["char_id"], 1)  # the speaker stays
        self.st.undo()
        self.assertIsNotNone(self.st._row("quotes", quote)["m_start"])

    def test_supersense_create_edit_delete(self):
        a, b = ords(6, 1, 1)
        with self.assertRaises(EditError):
            self.st.create_span("supersenses", {"s": a, "e": b})
        r = self.st.create_span("supersenses", {"s": a, "e": b, "cat": "verb.communication"})
        self.st.edit_span("supersenses", r["uid"], {"cat": "verb.social"})
        self.assertEqual(self.st._row("supersenses", r["uid"])["cat"], "verb.social")
        self.st.delete_span("supersenses", r["uid"])
        self.assertIsNone(self.st._row("supersenses", r["uid"]))

    def test_unknown_layer(self):
        with self.assertRaises(EditError):
            self.st.create_span("tokens", {"s": 0, "e": 0})


class GroupEdits(BookCase):
    def test_regroup_to_new_group_with_name(self):
        uids = [self.uid_at(4, 8), self.uid_at(5, 0)]
        r = self.st.regroup(uids, None, "The housekeeper")
        self.assertEqual(r["moved"], 2)
        self.assertEqual(self.st.clusters()[r["target"]]["name"], "The housekeeper")
        self.assertEqual(self.st.clusters()[2]["count"], 3)

    def test_regroup_moves_quote_attribution(self):
        quote = self.uid_at(4, 0, 7, table="quotes")
        self.st.regroup([self.uid_at(4, 8)], 0)
        self.assertEqual(self.st._row("quotes", quote)["char_id"], 0)

    def test_regroup_ignores_mentions_already_there(self):
        r = self.st.regroup([self.uid_at(0, 0)], 0)
        self.assertEqual(r["moved"], 0)
        self.assertEqual(self.st.history()["count"], 0)

    def test_merge_carries_name_pronoun_and_quotes(self):
        self.st.edit_group(1, {"name": "Dr. Watson", "pronoun": "he/him/his"})
        self.st.merge_groups(2, [1])
        c = self.st.clusters()
        self.assertNotIn(1, c)
        self.assertEqual(c[2]["name"], "Dr. Watson")
        self.assertEqual(c[2]["pronoun"], "he/him/his")
        self.assertEqual(c[2]["count"], 9)
        self.assertEqual(self.st._row("quotes", self.uid_at(1, 0, 5, table="quotes"))["char_id"], 2)
        self.assertIsNone(self.st._row("groups", 1))
        self.st.undo()
        self.assertEqual(self.st.clusters()[1]["name"], "Dr. Watson")

    def test_merge_needs_a_real_source(self):
        with self.assertRaises(EditError):
            self.st.merge_groups(0, [0])
        with self.assertRaises(EditError):
            self.st.merge_groups(99, [0])

    def test_group_settings(self):
        self.st.edit_group(0, {"name": "  Sherlock  "})
        self.assertEqual(self.st.clusters()[0]["name"], "Sherlock")
        self.st.edit_group(0, {"name": ""})
        self.assertEqual(self.st.clusters()[0]["name"], "Holmes")
        self.st.edit_group(0, {"hidden": True})
        self.assertNotIn(0, [c["id"] for c in self.st.groups_list()["items"]])
        self.assertIn(0, [c["id"] for c in self.st.groups_list(hidden=True)["items"]])
        with self.assertRaises(EditError):
            self.st.edit_group(0, {})
        with self.assertRaises(EditError):
            self.st.edit_group(77, {"name": "x"})

    def test_checking_is_a_review_mark_left_out_of_the_log(self):
        self.st.edit_group(0, {"checked": True})
        self.assertTrue(self.st.clusters()[0]["checked"])
        dest = self.tmp / "exp"
        self.st.export(dest)
        log = (dest / "fix.changes.md").read_text()
        self.assertNotIn("checked", log)
        self.assertEqual(self.st.groups_list(unchecked=True)["total"], 4)

    def test_retype_and_delete_many(self):
        uids = [self.uid_at(7, 0, 1), self.uid_at(7, 4, 5)]
        self.st.edit_entities(uids, {"cat": "VAR", "prop": "PROP"})
        self.assertEqual({self.ent(u)["cat"] for u in uids}, {"VAR"})
        self.assertEqual({self.ent(u)["prop"] for u in uids}, {"PROP"})
        with self.assertRaises(EditError):
            self.st.edit_entities(uids, {})
        self.st.delete_entities(uids)
        self.assertNotIn(3, self.st.clusters())
        self.assertEqual(self.st._count("entities"), 13)
        with self.assertRaises(EditError):
            self.st.delete_entities(uids)

    def test_move_variant_moves_every_use_of_a_name(self):
        r = self.st.move_variant(0, "Holmes", "PROP", None, "Sherlock")
        self.assertEqual(r["moved"], 3)
        self.assertEqual(self.st.clusters()[0]["count"], 1)

    def test_speakers_in_bulk(self):
        quotes = [self.uid_at(1, 0, 5, table="quotes"), self.uid_at(4, 0, 7, table="quotes")]
        self.st.set_speakers(quotes, 0)
        self.assertEqual({self.st._row("quotes", q)["char_id"] for q in quotes}, {0})
        self.st.set_speakers(quotes, None)
        self.assertEqual({self.st._row("quotes", q)["char_id"] for q in quotes}, {None})
        self.assertIsNone(self.st._row("quotes", quotes[0])["m_start"])


class BulkAndSearch(BookCase):
    def test_bulk_retype_from_find(self):
        r = self.st.bulk_edit("entities", "cat", "eq", "FAC", None, "cat", "VAR")
        self.assertEqual(r["changed"], 2)
        self.assertEqual(self.st.search("entities", "cat", "eq", "VAR")["total"], 2)
        self.st.undo()
        self.assertEqual(self.st.search("entities", "cat", "eq", "VAR")["total"], 0)

    def test_bulk_only_ticked(self):
        u = self.uid_at(7, 0, 1)
        r = self.st.bulk_edit("entities", "cat", "eq", "FAC", [u], "cat", "VAR")
        self.assertEqual(r["changed"], 1)

    def test_bulk_group_can_not_be_blanked(self):
        with self.assertRaises(EditError):
            self.st.bulk_edit("entities", "cat", "eq", "FAC", None, "coref", "")
        self.assertEqual(self.st.db.execute("SELECT COUNT(*) FROM entities WHERE coref IS NULL").fetchone()[0], 0)

    def test_bulk_speaker_can_be_blanked(self):
        r = self.st.bulk_edit("quotes", "text", "contains", "look", None, "char_id", "")
        self.assertEqual(r["changed"], 1)

    def test_search_operators(self):
        s = self.st
        self.assertEqual(s.search("tokens", "word", "eq", "Holmes")["total"], 3)
        self.assertEqual(s.search("tokens", "word", "contains", "hol")["total"], 3)
        self.assertEqual(s.search("tokens", "word", "regex", r"^H(olmes|udson)$")["total"], 4)
        self.assertEqual(s.search("entities", "coref", "eq", "2")["total"], 5)
        self.assertEqual(s.search("tokens", "pos", "invalid", "")["total"], 0)
        s.edit_token(0, {"pos": "WEIRD"})
        self.assertEqual(s.search("tokens", "pos", "invalid", "")["total"], 1)
        self.assertEqual(s.search("tokens", "pos", "edited", "")["total"], 1)
        with self.assertRaises(EditError):
            s.search("tokens", "word", "regex", "(")
        with self.assertRaises(EditError):
            s.search("tokens", "nonsense", "eq", "x")
        with self.assertRaises(EditError):
            s.search("tokens", "word", "wat", "x")

    def test_search_paging_and_context(self):
        r = self.st.search("tokens", "pos", "eq", "PUNCT", offset=2, limit=3)
        self.assertEqual((r["total"], len(r["hits"])), (15, 3))
        h = r["hits"][0]
        self.assertLessEqual(h["context_start"], h["s"])


class ContainsSearch(BookCase):
    """"contains" ignores case the way Python does (accents, ß) and treats _ and % as the characters they are."""

    def lemmas(self, value, layer="tokens", field="lemma"):
        return sorted(h["value"] for h in self.st.search(layer, field, "contains", value)["hits"])

    def test_accented_letters_match_whatever_their_case(self):
        self.st.edit_token(self.st.uid_at[0], {"lemma": "Évariste"})
        self.st.edit_token(self.st.uid_at[1], {"lemma": "Straße"})
        self.assertEqual(self.lemmas("évariste"), ["Évariste"])
        self.assertEqual(self.lemmas("ÉVARISTE"), ["Évariste"])
        self.assertEqual(self.lemmas("STRASSE"), ["Straße"])         # case folding: ß = ss

    def test_underscore_and_percent_are_not_wildcards(self):
        self.st.edit_token(self.st.uid_at[0], {"lemma": "a_b"})
        self.st.edit_token(self.st.uid_at[1], {"lemma": "axb"})
        self.st.edit_token(self.st.uid_at[2], {"lemma": "100%"})
        self.assertEqual(self.lemmas("a_b"), ["a_b"])
        self.assertEqual(self.lemmas("%"), ["100%"])

    def test_the_groups_and_quotes_searches_fold_case_too(self):
        self.st.edit_group(0, {"name": "Évariste Gallois"})
        self.assertIn(0, [c["id"] for c in self.st.groups_list(q="évariste")["items"]])
        self.assertEqual(self.st.quotes_list(q="TIRED")["total"], 1)           # “I am tired,”
        self.assertEqual(self.st.quotes_list(q="t_red")["total"], 0)


class Structure(BookCase):
    def test_split_token_keeps_text_and_repoints_spans(self):
        before = self.snapshot()
        uid = self.st.uid_at[SENT_START[3]]  # "Mrs."
        with self.assertRaises(EditError):
            self.st.split_token(uid, "Mrs")
        with self.assertRaises(EditError):
            self.st.split_token(uid, "Mrs.")
        r = self.st.split_token(uid, "Mrs .")
        self.assertIn("retag_error", r)  # spaCy isn't available in the tests
        self.assertEqual(self.st.n_tokens, N_TOKENS + 1)
        words = [t["word"] for t in self.st._sent_tokens(3)]
        self.assertEqual(words[:3], ["Mrs", ".", "Hudson"])
        ent = self.ent(self.uid_at(3, 0, 2))
        self.assertEqual(ent["text"], "Mrs . Hudson")
        # every token still points into the original text
        for t in self.st.db.execute("SELECT word, char_start, char_end FROM tokens"):
            self.assertEqual(self.st.meta["text"][t["char_start"]:t["char_end"]], t["word"])
        self.st.undo()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.st.n_tokens, N_TOKENS)

    def test_merge_tokens_only_when_touching(self):
        with self.assertRaises(EditError):
            self.st.merge_tokens(SENT_START[3], SENT_START[3] + 1)  # "Mrs." and "Hudson" have a space between
        with self.assertRaises(EditError):
            self.st.merge_tokens(SENT_START[2], SENT_START[3])  # across sentences
        before = self.snapshot()
        a, b = ords(1, 7, 8)  # "Watson" "."
        self.st.merge_tokens(a, b)
        self.assertEqual(self.st.n_tokens, N_TOKENS - 1)
        self.assertEqual(self.ent(self.uid_at(1, 7))["text"], "Watson.")
        self.st.undo()
        self.assertEqual(self.snapshot(), before)

    def test_merge_repoints_heads_and_spans_inside_the_merge(self):
        # the dependents of a merged-away token attach to the merged token
        a, b = ords(1, 7, 8)
        dot = self.st.uid_at[b]
        watson = self.st.uid_at[a]
        self.st.edit_token(self.st.uid_at[ords(1, 3)[0]], {"head_ord": b})
        self.st.merge_tokens(a, b)
        self.assertEqual(self.st._row("tokens", self.st.uid_at[ords(1, 3)[0]])["head"], watson)
        self.assertIsNone(self.st._row("tokens", dot))

    def test_sentences_and_paragraphs(self):
        n = len(SENT_START)
        self.assertEqual(self.st.info()["n_sentences"], n)
        self.st.split_sentence(SENT_START[7] + 2)
        self.assertEqual(self.st.info()["n_sentences"], n + 1)
        with self.assertRaises(EditError):
            self.st.split_sentence(SENT_START[7] + 2)
        self.st.merge_sentence(7)
        self.assertEqual(self.st.info()["n_sentences"], n)
        with self.assertRaises(EditError):
            self.st.merge_sentence(n - 1)
        self.st.set_paragraph(7, True)
        self.assertEqual(self.st.info()["n_paragraphs"], 4)
        self.st.set_paragraph(7, False)
        self.assertEqual(self.st.info()["n_paragraphs"], 3)
        with self.assertRaises(EditError):
            self.st.set_paragraph(0, False)

    def test_merging_sentences_across_a_paragraph_break_removes_it(self):
        self.st.merge_sentence(0)
        self.assertEqual(self.st.info()["n_paragraphs"], 2)

    def test_review_marks(self):
        r = self.st.set_reviewed([0, 1, 1, 99], True)
        self.assertEqual(r["progress"]["reviewed"], 2)
        self.assertEqual(self.st.next_unreviewed(-1)["sent"], 2)
        self.assertEqual(self.st.next_unreviewed(7)["sent"], 2)  # wraps round
        self.st.set_reviewed([0, 1], False)
        self.assertEqual(self.st.review_progress()["reviewed"], 0)
        with self.assertRaises(EditError):
            self.st.set_reviewed([], True)

    def test_window(self):
        w = self.st.window(2, 3)
        self.assertEqual((w["first"], w["last"]), (2, 4))
        self.assertEqual(w["start"], SENT_START[2])
        self.assertEqual(len(w["sentences"]), 3)
        self.assertTrue(w["sentences"][1]["para_start"])
        self.assertEqual({e["uid"] for e in w["entities"]} & {self.uid_at(7, 0, 1)}, set())
        self.assertEqual(self.st.window(99, 5)["first"], 7)

    def test_centered_start_puts_the_excerpt_in_the_middle(self):
        st = self.st  # 8 sentences, 0..7
        self.assertEqual(st.centered_start(SENT_START[4], SENT_START[4], 3), 3)  # one sentence above, one below
        self.assertEqual(st.centered_start(SENT_START[4], SENT_START[4], 5), 2)
        self.assertEqual(st.centered_start(SENT_START[0], SENT_START[0], 5), 0)  # at the top there is nothing above
        self.assertEqual(st.centered_start(SENT_START[7], SENT_START[7], 5), 3)  # at the end the page is filled upwards
        self.assertEqual(st.centered_start(SENT_START[1], SENT_START[5], 3), 1)  # a long excerpt still starts the page
        w = st.window(st.centered_start(SENT_START[4], SENT_START[4], 3), 3)
        self.assertEqual([x["index"] for x in w["sentences"]], [3, 4, 5])
        self.assertEqual(st.centered_start(SENT_START[3], SENT_START[5], 3), 3)  # excerpt across 3 sentences fills the page


class Proposals(BookCase):
    def test_accept_and_reject(self):
        st = self.st
        old = st._row("tokens", 2)["lemma"]
        st.db.executemany("INSERT INTO proposals(uid, field, old, new, created, sig) VALUES (?,?,?,?,0,NULL)",
                          [(2, "lemma", old, "greet2"), (3, "lemma", st._row("tokens", 3)["lemma"], "x")])
        st.db.commit()
        p = st.proposals()
        self.assertEqual(p["count"], 2)
        ids = [i["id"] for i in p["sentences"][0]["items"]]
        r = st.accept_proposals([ids[0]])
        self.assertEqual(r["pending"], 1)
        self.assertEqual(st._row("tokens", 2)["lemma"], "greet2")
        self.assertNotIn("lemma", st._row("tokens", 2)["manual"])  # accepted suggestions aren't hand edits
        st.reject_proposals([ids[1]])
        self.assertEqual(st.pending_count(), 0)

    def test_stale_suggestions_vanish(self):
        st = self.st
        st.db.execute("INSERT INTO proposals(uid, field, old, new, created, sig) VALUES (2, 'lemma', ?, 'zzz', 0, NULL)",
                      (st._row("tokens", 2)["lemma"],))
        st.db.commit()
        self.assertEqual(st.pending_count(), 1)
        st.edit_token(2, {"lemma": "mine"})
        self.assertEqual(st.pending_count(), 0)


class Caches(BookCase):
    def test_caches_follow_undo_then_a_different_edit(self):
        """A new edit after an undo must never be served from a cache built for the undone one."""
        u = self.uid_at(6, 2)  # "her"
        self.st.regroup([u], 0)
        rows, _, _ = self.st._ents()
        self.assertEqual({r["coref"] for r in rows if r["uid"] == u}, {0})
        self.st.undo()
        self.st.regroup([u], 1)  # the same number of batches as before, different content
        rows, _, _ = self.st._ents()
        self.assertEqual({r["coref"] for r in rows if r["uid"] == u}, {1})

    def test_book_cache_follows_undo_then_a_different_edit(self):
        self.st.edit_group(0, {"name": "First name"})
        names = {c["id"]: c["name"] for c in self.st.build_book()[0]["characters"]}
        self.assertEqual(names[0], "First name")
        self.st.undo()
        self.st.edit_group(0, {"name": "Second name"})
        names = {c["id"]: c["name"] for c in self.st.build_book()[0]["characters"]}
        self.assertEqual(names[0], "Second name")

    def warm(self):
        """Build one cache of each kind: from the parse alone, from mentions, and from review marks."""
        self.st._tok_arrays(), self.st._kids(), self.st._ents(), self.st._pass_list(), self.st._qpass_list()
        return {"tokens", "kids", "ents", "pass", "qpass"}

    def test_each_kind_of_edit_drops_only_the_caches_it_can_change(self):
        """Review marks keep everything but the pass lists; changes to groups and mentions keep what is read from the parse;
        a change to the tokens themselves keeps nothing. (The fuzz test checks the caches are never stale.)"""
        warm = self.warm()
        self.st.set_reviewed([0], True)
        self.assertEqual(warm - set(self.st._memos), {"pass", "qpass"})
        self.warm()
        self.st.edit_group(0, {"checked": True})
        self.assertEqual(warm - set(self.st._memos), {"pass", "qpass"})
        self.warm()
        self.st.set_flag("sentence", self.st.uid_at[0], "later")
        self.assertEqual(warm - set(self.st._memos), set())                 # a flag changes nothing derived
        self.st.regroup([self.uid_at(6, 2)], 0)
        self.assertEqual(set(self.st._memos) & warm, {"tokens", "kids"})   # mentions moved: the parse is unchanged
        self.warm()
        self.st.edit_token(self.st.uid_at[0], {"lemma": "zzz"})
        self.assertEqual(set(self.st._memos) & warm, set())

    def test_new_entity_does_not_take_over_a_deleted_ones_history(self):
        """Ids of deleted rows aren't reused, so the .book gender bookkeeping can't mix two entities up."""
        last = self.st.db.execute("SELECT MAX(uid) FROM entities").fetchone()[0]
        self.st.delete_span("entities", last)
        a, b = ords(7, 3, 3)
        new = self.st.create_span("entities", {"s": a, "e": b, "coref": 1})["uid"]
        self.assertNotEqual(new, last)


if __name__ == "__main__":
    unittest.main()
