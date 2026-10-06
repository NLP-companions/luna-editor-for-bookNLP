"""Speaker suggestions, checks for impossible links, the narrator rule and the pronoun pass."""
from __future__ import annotations

import unittest

from luna.core.errors import EditError
from tests.fixture import Book, BookCase, SECOND

# Holmes' quote is tagged; the rest of the conversation is not. Watson (1) answers Holmes (0), then BookNLP wrongly
# has Watson speak again right after his own untagged turn, then again after a narrated beat has come between.
TURNS = Book(
    text=('Holmes met Watson.\n\n'
          '"I am cold," said Holmes.\n\n'
          '"So am I."\n\n'
          '"Perhaps."\n\n'
          'Time passed.\n\n'
          '"Indeed."\n'),
    sentences=[
        [("Holmes", "Holmes", "PROPN", "NNP", "nsubj", 1), ("met", "meet", "VERB", "VBD", "ROOT", 1),
         ("Watson", "Watson", "PROPN", "NNP", "dobj", 1), (".", ".", "PUNCT", ".", "punct", 1)],
        [('"', '"', "PUNCT", "``", "punct", 6), ("I", "I", "PRON", "PRP", "nsubj", 2), ("am", "be", "AUX", "VBP", "ccomp", 6),
         ("cold", "cold", "ADJ", "JJ", "acomp", 2), (",", ",", "PUNCT", ",", "punct", 2), ('"', '"', "PUNCT", "''", "punct", 6),
         ("said", "say", "VERB", "VBD", "ROOT", 6), ("Holmes", "Holmes", "PROPN", "NNP", "nsubj", 6), (".", ".", "PUNCT", ".", "punct", 6)],
        [('"', '"', "PUNCT", "``", "punct", 2), ("So", "so", "ADV", "RB", "advmod", 2), ("am", "be", "AUX", "VBP", "ROOT", 2),
         ("I", "I", "PRON", "PRP", "nsubj", 2), (".", ".", "PUNCT", ".", "punct", 2), ('"', '"', "PUNCT", "''", "punct", 2)],
        [('"', '"', "PUNCT", "``", "punct", 1), ("Perhaps", "perhaps", "ADV", "RB", "ROOT", 1),
         (".", ".", "PUNCT", ".", "punct", 1), ('"', '"', "PUNCT", "''", "punct", 1)],
        [("Time", "time", "NOUN", "NN", "nsubj", 1), ("passed", "pass", "VERB", "VBD", "ROOT", 1), (".", ".", "PUNCT", ".", "punct", 1)],
        [('"', '"', "PUNCT", "``", "punct", 1), ("Indeed", "indeed", "ADV", "RB", "ROOT", 1),
         (".", ".", "PUNCT", ".", "punct", 1), ('"', '"', "PUNCT", "''", "punct", 1)],
    ],
    para_starts={0, 1, 2, 3, 4, 5},
    entities=[(0, 0, 0, "PROP", "PER", 0), (0, 2, 2, "PROP", "PER", 1), (1, 7, 7, "PROP", "PER", 0)],
    supersenses=[],
    quotes=[(1, 0, 5, (1, 7, 7), 0), (2, 0, 5, None, 1), (3, 0, 3, None, 1), (5, 0, 3, None, 1)],
    genders={0: "he/him/his", 1: "he/him/his"},
)


