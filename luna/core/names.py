"""Personal names: titles (Mr., Mrs., Dr.…), splitting a name into its title and its words, a name without titles, its
capitalised words, and the gender its titles imply.

Shared by the coreference tools (core/tools/coref.py), character profiles and matching (core/matching.py), so none of them
needs the others to compare names.
"""
from __future__ import annotations

import re

MALE_TITLES = {"mr", "mister", "sir", "lord", "master", "monsieur", "uncle", "king", "prince", "father", "herr", "signor",
               "don", "brother", "count", "duke", "baron"}
FEMALE_TITLES = {"mrs", "miss", "ms", "lady", "madam", "madame", "mme", "mlle", "mademoiselle", "mistress", "aunt",
                 "queen", "princess", "mother", "frau", "fräulein", "dame", "sister", "countess", "duchess", "baroness"}
OTHER_TITLES = {"dr", "doctor", "captain", "colonel", "major", "general", "inspector", "professor", "saint", "st",
                "reverend", "rev", "sergeant", "lieutenant", "the", "old", "young", "little", "poor", "dear"}
ALL_TITLES = MALE_TITLES | FEMALE_TITLES | OTHER_TITLES


def _norm_token(w):
    """A name word without a possessive 's and without punctuation, for comparing names."""
    w = re.sub(r"[’']s$", "", w)
    return re.sub(r"[^\w’'\-]", "", w)


def name_tokens(text):
    """Split a proper name into (title set, other tokens)."""
    titles, toks = set(), []
    for w in re.split(r"[\s]+", text.strip()):
        w = _norm_token(w)
        if not w:
            continue
        lw = w.lower().rstrip(".")
        if lw in ALL_TITLES and not toks:
            titles.add(lw)
            continue
        toks.append(w)
    return titles, toks


def bare_name(name):
    """A name without titles, lower case ("Mr. Sherlock Holmes" → "sherlock holmes"); a title alone stays ("doctor")."""
    return " ".join(name_tokens(name)[1]).lower() or name.lower().strip()


def name_words(toks):
    """The capitalised words of a name, lower case ("that fool Lestrade" → {"lestrade"})."""
    return {t.lower() for t in toks if len(t) >= 2 and t[0].isupper()}


def title_gender(titles):
    """m / f when the titles are only male or only female ones (Mr., Sir / Mrs., Miss), else None."""
    t = set(titles)
    m, f = bool(t & MALE_TITLES), bool(t & FEMALE_TITLES)
    return "m" if m and not f else "f" if f and not m else None
