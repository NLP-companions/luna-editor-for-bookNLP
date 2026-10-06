"""The Entities tab's data: group list and filters, fragments, merge suggestions, scopes, the quote rule."""
from __future__ import annotations

import unittest

from luna.core.errors import EditError
from tests.fixture import BookCase, ords


class GroupList(BookCase):
    def test_types_and_counts(self):
        r = self.st.groups_list()
        self.assertEqual(r["cats"], {"PER": 3, "FAC": 2})
        self.assertEqual([c["id"] for c in r["items"]], [2, 0, 1, 3, 4])  # most mentions first
        self.assertEqual(r["progress"], {"checked": 0, "total": 5})

    def test_sorting_and_search(self):
        self.assertEqual([c["id"] for c in self.st.groups_list(sort="name")["items"]], [0, 2, 4, 3, 1])
        self.assertEqual([c["id"] for c in self.st.groups_list(sort="id")["items"]], [0, 1, 2, 3, 4])
        self.assertEqual([c["id"] for c in self.st.groups_list(q="hud")["items"]], [2])
        self.assertEqual([c["id"] for c in self.st.groups_list(q="1")["items"]], [1])
        self.assertEqual(self.st.groups_list(q="nobody")["total"], 0)

    def test_var_mentions_inside_a_mixed_group_are_listed_under_var(self):
        """A group is listed under every type it has a mention of, not only its most common one."""
        self.st.edit_entities([self.uid_at(5, 2)], {"cat": "VAR"})  # "herself" inside Mrs. Hudson's PER group
        r = self.st.groups_list()
        self.assertEqual(r["cats"], {"PER": 3, "FAC": 2, "VAR": 1})
        self.assertEqual([c["id"] for c in self.st.groups_list(cat="VAR")["items"]], [2])
        self.assertIn(2, [c["id"] for c in self.st.groups_list(cat="PER")["items"]])  # it is still a PER group
        self.assertEqual(self.st.clusters()[2]["cat"], "PER")
        self.assertEqual(self.st.clusters()[2]["cats"], {"PER": 4, "VAR": 1})

    def test_group_that_is_all_var_is_listed_under_var_only(self):
        self.st.edit_entities([self.uid_at(7, 0, 1)], {"cat": "VAR"})
        r = self.st.groups_list()
        self.assertEqual(r["cats"], {"PER": 3, "FAC": 1, "VAR": 1})
        self.assertEqual([c["id"] for c in self.st.groups_list(cat="VAR")["items"]], [3])
        self.assertEqual([c["id"] for c in self.st.groups_list(cat="FAC")["items"]], [4])

    def test_var_filter_respects_hidden_and_progress(self):
        self.st.edit_entities([self.uid_at(5, 2)], {"cat": "VAR"})
        self.st.edit_group(2, {"hidden": True})
        self.assertEqual(self.st.groups_list()["cats"].get("VAR"), None)
        self.assertEqual(self.st.groups_list(hidden=True)["cats"]["VAR"], 1)
        self.assertEqual(self.st.groups_list(cat="VAR")["progress"], {"checked": 0, "total": 0})

    def test_group_detail(self):
        d = self.st.group_detail(2)
        self.assertEqual((d["total"], d["cat"]), (5, "PER"))
        self.assertEqual([v["text"] for v in d["variants"]][0], "Mrs. Hudson")  # names before descriptions before pronouns
        self.assertEqual(len(d["mentions"]), 5)
        self.assertEqual(self.st.group_detail(2, offset=3, limit=10)["mentions"][0]["uid"], d["mentions"][3]["uid"])
        with self.assertRaises(EditError):
            self.st.group_detail(99)

    def test_group_names_prefer_names_over_pronouns(self):
        c = self.st.clusters()
        self.assertEqual((c[0]["name"], c[1]["name"], c[2]["name"], c[3]["name"]), ("Holmes", "Watson", "Mrs. Hudson", "The violin"))
        self.assertEqual(c[2]["pronoun"], "she/her")  # from BookNLP's .book
        self.assertEqual(c[1]["quotes"], 1)

    def test_mentions_detail_and_hotkeys(self):
        r = self.st.mentions_detail([self.uid_at(0, 0), 9999])
        self.assertEqual((len(r["items"]), r["missing"]), (1, 1))
        self.st.set_hotkey("3", 1)
        self.assertEqual(self.st.hotkeys()["3"]["id"], 1)
        self.st.set_hotkey("4", 1)  # a group has one key
        self.assertEqual(list(self.st.hotkeys()), ["4"])
        self.st.set_hotkey("4", None)
        self.assertEqual(self.st.hotkeys(), {})
        with self.assertRaises(EditError):
            self.st.set_hotkey("0", 1)
        with self.assertRaises(EditError):
            self.st.set_hotkey("1", 99)
        self.st.set_hotkey("1", 0)
        self.st.merge_groups(1, [0])  # the pinned group is merged away: its key disappears rather than pointing nowhere
        self.assertEqual(self.st.hotkeys(), {})


