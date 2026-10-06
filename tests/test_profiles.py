"""Character profiles: what is read from a book (core/tools/profile.py), how profiles add up (core/profiles.py), and
how they are kept in the collection."""
from __future__ import annotations

import unittest

from luna.core import profiles as P
from tests.fixture import Book, BookCase
from tests.test_carry import CarryBase

# "his wife": a possessive between two named characters, for relations and actions.
FAMILY = Book(
    text='Holmes saw his wife. Mary smiled.\n',
    sentences=[
        [("Holmes", "Holmes", "PROPN", "NNP", "nsubj", 1), ("saw", "see", "VERB", "VBD", "ROOT", 1),
         ("his", "his", "PRON", "PRP$", "poss", 3), ("wife", "wife", "NOUN", "NN", "dobj", 1), (".", ".", "PUNCT", ".", "punct", 1)],
        [("Mary", "Mary", "PROPN", "NNP", "nsubj", 1), ("smiled", "smile", "VERB", "VBD", "ROOT", 1), (".", ".", "PUNCT", ".", "punct", 1)],
    ],
    para_starts={0},
    entities=[(0, 0, 0, "PROP", "PER", 0), (0, 2, 2, "PRON", "PER", 0), (0, 2, 3, "NOM", "PER", 1), (1, 0, 0, "PROP", "PER", 1)],
    supersenses=[], quotes=[], genders={0: "he/him/his", 1: "she/her"},
)


class FromTheBook(BookCase):
    def test_names_pronouns_gender_and_actions(self):
        w = self.st.character_profile(1)  # Watson: named twice, "I" in his quote, "He sat down"
        self.assertEqual((w["name"], w["group"], w["mentions"], w["quotes"]), ("Watson", 1, 4, 1))
        self.assertEqual(w["names"], {"Watson": 2})
        self.assertEqual(w["pronouns"], {"1": 1, "m": 1})
        self.assertEqual(w["gender"], "m")
        self.assertEqual(w["actions"]["agent"], {"say": 1, "sit": 1})
        h = self.st.character_profile(2)  # Mrs. Hudson
        self.assertEqual((h["titles"], h["name_tokens"], h["gender"]), ({"mrs": 1}, {"Hudson": 1}, "f"))
        self.assertEqual(h["actions"]["patient"], {"help": 1, "thank": 1})  # "helped herself", "thanked her"
        self.assertIsNone(self.st.character_profile(99))

    def test_neighbours_in_the_same_paragraph(self):
        holmes = self.st.character_profile(0)
        self.assertEqual(holmes["cooccur"], {"n:watson": 1, "n:hudson": 1})
        self.assertEqual(holmes["others"]["n:hudson"], "Mrs. Hudson")
        self.assertEqual(holmes["places"], {})  # the violin and the table are mentioned once: not places worth keeping

    def test_speech_counts_come_from_the_quotes(self):
        sp = self.st.character_profile(1)["speech"]  # "I am tired,"
        self.assertEqual((sp["quotes"], sp["words"]), (1, 3))
        self.assertEqual(sp["fw"], {"i": 1, "am": 1})
        self.assertEqual(sp["punct"], {",": 1})
        self.assertEqual(sp["content"], {"tired": 1})

    def test_linked_groups_are_named_by_their_collection_entry(self):
        self.st.edit_group(1, {"name": "Dr. Watson"})
        with self.st.batch("link"):
            self.st._group_upsert(1, {"carry": "kabc"})
        self.assertIn("k:kabc", self.st.character_profile(0)["cooccur"])

    def test_a_profile_handed_out_is_a_copy(self):
        self.st.character_profile(1)["names"]["Nobody"] = 1                   # e.g. kept and changed in collections.json
        self.assertEqual(self.st.character_profile(1)["names"], {"Watson": 2})

    def test_profiles_follow_edits(self):
        self.st.merge_groups(0, [1])
        self.assertIn("Watson", self.st.character_profile(0)["names"])
        self.st.undo()
        self.assertNotIn("Watson", self.st.character_profile(0)["names"])

    def test_profile_summary_is_the_summary_of_the_profile(self):
        self.assertEqual(self.st.profile_summary(1), P.summary(self.st.character_profile(1), 12))
        self.assertIsNone(self.st.profile_summary(99))