class SpeakerSuggestions(BookCase):
    def test_no_suggestions_when_bookNLP_is_right(self):
        self.assertEqual(self.st.quote_suggestions()["total"], 0)

    def test_tag_names_a_different_speaker(self):
        q = self.uid_at(1, 0, 5, table="quotes")
        self.st.set_speakers([q], 0)  # wrong: the tag says "said Watson"
        r = self.st.quote_suggestions()
        self.assertEqual(r["total"], 1)
        it = r["items"][0]
        self.assertEqual((it["kind"], it["cur"]["id"], it["best"]["id"]), ("other", 0, 1))
        self.assertTrue(it["strong_tag"])
        self.assertEqual(it["best_mention"]["uid"], self.uid_at(1, 7))
        self.assertTrue(any("said" in x["text"] for x in it["best_reasons"]))
        self.assertEqual(len(r["bundles"]), 1)

    def test_missing_speaker_is_suggested_and_accepting_links_the_mention(self):
        q = self.uid_at(4, 0, 7, table="quotes")
        self.st.set_speakers([q], None)
        it = self.st.quote_suggestions()["items"][0]
        self.assertEqual((it["kind"], it["best"]["id"]), ("none", 2))
        r = self.st.apply_quote_suggestions([{"quote": q, "speaker": it["best"]["id"], "mention": it["best_mention"]["uid"]}])
        self.assertEqual(r["changed"], 1)
        row = self.st._row("quotes", q)
        self.assertEqual((row["char_id"], row["m_start"]), (2, self.ent(self.uid_at(4, 8))["tok_start"]))

    def test_dismissed_suggestions_stay_away(self):
        q = self.uid_at(1, 0, 5, table="quotes")
        self.st.set_speakers([q], 0)
        key = self.st.quote_suggestions()["items"][0]["key"]
        self.st.dismiss_quote_suggestion(key)
        self.assertEqual(self.st.quote_suggestions()["total"], 0)

    def test_apply_needs_something_to_apply(self):
        with self.assertRaises(EditError):
            self.st.apply_quote_suggestions([])

    def test_suggestions_follow_edits_and_undo(self):
        q = self.uid_at(1, 0, 5, table="quotes")
        self.assertEqual(self.st.quote_suggestions()["total"], 0)
        self.st.set_speakers([q], 0)
        self.assertEqual(self.st.quote_suggestions()["total"], 1)
        self.st.undo()
        self.assertEqual(self.st.quote_suggestions()["total"], 0)


class TurnTaking(BookCase):
    """The `turns` (return to the speaker two turns back) and `repeats` (a speaker rarely repeats right after an
    untagged turn change) evidence, on the TURNS book: Holmes (0) is tagged, then Watson (1) answers untagged, then
    BookNLP wrongly repeats Watson for the next two untagged turns, one adjacent and one after a narrated beat."""
    book = TURNS

    def test_repeat_right_after_a_turn_change_is_corrected(self):
        q = self.uid_at(3, 0, 3, table="quotes")   # "Perhaps." - BookNLP says Watson again, no narration since his last turn
        it = next(i for i in self.st.quote_suggestions()["items"] if i["uid"] == q)
        self.assertEqual((it["kind"], it["cur"]["id"], it["best"]["id"]), ("other", 1, 0))
        self.assertTrue(any("spoke the turn just before" in x["text"] for x in it["cur_reasons"]))

    def test_repeat_after_a_narrated_beat_is_left_alone(self):
        q = self.uid_at(5, 0, 3, table="quotes")   # "Indeed." - BookNLP says Watson again, but "Time passed." came between
        self.assertFalse(any(i["uid"] == q for i in self.st.quote_suggestions()["items"]))

    def test_first_untagged_answer_is_not_flagged(self):
        q = self.uid_at(2, 0, 5, table="quotes")   # "So am I." - Watson's first turn, nothing to compare it against yet
        self.assertFalse(any(i["uid"] == q for i in self.st.quote_suggestions()["items"]))


class ChecksMainBook(BookCase):
    def test_a_clean_book_has_no_checks(self):
        self.assertEqual(self.st.checks()["total"], 0)

    def test_reflexive_in_another_group(self):
        u = self.uid_at(5, 2)  # "herself" in "She helped herself"
        self.st.regroup([u], 0)
        r = self.st.checks(kinds=["reflexive"])
        self.assertEqual(r["total"], 1)
        x = r["items"][0]
        self.assertEqual((x["uid"], x["other"]["group"], x["group"]), (u, 2, 0))
        self.assertTrue(x["candidates"])
        self.st.dismiss_check(x["key"])
        self.assertEqual(self.st.checks(kinds=["reflexive"])["total"], 0)

    def test_object_pronoun_in_the_subjects_group(self):
        self.st.regroup([self.uid_at(6, 2)], 0)  # "Holmes thanked her" with her → Holmes
        r = self.st.checks(kinds=["objsubj"])
        self.assertEqual(r["total"], 1)
        self.assertEqual(r["items"][0]["other"]["text"], "Holmes")
        self.assertIn("that would be “herself”", r["items"][0]["why"])

    def test_pronoun_that_does_not_fit_a_place(self):
        self.st.merge_groups(3, [4])  # two FAC mentions, so the group is clearly a FAC group
        self.st.regroup([self.uid_at(4, 8)], 3)  # "she" in the FAC group "The violin"
        r = self.st.checks(kinds=["mismatch"], limit=50)
        self.assertEqual([x["why"] for x in r["items"]], ["“she” in a FAC group"])

    def test_pronoun_of_another_gender(self):
        self.st.regroup([self.uid_at(2, 0)], 2)  # "He" moved into Mrs. Hudson's group
        r = self.st.checks(kinds=["mismatch"], limit=50)
        self.assertEqual(len(r["items"]), 1)
        self.assertIn("she/her", r["items"][0]["why"])

    def test_checks_follow_edits_and_undo(self):
        self.st.regroup([self.uid_at(5, 2)], 0)
        self.assertEqual(self.st.checks(kinds=["reflexive"])["total"], 1)
        self.st.undo()
        self.assertEqual(self.st.checks(kinds=["reflexive"])["total"], 0)


