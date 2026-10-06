"""Character profiles of one book's groups (the layout is described in core/profiles.py).

Mixed into Store. Everything is read from the corrected tables in one pass over the mentions, paragraphs and quotes,
and cached until the next edit:
  * names, name words and titles from proper-name mentions; head nouns from common-noun mentions;
  * pronoun classes from pronoun mentions, and a gender (pronouns set by hand, else 3 in 4 of its he/she/they/it
    pronouns, else BookNLP's guess);
  * actions from the dependency parse of each mention's head word: subject of a verb (agent), object or passive
    subject (patient), possessor of a noun (poss), adjectives and "X was <adj/noun>" (mod);
  * relations from possessives: "his wife" in group B with "his" in group A makes B "wife of" A and A "has wife" B;
  * named characters and places (with at least 2 mentions) in the same paragraphs;
  * stylometric counts of the group's quotes;
  * a transformer embedding of its quotes, when you computed them (`compute_speech_embeddings`, kept in `meta` with a
    fingerprint of the quotes it was made from, so it's left out once the group's quotes change).
"""
from __future__ import annotations

import copy
import hashlib
import re
from collections import Counter, defaultdict

from luna.core import profiles as P
from luna.core.names import bare_name, name_tokens
from luna.core.tools.quote import QUOTE_MARKS
from luna.core.tools.rule import PRON_CLASS, pronoun_group_class

PLACE_CATS = {"LOC", "FAC", "GPE"}
DESCRIBING = {"ADJ", "NOUN"}               # what counts as a description ("tall", "a governess")
_WORD = re.compile(r"[^\W\d_]", re.UNICODE)


def group_key(c):
    """How other books' profiles refer to a group: its collection entry when linked ("k:<id>"), else its name ("n:<name>")."""
    return f"k:{c['carry']}" if c.get("carry") else f"n:{bare_name(c['name'])}"


