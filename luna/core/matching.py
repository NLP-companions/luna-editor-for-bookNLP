"""Matching a book's groups to a collection's characters by evidence, not by name alone.

Both sides are character profiles (core/profiles.py): a group's profile in this book, and a character entry's profiles
from the other books added up. Each kind of evidence gives a similarity from 0 to 1, or None when either side has no
data for it. Two kinds of evidence say who someone is (identity: name, title); the rest only support it, because each
book has its own cast, places and topics, so having little in common says little. Hence

  score = Σ w·sim over identity evidence / max(Σ w of the identity evidence present, min_evidence)
        + Σ w·sim over supporting evidence
        − penalties for contradictions                                          (between 0 and 1)

  name       same name as written 1, same name without titles 0.9, else 0.7 × the share of its name words the entry has
  title      titles in common (cosine of the counts)
  cooccur    the same characters in the same paragraphs      ┐ cosine of the counts, each item weighted by how rare it
  relations  the same relations to the same characters       │ is among all profiles compared (idf), so "say" or
  nouns      the same descriptions ("the doctor")             │ "London" count less than "violin" or "Baker Street";
  places     the same places                                  │ then scaled by how specific the shared items are (two
  actions    the same verbs, possessions and descriptions     ┘ groups described only as "man" share little)
  speech     speaking alike: the rates of the `speech_features` most frequent function words, each as a z-score
             against all speakers compared (Burrows' Delta, cosine form); only for speakers with at least `min_words`
             quoted words. A weak signal between characters of one author (tried on the Holmes books: a character's
             own voice was often only 2nd or 3rd closest), so it only supports. Punctuation is kept in the profile but
             not compared: it depends on the edition more than on the speaker
  embedding  a transformer embedding of the quotes (off by default; see settings), centred on all speakers compared

  penalties  he vs she, Mr. vs Mrs., different first names (Sherlock vs Mycroft)

Dividing by at least `min_evidence` keeps a lone name from counting as full proof: an exact name alone gives 0.7, just
under `match` (0.72), so it is offered as a choice until some supporting evidence (neighbours, places, speech, ...) or a
title backs it up; a one-off "Perkins" in two stories isn't taken for the same person.
A group without a name can only reach what its supporting evidence adds up to. A match is proposed (ticked) when it
scores at least `match`, leads the next character by `margin` and has name evidence; characters scoring at least
`possible` are offered as a choice (`possible_unnamed` for a group without a proper name, which has only supporting
evidence to go by).

The settings live in collections.json (`settings.matching`) and are edited in the library.
"""
from __future__ import annotations

import copy
import math
from collections import Counter

from luna.core import profiles as P
from luna.core.embed import DEFAULT_MODEL
from luna.core.names import bare_name, name_tokens, name_words, title_gender
from luna.core.tools.rule import pronoun_group_class

DEFAULTS = {
    "weights": {"name": 0.35, "title": 0.10, "cooccur": 0.15, "relations": 0.10, "speech": 0.15, "nouns": 0.05,
                "places": 0.05, "actions": 0.05, "embedding": 0.10},
    "penalties": {"gender": 0.30, "title_gender": 0.20, "first_name": 0.20},
    "match": 0.72, "margin": 0.10, "possible": 0.35, "possible_unnamed": 0.25, "min_evidence": 0.50, "min_words": 100,
    "speech_features": 60,
    "embedding": {"use": False, "model": DEFAULT_MODEL},
    "off": [],                      # kinds of evidence (weights or penalties) switched off
}
LABELS = {"name": "Names", "title": "Titles", "cooccur": "Appears with", "relations": "Relations", "speech": "Speech style",
          "nouns": "Described as", "places": "Places", "actions": "Actions", "embedding": "Speech (transformer)",
          "gender": "Pronouns differ", "title_gender": "Titles differ", "first_name": "First names differ"}
VECTORS = ("cooccur", "relations", "nouns", "places", "actions")
# a similarity from a single item is noise (one shared verb makes two tiny profiles "100% alike"); descriptions and
# relations are exempt: a lone description or relation is exactly the evidence a small group has
MIN_ITEMS = {"cooccur": 2, "places": 2, "actions": 2}
MIN_POPULATION = 10     # profiles needed to judge how specific a shared item is (`_specific_dot`)
IDENTITY = ("name", "title")