class SecondBookCase(BookCase):
    book = SECOND


class ChecksSecondBook(SecondBookCase):
    def test_apposition_in_two_groups_is_found_and_merging_fixes_it(self):
        r = self.st.checks(limit=50)
        self.assertEqual([x["kind"] for x in r["items"]], ["appos"])
        x = r["items"][0]
        self.assertEqual((x["text"], x["other"]["text"]), ("the detective", "Holmes"))
        self.st.merge_groups(1, [3])
        self.assertEqual(self.st.checks()["total"], 0)

    def test_he_and_she_in_one_group(self):
        self.st.regroup([self.uid_at(4, 0), self.uid_at(5, 0)], 1)  # both "She" into Holmes' group (two "He" already)
        r = self.st.checks(kinds=["mixed"])
        self.assertEqual(r["total"], 1)
        x = r["items"][0]
        self.assertEqual(x["group"], 1)
        self.assertEqual(len(x["minority_uids"]), 2)
        self.st.regroup(x["minority_uids"], 2)
        self.assertEqual(self.st.checks(kinds=["mixed"])["total"], 0)

    def test_it_in_a_person_group(self):
        self.st.regroup([self.uid_at(8, 0)], 1)
        r = self.st.checks(kinds=["mismatch"], limit=50)
        self.assertEqual([x["why"] for x in r["items"]], ["“It” in a PER group"])

    def test_counts_by_kind(self):
        self.assertEqual(self.st.checks()["counts"]["appos"], 1)


class NarratorRule(SecondBookCase):
    def test_suggests_the_group_with_most_first_person_pronouns(self):
        info = self.st.narrator_info()
        self.assertEqual((info["cid"], info["suggested"], info["total"]), (None, 0, 2))
        self.assertFalse(info["candidates"][0]["named"])
        self.assertEqual(self.st.narrator_rule()["set"], False)

    def test_moves_first_person_pronouns_to_the_narrator(self):
        self.st.set_narrator(0)
        self.assertEqual(self.st.narrator_rule()["n"], 0)
        i = self.uid_at(6, 0)
        self.st.regroup([i], 1)
        rule = self.st.narrator_rule()
        self.assertEqual((rule["n"], rule["items"][0]["to"], rule["items"][0]["from"]), (1, 0, 1))
        self.assertEqual(self.st.narrator_rule(exclude=[i])["n"], 0)
        self.st.narrator_rule(apply=True)
        self.assertEqual(self.ent(i)["coref"], 0)
        with self.assertRaises(EditError):
            self.st.narrator_rule(apply=True)

    def test_passages_with_another_or_no_narrator(self):
        self.st.set_narrator(0)
        i = self.uid_at(6, 0)
        self.st.regroup([i], 1)
        a, b = self.book.ords(6, 0, 2)
        self.st.add_narrator_passage(a, b, 1)  # in this passage the narrator is Holmes: the "I" is right where it is
        self.assertEqual(self.st.narrator_rule()["n"], 0)
        self.st.remove_narrator_passage(0)
        self.st.add_narrator_passage(a, b, None)  # no narrator in this passage: left alone
        r = self.st.narrator_rule()
        self.assertEqual((r["n"], r["no_narrator"]), (0, 1))
        self.assertEqual(len(self.st.narrator_info()["passages"]), 1)

    def test_the_setting_follows_a_merge(self):
        self.st.set_narrator(0)
        self.st.merge_groups(1, [0])
        self.assertEqual(self.st.narrator_info()["cid"], 1)

    def test_bad_input(self):
        with self.assertRaises(EditError):
            self.st.set_narrator(99)
        with self.assertRaises(EditError):
            self.st.remove_narrator_passage(0)
        with self.assertRaises(EditError):
            self.st.add_narrator_passage(0, 9999, None)

    def test_a_narrator_group_that_lost_all_mentions_is_reported(self):
        self.st.set_narrator(2)
        self.st.merge_groups(1, [2])  # repointed: no longer "lost"
        self.assertEqual(self.st.narrator_info()["cid"], 1)
        self.st.set_narrator(0)
        self.st.delete_entities([self.uid_at(0, 0), self.uid_at(1, 1), self.uid_at(6, 0)])
        info = self.st.narrator_info()
        self.assertEqual((info["cid"], info["lost"]), (None, True))