class ProfileTools:
    """Character profiles mixed into Store; see the module docstring for what goes in and core/profiles.py for the layout."""

    def character_profile(self, cid):
        """The profile of one group in this book (None when the group has no mentions)."""
        with self.lock:
            p = self._profiles().get(cid)
            # a copy: it goes into collections.json and must never share its counts with the cached profiles
            return None if p is None else {**copy.deepcopy(p), "book": self.meta.get("book_id"), "group": cid,
                                           "name": self.clusters()[cid]["name"]}

    def profile_summary(self, cid, n=12):
        """A group's profile as short readable lists (core/profiles.py `summary`), for the Compare pane; None when the
        group has no mentions."""
        p = self.character_profile(cid)
        return None if p is None else P.summary(p, n)

    def _profiles(self):
        """Profiles of every group, by group id (cached until the next edit)."""
        return self._memo("profiles", None, self._build_profiles)

    def _build_profiles(self):
        """Build all groups' profiles in one pass over the mentions, then add paragraph neighbours and speech."""
        cl = self.clusters()
        T = self._tok_arrays()
        kids = self._kids()
        at = self._mention_at_token()
        ents = self._ents()[0]
        key = {cid: group_key(c) for cid, c in cl.items()}
        people = {cid for cid, c in cl.items() if not c["hidden"] and (c["cat"] == "PER" or c["carry"])}
        # other characters count as neighbours or relatives only when they have a name ("we", "my friend" say nothing)
        props = {r["coref"] for r in ents if r["prop"] == "PROP"}
        known = {cid for cid in people if cid in props or cl[cid]["named"] or cl[cid]["carry"]}
        places = {cid for cid, c in cl.items() if not c["hidden"] and c["cat"] in PLACE_CATS and c["count"] >= 2}
        acc = defaultdict(lambda: {k: Counter() for k in (*P.COUNTERS, *P.ACTIONS, *P.SPEECH_COUNTERS)})
        others = defaultdict(dict)
        in_para = defaultdict(set)
        for r in ents:
            g = r["coref"]
            if g not in cl:
                continue
            a = acc[g]
            in_para[T["para"][r["s"]]].add(g)
            h = self._head_in_range(T, r["s"], r["e"])
            if r["prop"] == "PROP":
                a["names"][r["text"]] += 1
                titles, toks = name_tokens(r["text"])
                a["titles"].update(titles)
                a["name_tokens"].update(toks)
            elif r["prop"] == "NOM":
                a["nouns"][T["lemma"][h] or T["word"][h].lower()] += 1
            else:
                cls = PRON_CLASS.get(r["text"].lower())
                if cls:
                    a["pronouns"][cls] += 1
            self._profile_actions(T, kids, h, r["prop"], a)
            if r["prop"] == "NOM" and g in known:
                # "his wife": the possessor is another character this one is related to
                for k in kids.get(h, ()):
                    o = at.get(k)
                    if T["dep"][k] == "poss" and o and o["coref"] != g and o["coref"] in known:
                        noun = T["lemma"][h] or T["word"][h].lower()
                        a["relations"][f"{noun} of|{key[o['coref']]}"] += 1
                        acc[o["coref"]]["relations"][f"has {noun}|{key[g]}"] += 1
                        others[g][key[o["coref"]]] = cl[o["coref"]]["name"]
                        others[o["coref"]][key[g]] = cl[g]["name"]
        for gs in in_para.values():
            ppl, plc = [g for g in gs if g in people], [g for g in gs if g in places]
            for g in ppl:
                for o in ppl:
                    if o != g and o in known:
                        acc[g]["cooccur"][key[o]] += 1
                        others[g][key[o]] = cl[o]["name"]
                for o in plc:
                    acc[g]["places"][cl[o]["name"].lower()] += 1
        speech = self._profile_speech(T, acc)
        emb, sigs = self.meta.get("speech_embeddings") or {}, self._quote_sigs()
        manual = {r[0]: r[1] for r in self.db.execute("SELECT uid, pronoun FROM groups WHERE pronoun IS NOT NULL")}
        out = {}
        for cid, c in cl.items():
            a = acc[cid]
            prof = P.empty()
            prof.update({"mentions": c["count"], "quotes": c["quotes"], "cat": c["cat"],
                         "gender": self._profile_gender(c, a["pronouns"], cid in manual)})
            for k in P.COUNTERS:
                prof[k] = P.top(a[k], P.KEEP.get(k))
            prof["actions"] = {k: P.top(a[k], P.KEEP["actions"]) for k in P.ACTIONS}
            prof["speech"] = {**speech.get(cid, {"quotes": 0, "words": 0}), "fw": P.top(a["fw"]),
                              "punct": P.top(a["punct"]), "content": P.top(a["content"], P.KEEP["content"])}
            kept = set(prof["cooccur"]) | {k.partition("|")[2] for k in prof["relations"]}
            prof["others"] = {k: v for k, v in others[cid].items() if k in kept}
            e = (emb.get("groups") or {}).get(str(cid))
            if e and e["sig"] == sigs.get(cid):
                prof["embedding"] = {"model": emb["model"], "vec": e["vec"], "quotes": e["quotes"]}
            out[cid] = prof
        return out

    def _quote_sigs(self):
        """A fingerprint of each speaker's quotes (which quotes, by uid), to tell whether an embedding still fits."""
        by = defaultdict(list)
        for q in self._quote_rows():
            if q["char_id"] is not None:
                by[q["char_id"]].append(q["uid"])
        return {c: hashlib.md5(",".join(map(str, sorted(u))).encode()).hexdigest() for c, u in by.items()}

    def compute_speech_embeddings(self, model, embedder=None):
        """Embed the quotes of every speaker of a character type (not "not a character") with a downloaded transformer model
        (core/embed.py) and keep the vectors in `meta` for the profiles. Not an edit: nothing to undo. `embedder` lets tests
        use a stand-in. Returns {model, speakers, quotes}."""
        from luna.core import embed
        with self.lock:
            cl, types = self.clusters(), self._character_types()
            texts = defaultdict(list)
            for q in self._quote_rows():
                c = cl.get(q["char_id"])
                if c and not c["hidden"] and c["cat"] in types:
                    words = [w for w in (q["text"] or "").split() if w not in QUOTE_MARKS]
                    if words:
                        texts[q["char_id"]].append(" ".join(words))
            vecs = (embedder or embed.Embedder(model)).speakers(texts)
            sigs = self._quote_sigs()
            self._set_meta("speech_embeddings", {"model": model, "groups": {
                str(c): {"vec": v, "quotes": n, "sig": sigs[c]} for c, (v, n) in vecs.items()}})
            self._memos.clear()     # profiles and everything compared through them (matches, merge suggestions) change
            return {"model": model, "speakers": len(vecs), "quotes": sum(n for _, n in vecs.values())}

    @staticmethod
    def _profile_actions(T, kids, h, prop, a):
        """Count what a mention does, undergoes, has and how it's described, from its head word's place in the parse.
        A conjunct takes the role of the first conjunct ("Holmes and Watson walked": Watson walked too), as in the analyser."""
        he = h
        for _ in range(5):
            if T["dep"][he] == "conj" and T["head"][he] != he:
                he = T["head"][he]
            else:
                break
        dep, head = T["dep"][he], T["head"][he]
        if head != he:
            verb = T["lemma"][head]
            if dep == "nsubj" and T["pos"][head] == "VERB":
                a["agent"][verb] += 1
            elif dep in ("dobj", "nsubjpass") and T["pos"][head] == "VERB":
                a["patient"][verb] += 1
            elif dep == "poss":
                a["poss"][verb] += 1
            if dep == "nsubj" and verb == "be":
                # "Holmes was tired", "she was a governess"
                a["mod"].update(T["lemma"][k] for k in kids.get(head, ())
                                if T["dep"][k] in ("acomp", "attr") and T["pos"][k] in DESCRIBING)
        if prop != "PRON":
            a["mod"].update(T["lemma"][k] for k in kids.get(h, ()) if T["dep"][k] == "amod" and T["pos"][k] in DESCRIBING)

    def _profile_speech(self, T, acc):
        """Stylometric counts of each speaker's quotes (function words, punctuation, content lemmas); returns quotes and words."""
        out = defaultdict(lambda: {"quotes": 0, "words": 0})
        for q in self._quote_rows():
            g = q["char_id"]
            if g is None:
                continue
            a, s = acc[g], out[g]
            s["quotes"] += 1
            for i in range(q["s"], min(q["e"], len(T["word"]) - 1) + 1):
                w = T["word"][i]
                if w in QUOTE_MARKS:
                    continue
                if w in P.PUNCT:
                    a["punct"][w] += 1
                elif _WORD.search(w):
                    lw = w.lower()
                    s["words"] += 1
                    if lw in P.FUNCTION_SET:
                        a["fw"][lw] += 1
                    elif T["pos"][i] in P.CONTENT_POS:
                        a["content"][T["lemma"][i] or lw] += 1
        return out

    @staticmethod
    def _profile_gender(c, prons, manual):
        """m / f / p / n: the pronouns set by hand, else 3 in 4 of its third-person pronouns (at least 2), else BookNLP's guess."""
        if manual:
            return pronoun_group_class(c["pronoun"])
        third = Counter({k: v for k, v in prons.items() if k in ("m", "f", "p", "n")})
        n = sum(third.values())
        if n >= 2:
            k, v = third.most_common(1)[0]
            if v / n >= 0.75:
                return k
        return pronoun_group_class(c["pronoun"])
