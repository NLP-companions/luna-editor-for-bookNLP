"""Character profiles: what a character is like in one book, and across the books of a collection.

A profile is plain JSON built from one book's corrected data (core/tools/profile.py builds it) and kept in the
collection's character entry per book (`entry["profiles"][book_id]`), so it survives the working copy and adds up over
books. Every part is a count, so profiles of several books combine by adding them up (`combine`).

  names        proper-name mentions as written          {"Mr. Holmes": 19, "Holmes": 21}
  name_tokens  the words of those names without titles  {"Sherlock": 2, "Holmes": 41}
  titles       titles in front of the names             {"mr": 19}
  nouns        head nouns of common-noun mentions       {"detective": 3, "friend": 2}
  pronouns     pronoun mentions by class (PRON_CLASS)   {"m": 50, "1": 80, "2": 40}
  gender       m / f / p / n from the pronouns set by hand, else from the he/she/they/it pronouns (3 in 4 agree)
  relations    "<noun> of|<other>" (she is his wife) and "has <noun>|<other>" (he has a wife)
  cooccur      other characters in the same paragraphs  {"k:<entry id>": 12, "n:lestrade": 4}
  places       places (LOC, FAC, GPE) in the same paragraphs, by name
  actions      verbs the character does (agent) or undergoes (patient), what it has (poss), how it is described (mod)
  speech       stylometric counts of its quotes: quotes, words, function words, punctuation, content words (lemmas)
  others       display names for the keys used in relations and cooccur
  embedding    only when computed (core/embed.py): {model, vec, quotes}, the mean transformer vector of its quotes
Other characters are keyed by their collection entry when linked ("k:<id>", the same in every book), else by their
name without titles ("n:<name>").
"""
from __future__ import annotations

from collections import Counter

VERSION = 1
# Frequent English function words: how often a speaker uses each is a style marker that doesn't depend on the topic.
# Each word once: the speech comparison (core/matching.py) uses them as features, in this order.
FUNCTION_WORDS = tuple(dict.fromkeys("""a about above after again against all am an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further had has have having he her here hers
herself him himself his how i if in into is it its itself just me more most my myself no nor not now of off on once only or
other our ours ourselves out over own same shall she should so some such than that the their theirs them themselves then
there these they this those through to too under until up upon very was we were what when where which while who whom why
will with would you your yours yourself yourselves must might may yet shall indeed well oh ah pray sir madam
n't 's 'll 'm 're 've 'd""".split()))
FUNCTION_SET = frozenset(FUNCTION_WORDS)
CONTENT_POS = frozenset({"NOUN", "VERB", "ADJ", "ADV"})    # what counts as a content word in speech
PUNCT = ("!", "?", ",", ";", ":", "—", "-", "…", "...")
COUNTERS = ("names", "name_tokens", "titles", "nouns", "pronouns", "relations", "cooccur", "places")
ACTIONS = ("agent", "patient", "poss", "mod")
SPEECH_COUNTERS = ("fw", "punct", "content")
# How many entries of each count are kept per book (the rest are rare and only make collections.json bigger).
KEEP = {"nouns": 20, "cooccur": 25, "places": 15, "relations": 20, "actions": 25, "content": 40}


def top(counter, n=None):
    """A Counter as a plain dict of its n most common entries (all when n is None), for storing."""
    return dict(Counter(counter).most_common(n))


def empty():
    """A profile with nothing in it (the neutral element of `combine`)."""
    return {"version": VERSION, "mentions": 0, "quotes": 0, "gender": None, "cat": None,
            **{k: {} for k in COUNTERS}, "actions": {k: {} for k in ACTIONS},
            "speech": {"quotes": 0, "words": 0, **{k: {} for k in SPEECH_COUNTERS}}, "others": {}}