class Relations(BookCase):
    book = FAMILY

    def test_a_possessive_makes_a_relation_both_ways(self):
        mary, holmes = self.st.character_profile(1), self.st.character_profile(0)
        self.assertEqual(mary["relations"], {"wife of|n:holmes": 1})
        self.assertEqual(holmes["relations"], {"has wife|n:mary": 1})
        self.assertEqual(mary["nouns"], {"wife": 1})
        self.assertEqual((holmes["actions"]["poss"], mary["actions"]["patient"]), ({"wife": 1}, {"see": 1}))
        self.assertEqual(P.summary(mary)["relations"], ["wife of Holmes"])
        self.assertEqual(P.summary(holmes)["relations"], ["has wife: Mary"])


class Combining(unittest.TestCase):
    def test_counts_add_up_and_the_bigger_book_decides_gender(self):
        a = {**P.empty(), "mentions": 10, "gender": "m", "names": {"Holmes": 3}, "speech": {"quotes": 2, "words": 20, "fw": {"i": 2}}}
        b = {**P.empty(), "mentions": 30, "gender": "p", "names": {"Holmes": 1, "Sherlock": 1}, "actions": {"agent": {"say": 2}}}
        c = P.combine([a, b, None])
        self.assertEqual((c["mentions"], c["gender"]), (40, "p"))
        self.assertEqual(c["names"], {"Holmes": 4, "Sherlock": 1})
        self.assertEqual((c["speech"]["quotes"], c["speech"]["fw"], c["actions"]["agent"]), (2, {"i": 2}, {"say": 2}))
        self.assertEqual(P.combine([]), P.empty())

    def test_each_function_word_is_one_speech_feature(self):
        self.assertEqual(len(P.FUNCTION_WORDS), len(P.FUNCTION_SET))

    def test_summary_names_linked_characters_by_their_current_name(self):
        p = {**P.empty(), "cooccur": {"k:k1": 3, "n:lestrade": 1}, "others": {"k:k1": "Watson", "n:lestrade": "Lestrade"},
             "pronouns": {"m": 3, "1": 1}}
        s = P.summary(p, names={"k1": "Dr. John Watson"})
        self.assertEqual(s["cooccur"], ["Dr. John Watson", "Lestrade"])
        self.assertEqual(s["pronouns"], {"m": 0.75, "1": 0.25})


class InTheCollection(CarryBase):
    def entry(self):
        return self.colls.get_list(self.coll["id"])["characters"][0]

    def test_checking_a_group_keeps_its_profile_for_this_book(self):
        self.st.edit_group(1, {"checked": True})
        e = self.entry()
        self.assertEqual(list(e["profiles"]), ["fix"])
        self.assertEqual(e["profiles"]["fix"]["names"], {"Watson": 2})

    def test_the_list_summarises_exports_and_bulk_deletes_characters(self):
        import csv, io, json as js
        self.st.edit_group(0, {"checked": True})
        self.st.edit_group(1, {"checked": True})
        lid = self.coll["id"]
        ids = {e["name"]: e["id"] for e in self.colls.get_list(lid)["characters"]}
        sm = self.colls.summaries(lid)
        self.assertEqual(sm[ids["Holmes"]]["actions"], ["greet", "look", "thank"])   # "You look well, Holmes": the You is his
        self.assertEqual(sm[ids["Holmes"]]["cooccur"], ["Watson", "Mrs. Hudson"])     # Watson by his entry's name
        name, media, text = self.colls.export_list(lid, "csv")
        self.assertEqual((name, media), ("series-characters.csv", "text/csv"))
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual([r["name"] for r in rows], ["Holmes", "Watson"])
        self.assertEqual((rows[1]["mentions"], rows[1]["quotes"], rows[1]["books"], rows[0]["actions"]), ("4", "1", "fix", "greet; look; thank"))
        data = js.loads(self.colls.export_list(lid, "json")[2])
        self.assertEqual(sorted(data["characters"][0]["profiles"]), ["fix"])
        with self.assertRaises(Exception):
            self.colls.export_list(lid, "xlsx")
        self.assertEqual(self.colls.delete_entries(lid, "characters", list(ids.values())), 2)
        self.assertEqual(self.colls.get_list(lid)["characters"], [])

    def test_update_profiles_refreshes_linked_groups(self):
        self.st.edit_group(1, {"checked": True})
        self.st.merge_groups(1, [2])  # a (wrong) correction after checking
        out = self.st.carry_update_profiles()
        self.assertEqual({k: out[k] for k in ("recorded", "linked", "updated")}, {"recorded": 1, "linked": 0, "updated": 1})
        self.assertIn("Mrs. Hudson", self.entry()["profiles"]["fix"]["names"])
        self.st.carry_colls = None
        with self.assertRaises(Exception):
            self.st.carry_update_profiles()


if __name__ == "__main__":
    unittest.main()