class Fragments(BookCase):
    def test_lists_small_groups_in_reading_order(self):
        r = self.st.fragments(max_count=1)
        self.assertEqual([i["id"] for i in r["items"]], [3, 4])
        self.assertEqual(r["total"], 2)
        self.assertEqual(self.st.fragments(max_count=1, cat="FAC")["total"], 2)
        self.assertEqual(self.st.fragments(max_count=1, cat="PER")["total"], 0)

    def test_offers_every_mention_to_delete_not_only_the_ones_shown(self):
        r = self.st.fragments(max_count=5)
        big = next(i for i in r["items"] if i["id"] == 2)
        self.assertEqual(big["count"], 5)
        self.assertLess(len(big["mentions"]), 5)  # only a few are shown in context
        self.assertEqual(len(big["uids"]), 5)  # but deleting the fragment must remove them all
        self.st.delete_entities(big["uids"])
        self.assertNotIn(2, self.st.clusters())

    def test_checked_and_hidden_groups_are_left_out(self):
        self.st.edit_group(3, {"checked": True})
        self.st.edit_group(4, {"hidden": True})
        self.assertEqual(self.st.fragments(max_count=1)["total"], 0)

    def test_type_filter_counts_var_mentions(self):
        self.st.edit_entities([self.uid_at(7, 4, 5)], {"cat": "VAR"})
        self.assertEqual([i["id"] for i in self.st.fragments(max_count=1, cat="VAR")["items"]], [4])

    def test_candidates_include_nearby_groups(self):
        item = self.st.fragments(max_count=1)["items"][0]
        self.assertTrue(item["candidates"])
        self.assertNotIn(item["id"], [c["id"] for c in item["candidates"]])


class MergeSuggestions(BookCase):
    def _split_holmes(self):
        r = self.st.regroup([self.uid_at(6, 0)], None, "Sherlock Holmes")
        return r["target"]

    def test_same_name_groups_are_suggested_and_dismissable(self):
        new = self._split_holmes()
        r = self.st.merge_suggestions()
        pair = next(p for p in r["items"] if {p["a"]["id"], p["b"]["id"]} == {0, new})
        self.assertGreaterEqual(pair["score"], 1.0)
        self.assertTrue(any("part of" in x["text"] or "Same name" in x["text"] for x in pair["reasons"]))
        self.assertEqual([p["key"] for p in self.st.merge_suggestions(group=new)["items"]], [pair["key"]])
        self.st.dismiss_pair(pair["key"])
        self.assertFalse([p for p in self.st.merge_suggestions()["items"] if p["key"] == pair["key"]])

    def test_different_pronouns_count_against(self):
        # give one group he and the other she: the pair is no longer suggested
        new = self._split_holmes()
        self.st.edit_group(new, {"pronoun": "she/her"})
        self.st.edit_group(0, {"pronoun": "he/him/his"})
        self.assertFalse([p for p in self.st.merge_suggestions()["items"] if {p["a"]["id"], p["b"]["id"]} == {0, new}])

    def test_same_name_bundles(self):
        new = [self.st.regroup([u], None, None)["target"] for u in (self.uid_at(6, 0), self.uid_at(4, 5))]
        (b,) = self.st.merge_suggestions()["bundles"]         # three groups called Holmes, nothing against them
        self.assertEqual((b["keep"], sorted(d["id"] for d in b["drops"]), b["mentions"]), (0, sorted(new), 2))

    def test_a_bundle_needs_the_same_name_somewhere_and_nothing_against(self):
        def pair(x, y, *reasons):
            """A merge suggestion of groups x and y with reasons (weight, text), strongest first."""
            g = lambda i: {"id": i, "name": "Holmes", "cat": "PER", "count": 10 - i}
            return {"a": g(x), "b": g(y), "a_ctx": None, "b_ctx": None, "reasons": [{"w": w, "text": t} for w, t in reasons]}
        same = (0.5, "Same name: “Holmes” and “Holmes”")
        items = [pair(1, 2, (1.2, "Act alike (80%)"), same), pair(2, 3, same)]   # the name needn't be the strongest reason
        self.assertEqual([(b["keep"], [d["id"] for d in b["drops"]]) for b in self.st._bundles(items)], [(1, [2, 3])])
        items[1]["reasons"].append({"w": -0.5, "text": "Named together in 2 sentences"})
        self.assertEqual(self.st._bundles(items), [])