class PronounPass(SecondBookCase):
    def walk(self):
        seen, ord_ = [], -1
        while True:
            r = self.st.pass_step(ord_, "next")
            if not r["item"]:
                return seen
            seen.append((r["item"]["text"], r["item"]["group"]))
            ord_ = r["item"]["s"]

    def test_walks_through_the_selected_pronouns_in_reading_order(self):
        self.st.set_pass_settings(preset="check")
        self.assertEqual(self.walk(), [("He", 1), ("He", 1), ("She", 2), ("She", 2)])
        self.st.set_pass_settings(include=["first"], skip=[])
        self.assertEqual([t for t, _ in self.walk()], ["I", "I"])  # the two in the narration
        self.st.set_pass_settings(include=["you", "it"], skip=[])
        self.assertEqual([t for t, _ in self.walk()], ["You", "It"])

    def test_rules_skip_first_person_once_the_narrator_is_set(self):
        self.st.set_pass_settings(include=["first"], skip=["rules"])
        self.assertEqual(len(self.walk()), 2)
        self.st.set_narrator(0)
        r = self.st.pass_step(-1, "next")
        self.assertEqual((r["shown"], r["skipped"]), (0, {"rules": 2}))  # both "I" in the narration

    def test_settings_and_presets(self):
        self.assertEqual(self.st.set_pass_settings(preset="edit")["preset"], "edit")
        self.assertIsNone(self.st.set_pass_settings(include=["he_she", "it"], skip=["reviewed"])["preset"])
        with self.assertRaises(EditError):
            self.st.set_pass_settings(include=[])
        with self.assertRaises(EditError):
            self.st.set_pass_settings(preset="nope")

    def test_how_to_advance_is_independent_of_the_preset(self):
        self.assertEqual(self.st.pass_settings()["advance"], "auto")
        self.assertEqual(self.st.set_pass_settings(advance="manual")["advance"], "manual")
        self.assertEqual(self.st.set_pass_settings(preset="check")["advance"], "manual")  # carries over a preset change
        with self.assertRaises(EditError):
            self.st.set_pass_settings(advance="nope")

    def test_confirmed_and_reviewed_are_skipped_when_asked(self):
        self.st.set_pass_settings(include=["he_she"], skip=["confirmed", "reviewed"])
        self.assertEqual(self.st.pass_step(-1, "next")["shown"], 4)
        he = self.uid_at(2, 0)
        self.st.confirm_mentions([he])
        self.assertEqual(self.st.pass_step(-1, "next")["shown"], 3)
        self.st.set_reviewed([4], True)  # "She frowned."
        self.assertEqual(self.st.pass_step(-1, "next")["shown"], 2)
        self.st.regroup([he], 2)  # moved after it was confirmed → shown again
        self.assertEqual(self.st.pass_step(-1, "next")["shown"], 3)

    def test_only_one_candidate_rule(self):
        self.st.set_pass_settings(include=["he_she"], skip=["one"])
        r = self.st.pass_step(-1, "next")
        # the second "He" has only Holmes (he) nearby; the "She"s are alone with she-class candidates too
        self.assertLess(r["shown"], 4)
        self.assertGreater(r["skipped"]["one"], 0)

    def test_back_and_single_item(self):
        self.st.set_pass_settings(preset="check")
        first = self.st.pass_step(-1, "next")["item"]
        second = self.st.pass_step(first["s"], "next")["item"]
        self.assertEqual(self.st.pass_step(second["s"], "prev")["item"]["uid"], first["uid"])
        self.assertEqual(self.st.pass_step(second["s"], "here")["item"]["uid"], second["uid"])
        self.assertEqual(self.st.pass_item(second["uid"])["item"]["uid"], second["uid"])
        self.assertIsNone(self.st.pass_step(-1, "prev")["item"])
        with self.assertRaises(EditError):
            self.st.pass_item(99999)


