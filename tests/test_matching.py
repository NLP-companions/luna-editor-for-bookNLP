"""Matching groups to collection characters by evidence (core/matching.py), and recording checked groups with it."""
from __future__ import annotations

import unittest
from collections import Counter

from luna.core import matching as M
from luna.core import suggest
from luna.core import profiles as P
from tests.test_carry import CarryBase


def prof(**kw):
    """A profile with only the given parts filled in."""
    return {**P.empty(), **kw}


def entry(eid, name, names=(), pronoun=None, **profile):
    """A collection entry with one book's profile."""
    return {"id": eid, "name": name, "names": {n: 1 for n in names}, "pronoun": pronoun, "cat": "PER",
            "profiles": {"b1": prof(**profile)} if profile else {}}


class Settings(unittest.TestCase):
    def test_saved_values_are_laid_over_the_defaults_and_bad_ones_ignored(self):
        s = M.settings({"weights": {"name": 0.5, "nope": 1, "title": -1}, "match": 0.7, "possible": "x",
                        "embedding": {"use": True, "model": " "}})
        self.assertEqual((s["weights"]["name"], s["weights"]["title"], s["match"], s["possible"]), (0.5, 0.10, 0.7, 0.35))
        self.assertEqual(s["embedding"], {"use": True, "model": M.DEFAULTS["embedding"]["model"]})
        self.assertNotIn("nope", s["weights"])
        self.assertEqual(M.settings(), M.DEFAULTS)


