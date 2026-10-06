"""Settings for the editor's suggestions: which kinds of evidence each kind of suggestion uses, and how much each counts.

`TYPES` lists, per kind of suggestion, every kind of evidence it can use: a key, a label and an explanation for the settings
dialog, its default weight, and whether it counts for (+1) or against (-1). Profile evidence (keys starting with "p_") compares
the character profiles of core/tools/profile.py through core/matching.py (`Matcher.similarity`): a similarity from 0 to 1,
times the weight. Rule evidence uses the weight as its score (or as its cap, where the explanation says so).

Saved settings live in collections.json (`settings.suggestions`, per type: `weights`, `off`, `thresholds`); `settings` lays
them over the defaults. Collection matching has its own settings in core/matching.py and is shown beside these.
"""
from __future__ import annotations

import copy

# The profile evidence merges and fragments use: "p_" + a kind of supporting evidence of core/matching.py (`VECTORS`,
# speech, embedding), its label and explanation, and its default weight.
PROFILE = [
    ("p_nouns", "Described alike", "The same descriptions (the doctor, the landlady).", 0.75),
    ("p_relations", "Same relations", "The same relations to the same characters (wife of Holmes).", 0.75),
    ("p_cooccur", "Appear with the same people", "The same other characters in their paragraphs.", 0.5),
    ("p_places", "Same places", "The same places in their paragraphs.", 0.25),
    ("p_actions", "Act alike", "The same verbs, possessions and descriptions.", 0.5),
    ("p_speech", "Speak alike (style)", "Function-word rates of their quotes; weak between characters of one author.", 0.5),
    ("p_embedding", "Speak alike (transformer)", "Transformer vectors of their quotes, where computed (Collection tool).", 0.5),
]
# Two groups of one book mentioned in the same paragraphs share their neighbours and places whether or not they are one
# person, so within a book these measure closeness in the text, not identity (tried on the Holmes books): off by default.
LOCAL = ["p_cooccur", "p_places"]


def _profile_evidence():
    """The PROFILE rows as evidence of a suggestion type (all count for)."""
    return [{"key": k, "label": label, "help": help, "w": w, "sign": 1, "profile": True} for k, label, help, w in PROFILE]