class Scopes(BookCase):
    def test_select_by_scope(self):
        anchor = self.uid_at(4, 5)  # "Holmes" in the quote
        s = self.st
        self.assertEqual(len(s.select_scope([anchor], "sentence")["uids"]), 2)  # "You" and "Holmes": the same group
        r = s.select_scope([anchor], "sentence", groups="all")
        self.assertEqual(r["n"], 3)  # You, Holmes, she
        self.assertEqual(s.select_scope([anchor], "paragraph", groups="all")["n"], 10)
        self.assertEqual(s.select_scope([anchor], "quote", groups="all")["n"], 2)
        self.assertEqual(s.select_scope([anchor], "book", groups=[2])["n"], 5)
        a, b = ords(0, 0, 3)
        self.assertEqual(s.select_scope([], "passage", groups="all", a=a, b=b)["n"], 2)
        with self.assertRaises(EditError):
            s.select_scope([self.uid_at(0, 0)], "quote")  # not inside a quote
        with self.assertRaises(EditError):
            s.select_scope([anchor], "galaxy")
        with self.assertRaises(EditError):
            s.select_scope([99999], "sentence")

    def test_conversation_spans_neighbouring_quotes(self):
        anchor = self.uid_at(4, 5)
        lo, hi = self.st.scope_bounds("conversation", ords(4, 5)[0])
        self.assertEqual(lo, ords(1, 0)[0])
        self.assertEqual(hi, ords(4, 7)[0])

    def test_nearby_groups_and_stepping(self):
        a, b = ords(3, 0, 1)
        near = self.st.nearby_groups(a, b, exclude=[2])
        self.assertTrue(near)
        self.assertNotIn(2, [n["id"] for n in near])
        first = self.st.step_mention(0, -1, "next")
        self.assertEqual((first["index"], first["total"]), (1, 4))
        last = self.st.step_mention(0, -1, "prev")
        self.assertEqual(last["index"], 4)
        self.assertEqual(self.st.step_mention(0, first["s"], "prev")["index"], 4)  # wraps round
        with self.assertRaises(EditError):
            self.st.step_mention(99, 0, "next")


class QuoteRule(BookCase):
    def test_moves_first_person_pronouns_in_quotes_to_the_speaker(self):
        i = self.uid_at(1, 1)  # "I", spoken by Watson (group 1)
        self.assertEqual(self.st.quote_rule()["n"], 0)
        self.st.regroup([i], 0)
        r = self.st.quote_rule()
        self.assertEqual((r["n"], r["items"][0]["to"], r["items"][0]["from"]), (1, 1, 0))
        self.assertEqual(self.st.quote_rule(exclude=[i])["n"], 0)
        self.assertEqual(self.st.quote_rule(scope="passage", a=0, b=1)["n"], 0)
        self.st.quote_rule(apply=True)
        self.assertEqual(self.ent(i)["coref"], 1)
        with self.assertRaises(EditError):
            self.st.quote_rule(apply=True)

    def test_quote_and_conversation_scope(self):
        i = self.uid_at(1, 1)
        self.st.regroup([i], 0)
        q = self.uid_at(1, 0, 5, table="quotes")
        self.assertEqual(self.st.quote_rule(scope="quote", quote=q)["n"], 1)
        self.assertEqual(self.st.quote_rule(scope="conversation", quote=q)["n"], 1)
        other = self.uid_at(4, 0, 7, table="quotes")
        self.assertEqual(self.st.quote_rule(scope="quote", quote=other)["n"], 0)
        with self.assertRaises(EditError):
            self.st.quote_rule(scope="quote")

    def test_quotes_without_speaker_are_counted_not_changed(self):
        self.st.set_speakers([self.uid_at(1, 0, 5, table="quotes")], None)
        r = self.st.quote_rule()
        self.assertEqual((r["n"], r["no_speaker"]), (0, 1))