class Scoring(unittest.TestCase):
    def test_the_one_pass_specific_similarity_agrees_with_the_two_pass_definition(self):
        """`_specific_dot` is dot × (Σ a·b·idf / dot / max idf); check the shortcut against that on random profiles."""
        import random
        rnd = random.Random(5)
        vocab = [f"w{i}" for i in range(30)]

        def random_profile():
            return prof(names={f"N{rnd.randrange(50)}": 1},
                        nouns={w: rnd.randint(1, 5) for w in rnd.sample(vocab, rnd.randint(1, 8))},
                        places={w: rnd.randint(1, 4) for w in rnd.sample(vocab, rnd.randint(2, 6))})
        groups = {i: random_profile() for i in range(1, 25)}
        m = M.Matcher({f"k{i}": entry(f"k{i}", f"E{i}", **{k: v for k, v in random_profile().items() if k in ("nouns", "places")})
                       for i in range(15)}, groups)
        self.assertGreaterEqual(m.population, M.MIN_POPULATION)
        for f in ("nouns", "places"):
            for cid, g in m.groups.items():
                for e in m.entries.values():
                    a, b = g.vec[f], e.vec[f]
                    dot = M._dot(a, b)
                    want = 0.0 if not dot else dot * (sum(x * b.get(k, 0.0) * m.idf[f].get(k, m.default_idf) for k, x in a.items())
                                                      / (dot * m.default_idf))
                    self.assertAlmostEqual(m._specific_dot(f, g, e), want, places=9)

    def match(self, group, entries):
        m = M.Matcher({e["id"]: e for e in entries}, {1: group})
        ranked = m.rank(1)
        return m, ranked, m.decide(ranked)

    def test_the_same_name_alone_is_only_a_choice(self):
        _, r, d = self.match(prof(names={"Perkins": 3}), [entry("k1", "Perkins")])
        self.assertEqual((d, r[0]["score"]), ("possible", 0.7))  # 0.35 / min_evidence 0.5, under match 0.72
        self.assertEqual(r[0]["parts"][0]["why"], "“Perkins” is one of its names")
        # any supporting evidence makes it a match
        _, r, d = self.match(prof(names={"Lestrade": 3}, places={"scotland yard": 2}),
                             [entry("k1", "Lestrade", places={"scotland yard": 4})])
        self.assertEqual((d, r[0]["score"]), ("match", 0.75))

    def test_names_without_titles_and_shared_name_words(self):
        m, _, _ = self.match(prof(names={"Mr. Sherlock Holmes": 1}), [entry("k1", "Sherlock Holmes"), entry("k2", "Holmes")])
        self.assertEqual(m.score(1, "k1")["parts"][0]["sim"], 0.9)
        m, _, _ = self.match(prof(names={"that fool Lestrade": 1}), [entry("k1", "Inspector Lestrade")])
        self.assertEqual(m.score(1, "k1")["parts"][0]["sim"], 0.7)  # lower-case words don't dilute the name

    def test_supporting_evidence_decides_between_two_characters_with_one_surname(self):
        group = prof(names={"Holmes": 5}, cooccur={"n:watson": 4}, places={"baker street": 2}, others={"n:watson": "Watson"})
        sherlock = entry("k1", "Sherlock Holmes", ["Holmes"], cooccur={"n:watson": 9}, places={"baker street": 5})
        mycroft = entry("k2", "Mycroft Holmes", ["Holmes"], cooccur={"n:pycroft": 2}, places={"diogenes club": 3})
        _, r, d = self.match(group, [sherlock, mycroft])
        self.assertEqual((d, r[0]["entry"]), ("match", "k1"))
        self.assertEqual({p["kind"] for p in r[0]["parts"]}, {"name", "cooccur", "places"})
        self.assertIn("Watson", [p for p in r[0]["parts"] if p["kind"] == "cooccur"][0]["why"])

    def test_two_equally_likely_characters_are_a_choice(self):
        _, r, d = self.match(prof(names={"Holmes": 5}), [entry("k1", "Sherlock Holmes", ["Holmes"]),
                                                         entry("k2", "Mycroft Holmes", ["Holmes"])])
        self.assertEqual((d, len(r)), ("possible", 2))

    def test_contradictions_are_penalties(self):
        _, r, d = self.match(prof(names={"Mrs. Hudson": 2}, gender="f"), [entry("k1", "Mr. Hudson", pronoun="he/him/his")])
        self.assertEqual({p["kind"] for p in r[0]["penalties"]} if r else set(), {"gender", "title_gender"} if r else set())
        self.assertIsNone(d)  # 0.9 × 0.35/0.5 = 0.63, less 0.3 and 0.2
        m, _, _ = self.match(prof(names={"Sherlock Holmes": 2}), [entry("k1", "Mycroft Holmes")])
        self.assertEqual([p["kind"] for p in m.score(1, "k1")["penalties"]], ["first_name"])

    def test_a_group_without_a_name_is_at_most_possible(self):
        group = prof(nouns={"landlady": 3}, cooccur={"n:holmes": 5}, places={"baker street": 2})
        hudson = entry("k1", "Mrs. Hudson", nouns={"landlady": 6}, cooccur={"n:holmes": 20}, places={"baker street": 9})
        _, r, d = self.match(group, [hudson, entry("k2", "Lestrade", nouns={"inspector": 5})])
        self.assertEqual((d, r[0]["entry"], r[0]["named"]), ("possible", "k1", False))
        self.assertAlmostEqual(r[0]["score"], 0.25)  # nouns 0.05 + neighbours 0.15 + places 0.05, all alike

    def test_other_characters_are_compared_through_their_entries(self):
        # Watson is "n:watson" in one book and linked ("k:w") in the other: both mean the entry k:w
        group = prof(names={"Holmes": 1}, cooccur={"n:watson": 3})
        holmes = entry("k1", "Holmes", cooccur={"k:w": 3})
        m, _, _ = self.match(group, [holmes, entry("w", "Watson")])
        self.assertEqual([p for p in m.score(1, "k1")["parts"] if p["kind"] == "cooccur"][0]["sim"], 1.0)

    def test_speech_needs_enough_words_and_three_speakers(self):
        def speaker(**fw):
            return {"quotes": 10, "words": 200, "fw": fw}
        a = entry("k1", "Holmes", speech=speaker(i=20, the=5, you=10))
        b = entry("k2", "Watson", speech=speaker(i=5, the=20, you=2))
        c = entry("k3", "Hudson", speech=speaker(i=10, the=10, you=20))
        m, _, _ = self.match(prof(names={"Holmes": 1}, speech=speaker(i=19, the=6, you=11)), [a, b, c])
        sp = {eid: [p for p in m.score(1, eid)["parts"] if p["kind"] == "speech"] for eid in ("k1", "k2")}
        self.assertGreater(sp["k1"][0]["sim"], 0.5)
        self.assertEqual([p["sim"] for p in sp["k2"]], [0.0])  # unlike speech is shown but adds nothing
        m, _, _ = self.match(prof(names={"Holmes": 1}, speech={"quotes": 1, "words": 5, "fw": {"i": 1}}), [a, b, c])
        self.assertNotIn("speech", [p["kind"] for p in m.score(1, "k1")["parts"]])

    def test_entries_and_groups_can_be_added_later(self):
        m = M.Matcher({"k1": entry("k1", "Holmes")}, {})
        m.add_group(7, prof(names={"Watson": 1}))
        self.assertEqual(m.rank(7), [])
        m.add_entry("k2", entry("k2", "Watson"))
        self.assertEqual([r["entry"] for r in m.rank(7)], ["k2"])