TYPES = {
    "merge": {
        "label": "Merge suggestions",
        "note": "Pairs of groups of one book that may be one. Names make the candidates; the rest adds or takes away. A pair "
                "is listed when its score reaches “Listed from”. “Appear with the same people” and “Same places” are off by "
                "default here: two groups mentioned in the same paragraphs share those whether or not they are one person.",
        "evidence": [
            {"key": "same_name", "label": "Same name", "help": "Their main names are the same without titles.", "w": 3.0, "sign": 1},
            {"key": "name_part", "label": "One name part of the other", "help": "“Holmes” and “Sherlock Holmes”.", "w": 2.0, "sign": 1},
            {"key": "shared_word", "label": "Share a name word", "help": "Some word of their names is the same.", "w": 1.0, "sign": 1},
            {"key": "spelling", "label": "Spelled almost the same", "help": "Name words that differ by a letter or two.", "w": 1.5, "sign": 1},
            {"key": "apposition", "label": "Description next to a name", "help": "A lone description set off by a comma from a name (Holmes, the detective).", "w": 3.0, "sign": 1},
            {"key": "name_before", "label": "Description after a name", "help": "A lone description a few words after a name.", "w": 0.75, "sign": 1},
            {"key": "pronouns_agree", "label": "Same pronouns", "help": "Both he, both she, or both they.", "w": 0.5, "sign": 1},
            {"key": "pronouns_differ", "label": "Different pronouns", "help": "he vs she.", "w": 3.0, "sign": -1},
            {"key": "titles_differ", "label": "Titles differ", "help": "Mr. vs Mrs.", "w": 3.0, "sign": -1},
            {"key": "first_names_differ", "label": "Different first names", "help": "Sherlock vs Mycroft Holmes: perhaps relatives.", "w": 2.0, "sign": -1},
            {"key": "named_together", "label": "Named together", "help": "Named in the same sentences: a quarter of the weight per sentence, at most the weight.", "w": 2.0, "sign": -1},
            {"key": "talk", "label": "Speak to each other", "help": "Take turns in conversations: half the weight, plus a twelfth per exchange, at most the weight.", "w": 3.0, "sign": -1},
            *_profile_evidence(),
        ],
        "thresholds": [{"key": "min_score", "label": "Listed from", "help": "The score a pair needs to be listed.", "v": 1.0}],
        "off": LOCAL,
    },
    "speaker": {
        "label": "Speaker suggestions",
        "note": "For each quote, candidate speakers get a score from the evidence below; another speaker than BookNLP's is "
                "suggested when it leads by the margin. “Sounds like” compares the quote's words with each candidate's "
                "other quotes (in this book, and a linked character's in the collection's other books).",
        "evidence": [
            {"key": "tag_name", "label": "Tag with a name", "help": "“…,” said Holmes: the speech verb's subject is a name.", "w": 4.0, "sign": 1},
            {"key": "tag_noun", "label": "Tag with a description", "help": "“…,” said the inspector.", "w": 3.0, "sign": 1},
            {"key": "tag_pronoun", "label": "Tag with a pronoun", "help": "“…,” he said.", "w": 2.5, "sign": 1},
            {"key": "tag_unparsed", "label": "Tag found only nearby", "help": "The speech verb was found by nearness, not through the parse.", "w": 0.5, "sign": -1},
            {"key": "continues", "label": "Goes on from the quote before", "help": "The quote before it in the same paragraph has this speaker.", "w": 2.0, "sign": 1},
            {"key": "turns", "label": "Turns alternate", "help": "A spoke, then B; with new paragraphs and no tag, A again.", "w": 1.5, "sign": 1},
            {"key": "repeats", "label": "Repeats right after a turn", "help": "The untagged turn just before, with nothing narrated in between, has this candidate speaking already; a speaker rarely repeats like that.", "w": 2.0, "sign": -1},
            {"key": "named_before", "label": "Named just before", "help": "A name just before a quote that has no speaker yet.", "w": 1.0, "sign": 1},
            {"key": "booknlp", "label": "BookNLP's choice", "help": "The speaker BookNLP gave the quote.", "w": 1.0, "sign": 1},
            {"key": "booknlp_inside", "label": "BookNLP's mention inside the quote", "help": "BookNLP attributed it through a mention within the quote itself.", "w": 3.0, "sign": -1},
            {"key": "booknlp_far", "label": "BookNLP's mention far away", "help": "In another paragraph or more than 50 words away.", "w": 1.0, "sign": -1},
            {"key": "addressed", "label": "Addressed in the quote", "help": "“Watson, you had better stay”: not Watson speaking.", "w": 2.5, "sign": -1},
            {"key": "p_words_book", "label": "Sounds like (this book)", "help": "The quote's words compared with the candidate's other quotes in this book; rare words count more.", "w": 1.0, "sign": 1, "profile": True},
            {"key": "p_words_collection", "label": "Sounds like (collection)", "help": "The same with a linked character's quotes in the collection's other books.", "w": 1.0, "sign": 1, "profile": True},
        ],
        "thresholds": [
            {"key": "margin", "label": "Margin", "help": "How far another speaker must lead BookNLP's to be suggested.", "v": 1.5},
            {"key": "min_none", "label": "For quotes without a speaker", "help": "The score a speaker needs to be suggested for a quote BookNLP gave none.", "v": 1.0},
        ],
    },
    "fragments": {
        "label": "Fragments",
        "note": "The likely targets offered for each small group, best first: each candidate's score adds up the evidence "
                "below. “Appear with the same people” and “Same places” are off by default, as for merges: groups mentioned "
                "near each other share those anyway.",
        "evidence": [
            {"key": "merge", "label": "Merge suggestion", "help": "The pair is a merge suggestion: its score times the weight.", "w": 1.0, "sign": 1},
            {"key": "near", "label": "Mentioned nearby", "help": "The weight for the very next mention, half of it 20 words away, a sixth 100 words away (a name counts as nearer than a pronoun).", "w": 1.0, "sign": 1},
            {"key": "same_type", "label": "Same type", "help": "Both PER, both LOC…", "w": 0.5, "sign": 1},
            {"key": "bigger", "label": "An established group", "help": "The candidate has more mentions than the fragment size (not another fragment).", "w": 0.5, "sign": 1},
            {"key": "pronouns_agree", "label": "Same pronouns", "help": "Both he, both she, or both they.", "w": 0.5, "sign": 1},
            {"key": "pronouns_differ", "label": "Different pronouns", "help": "he vs she.", "w": 1.0, "sign": -1},
            *_profile_evidence(),
        ],
        "thresholds": [{"key": "window", "label": "Nearby within", "help": "How many words around the fragment count as nearby.", "v": 300}],
        "off": LOCAL,
    },
}


def defaults():
    """Every type's default settings: {type: {weights: {key: w}, off: [keys switched off by default], thresholds: {key: v}}}."""
    return {t: {"weights": {e["key"]: e["w"] for e in spec["evidence"]}, "off": list(spec.get("off", [])),
                "thresholds": {x["key"]: x["v"] for x in spec.get("thresholds", [])}} for t, spec in TYPES.items()}


def settings(saved=None):
    """The defaults with the saved values laid over them (unknown keys and bad values ignored)."""
    out = defaults()
    for t, got in (saved or {}).items():
        if t not in out or not isinstance(got, dict):
            continue
        cur = out[t]
        for k, v in (got.get("weights") or {}).items():
            if k in cur["weights"] and isinstance(v, (int, float)) and v >= 0:
                cur["weights"][k] = float(v)
        for k, v in (got.get("thresholds") or {}).items():
            if k in cur["thresholds"] and isinstance(v, (int, float)):
                cur["thresholds"][k] = float(v)
        if isinstance(got.get("off"), list):
            cur["off"] = sorted({k for k in got["off"] if k in cur["weights"]})
    return out


def weight(cfg, key):
    """One kind of evidence's weight in a type's settings, 0 when switched off."""
    return 0.0 if key in cfg["off"] else cfg["weights"].get(key, 0.0)


def spec():
    """The types and their evidence for the settings dialog (labels, explanations, defaults, for or against)."""
    return copy.deepcopy(TYPES)