def combine(profiles):
    """Add up profiles (e.g. one per book) into one: counts are summed; gender and type are those of the most mentions;
    embeddings are averaged (`_combine_embeddings`)."""
    profiles = list(profiles)
    out = empty()
    counts = {k: Counter() for k in COUNTERS}
    actions = {k: Counter() for k in ACTIONS}
    speech = {k: Counter() for k in SPEECH_COUNTERS}
    gender, cat = Counter(), Counter()
    for p in profiles:
        if not p:
            continue
        out["mentions"] += p.get("mentions", 0)
        out["quotes"] += p.get("quotes", 0)
        for k in COUNTERS:
            counts[k].update(p.get(k) or {})
        for k in ACTIONS:
            actions[k].update((p.get("actions") or {}).get(k) or {})
        sp = p.get("speech") or {}
        out["speech"]["quotes"] += sp.get("quotes", 0)
        out["speech"]["words"] += sp.get("words", 0)
        for k in SPEECH_COUNTERS:
            speech[k].update(sp.get(k) or {})
        if p.get("gender"):
            gender[p["gender"]] += p.get("mentions", 0) or 1
        if p.get("cat"):
            cat[p["cat"]] += p.get("mentions", 0) or 1
        out["others"].update(p.get("others") or {})
    for k in COUNTERS:
        out[k] = top(counts[k])
    out["actions"] = {k: top(v) for k, v in actions.items()}
    out["speech"].update({k: top(v) for k, v in speech.items()})
    out["gender"] = gender.most_common(1)[0][0] if gender else None
    out["cat"] = cat.most_common(1)[0][0] if cat else None
    emb = _combine_embeddings([p.get("embedding") for p in profiles if p])
    if emb:
        out["embedding"] = emb
    return out


def _combine_embeddings(embs):
    """Embeddings of several books as one: the mean vector weighted by quotes, of the model used for the most quotes."""
    by_model = {}
    for e in embs:
        if e and e.get("vec") and e.get("quotes", 0) > 0:
            by_model.setdefault(e["model"], []).append(e)
    if not by_model:
        return None
    model, es = max(by_model.items(), key=lambda kv: sum(e["quotes"] for e in kv[1]))
    dim = len(es[0]["vec"])
    es = [e for e in es if len(e["vec"]) == dim]           # one model always gives one length; skip anything else
    n = sum(e["quotes"] for e in es)
    return {"model": model, "quotes": n, "vec": [round(sum(e["vec"][i] * e["quotes"] for e in es) / n, 4) for i in range(dim)]}


def other_name(profile, key):
    """A readable name for another character's key: its display name if known, else the key without its prefix."""
    return (profile.get("others") or {}).get(key) or key.split(":", 1)[-1]


def summary(profile, n=5, names=None):
    """The main facts of a profile as short readable lists, for the list view and the CSV export.

    `names` maps collection entry ids to their current names (so "k:<id>" keys read as names).
    """
    names = names or {}

    def label(key):
        """Another character's key as a name (the collection's current name for linked ones)."""
        return names.get(key[2:]) if key.startswith("k:") and key[2:] in names else other_name(profile, key)

    def most(d, k=n):
        """The k most common entries of a count."""
        return [w for w, _ in Counter(d or {}).most_common(k)]
    pron = Counter(profile.get("pronouns") or {})
    total = sum(pron.values())
    rels = []
    for key, _ in Counter(profile.get("relations") or {}).most_common(n):
        rel, _, other = key.partition("|")
        rels.append(f"{rel} {label(other)}" if rel.endswith(" of") else f"{rel}: {label(other)}")
    sp = profile.get("speech") or {}
    act = profile.get("actions") or {}
    return {"titles": most(profile.get("titles")), "gender": profile.get("gender"),
            "pronouns": {k: round(v / total, 2) for k, v in pron.most_common()} if total else {},
            "nouns": most(profile.get("nouns")), "relations": rels,
            "cooccur": [label(k) for k in most(profile.get("cooccur"))],
            "places": most(profile.get("places")),
            "actions": most(act.get("agent")), "undergoes": most(act.get("patient")), "has": most(act.get("poss")),
            "described": most(act.get("mod")), "quotes": sp.get("quotes", 0), "words": sp.get("words", 0),
            "speech_words": most(sp.get("content"), 8), "mentions": profile.get("mentions", 0)}
