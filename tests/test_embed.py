"""Transformer speech embeddings (core/embed.py): computing them for a book, keeping them only while the quotes are the same,
adding them up over books, using them in matching when switched on, and the settings for them."""
from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from luna.core import embed, matching
from luna.core import profiles as P
from tests.test_carry import CarryBase

TINY = "google/bert_uncased_L-2_H-256_A-4"


class FakeEmbedder:
    """Stands in for a model: a speaker's vector is [number of quotes, number of tokens in them]."""

    def __init__(self, model="fake"):
        self.model_id = model

    def speakers(self, quotes):
        return {k: ([float(len(qs)), float(sum(len(q.split()) for q in qs))], len(qs)) for k, qs in quotes.items() if qs}


class InTheBook(CarryBase):
    def test_vectors_are_kept_while_the_quotes_stay_the_same(self):
        out = self.st.compute_speech_embeddings("fake", FakeEmbedder())
        self.assertEqual(out, {"model": "fake", "speakers": 2, "quotes": 2})
        self.assertEqual(self.st.character_profile(1)["embedding"], {"model": "fake", "vec": [1.0, 4.0], "quotes": 1})   # "I am tired ,"
        self.assertNotIn("embedding", self.st.character_profile(0))                                                     # no quotes
        q = self.uid_at(1, 0, 5, table="quotes")
        self.st.set_speakers([q], 2)                 # Watson's quote goes to Mrs. Hudson: both vectors no longer fit
        self.assertNotIn("embedding", self.st.character_profile(1))
        self.assertNotIn("embedding", self.st.character_profile(2))
        self.st.undo()
        self.assertEqual(self.st.character_profile(1)["embedding"]["vec"], [1.0, 4.0])

    def test_new_vectors_reach_the_merge_suggestions(self):
        self.st.merge_suggestions()                                                # cached before
        self.assertFalse(self.st._book_matcher().cfg["embedding"]["use"])
        self.st.compute_speech_embeddings("fake", FakeEmbedder())
        self.assertEqual(self.st._book_matcher().cfg["embedding"], {"use": True, "model": "fake"})

    def test_places_and_things_are_not_embedded(self):
        self.st.edit_entities(self.group_uids(2), {"cat": "FAC"})
        self.assertEqual(self.st.compute_speech_embeddings("fake", FakeEmbedder())["speakers"], 1)

    def test_the_collection_tool_embeds_and_refreshes_linked_profiles(self):
        self.st.edit_group(1, {"checked": True})
        with mock.patch.object(embed, "Embedder", FakeEmbedder):
            out = self.st.carry_compute_embeddings()
        self.assertEqual((out["model"], out["speakers"], out["updated"]), (matching.DEFAULTS["embedding"]["model"], 2, 1))
        e = self.colls.get_list(self.coll["id"])["characters"][0]
        self.assertEqual(e["profiles"]["fix"]["embedding"]["vec"], [1.0, 4.0])
        self.assertEqual(self.st.carry_review()["embeddings"]["book_model"], matching.DEFAULTS["embedding"]["model"])
        with mock.patch.object(embed, "Embedder", side_effect=OSError("not downloaded")):
            with self.assertRaises(Exception) as err:
                self.st.carry_compute_embeddings()
        self.assertIn("not downloaded", str(err.exception))


class Combining(unittest.TestCase):
    def test_books_are_averaged_by_quotes_and_the_main_model_wins(self):
        a = {**P.empty(), "embedding": {"model": "m", "vec": [1.0, 0.0], "quotes": 3}}
        b = {**P.empty(), "embedding": {"model": "m", "vec": [0.0, 1.0], "quotes": 1}}
        c = {**P.empty(), "embedding": {"model": "other", "vec": [9.0, 9.0], "quotes": 2}}
        self.assertEqual(P.combine([a, b, c])["embedding"], {"model": "m", "quotes": 4, "vec": [0.75, 0.25]})
        self.assertNotIn("embedding", P.combine([P.empty()]))
        odd = {**P.empty(), "embedding": {"model": "m", "vec": [5.0], "quotes": 9}}           # a vector of another length
        none = {**P.empty(), "embedding": {"model": "m", "vec": [5.0, 5.0], "quotes": 0}}     # no quotes: no weight
        self.assertEqual(P.combine([a, odd, none])["embedding"], {"model": "m", "quotes": 3, "vec": [1.0, 0.0]})

    def test_matching_uses_them_only_when_switched_on(self):
        def entry(eid, vec):
            return {"id": eid, "name": eid, "names": {}, "profiles": {"b": {**P.empty(), "embedding": {"model": "m", "vec": vec, "quotes": 5}}}}
        entries = {"k1": entry("k1", [1.0, 0.0, 0.0]), "k2": entry("k2", [0.0, 1.0, 0.0]), "k3": entry("k3", [0.0, 0.0, 1.0])}
        group = {**P.empty(), "embedding": {"model": "m", "vec": [0.9, 0.1, 0.0], "quotes": 5}}
        on = matching.settings({"embedding": {"use": True, "model": "m"}})
        m = matching.Matcher(entries, {1: group}, on)
        self.assertEqual([p["kind"] for p in m.score(1, "k1")["parts"]], ["embedding"])
        self.assertGreater(m.score(1, "k1")["score"], m.score(1, "k2")["score"])
        off = matching.Matcher(entries, {1: group}, matching.settings({"embedding": {"model": "m"}}))
        self.assertEqual(off.score(1, "k1")["parts"], [])
        other_model = matching.Matcher(entries, {1: group}, matching.settings({"embedding": {"use": True, "model": "x"}}))
        self.assertEqual(other_model.score(1, "k1")["parts"], [])


class Settings(unittest.TestCase):
    def test_changing_one_embedding_setting_keeps_the_other(self):
        from luna.core.collections_store import Collections
        with tempfile.TemporaryDirectory() as d:
            c = Collections(Path(d) / "collections.json")
            c.set_matching_settings({"embedding": {"model": TINY}})
            c.set_matching_settings({"embedding": {"use": True}})
            self.assertEqual(c.matching_settings()["embedding"], {"use": True, "model": TINY})

    def test_only_downloaded_models_are_offered(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"HF_HUB_CACHE": d}):
            self.assertEqual(embed.available_models(), [])


@unittest.skipUnless(importlib.util.find_spec("torch") and (embed.hub_dir() / ("models--" + TINY.replace("/", "--"))).exists(),
                     "torch isn't installed or the small BERT isn't downloaded")
class RealModel(unittest.TestCase):
    def test_a_small_bert_gives_one_vector_per_speaker(self):
        vecs = embed.Embedder(TINY).speakers({"a": ["I am tired.", "Come here."], "b": ["You look well."], "c": []})
        self.assertEqual(sorted(vecs), ["a", "b"])
        self.assertEqual((len(vecs["a"][0]), vecs["a"][1]), (256, 2))
        self.assertIn(TINY, [m["id"] for m in embed.available_models()])


if __name__ == "__main__":
    unittest.main()
