"""Collections (carrying corrections between books): the shared store and the per-book review and apply steps."""
from __future__ import annotations

import json
import unittest

from luna.core.collections_store import ALL, Collections
from luna.core.errors import EditError
from luna.core.store import Store, create_project
from tests.fixture import BookCase


class CollectionsStore(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.dir = Path(tempfile.mkdtemp(prefix="bne-coll-"))
        self.addCleanup(__import__("shutil").rmtree, self.dir, ignore_errors=True)
        self.path = self.dir / "collections.json"
        self.c = Collections(self.path)

    def test_create_rename_delete_and_membership(self):
        a = self.c.create("  Sherlock Holmes ")
        self.assertEqual(a["name"], "Sherlock Holmes")
        with self.assertRaises(EditError):
            self.c.create("sherlock holmes")
        with self.assertRaises(EditError):
            self.c.create("  ")
        self.c.set_member("book1", a["id"])
        self.assertEqual(self.c.collection_of("book1")["name"], "Sherlock Holmes")
        self.assertEqual(self.c.overview()["collections"][0]["books"], ["book1"])
        self.c.rename(a["id"], "Holmes")
        self.assertEqual(Collections(self.path).collection_of("book1")["name"], "Holmes")  # saved to disk
        self.assertEqual([lid for lid, _, _ in self.c.lists_for("book1")], [a["id"], ALL])
        self.assertEqual([lid for lid, _, _ in self.c.lists_for("other")], [ALL])
        self.c.set_member("book1", None)
        self.assertIsNone(self.c.collection_of("book1"))
        self.c.set_member("book1", a["id"])
        self.c.delete(a["id"])
        self.assertIsNone(self.c.collection_of("book1"))
        with self.assertRaises(EditError):
            self.c.delete(a["id"])
        with self.assertRaises(EditError):
            self.c.set_member("book1", "nope")

    def test_settings_keep_only_what_differs_from_the_defaults(self):
        self.c.set_matching_settings({"match": 0.8, "weights": {"name": 0.35, "speech": 0.2}, "embedding": {"use": True}})
        self.assertEqual(self.c.data["settings"]["matching"], {"match": 0.8, "weights": {"speech": 0.2}, "embedding": {"use": True}})
        self.c.set_matching_settings({"match": 0.72, "weights": {"speech": 0.15}, "embedding": {"use": False}})
        self.assertNotIn("matching", self.c.data["settings"])                  # all back to the defaults: nothing kept
        self.c.set_suggestion_settings("merge", {"off": []})                     # the locality kinds switched on
        self.c.set_suggestion_settings("merge", {"weights": {"talk": 1.0}})
        self.assertEqual(self.c.data["settings"]["suggestions"]["merge"], {"off": [], "weights": {"talk": 1.0}})
        self.assertEqual(Collections(self.path).suggestion_settings()["merge"]["weights"]["same_name"], 3.0)  # defaults fill in
        self.c.set_suggestion_settings("merge", None)
        self.assertEqual(self.c.suggestion_settings()["merge"]["off"], ["p_cooccur", "p_places"])

    def test_display_settings_keep_only_what_differs_from_the_defaults(self):
        self.assertEqual(self.c.display_settings(), {"context_sentences": 2})
        self.c.set_display_settings({"context_sentences": 4})
        self.assertEqual(self.c.data["settings"]["display"], {"context_sentences": 4})
        self.assertEqual(Collections(self.path).display_settings(), {"context_sentences": 4})  # saved to disk
        with self.assertRaises(EditError):
            self.c.set_display_settings({"context_sentences": "many"})
        with self.assertRaises(EditError):
            self.c.set_display_settings({"context_sentences": 99})
        self.c.set_display_settings({"context_sentences": 2})            # back to the default: nothing kept
        self.assertNotIn("display", self.c.data["settings"])
        self.c.set_display_settings({"context_sentences": 4})
        self.c.set_display_settings(None)                                # reset
        self.assertEqual(self.c.display_settings(), {"context_sentences": 2})

    def test_characters_are_found_by_link_or_by_names(self):
        coll = self.c.create("S")
        self.c.set_member("k", coll["id"])
        eid = self.c.record_character("k", "b1", None, {"name": "Holmes", "names": {"Holmes": 5, "Mr. Holmes": 1}, "pronoun": "he/him/his",
                                                         "pronoun_manual": False, "cat": "PER", "name_manual": False})
        self.assertIsNotNone(eid)
        # the same person in another book: found by the shared main name, not duplicated
        again = self.c.record_character("k", "b2", None, {"name": "Holmes", "names": {"Holmes": 2}, "pronoun": "she/her",
                                                           "pronoun_manual": False, "cat": "PER", "name_manual": False})
        self.assertEqual(again, eid)
        lst = self.c.get_list(coll["id"])
        self.assertEqual(len(lst["characters"]), 1)
        e = lst["characters"][0]
        self.assertEqual((e["books"], e["pronoun"], e["names"]["Holmes"]), (["b1", "b2"], "he/him/his", 5))  # a guess doesn't override
        self.c.record_character("k", "b2", eid, {"name": "Sherlock", "names": {}, "pronoun": "they/them/their", "pronoun_manual": True,
                                                  "cat": "PER", "name_manual": True})
        e = self.c.get_list(coll["id"])["characters"][0]
        self.assertEqual((e["name"], e["pronoun"]), ("Sherlock", "they/them/their"))
        # a book outside any collection records nothing unless a list for all books already has the character
        self.assertIsNone(self.c.record_character("loose", "b3", None, {"name": "X", "names": {"X": 1}, "cat": "PER"}))

    def test_entries_edit_move_delete(self):
        coll = self.c.create("S")
        self.c.set_member("k", coll["id"])
        eid = self.c.record_character("k", "b", None, {"name": "Holmes", "names": {"Holmes": 1}, "cat": "PER"})
        e = self.c.edit_entry(coll["id"], "characters", eid, {"names": "Holmes, Mr. Holmes; Sherlock\nthe detective", "pronoun": " he/him/his ", "cat": ""})
        self.assertEqual(sorted(e["names"]), ["Holmes", "Mr. Holmes", "Sherlock", "the detective"])
        self.assertEqual((e["pronoun"], e["cat"]), ("he/him/his", None))
        with self.assertRaises(EditError):
            self.c.edit_entry(coll["id"], "characters", eid, {"name": " "})
        self.c.move_entry(coll["id"], "characters", eid, ALL)
        self.assertEqual(len(self.c.get_list(ALL)["characters"]), 1)
        self.assertEqual(len(self.c.get_list(coll["id"])["characters"]), 0)
        self.c.delete_entry(ALL, "characters", eid)
        with self.assertRaises(EditError):
            self.c.delete_entry(ALL, "characters", eid)
        with self.assertRaises(EditError):
            self.c.delete_entry(ALL, "widgets", eid)

    def test_retypes_and_rules(self):
        coll = self.c.create("S")
        self.c.set_member("k", coll["id"])
        char = self.c.record_character("k", "b", None, {"name": "Baker Street", "names": {"Baker Street": 3}, "cat": "PER"})
        r = self.c.record_retype("k", "b", "Baker Street", "FAC", {"PER"})
        self.assertIsNotNone(r)
        self.assertEqual(self.c.get_list(coll["id"])["characters"][0]["cat"], "FAC")  # the character takes the new type too
        self.assertEqual(self.c.record_retype("k", "b2", "Baker Street", "LOC", {"FAC"}), r)  # updated, not duplicated
        e = self.c.get_list(coll["id"])["retypes"][0]
        self.assertEqual((e["cat"], e["from"], e["books"]), ("LOC", ["FAC", "PER"], ["b", "b2"]))
        rule = {"layer": "tokens", "sfield": "word", "op": "eq", "svalue": "x", "field": "pos", "value": "NOUN", "label": "l"}
        first = self.c.add_rule(coll["id"], rule, "b")
        self.assertEqual(self.c.add_rule(coll["id"], dict(rule, label="different label"), "b2")["id"], first["id"])
        self.assertEqual(len(self.c.get_list(coll["id"])["rules"]), 1)
        del char

    def test_the_file_is_indented_but_each_profile_sits_on_one_line_and_round_trips(self):
        coll = self.c.create("C")
        prof = {"mentions": 3, "names": {"Holmes": 3}, "speech": {"content": {"pipe": 2}}}
        self.c.set_member("b", coll["id"])
        eid = self.c.record_character("b", "b1", None, {"name": "Holmes", "names": {"Holmes": 3}, "profile": prof}, lookup=False)
        text = self.path.read_text(encoding="utf-8")
        self.assertEqual(json.loads(text), self.c.data)
        line = next(l for l in text.split("\n") if '"b1": {' in l)
        self.assertTrue(line.strip().startswith('"b1": {"mentions":3'), line)         # one line, however deep it is
        self.assertIn('\n "collections": [', text)                                  # the rest is indented as before
        summary_only = self.c.get_list(coll["id"], profiles=False)["characters"][0]
        self.assertNotIn("profiles", summary_only)
        self.assertEqual(self.c.get_list(coll["id"])["characters"][0]["profiles"]["b1"], prof)
        self.assertIn(eid, self.c.summaries(coll["id"]))

    def test_an_unreadable_file_is_kept_aside(self):
        self.path.write_text("{ not json")
        c = Collections(self.path)
        self.assertEqual(c.overview()["collections"], [])
        self.assertTrue(list(self.dir.glob("collections.unreadable-*.json")))


class CarryBase(BookCase):
    """A working copy that belongs to a collection."""

    def setUp(self):
        super().setUp()
        self.colls = Collections(self.tmp / "collections.json")
        self.coll = self.colls.create("Series")
        self.link(self.st, "key1")

    def link(self, st, key):
        self.colls.set_member(key, self.coll["id"])
        st.carry_colls, st.carry_key = self.colls, key

    def second_book(self):
        """A fresh working copy of the same output, as if it were the next book of the series (with its own book ID, so the
        characters' profiles from the first book count as another book's)."""
        db = self.tmp / "next.sqlite"
        create_project(db, self.out, self.text_path)
        st = Store(db, spacy_model="no-such-model")
        self.addCleanup(st.db.close)
        st._set_meta("book_id", "fix2")
        self.link(st, "key2")
        return st


class Recording(CarryBase):
    def test_checking_a_group_records_a_character_and_links_it(self):
        self.st.edit_group(0, {"checked": True})
        chars = self.colls.get_list(self.coll["id"])["characters"]
        self.assertEqual(len(chars), 1)
        self.assertEqual((chars[0]["name"], chars[0]["names"], chars[0]["cat"], chars[0]["books"]), ("Holmes", {"Holmes": 3}, "PER", ["fix"]))
        self.assertEqual(self.st.clusters()[0]["carry"], chars[0]["id"])
        self.assertEqual(self.st.carry_state()["linked"], 1)

    def test_groups_without_a_name_or_marked_not_a_character_are_not_recorded(self):
        self.st.edit_group(3, {"checked": True})  # "The violin": only a description
        self.st.edit_group(1, {"hidden": True})
        self.st.edit_group(1, {"checked": True})
        self.assertEqual(self.colls.get_list(self.coll["id"])["characters"], [])

    def test_retyping_every_use_of_a_name_records_a_retype(self):
        self.st.edit_entities([self.uid_at(7, 0, 1)], {"cat": "VAR"})
        r = self.colls.get_list(self.coll["id"])["retypes"]
        self.assertEqual([(e["text"], e["cat"], e["from"]) for e in r], [("The violin", "VAR", ["FAC"])])

    def test_retyping_only_some_uses_records_nothing(self):
        self.st.create_span("entities", {"s": 0, "e": 0, "cat": "PER", "prop": "PROP", "coref": 0})  # a second "Holmes"
        self.st.edit_entities([self.uid_at(0, 0)], {"cat": "VAR"})
        self.assertEqual(self.colls.get_list(self.coll["id"])["retypes"], [])

    def test_saving_a_rule(self):
        rule = {"layer": "entities", "sfield": "cat", "op": "eq", "svalue": "FAC", "field": "cat", "value": "LOC"}
        r = self.st.carry_save_rule(self.coll["id"], rule)
        self.assertIn("Entities whose type is “FAC”", r["label"])
        self.assertEqual(self.st.carry_save_rule(None, rule)["id"], r["id"])  # the book's own collection by default
        tok = {"layer": "tokens", "sfield": "word", "op": "contains", "svalue": "ed", "field": "pos", "value": "VERB"}
        self.st.carry_save_rule(ALL, tok)
        self.assertEqual(len(self.colls.get_list(ALL)["rules"]), 1)

    def test_only_tags_and_types_can_be_saved_as_rules(self):
        base = {"layer": "entities", "sfield": "cat", "op": "eq", "svalue": "FAC", "field": "cat", "value": "LOC"}
        for bad in (dict(base, field="coref"), dict(base, sfield="coref"), dict(base, layer="quotes", field="char_id"),
                    dict(base, op="invalid"), dict(base, value=" "), dict(base, layer="nonsense")):
            with self.assertRaises(EditError, msg=str(bad)):
                self.st.carry_save_rule(self.coll["id"], bad)

    def test_needs_a_book_opened_from_the_library(self):
        self.st.carry_colls = None
        with self.assertRaises(EditError):
            self.st.carry_save_rule(None, {})
        self.assertEqual(self.st.carry_state(), {"available": False})
        self.assertEqual(self.st.carry_review(), {"available": False})


class Review(CarryBase):
    def setUp(self):
        super().setUp()
        self.st.edit_group(0, {"checked": True})
        self.st.edit_group(1, {"checked": True})
        self.st.edit_entities([self.uid_at(7, 0, 1)], {"cat": "VAR"})
        self.st.carry_save_rule(self.coll["id"], {"layer": "entities", "sfield": "cat", "op": "eq", "svalue": "FAC", "field": "prop", "value": "PROP"})
        self.nxt = self.second_book()

    def test_a_new_book_offers_links_retypes_and_rules(self):
        r = self.nxt.carry_review()
        self.assertEqual({c["name"]: sorted(c["actions"]) for c in r["characters"]}, {"Holmes": ["link"], "Watson": ["link"]})
        self.assertEqual([(x["text"], x["cat"], x["n"]) for x in r["retypes"]], [("The violin", "VAR", 1)])
        self.assertEqual([(x["n"]) for x in r["rules"]], [2])
        self.assertEqual(r["entries"], 2)

    def test_apply_links_merges_renames_retypes_and_runs_rules(self):
        nxt = self.nxt
        new = nxt.regroup([self.uid_at_in(nxt, 6, 0)], None)["target"]  # a stray "Holmes" fragment
        entry = self.colls.get_list(self.coll["id"])["characters"]
        holmes = next(e for e in entry if e["name"] == "Holmes")
        self.colls.edit_entry(self.coll["id"], "characters", holmes["id"], {"name": "Sherlock Holmes", "pronoun": "they/them/their"})
        r = nxt.carry_review()
        h = next(c for c in r["characters"] if c["entry"] == holmes["id"])
        self.assertEqual(sorted(h["actions"]), ["link", "merge", "name", "pronoun"])
        self.assertEqual([g["id"] for g in h["actions"]["merge"]], [new])
        out = nxt.carry_apply(chars=[{"entry": holmes["id"], "do": ["merge", "name", "pronoun", "link"]}], retypes=[x["entry"] for x in r["retypes"]],
                              rules=[x["entry"] for x in r["rules"]])
        self.assertIn("merged 1 group", out["summary"])
        c = nxt.clusters()
        self.assertNotIn(new, c)
        self.assertEqual((c[0]["name"], c[0]["pronoun"], c[0]["carry"]), ("Sherlock Holmes", "they/them/their", holmes["id"]))
        self.assertEqual(c[3]["cats"], {"VAR": 1})
        self.assertEqual(nxt.history()["undo"], out["history"]["undo"])
        nxt.undo()  # everything in one step
        self.assertIn(new, nxt.clusters())
        self.assertEqual(nxt.clusters()[3]["cat"], "FAC")

    def uid_at_in(self, st, sent, first):
        a, _ = self.book.ords(sent, first)
        return st.db.execute("SELECT x.uid FROM entities x JOIN tokens s ON s.uid=x.tok_start WHERE s.ord=?", (a,)).fetchone()[0]

    def add_mycroft(self):
        """A second character that is also called Holmes (without a profile)."""
        with self.colls.lock:
            self.colls.data["lists"][self.coll["id"]]["characters"].append({"id": "kzzz", "name": "Mycroft Holmes", "names": {"Holmes": 1}, "pronoun": None,
                                                                            "cat": "PER", "books": []})
            self.colls.save()

    def test_evidence_decides_between_characters_with_the_same_name(self):
        self.add_mycroft()
        r = self.nxt.carry_review()
        self.assertEqual(r["ambiguous"], [])
        h = next(c for c in r["characters"] if c["name"] == "Holmes")
        ev = h["evidence"]
        self.assertGreaterEqual(ev["score"], 0.9)
        self.assertEqual([p["kind"] for p in ev["parts"]][:1], ["name"])
        self.assertIn("cooccur", [p["kind"] for p in ev["parts"]])  # appears with Watson and Mrs. Hudson in both books

    def test_ambiguous_names_are_offered_as_a_choice(self):
        h = next(e for e in self.colls.get_list(self.coll["id"])["characters"] if e["name"] == "Holmes")
        self.colls.edit_entry(self.coll["id"], "characters", h["id"], {"names": "Holmes"})
        with self.colls.lock:  # an entry recorded before profiles existed: its names are all there is to go by
            next(e for e in self.colls.data["lists"][self.coll["id"]]["characters"] if e["id"] == h["id"]).pop("profiles")
        self.add_mycroft()
        r = self.nxt.carry_review()
        self.assertEqual([a["name"] for a in r["ambiguous"]], ["Holmes"])
        self.assertEqual(len(r["ambiguous"][0]["options"]), 2)
        out = self.nxt.carry_apply(assign=[{"group": 0, "entry": "kzzz"}])
        self.assertIn("linked 1 group", out["summary"])
        self.assertEqual(self.nxt.clusters()[0]["carry"], "kzzz")

    def test_applying_nothing_is_harmless(self):
        out = self.nxt.carry_apply()
        self.assertEqual(self.nxt.history()["count"], 0)
        self.assertEqual(out["summary"], "")

    def test_marking_offered_and_state(self):
        self.assertFalse(self.nxt.carry_state()["offered"])
        self.nxt.carry_mark_offered()
        self.assertTrue(self.nxt.carry_state()["offered"])
        self.assertEqual(self.nxt.carry_state()["collection"]["name"], "Series")

    def test_a_rule_that_no_longer_fits_is_reported_not_applied(self):
        self.colls.data["lists"][ALL]["rules"].append({"id": "fbad", "layer": "tokens", "sfield": "word", "op": "regex", "svalue": "(", "field": "pos",
                                                       "value": "X", "label": "broken", "books": []})
        rules = self.nxt.carry_review()["rules"]
        broken = next(r for r in rules if r["entry"] == "fbad")
        self.assertIn("error", broken)


if __name__ == "__main__":
    unittest.main()
