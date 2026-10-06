"""Books with next to nothing in them: no mentions, no quotes, one sentence. Every view and tool must answer, not crash."""
from __future__ import annotations

from luna.core.errors import EditError
from tests.fixture import Book, BookCase

# One sentence, no mentions, no quotes, no supersenses.
BARE = Book(
    text="It rained.\n",
    sentences=[[("It", "it", "PRON", "PRP", "nsubj", 1), ("rained", "rain", "VERB", "VBD", "ROOT", 1), (".", ".", "PUNCT", ".", "punct", 1)]],
    para_starts={0}, entities=[], supersenses=[], quotes=[], genders={})

# One mention and one quote with no speaker, nothing else.
LONE = Book(
    text='"Yes," said Anna.\n',
    sentences=[[('"', '"', "PUNCT", "``", "punct", 3), ("Yes", "yes", "INTJ", "UH", "intj", 3), (",", ",", "PUNCT", ",", "punct", 3),
                ('"', '"', "PUNCT", "''", "punct", 3), ("said", "say", "VERB", "VBD", "ROOT", 4), ("Anna", "Anna", "PROPN", "NNP", "nsubj", 4),
                (".", ".", "PUNCT", ".", "punct", 4)]],
    para_starts={0}, entities=[(0, 5, 5, "PROP", "PER", 0)], supersenses=[], quotes=[(0, 0, 3, None, None)], genders={0: "she/her"})


class EveryViewAnswers:
    """Mixed into a BookCase: call everything the pages call and check nothing raises."""

    def test_reading(self):
        st = self.st
        st.info()
        st.window(0, 15)
        st.window(99, 15)                     # past the end is clamped
        st.groups_list()
        st.groups_list(q="x", cat="PER", hidden=True, unchecked=True)
        st.quotes_list()
        st.quotes_list(speaker="none", q="a")
        st.history()
        st.proposals()
        st.review_progress()
        st.next_unreviewed(0)
        st.book_settings()
        st.flags_list()
        st.hotkeys()
        st.carry_state()
        st.observed_values()

    def test_suggestions_and_tools(self):
        st = self.st
        self.assertEqual(st.merge_suggestions()["items"], [])
        st.fragments()
        st.quote_suggestions()
        st.checks(limit=5)
        st.narrator_info()
        st.narrator_rule()
        st.quote_rule()
        st.pass_step(-1, "next")
        st.pass_step(0, "prev")
        st.qpass_step(-1, "next")
        st.nearby_groups(0, 0)
        st.search("tokens", "pos", "eq", "VERB")
        st.search("tokens", "word", "phrase", "It rained")
        st.search("entities", "text", "contains", "x")
        st.search("quotes", "text", "regex", "a+")

    def test_exporting_and_profiles(self):
        out = self.tmp / "out"
        self.st.export(out)
        self.assertTrue((out / "fix.tokens").exists())
        for cid in self.st.clusters():
            self.st.character_profile(cid)
            self.st.book_preview(cid)
        self.st._profiles()

    def test_edits_that_cannot_apply_are_refused_politely(self):
        for call in (lambda: self.st.regroup([], 0), lambda: self.st.merge_groups(0, []), lambda: self.st.set_speakers([], 0),
                     lambda: self.st.edit_group(99, {"name": "x"}), lambda: self.st.undo(), lambda: self.st.redo(),
                     lambda: self.st.set_reviewed([], True), lambda: self.st.split_sentence(0)):
            with self.assertRaises(EditError):
                call()


class BareBook(EveryViewAnswers, BookCase):
    book = BARE
    with_book = False        # nothing for BookNLP's report either


class LoneBook(EveryViewAnswers, BookCase):
    book = LONE
