"""Tag lists used for validation and autocompletion.

These are the label sets BookNLP produces: spaCy's English tags for
POS and dependencies, the 41 WordNet supersenses, and BookNLP's entity
labels. Values outside these lists are allowed but flagged.
"""

UPOS = ["ADJ", "ADP", "ADV", "AUX", "CCONJ", "DET", "INTJ", "NOUN", "NUM", "PART",
        "PRON", "PROPN", "PUNCT", "SCONJ", "SYM", "VERB", "X", "SPACE"]

PENN = ["$", "''", ",", "-LRB-", "-RRB-", ".", ":", "ADD", "AFX", "CC", "CD", "DT", "EX",
        "FW", "HYPH", "IN", "JJ", "JJR", "JJS", "LS", "MD", "NFP", "NN", "NNP", "NNPS", "NNS",
        "PDT", "POS", "PRP", "PRP$", "RB", "RBR", "RBS", "RP", "SYM", "TO", "UH", "VB", "VBD",
        "VBG", "VBN", "VBP", "VBZ", "WDT", "WP", "WP$", "WRB", "XX", "_SP", "``"]

DEPS = ["ROOT", "acl", "acomp", "advcl", "advmod", "agent", "amod", "appos", "attr", "aux",
        "auxpass", "case", "cc", "ccomp", "compound", "conj", "csubj", "csubjpass", "dative",
        "dep", "det", "dobj", "expl", "intj", "mark", "meta", "neg", "nmod", "npadvmod",
        "nsubj", "nsubjpass", "nummod", "oprd", "parataxis", "pcomp", "pobj", "poss",
        "preconj", "predet", "prep", "prt", "punct", "quantmod", "relcl", "xcomp"]

EVENT = ["EVENT", "O"]

SUPERSENSES = [
    "noun.Tops", "noun.act", "noun.animal", "noun.artifact", "noun.attribute", "noun.body",
    "noun.cognition", "noun.communication", "noun.event", "noun.feeling", "noun.food",
    "noun.group", "noun.location", "noun.motive", "noun.object", "noun.person",
    "noun.phenomenon", "noun.plant", "noun.possession", "noun.process", "noun.quantity",
    "noun.relation", "noun.shape", "noun.state", "noun.substance", "noun.time",
    "verb.body", "verb.change", "verb.cognition", "verb.communication", "verb.competition",
    "verb.consumption", "verb.contact", "verb.creation", "verb.emotion", "verb.motion",
    "verb.perception", "verb.possession", "verb.social", "verb.stative", "verb.weather"]

# BookNLP's six types, plus VAR ("various"): set by hand in the editor for objects and other
# things that don't fit the others. BookNLP itself never produces VAR.
ENTITY_CATS = ["PER", "LOC", "FAC", "GPE", "VEH", "ORG", "VAR"]
ENTITY_PROPS = ["PROP", "NOM", "PRON"]

# (table, field) -> allowed values
TAGSETS = {
    ("tokens", "pos"): UPOS,
    ("tokens", "tag"): PENN,
    ("tokens", "dep"): DEPS,
    ("tokens", "event"): EVENT,
    ("entities", "cat"): ENTITY_CATS,
    ("entities", "prop"): ENTITY_PROPS,
    ("supersenses", "cat"): SUPERSENSES,
}