def settings(saved=None):
    """The matching settings: the defaults with the saved values laid over them (unknown keys and bad values ignored)."""
    out = copy.deepcopy(DEFAULTS)
    saved = saved or {}
    for group in ("weights", "penalties"):
        for k, v in (saved.get(group) or {}).items():
            if k in out[group] and isinstance(v, (int, float)) and v >= 0:
                out[group][k] = float(v)
    for k in ("match", "margin", "possible", "possible_unnamed", "min_evidence", "min_words", "speech_features"):
        v = saved.get(k)
        if isinstance(v, (int, float)) and v >= 0:
            out[k] = v
    kinds = set(out["weights"]) | set(out["penalties"])
    if isinstance(saved.get("off"), list):
        out["off"] = sorted({k for k in saved["off"] if k in kinds})
    emb = saved.get("embedding") or {}
    if isinstance(emb.get("use"), bool):
        out["embedding"]["use"] = emb["use"]
    if isinstance(emb.get("model"), str) and emb["model"].strip():
        out["embedding"]["model"] = emb["model"].strip()
    return out


def _unit(d, idf=None):
    """A count as a unit-length vector (square-rooted counts, times idf when given), for cosine similarity."""
    v = {k: math.sqrt(n) * (idf.get(k, 1.0) if idf else 1.0) for k, n in d.items() if n > 0}
    norm = math.sqrt(sum(x * x for x in v.values()))
    return {k: x / norm for k, x in v.items()} if norm else {}


def _unit_dense(xs):
    """A list of numbers scaled to unit length (None when it is all zeros)."""
    norm = math.sqrt(sum(x * x for x in xs))
    return [x / norm for x in xs] if norm else None


def _dot(a, b):
    """Dot product of two sparse vectors (dicts)."""
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(k, 0.0) for k, x in a.items())


class View:
    """One side of a comparison, prepared once: name sets, gender, and every vector ready for cosine similarity."""

    def __init__(self, prof, names=None, pronoun=None):
        """Prepare a profile; for a collection entry `names` adds the names typed in the list and `pronoun` its pronouns."""
        self.prof = prof
        written = Counter(prof.get("names") or {})
        for n in names or ():
            written[n] += 0  # known names without a count still count as names
        self.names = written
        self.exact, self.bare, self.tokens, self.first = set(), set(), set(), set()
        self.name_info = []     # (name, times used, lower case, without titles, its capitalised words), worked out once
        titles = Counter({k.lower(): v for k, v in (prof.get("titles") or {}).items()})
        for n, uses in written.items():
            t, toks = name_tokens(n)
            words = name_words(toks)
            self.name_info.append((n, uses, n.lower().strip(), bare_name(n), words))
            self.exact.add(n.lower().strip())
            self.bare.add(bare_name(n))
            self.tokens |= words
            if len(toks) >= 2 and toks[0][:1].isupper():
                self.first.add(toks[0].lower())
            for x in t:
                titles.setdefault(x, 1)  # a title in a name typed in the list
        self.titles = titles
        self.title_vec = _unit(titles)
        self.gender = pronoun_group_class(pronoun) or prof.get("gender")
        self.title_gender = title_gender(self.titles)
        sp = prof.get("speech") or {}
        self.words = sp.get("words", 0)
        emb = prof.get("embedding") or {}
        self.embedding = emb.get("vec")
        self.embedding_model = emb.get("model")
        self.vec = {}
        self.wvec = {}      # each vector's items times their rarity (idf), so a "specific" similarity is a single dot product
        self.z = None
        self.e = None