class Recording(CarryBase):
    def test_a_checked_group_joins_a_character_only_on_clear_evidence(self):
        self.st.edit_group(1, {"checked": True})
        watson = self.colls.get_list(self.coll["id"])["characters"][0]["id"]
        nxt = self.second_book()
        nxt.edit_group(1, {"checked": True})  # the same Watson in the next book
        self.assertEqual(nxt.clusters()[1]["carry"], watson)
        self.colls.edit_entry(self.coll["id"], "characters", watson, {"pronoun": "she/her"})
        nxt.edit_group(2, {"name": "Watson"})  # Mrs. Hudson renamed: same name, but she / Mrs. contradict
        nxt.edit_group(2, {"checked": True})
        self.assertNotEqual(nxt.clusters()[2]["carry"], watson)
        self.assertEqual(len(self.colls.get_list(self.coll["id"])["characters"]), 2)

    def test_a_book_is_compared_with_the_profiles_from_the_other_books(self):
        self.st.edit_group(0, {"checked": True})
        holmes = self.st.clusters()[0]["carry"]
        new = self.st.regroup([self.uid_at(6, 0)], None)["target"]    # a stray "Holmes" in the book Holmes was recorded from
        m = self.st._carry_matcher()
        self.assertEqual(m.entries[holmes].prof["mentions"], 0)      # its profile from this book is left out; its names count
        self.assertEqual([p["kind"] for p in m.score(new, holmes)["parts"]], ["name"])
        r = self.st.carry_review()
        self.assertEqual(([c["name"] for c in r["characters"]], [a["group"] for a in r["ambiguous"]]), ([], [new]))
        nxt = self.second_book()                                       # in the next book it is another book's profile
        self.assertIn("cooccur", [p["kind"] for p in nxt._carry_matcher().score(0, holmes)["parts"]])

    def test_a_group_hidden_in_the_same_step_is_not_recorded(self):
        self.st.edit_group(1, {"checked": True, "hidden": True})
        self.assertEqual(self.colls.get_list(self.coll["id"])["characters"], [])

    def test_only_character_types_are_recorded(self):
        self.st.edit_group(3, {"name": "The violin"})       # a FAC group with a name, checked
        self.st.edit_group(3, {"checked": True})
        self.assertEqual(self.colls.get_list(self.coll["id"])["characters"], [])
        self.assertNotIn(3, self.st._carry_candidates())
        self.st.set_book_types(["PER", "FAC"])               # FAC chosen for the .book: now it is a character type
        self.st.edit_group(3, {"checked": True})
        self.assertEqual([e["name"] for e in self.colls.get_list(self.coll["id"])["characters"]], ["The violin"])

    def test_update_profiles_records_checked_groups_and_repairs_stale_links(self):
        with self.st.batch("stale"):
            self.st._group_upsert(0, {"checked": 1, "carry": "kgone"})
        out = self.st.carry_update_profiles()
        self.assertEqual((out["recorded"], out["linked"]), (1, 1))
        e = self.colls.get_list(self.coll["id"])["characters"][0]
        self.assertEqual((e["name"], self.st.clusters()[0]["carry"], list(e["profiles"])), ("Holmes", e["id"], ["fix"]))
        self.st.undo()  # the new link is one undoable step
        self.assertEqual(self.st.clusters()[0]["carry"], "kgone")