class OtherSide(BookCase):
    """`_other_side` is a pure helper (no DB, no lock needed): who a quote's "you" probably addresses."""

    def test_nearest_other_speaker_either_side(self):
        conv = [{"uid": 1, "char_id": 10}, {"uid": 2, "char_id": 99}, {"uid": 3, "char_id": 20}]
        self.assertEqual(self.st._other_side(conv, conv[1], 99), 10)  # ties go to the quote just before

    def test_skips_quotes_with_no_speaker_or_the_excluded_one(self):
        conv = [{"uid": 1, "char_id": None}, {"uid": 2, "char_id": 99}, {"uid": 3, "char_id": 20}]
        self.assertEqual(self.st._other_side(conv, conv[1], 99), 20)  # before has no speaker: look after
        conv = [{"uid": 1, "char_id": 10}, {"uid": 2, "char_id": 10}, {"uid": 3, "char_id": 10}]
        self.assertIsNone(self.st._other_side(conv, conv[1], 10))  # every other quote is the excluded speaker

    def test_alone_in_its_conversation(self):
        conv = [{"uid": 1, "char_id": 10}]
        self.assertIsNone(self.st._other_side(conv, conv[0], 10))


class QuoteSpeakerPronouns(BookCase):
    """Reassigning a quote's speaker with `quote_rule=True` (Store.set_speakers): I/me/... follow the new speaker,
    you/... follow the other side of the conversation, when there is one."""

    def test_first_person_follows_the_new_speaker(self):
        i = self.uid_at(1, 1)  # "I", correctly Watson's group (1) in the fixture
        self.st.regroup([i], 0)  # break it, as in the quote_rule tests
        q1 = self.uid_at(1, 0, 5, table="quotes")
        r = self.st.set_speakers([q1], 1, quote_rule=True)  # reassign to the same speaker: still a move to apply
        self.assertEqual(r["moved"], 1)
        self.assertEqual(self.ent(i)["coref"], 1)

    def test_second_person_follows_the_other_side_of_the_conversation(self):
        you = self.uid_at(4, 1)  # "You", group 0 (Holmes) in the fixture: the one q1's speaker (Watson) addresses
        self.assertEqual(self.ent(you)["coref"], 0)
        q1, q2 = self.uid_at(1, 0, 5, table="quotes"), self.uid_at(4, 0, 7, table="quotes")
        self.assertEqual(self.st.scope_bounds("conversation", self.book.ords(4, 5)[0]),
                          (self.book.ords(1, 0)[0], self.book.ords(4, 7)[0]))  # q1 and q2 share a conversation
        r = self.st.set_speakers([q2], 3, quote_rule=True)  # reassign q2 away from both q1's speaker and "you"'s group
        self.assertEqual(r["moved"], 1)
        self.assertEqual(self.ent(you)["coref"], 1)  # "you" now follows q1's speaker (Watson), the other side

    def test_no_pronoun_move_without_the_flag_or_without_a_speaker(self):
        i = self.uid_at(1, 1)
        self.st.regroup([i], 0)
        q1 = self.uid_at(1, 0, 5, table="quotes")
        r = self.st.set_speakers([q1], 1)  # quote_rule defaults to False
        self.assertEqual(r["moved"], 0)
        self.assertEqual(self.ent(i)["coref"], 0)
        r = self.st.set_speakers([q1], None, quote_rule=True)  # no speaker: nothing to move pronouns to
        self.assertEqual(r["moved"], 0)