class Matcher:
    """Scores the groups of one book against the character entries of its lists."""

    def __init__(self, entries, groups, cfg=None):
        """`entries`: {entry id: collection entry}; `groups`: {group id: profile in this book}; `cfg`: settings().

        The statistics every comparison uses (how rare each item is, the speakers' average style, the embeddings' centre)
        come from these profiles; entries and groups added later (`add_entry`, `add_group`) are measured against them.
        """
        self.cfg = cfg or settings()
        off = set(self.cfg["off"])      # kinds switched off count as having no weight
        self.w = {k: (0.0 if k in off else v) for k, v in self.cfg["weights"].items()}
        self.pen = {k: (0.0 if k in off else v) for k, v in self.cfg["penalties"].items()}
        self.entries, self.groups, self.entry_names, self.owners = {}, {}, {}, {}
        for eid, e in entries.items():
            self._entry_view(eid, e)
        for cid, p in groups.items():
            self.groups[cid] = View(p)
        views = [*self.entries.values(), *self.groups.values()]
        raw = [self._raw_vectors(v.prof) for v in views]
        df = {f: Counter() for f in VECTORS}
        for r in raw:
            for f in VECTORS:
                df[f].update(r[f].keys())
        n = self.population = len(views)
        self.idf = {f: {k: math.log((n + 1) / (d + 1)) + 1 for k, d in df[f].items()} for f in VECTORS}
        self.default_idf = math.log(n + 1) + 1  # an item none of them has
        self._speech_stats(views)
        self._embedding_stats(views)
        for v in views:
            self._prepare(v)

    def add_entry(self, eid, entry):
        """Compare against one more character entry (e.g. one just created from this book)."""
        self._prepare(self._entry_view(eid, entry))

    def add_group(self, cid, prof):
        """Compare one more group of this book (e.g. a small group you just marked as checked)."""
        self.groups[cid] = View(prof)
        self._prepare(self.groups[cid])

    # ------------------------------------------------------------ preparation
    def _entry_view(self, eid, e):
        """An entry's view: its profiles over all books added up, plus the names and pronouns in the list."""
        v = self.entries[eid] = View(P.combine((e.get("profiles") or {}).values()),
                                     names=[e["name"], *(e.get("names") or {})], pronoun=e.get("pronoun"))
        self.entry_names[eid] = e["name"]
        for b in v.bare:
            self.owners.setdefault(b, set()).add(eid)
        return v

    def key(self, k):
        """Another character's key in a form comparable across books: a name that belongs to exactly one entry is that
        entry, whatever the book ("n:watson" → "k:<Watson's entry>")."""
        if k.startswith("n:"):
            ids = self.owners.get(k[2:])
            if ids and len(ids) == 1:
                return f"k:{next(iter(ids))}"
        return k

    def _raw_vectors(self, prof):
        """The counts compared by cosine: neighbours and relations with comparable keys, nouns, places, actions."""
        rel = Counter()
        for k, n in (prof.get("relations") or {}).items():
            r, _, other = k.partition("|")
            rel[f"{r}|{self.key(other)}"] += n
        co = Counter()
        for k, n in (prof.get("cooccur") or {}).items():
            co[self.key(k)] += n
        act = Counter()
        for kind, d in (prof.get("actions") or {}).items():
            for w, n in d.items():
                act[f"{kind}:{w}"] += n
        return {"cooccur": co, "relations": rel, "nouns": Counter(prof.get("nouns") or {}),
                "places": Counter(prof.get("places") or {}), "actions": act}

    def _speech_stats(self, views):
        """The average and spread of the most frequent function words' rates over all speakers with enough words
        (needs 3 speakers; otherwise speech isn't compared)."""
        self.speech = None
        rates = [self._rates(v) for v in views if v.words >= self.cfg["min_words"]]
        if len(rates) < 3:
            return
        n = len(rates)
        mean = [sum(r[i] for r in rates) / n for i in range(len(P.FUNCTION_WORDS))]
        keep = sorted(range(len(mean)), key=lambda i: -mean[i])[:int(self.cfg["speech_features"])]
        std = {i: math.sqrt(sum((r[i] - mean[i]) ** 2 for r in rates) / n) for i in keep}
        self.speech = (mean, [i for i in keep if std[i] > 0], std)

    @staticmethod
    def _rates(v):
        """How often a speaker uses each function word, per word spoken."""
        fw = v.prof["speech"].get("fw") or {}
        return [fw.get(f, 0) / v.words for f in P.FUNCTION_WORDS]

    def _embedding_stats(self, views):
        """The centre of the transformer embeddings made by the chosen model (only when switched on and 3 exist)."""
        self.emb_mean = None
        emb = self.cfg["embedding"]
        have = [v.embedding for v in views if v.embedding and v.embedding_model == emb["model"]]
        if emb["use"] and len(have) >= 3:
            dim = len(have[0])
            have = [x for x in have if len(x) == dim]
            self.emb_mean = [sum(x[i] for x in have) / len(have) for i in range(dim)]

    def _prepare(self, v):
        """Turn a view's counts into the vectors compared: idf-weighted unit vectors, speech z-scores, centred embedding."""
        raw = self._raw_vectors(v.prof)
        v.vec = {f: _unit(raw[f], {k: self.idf[f].get(k, self.default_idf) for k in raw[f]})
                 if sum(raw[f].values()) >= MIN_ITEMS.get(f, 1) else {} for f in VECTORS}
        v.wvec = {f: {k: x * self.idf[f].get(k, self.default_idf) for k, x in v.vec[f].items()} for f in VECTORS}
        if self.speech and v.words >= self.cfg["min_words"]:
            mean, keep, std = self.speech
            r = self._rates(v)
            v.z = _unit_dense([(r[i] - mean[i]) / std[i] for i in keep])
        if self.emb_mean and v.embedding and v.embedding_model == self.cfg["embedding"]["model"] \
                and len(v.embedding) == len(self.emb_mean):
            v.e = _unit_dense([x - m for x, m in zip(v.embedding, self.emb_mean)])

    # ------------------------------------------------------------ comparing
    def _name(self, g, e):
        """Name similarity and why: the group's proper names weighted by how often each is used."""
        if not g.names or not e.names:
            return None, ""
        total, best = 0.0, (0.0, "")
        for n, k, lower, bare, words in g.name_info:
            if lower in e.exact:
                s, why = 1.0, f"“{n}” is one of its names"
            elif bare in e.bare:
                s, why = 0.9, f"“{n}” is one of its names (without the title)"
            else:
                shared = words & e.tokens
                s = 0.7 * len(shared) / len(words) if words else 0.0
                why = f"shares “{' '.join(sorted(shared))}”" if shared else ""
            total += s * max(k, 1)
            if s > best[0]:
                best = (s, why)
        return total / sum(max(k, 1) for k in g.names.values()), best[1]

    def _shared(self, g, e, f, n=3):
        """The items two vectors share, strongest first, as readable words (other characters by name)."""
        both = sorted(set(g.vec[f]) & set(e.vec[f]), key=lambda k: -g.vec[f][k] * e.vec[f][k])[:n]
        return [self._readable(f, k, g) for k in both]

    def _readable(self, f, k, g):
        """One vector item as words: a character key as its name, a relation as "wife of Holmes", an action as its verb."""
        def who(key):
            """A character key as a name."""
            if key.startswith("k:") and key[2:] in self.entry_names:
                return self.entry_names[key[2:]]
            return P.other_name(g.prof, key)
        if f == "cooccur":
            return who(k)
        if f == "relations":
            r, _, other = k.partition("|")
            return f"{r} {who(other)}" if r.endswith(" of") else f"{r}: {who(other)}"
        if f == "actions":
            return k.split(":", 1)[1]
        return k

    def supporting(self, g, e, why=True):
        """The supporting evidence between two prepared views (a group and an entry, or two groups of one book), without
        weights: [(kind, similarity 0..1, reason)] for every kind both have data for. `why=False` leaves the reasons empty,
        which is much cheaper when only the numbers matter (ranking many pairs)."""
        out = []
        for f in VECTORS:
            if g.vec[f] and e.vec[f]:
                out.append((f, self._specific_dot(f, g, e), ", ".join(self._shared(g, e, f)) if why else ""))
        if g.z and e.z:
            cos = sum(a * b for a, b in zip(g.z, e.z))
            out.append(("speech", cos, f"{'alike' if cos > 0.2 else 'different' if cos < -0.2 else 'neither alike nor different'}"
                                       f" ({g.words:,} and {e.words:,} words)" if why else ""))
        if g.e and e.e:
            out.append(("embedding", sum(a * b for a, b in zip(g.e, e.e)), ""))
        return [(k, max(0.0, min(1.0, sim)), why) for k, sim, why in out]

    def _specific_dot(self, f, g, e):
        """Cosine similarity of one kind's vectors of two views, scaled by how specific the shared items are: each shared item's
        rarity (idf) relative to the rarest possible. Two groups described only as "man" are identical vectors but share
        little. Among fewer than MIN_POPULATION profiles the plain cosine is used.

        The scaling is dot × (Σ a·b·idf / dot / max idf), i.e. just Σ a·b·idf / max idf, which is what is computed (one pass)."""
        if self.population < MIN_POPULATION:
            return _dot(g.vec[f], e.vec[f])                 # rarity can't be judged among a handful of profiles
        return _dot(g.wvec[f], e.vec[f]) / self.default_idf

    def similarity(self, a, b):
        """The supporting evidence between two groups of this book (see `supporting`), e.g. for merge suggestions."""
        return self.supporting(self.groups[a], self.groups[b])

    def score(self, cid, eid, why=True):
        """How well group `cid` of this book matches entry `eid`: score, the evidence for it and the penalties against it.
        Kinds of evidence switched off in the settings (`off`) count as having no weight. `why=False` skips the readable
        reasons (see `supporting`)."""
        g, e = self.groups[cid], self.entries[eid]
        w, pen = self.w, self.pen
        parts = []

        def add(kind, sim, reason=""):
            """Record one kind of evidence (skipped when there's no data for it or no weight)."""
            if sim is not None and w.get(kind, 0) > 0:
                parts.append({"kind": kind, "label": LABELS[kind], "sim": round(max(0.0, min(1.0, sim)), 3),
                              "w": w[kind], "why": reason})
        s, reason = self._name(g, e)
        add("name", s, reason)
        if g.titles and e.titles:
            add("title", _dot(g.title_vec, e.title_vec), ", ".join(sorted(set(g.titles) & set(e.titles))) if why else "")
        for kind, sim, reason in self.supporting(g, e, why):
            add(kind, sim, reason)
        penalties = []
        if g.gender in ("m", "f") and e.gender in ("m", "f") and g.gender != e.gender:
            penalties.append({"kind": "gender", "label": LABELS["gender"], "w": pen["gender"], "why": f"{g.gender} vs {e.gender}"})
        if g.title_gender and e.title_gender and g.title_gender != e.title_gender:
            penalties.append({"kind": "title_gender", "label": LABELS["title_gender"], "w": pen["title_gender"],
                              "why": f"{', '.join(g.titles)} vs {', '.join(e.titles)}"})
        if g.first and e.first and not g.first & e.first:
            penalties.append({"kind": "first_name", "label": LABELS["first_name"], "w": pen["first_name"],
                              "why": f"{', '.join(sorted(g.first))} vs {', '.join(sorted(e.first))}"})
        penalties = [p for p in penalties if p["w"] > 0]
        ident = [p for p in parts if p["kind"] in IDENTITY]
        have = sum(p["w"] for p in ident)
        total = sum(p["w"] * p["sim"] for p in ident) / max(have, self.cfg["min_evidence"]) if ident else 0.0
        total += sum(p["w"] * p["sim"] for p in parts if p["kind"] not in IDENTITY)
        total -= sum(p["w"] for p in penalties)
        name = next((p["sim"] for p in parts if p["kind"] == "name"), 0.0)
        return {"entry": eid, "score": round(max(0.0, min(1.0, total)), 3), "named": name > 0,
                "parts": sorted(parts, key=lambda p: -p["w"] * p["sim"]), "penalties": penalties}

    def rank(self, cid, limit=4):
        """The entries group `cid` could be, best first, down to the `possible` threshold (`possible_unnamed` without a name)."""
        low = self.cfg["possible"] if self.groups[cid].names else self.cfg["possible_unnamed"]
        out = [r for r in (self.score(cid, eid, why=False) for eid in self.entries) if r["score"] >= low]
        out.sort(key=lambda r: (-r["score"], r["entry"]))
        return [self.score(cid, r["entry"]) for r in out[:limit]]       # the readable reasons, for the few that are shown

    def decide(self, ranked):
        """'match' when the best is strong, clearly ahead and has name evidence; 'possible' when any are listed; else None."""
        if not ranked:
            return None
        best = ranked[0]
        second = ranked[1]["score"] if len(ranked) > 1 else 0.0
        if best["score"] >= self.cfg["match"] and best["score"] - second >= self.cfg["margin"] and best["named"]:
            return "match"
        return "possible"