class QuotePass(BookCase):
    def walk(self):
        seen, ord_ = [], -1
        while True:
            r = self.st.qpass_step(ord_, "next")
            if not r["item"]:
                return seen
            seen.append((r["item"]["uid"], r["item"]["speaker"]))
            ord_ = r["item"]["s"]

    def test_walks_through_every_quote_in_reading_order(self):
        q1, q2 = self.uid_at(1, 0, 5, table="quotes"), self.uid_at(4, 0, 7, table="quotes")
        self.assertEqual(self.walk(), [(q1, 1), (q2, 2)])

    def test_settings_and_presets(self):
        self.assertEqual(self.st.set_qpass_settings(preset="edit")["preset"], "edit")
        self.assertIsNone(self.st.set_qpass_settings(skip=["reviewed", "manual"])["preset"])
        self.assertEqual(self.st.set_qpass_settings(preset="check")["skip"], ["reviewed"])
        with self.assertRaises(EditError):
            self.st.set_qpass_settings(preset="nope")

    def test_how_to_advance_is_independent_of_the_preset(self):
        self.assertEqual(self.st.qpass_settings()["advance"], "auto")
        self.assertEqual(self.st.set_qpass_settings(advance="manual")["advance"], "manual")
        self.assertEqual(self.st.set_qpass_settings(preset="check")["advance"], "manual")  # carries over a preset change
        with self.assertRaises(EditError):
            self.st.set_qpass_settings(advance="nope")

    def test_confirmed_is_skipped_while_the_speaker_stays_the_same(self):
        self.st.set_qpass_settings(skip=["confirmed"])
        q1 = self.uid_at(1, 0, 5, table="quotes")
        self.assertEqual(self.st.qpass_step(-1, "next")["shown"], 2)
        self.st.confirm_quotes([q1])
        self.assertEqual(self.st.qpass_step(-1, "next")["shown"], 1)
        self.st.set_speakers([q1], 0)  # speaker changed since it was confirmed → shown again
        self.assertEqual(self.st.qpass_step(-1, "next")["shown"], 2)

    def test_manual_is_skipped_when_asked(self):
        self.st.set_qpass_settings(skip=["manual"])
        q1 = self.uid_at(1, 0, 5, table="quotes")
        self.assertEqual(self.st.qpass_step(-1, "next")["shown"], 2)
        self.st.set_speakers([q1], 0)
        self.assertEqual(self.st.qpass_step(-1, "next")["shown"], 1)

    def test_reviewed_is_skipped_when_asked(self):
        self.st.set_qpass_settings(skip=["reviewed"])
        self.assertEqual(self.st.qpass_step(-1, "next")["shown"], 2)
        self.st.set_reviewed([1], True)
        self.assertEqual(self.st.qpass_step(-1, "next")["shown"], 1)

    def test_a_checked_speaker_is_skipped_when_asked(self):
        self.st.set_qpass_settings(skip=["checked"])
        self.assertEqual(self.st.qpass_step(-1, "next")["shown"], 2)
        self.st.edit_group(1, {"checked": True})  # speaker of the first quote
        self.assertEqual(self.st.qpass_step(-1, "next")["shown"], 1)

    def test_a_quote_that_already_has_a_speaker_is_skipped_when_asked(self):
        self.st.set_qpass_settings(skip=["has_speaker"])
        self.assertEqual(self.st.qpass_step(-1, "next")["shown"], 0)
        self.st.set_speakers([self.uid_at(1, 0, 5, table="quotes")], None)
        self.assertEqual(self.st.qpass_step(-1, "next")["shown"], 1)

    def test_candidates_are_nearby_groups_excluding_the_current_speaker(self):
        q1 = self.uid_at(1, 0, 5, table="quotes")
        item = self.st.qpass_step(-1, "next")["item"]
        self.assertEqual(item["uid"], q1)
        self.assertNotIn(1, [c["id"] for c in item["candidates"]])
        self.assertIn(0, [c["id"] for c in item["candidates"]])  # Holmes, mentioned right before

    def test_confirming_a_quote_pass_speaker_records_it(self):
        q1 = self.uid_at(1, 0, 5, table="quotes")
        self.assertEqual(self.st.confirm_quotes([q1])["confirmed"], 1)
        self.assertEqual(self.st.confirm_quotes([99999])["confirmed"], 0)  # no such quote

    def test_back_and_single_item(self):
        first = self.st.qpass_step(-1, "next")["item"]
        second = self.st.qpass_step(first["s"], "next")["item"]
        self.assertEqual(self.st.qpass_step(second["s"], "prev")["item"]["uid"], first["uid"])
        self.assertEqual(self.st.qpass_step(second["s"], "here")["item"]["uid"], second["uid"])
        self.assertEqual(self.st.qpass_item(second["uid"])["item"]["uid"], second["uid"])
        self.assertIsNone(self.st.qpass_step(-1, "prev")["item"])
        with self.assertRaises(EditError):
            self.st.qpass_item(99999)


if __name__ == "__main__":
    unittest.main()