class MergeSettings(CarryBase):
    """Merge suggestions follow the suggestion settings (core/suggest.py, type "merge")."""

    def pair(self):
        """The suggested pair of Holmes (0) and a stray "Holmes" split off from him, or None."""
        new = self.st.regroup([self.uid_at(6, 0)], None)["target"]
        return new, next((p for p in self.st.merge_suggestions()["items"] if {p["a"]["id"], p["b"]["id"]} == {0, new}), None)

    def test_weights_switches_and_the_threshold_apply(self):
        new, p = self.pair()
        self.assertEqual(p["reasons"][0]["text"], "Same name: “Holmes” and “Holmes”")
        self.assertEqual(p["reasons"][0]["w"], 3.0)
        self.colls.set_suggestion_settings("merge", {"weights": {"same_name": 2.0}})
        self.assertEqual(self.pair_of(new)["reasons"][0]["w"], 2.0)      # the cached list follows the new settings
        self.colls.set_suggestion_settings("merge", {"off": ["same_name"]})
        self.assertIsNone(self.pair_of(new))                             # no name evidence left: not a candidate
        self.colls.set_suggestion_settings("merge", None)
        self.colls.set_suggestion_settings("merge", {"thresholds": {"min_score": 9}})
        self.assertIsNone(self.pair_of(new))

    def pair_of(self, new):
        """The pair of Holmes and `new` in the current suggestions, or None."""
        return next((p for p in self.st.merge_suggestions()["items"] if {p["a"]["id"], p["b"]["id"]} == {0, new}), None)

    def test_profiles_alike_count_for_a_pair(self):
        self.colls.set_suggestion_settings("merge", {"off": []})         # the locality kinds too, for this test
        new, p = self.pair()
        profile = [r for r in p["reasons"] if r["text"].startswith(("Appear with", "Act alike", "Described alike", "Same places"))]
        self.assertTrue(profile, p["reasons"])
        self.colls.set_suggestion_settings("merge", {"off": [e["key"] for e in suggest.TYPES["merge"]["evidence"] if e.get("profile")]})
        self.assertFalse([r for r in self.pair_of(new)["reasons"] if r["text"].startswith(("Appear with", "Act alike"))])

    def test_a_book_not_opened_from_the_library_uses_the_defaults(self):
        self.st.carry_colls = None
        self.assertEqual(self.st.suggest_cfg("merge"), suggest.settings()["merge"])


class FragmentSettings(CarryBase):
    """Fragment targets are scored from the suggestion settings (core/suggest.py, type "fragments")."""

    def targets(self):
        """The candidates offered for a stray "Holmes" split off from Holmes (0), by id."""
        new = self.st.regroup([self.uid_at(6, 0)], None)["target"]
        f = next(x for x in self.st.fragments(max_count=2)["items"] if x["id"] == new)
        return {c["id"]: c for c in f["candidates"]}, [c["id"] for c in f["candidates"]]

    def test_candidates_have_a_score_and_reasons(self):
        cands, order = self.targets()
        self.assertEqual(order[0], 0)                                   # Holmes: a merge suggestion, near, same type
        texts = [r["text"] for r in cands[0]["reasons"]]
        self.assertTrue(texts[0].startswith("Same name"), texts)
        self.assertAlmostEqual(cands[0]["score"], sum(r["w"] for r in cands[0]["reasons"]), places=1)

    def test_switching_evidence_off_changes_the_scores(self):
        before = self.targets()[0][0]["score"]
        self.st.undo()
        self.colls.set_suggestion_settings("fragments", {"off": ["merge", "p_cooccur", "p_places"]})
        after = self.targets()[0][0]
        self.assertLess(after["score"], before)
        self.assertFalse([r for r in after["reasons"] if r["text"].startswith("Same name")])

    def test_the_window_limits_what_counts_as_nearby(self):
        self.colls.set_suggestion_settings("fragments", {"thresholds": {"window": 1}, "off": ["merge"]})
        cands, _ = self.targets()
        self.assertFalse([c for c in cands.values() if any(r["text"].startswith("Mentioned") for r in c["reasons"])])


class SpeakerSettings(CarryBase):
    """Speaker suggestions follow the suggestion settings (core/suggest.py, type "speaker")."""

    def suggestion(self):
        """The suggestion for Watson's quote after giving it to Mrs. Hudson (its tag, "said Watson", says otherwise)."""
        self.st.set_speakers([self.uid_at(1, 0, 5, table="quotes")], 2)
        return next((i for i in self.st.quote_suggestions()["items"] if i["cur"] and i["cur"]["id"] == 2 and i["s"] == 4), None)

    def test_the_tag_weight_and_the_margin_apply(self):
        sug = self.suggestion()
        self.assertEqual((sug["best"]["name"], sug["best_reasons"][0]["w"]), ("Watson", 4.0))
        self.colls.set_suggestion_settings("speaker", {"weights": {"tag_name": 2.0}})
        self.assertEqual(self.st.quote_suggestions()["items"][0]["best_reasons"][0]["w"], 2.0)
        self.colls.set_suggestion_settings("speaker", {"thresholds": {"margin": 5}})
        self.assertFalse([i for i in self.st.quote_suggestions()["items"] if i["kind"] == "other"])
        self.colls.set_suggestion_settings("speaker", None)
        self.colls.set_suggestion_settings("speaker", {"off": ["tag_name"]})
        self.assertFalse([i for i in self.st.quote_suggestions()["items"] if i["best"] and i["best"]["name"] == "Watson"])


