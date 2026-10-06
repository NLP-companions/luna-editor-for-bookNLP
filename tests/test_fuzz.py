"""Random sequences of edits on the fixture book. After every step:
  * the data is consistent (positions, spans and their texts, no missing groups),
  * undo gives back exactly the state before, and redo exactly the state after,
  * the cached views (groups, mention positions, token arrays) equal a fresh recomputation.
Seeds are fixed, so a failure can be replayed by its seed."""
from __future__ import annotations

import random
import unittest

from luna.core.errors import EditError
from tests.fixture import BookCase, SECOND

SEEDS = range(25)
STEPS = 14


class Fuzz(BookCase):
    def check_consistent(self, context):
        st, db = self.st, self.st.db
        toks = [dict(r) for r in db.execute("SELECT * FROM tokens ORDER BY ord")]
        self.assertEqual([t["ord"] for t in toks], list(range(len(toks))), context)
        self.assertEqual(st.n_tokens, len(toks), context)
        text = st.meta["text"]
        for a, b in zip(toks, toks[1:]):
            self.assertLessEqual(a["char_end"], b["char_start"], context)
        for t in toks:
            self.assertEqual(text[t["char_start"]:t["char_end"]], t["word"], context)
            self.assertIn(t["head"], {x["uid"] for x in toks}, f"{context}: head of {t['word']}")
        ord_of = {t["uid"]: t["ord"] for t in toks}
        words = [t["word"] for t in toks]
        for tbl in ("entities", "supersenses", "quotes"):
            for r in db.execute(f"SELECT * FROM {tbl}"):
                self.assertIn(r["tok_start"], ord_of, f"{context}: {tbl} start")
                self.assertIn(r["tok_end"], ord_of, f"{context}: {tbl} end")
                a, b = ord_of[r["tok_start"]], ord_of[r["tok_end"]]
                self.assertLessEqual(a, b, f"{context}: {tbl} {r['uid']}")
                self.assertEqual(r["text"], " ".join(words[a:b + 1]), f"{context}: {tbl} {r['uid']} text")
        for r in db.execute("SELECT * FROM entities"):
            self.assertIsNotNone(r["coref"], context)
        for q in db.execute("SELECT * FROM quotes WHERE m_start IS NOT NULL"):
            self.assertIn(q["m_start"], ord_of, context)
            self.assertIn(q["m_end"], ord_of, context)
            self.assertEqual(q["m_phrase"], " ".join(words[ord_of[q["m_start"]]:ord_of[q["m_end"]] + 1]), f"{context}: mention phrase")
        # sentence bookkeeping
        self.assertEqual(st.sent_starts[0], 0, context)
        self.assertEqual(len(st.sent_starts), len(st.sent_para), context)
        # cached views equal a fresh computation
        cached = st.clusters()
        st._clusters = None
        self.assertEqual(cached, st.clusters(), f"{context}: groups cache")
        self.assertEqual(st._ents(), st._build_ents(), f"{context}: mention cache")
        self.assertEqual(st._tok_arrays(), st._build_tok_arrays(), f"{context}: token cache")
        self.assertEqual(st._kids(), st._build_kids(), f"{context}: dependents cache")
        # the pass lists depend on review marks and checked groups, which are the steps that keep the other caches
        self.assertEqual(st._pass_list(), st._build_pass_list(st.pass_settings()), f"{context}: pronoun pass list")
        self.assertEqual(st._qpass_list(), st._build_qpass_list(st.qpass_settings()), f"{context}: quote pass list")

    # ---- random operations; each returns nothing and may raise EditError
    def random_op(self, rng):
        st, db = self.st, self.st.db
        ents = [r[0] for r in db.execute("SELECT uid FROM entities")]
        quotes = [r[0] for r in db.execute("SELECT uid FROM quotes")]
        supers = [r[0] for r in db.execute("SELECT uid FROM supersenses")]
        groups = list(st.clusters())
        n = st.n_tokens
        span = lambda: sorted((rng.randrange(n), min(n - 1, rng.randrange(n))))
        ops = ["regroup", "retype", "delete_entity", "create_entity", "edit_bounds", "create_supersense", "delete_supersense",
               "create_quote", "delete_quote", "split_token", "merge_tokens", "split_sentence", "merge_sentence", "paragraph",
               "edit_token", "merge_groups", "speakers", "group_settings", "new_group", "review", "flag", "check_group"]
        op = rng.choice(ops)
        if op == "regroup" and ents:
            st.regroup(rng.sample(ents, min(len(ents), rng.randint(1, 3))), rng.choice(groups + [None]), rng.choice([None, "Named"]))
        elif op == "retype" and ents:
            st.edit_entities(rng.sample(ents, min(len(ents), 2)), {"cat": rng.choice(["PER", "LOC", "VAR"]), "prop": rng.choice(["PROP", "NOM", "PRON"])})
        elif op == "delete_entity" and ents:
            st.delete_span("entities", rng.choice(ents))
        elif op == "create_entity":
            a, b = span()
            st.create_span("entities", {"s": a, "e": b, "cat": rng.choice(["PER", "FAC", "VAR"]), "prop": "NOM", "coref": rng.choice(groups + [None])})
        elif op == "edit_bounds" and ents:
            a, b = span()
            st.edit_span("entities", rng.choice(ents), {"s": a, "e": b})
        elif op == "create_supersense":
            a, b = span()
            st.create_span("supersenses", {"s": a, "e": b, "cat": "noun.person"})
        elif op == "delete_supersense" and supers:
            st.delete_span("supersenses", rng.choice(supers))
        elif op == "create_quote":
            a, b = span()
            st.create_span("quotes", {"s": a, "e": b, "char_id": rng.choice(groups + [None])})
        elif op == "delete_quote" and quotes:
            st.delete_span("quotes", rng.choice(quotes))
        elif op == "split_token":
            t = rng.choice([dict(r) for r in db.execute("SELECT uid, word FROM tokens WHERE length(word) >= 2")])
            k = rng.randrange(1, len(t["word"]))
            st.split_token(t["uid"], t["word"][:k] + " " + t["word"][k:])
        elif op == "merge_tokens":
            i = rng.randrange(n - 1)
            st.merge_tokens(i, i + rng.randint(1, 2))
        elif op == "split_sentence":
            st.split_sentence(rng.randrange(n))
        elif op == "merge_sentence":
            st.merge_sentence(rng.randrange(len(st.sent_starts)))
        elif op == "paragraph":
            st.set_paragraph(rng.randrange(len(st.sent_starts)), rng.random() < 0.5)
        elif op == "edit_token":
            uid = st.uid_at[rng.randrange(n)]
            st.edit_token(uid, rng.choice([{"lemma": "zzz"}, {"pos": "X"}, {"head_ord": rng.randrange(n)}, {"dep": "dep"}]))
        elif op == "merge_groups" and len(groups) > 1:
            a, b = rng.sample(groups, 2)
            st.merge_groups(a, [b])
        elif op == "speakers" and quotes:
            st.set_speakers(rng.sample(quotes, min(len(quotes), 2)), rng.choice(groups + [None]))
        elif op == "group_settings" and groups:
            st.edit_group(rng.choice(groups), rng.choice([{"name": "Renamed"}, {"hidden": True}, {"checked": True}, {"pronoun": "she/her"}]))
        elif op == "new_group" and ents:
            st.regroup([rng.choice(ents)], None, None)
        elif op == "review":
            st.set_reviewed([rng.randrange(len(st.sent_starts))], rng.random() < 0.7)
        elif op == "flag":
            kind = rng.choice(["entities", "quotes", "groups", "sentence"])
            target = {"entities": ents, "quotes": quotes, "groups": groups, "sentence": [st.uid_at[0]]}[kind]
            if target:
                st.set_flag(kind, rng.choice(target), rng.choice(["", "look again"]))
        elif op == "check_group" and groups:
            st.edit_group(rng.choice(groups), {"checked": rng.random() < 0.7})

    def run_sequence(self, seed):
        rng = random.Random(seed)
        # passes that skip reviewed sentences and checked groups, so a stale pass list would show
        self.st.set_pass_settings(include=["he_she", "they", "first"], skip=["rules", "one", "reviewed", "checked"])
        self.st.set_qpass_settings(skip=["reviewed", "checked", "has_speaker"])
        self.check_consistent(f"seed {seed} start")
        for step in range(STEPS):
            before, count = self.snapshot(), self.st.history()["count"]
            try:
                self.random_op(rng)
            except EditError:
                self.assertEqual(self.snapshot(), before, f"seed {seed} step {step}: a refused edit changed something")
                continue
            except Exception as e:
                self.fail(f"seed {seed} step {step}: {type(e).__name__}: {e}")
            ctx = f"seed {seed} step {step}"
            self.check_consistent(ctx)
            if self.st.history()["count"] == count:
                continue  # nothing was recorded (an edit that changed nothing)
            after = self.snapshot()
            self.st.undo()
            self.assertEqual(self.snapshot(), before, f"{ctx}: undo")
            self.check_consistent(ctx + " after undo")
            self.st.redo()
            self.assertEqual(self.snapshot(), after, f"{ctx}: redo")
            self.check_consistent(ctx + " after redo")

    def test_random_edits_keep_everything_consistent_and_undoable(self):
        for seed in SEEDS:
            self.run_sequence(seed)
            self.setUp()  # a fresh working copy for the next sequence


class FuzzSecondBook(Fuzz):
    book = SECOND

    def test_random_edits_keep_everything_consistent_and_undoable(self):
        for seed in range(100, 100 + len(SEEDS)):
            self.run_sequence(seed)
            self.setUp()


if __name__ == "__main__":
    unittest.main()