class Quotes(BookCase):
    def test_list_and_filter(self):
        r = self.st.quotes_list()
        self.assertEqual((r["total"], r["no_speaker"]), (2, 0))
        self.assertEqual(self.st.quotes_list(speaker="2")["total"], 1)
        self.assertEqual(self.st.quotes_list(q="tired")["total"], 1)
        self.st.set_speakers([r["items"][0]["uid"]], None)
        self.assertEqual(self.st.quotes_list(speaker="none")["total"], 1)
        self.assertEqual(r["items"][0]["speaker"], "Watson")


class EntityContext(BookCase):
    """entity_context: excerpts around a group's mentions and quotes, for the annotation view's entity-filter mode."""

    def test_excerpts_merge_when_they_touch_or_overlap(self):
        r = self.st.entity_context(0, context=0)  # Holmes: mentions at sentences 0, 4, 6 — no context, no merging
        self.assertEqual(r["total"], 3)
        self.assertEqual([(i["excerpt_first"], i["excerpt_last"]) for i in r["items"]], [(0, 0), (4, 4), (6, 6)])
        r = self.st.entity_context(0, context=1)  # padded by 1: (0,1) and (3,5) now touch (3,7), so they merge
        self.assertEqual([(i["excerpt_first"], i["excerpt_last"]) for i in r["items"]], [(0, 1), (3, 7)])

    def test_quotes_count_as_context_even_without_checking_their_words(self):
        self.st.set_speakers([self.uid_at(4, 0, 7, table="quotes")], 1)  # Watson now also speaks the quote in sentence 4
        r = self.st.entity_context(1, context=0)  # his own mentions are at sentences 0-2 only
        self.assertTrue(any(i["excerpt_first"] <= 4 <= i["excerpt_last"] for i in r["items"]))

    def test_paging_and_a_group_with_nothing_to_show(self):
        r = self.st.entity_context(0, offset=1, limit=1, context=0)
        self.assertEqual((r["total"], len(r["items"]), r["items"][0]["excerpt_first"]), (3, 1, 4))
        self.assertEqual(self.st.entity_context(999)["total"], 0)

    def test_each_excerpt_is_a_window(self):
        r = self.st.entity_context(0, context=0, limit=1)
        self.assertEqual(r["items"][0]["tokens"], self.st.window(0, 1)["tokens"])


if __name__ == "__main__":
    unittest.main()

class SpellingCandidates(unittest.TestCase):
    """Merge candidates from name words spelled almost the same (`CorefTools._merge_by_spelling`)."""

    def pairs(self, tokens_by_group, cats=None):
        """The (group, group, reason) candidates found for groups with these name words (all PER unless `cats` says)."""
        from luna.core.tools.coref import CorefTools
        cl = {g: {"cat": (cats or {}).get(g, "PER")} for g in tokens_by_group}
        prof = {g: {"tokens": set(t)} for g, t in tokens_by_group.items()}
        got = []
        CorefTools()._merge_by_spelling(lambda x, y, w, why: got.append((min(x, y), max(x, y), why)), lambda k: 1.5, cl, prof, set(cl))
        return got

    def test_close_spellings_pair_up_across_groups(self):
        got = self.pairs({1: ["Stapleton", "Mary"], 2: ["Stapelton"], 3: ["Stapelton", "Barrymore"]})
        self.assertEqual(sorted((x, y) for x, y, _ in got), [(1, 2), (1, 3)])    # 2 and 3 share the very same word: not a spelling matter
        self.assertIn("“Stapelton” and “Stapleton” are spelled almost the same", got[0][2])

    def test_other_types_other_first_letters_and_short_words_are_left_alone(self):
        self.assertEqual(self.pairs({1: ["Stapleton"], 2: ["Stapelton"]}, cats={2: "LOC"}), [])
        self.assertEqual(self.pairs({1: ["Stapleton"], 2: ["Mtapleton"]}), [])
        self.assertEqual(self.pairs({1: ["Ann"], 2: ["Anne"]}), [])

    def test_a_crowded_block_is_still_compared(self):
        """Hundreds of distinct names with one first letter (a long book) are no reason to stop looking for variants."""
        crowd = {i: [f"Bxqz{i:04d}"] for i in range(10, 700)}
        got = self.pairs({**crowd, 1: ["Barrymore"], 2: ["Barrymoor"]})
        self.assertIn((1, 2), {(x, y) for x, y, _ in got})


if __name__ == "__main__":
    unittest.main()