class SoundsLikeWords(unittest.TestCase):
    def setUp(self):
        from luna.core.tools.quote import SoundsLike
        words = "I shall examine the footprint . The footprint of a hound . You look well . I shall examine the ash".split()
        pos = ["PRON", "AUX", "VERB", "DET", "NOUN", "PUNCT", "DET", "NOUN", "ADP", "DET", "NOUN", "PUNCT", "PRON", "VERB", "ADV", "PUNCT",
               "PRON", "AUX", "VERB", "DET", "NOUN"]
        self.T = {"word": words, "lemma": [w.lower() for w in words], "pos": pos}
        self.quotes = [{"uid": 1, "s": 0, "e": 5, "char_id": 7}, {"uid": 2, "s": 6, "e": 11, "char_id": 7},
                       {"uid": 3, "s": 12, "e": 15, "char_id": 8}, {"uid": 4, "s": 16, "e": 20, "char_id": 8}]
        self.S = SoundsLike

    def test_a_quote_sounds_like_the_speaker_who_uses_its_words_but_never_like_itself(self):
        s = self.S(self.T, self.quotes, (1.0, 1.0), {}, str)
        like7, like8 = s.like(self.quotes[3], 7), s.like(self.quotes[3], 8)
        self.assertTrue(like7 and like7[0][1].startswith("Sounds like 7’s other quotes") and "examine" in like7[0][1])
        self.assertEqual(like8, [])                     # 8's only other quote ("You look well") shares nothing
        self.assertEqual(s.like(self.quotes[2], 8), [])    # its own words don't count for its current speaker

    def test_the_collection_counts_too_and_weights_switch_it_off(self):
        s = self.S(self.T, self.quotes, (0.0, 1.0), {8: Counter({"examine": 3, "ash": 2})}, str)
        self.assertEqual(s.like(self.quotes[3], 8)[0][1], "Sounds like 8 in the other books (98%: ash, examine)")
        self.assertEqual(self.S(self.T, self.quotes, (0.0, 0.0), {}, str).like(self.quotes[3], 7), [])

    def test_the_quick_comparison_agrees_with_building_whole_vectors(self):
        """`like` reads the similarity off the quote's own words; check it against the plain definition (a cosine of full
        tf-idf vectors, the quote's words taken out of its current speaker's) on random speakers."""
        import math
        import random
        rnd = random.Random(3)
        vocab = [f"w{i}" for i in range(40)]
        words, quotes, uid = [], [], 0
        for _ in range(60):
            n = rnd.randint(2, 9)
            quotes.append({"uid": uid, "s": len(words), "e": len(words) + n - 1, "char_id": rnd.choice([1, 2, 3, None])})
            words += [rnd.choice(vocab) for _ in range(n)]
            uid += 1
        T = {"word": words, "lemma": words, "pos": ["NOUN"] * len(words)}
        s = self.S(T, quotes, (1.0, 0.0), {}, str)

        def plain(q, g):
            own = s.book[g] - s.qwords[q["uid"]] if q["char_id"] == g else s.book[g]
            qv, cv = s._vec(s.qwords[q["uid"]]), s._vec(own)
            return sum(x * cv.get(w, 0.0) for w, x in qv.items())

        for q in quotes:
            for g in (1, 2, 3):
                got = s.like(q, g)
                want = plain(q, g)
                if want >= 0.05:
                    self.assertAlmostEqual(got[0][0], want, places=9)
                    self.assertEqual(got[0][1].split("(")[1].split("%")[0], str(round(100 * want)))
                else:
                    self.assertEqual(got, [])


if __name__ == "__main__":
    unittest.main()
