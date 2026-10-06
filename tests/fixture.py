"""A tiny hand-annotated book in BookNLP's file formats, and a base TestCase that opens it as a Store.

The story (8 sentences, 3 paragraphs) has a named quote speaker, pronouns, a reflexive, a quote whose
speaker is named in a tag, and two common-noun objects, which is enough to exercise every feature.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from luna.core import bookfile  # noqa: E402
from luna.core.store import Store, create_project  # noqa: E402

# Tests run against the bundled copy of BookNLP's get_syntax so they don't depend on (or import) the installed BookNLP.
BUNDLED = (lambda *a: bookfile._get_syntax(None, *a), bookfile.Token, "the bundled copy (tests)")
bookfile._impl = BUNDLED

class Book:
    """A hand-annotated book: its text, its sentences, and the entity, supersense and quote annotations."""

    def __init__(self, text, sentences, para_starts, entities, supersenses, quotes, genders):
        self.text, self.sentences, self.para_starts = text, sentences, para_starts
        self.entities, self.supersenses, self.quotes, self.genders = entities, supersenses, quotes, genders
        self.sent_start, n = [], 0
        for s in sentences:
            self.sent_start.append(n)
            n += len(s)
        self.n_tokens = n
        self.words = [w[0] for s in sentences for w in s]

    def ords(self, sent, first, last=None):
        """Token positions (start, end) for words `first`..`last` of a sentence."""
        return self.sent_start[sent] + first, self.sent_start[sent] + (first if last is None else last)

    def joined(self, a, b):
        return " ".join(self.words[a:b + 1])


# word, lemma, POS, fine POS, dependency, head (index in the sentence)
# entities: sentence, first word, last word, prop, cat, group;  supersenses: sentence, first, last, category
# quotes: sentence, first word, last word, speaker mention (sentence, first, last) or None, speaker group
MAIN = Book(
    text=('Holmes greeted Watson.\n\n'
          '"I am tired," said Watson. He sat down.\n\n'
          'Mrs. Hudson smiled. "You look well, Holmes," she said. She helped herself to tea. '
          'Holmes thanked her. The violin lay on the table.\n'),
    sentences=[
        [("Holmes", "Holmes", "PROPN", "NNP", "nsubj", 1), ("greeted", "greet", "VERB", "VBD", "ROOT", 1),
         ("Watson", "Watson", "PROPN", "NNP", "dobj", 1), (".", ".", "PUNCT", ".", "punct", 1)],
        [('"', '"', "PUNCT", "``", "punct", 6), ("I", "I", "PRON", "PRP", "nsubj", 2), ("am", "be", "AUX", "VBP", "ccomp", 6),
         ("tired", "tired", "ADJ", "JJ", "acomp", 2), (",", ",", "PUNCT", ",", "punct", 2), ('"', '"', "PUNCT", "''", "punct", 6),
         ("said", "say", "VERB", "VBD", "ROOT", 6), ("Watson", "Watson", "PROPN", "NNP", "nsubj", 6), (".", ".", "PUNCT", ".", "punct", 6)],
        [("He", "he", "PRON", "PRP", "nsubj", 1), ("sat", "sit", "VERB", "VBD", "ROOT", 1),
         ("down", "down", "ADV", "RB", "advmod", 1), (".", ".", "PUNCT", ".", "punct", 1)],
        [("Mrs.", "Mrs.", "PROPN", "NNP", "compound", 1), ("Hudson", "Hudson", "PROPN", "NNP", "nsubj", 2),
         ("smiled", "smile", "VERB", "VBD", "ROOT", 2), (".", ".", "PUNCT", ".", "punct", 2)],
        [('"', '"', "PUNCT", "``", "punct", 9), ("You", "you", "PRON", "PRP", "nsubj", 2), ("look", "look", "VERB", "VBP", "ccomp", 9),
         ("well", "well", "ADV", "RB", "advmod", 2), (",", ",", "PUNCT", ",", "punct", 2), ("Holmes", "Holmes", "PROPN", "NNP", "npadvmod", 2),
         (",", ",", "PUNCT", ",", "punct", 2), ('"', '"', "PUNCT", "''", "punct", 9), ("she", "she", "PRON", "PRP", "nsubj", 9),
         ("said", "say", "VERB", "VBD", "ROOT", 9), (".", ".", "PUNCT", ".", "punct", 9)],
        [("She", "she", "PRON", "PRP", "nsubj", 1), ("helped", "help", "VERB", "VBD", "ROOT", 1),
         ("herself", "herself", "PRON", "PRP", "dobj", 1), ("to", "to", "ADP", "IN", "prep", 1),
         ("tea", "tea", "NOUN", "NN", "pobj", 3), (".", ".", "PUNCT", ".", "punct", 1)],
        [("Holmes", "Holmes", "PROPN", "NNP", "nsubj", 1), ("thanked", "thank", "VERB", "VBD", "ROOT", 1),
         ("her", "she", "PRON", "PRP", "dobj", 1), (".", ".", "PUNCT", ".", "punct", 1)],
        [("The", "the", "DET", "DT", "det", 1), ("violin", "violin", "NOUN", "NN", "nsubj", 2), ("lay", "lie", "VERB", "VBD", "ROOT", 2),
         ("on", "on", "ADP", "IN", "prep", 2), ("the", "the", "DET", "DT", "det", 5), ("table", "table", "NOUN", "NN", "pobj", 3),
         (".", ".", "PUNCT", ".", "punct", 2)],
    ],
    para_starts={0, 1, 3},
    entities=[(0, 0, 0, "PROP", "PER", 0), (0, 2, 2, "PROP", "PER", 1), (1, 1, 1, "PRON", "PER", 1), (1, 7, 7, "PROP", "PER", 1),
              (2, 0, 0, "PRON", "PER", 1), (3, 0, 1, "PROP", "PER", 2), (4, 1, 1, "PRON", "PER", 0), (4, 5, 5, "PROP", "PER", 0),
              (4, 8, 8, "PRON", "PER", 2), (5, 0, 0, "PRON", "PER", 2), (5, 2, 2, "PRON", "PER", 2), (6, 0, 0, "PROP", "PER", 0),
              (6, 2, 2, "PRON", "PER", 2), (7, 0, 1, "NOM", "FAC", 3), (7, 4, 5, "NOM", "FAC", 4)],
    supersenses=[(0, 1, 1, "verb.social"), (2, 1, 2, "verb.motion"), (7, 1, 1, "noun.artifact")],
    quotes=[(1, 0, 5, (1, 7, 7), 1), (4, 0, 7, (4, 8, 8), 2)],
    genders={0: "he/him/his", 1: "he/him/his", 2: "she/her"},
)

# A first-person narrator, he and she pronouns, an apposition in two groups, and "It" as a VAR of its own.
SECOND = Book(
    text=('I called on Holmes.\n\n'
          '"You are late," said Holmes. He laughed. He waved. She frowned. She sighed. I laughed.\n\n'
          'Holmes, the detective, waited. It rained.\n'),
    sentences=[
        [("I", "I", "PRON", "PRP", "nsubj", 1), ("called", "call", "VERB", "VBD", "ROOT", 1), ("on", "on", "ADP", "IN", "prep", 1),
         ("Holmes", "Holmes", "PROPN", "NNP", "pobj", 2), (".", ".", "PUNCT", ".", "punct", 1)],
        [('"', '"', "PUNCT", "``", "punct", 6), ("You", "you", "PRON", "PRP", "nsubj", 2), ("are", "be", "AUX", "VBP", "ccomp", 6),
         ("late", "late", "ADJ", "JJ", "acomp", 2), (",", ",", "PUNCT", ",", "punct", 2), ('"', '"', "PUNCT", "''", "punct", 6),
         ("said", "say", "VERB", "VBD", "ROOT", 6), ("Holmes", "Holmes", "PROPN", "NNP", "nsubj", 6), (".", ".", "PUNCT", ".", "punct", 6)],
        [("He", "he", "PRON", "PRP", "nsubj", 1), ("laughed", "laugh", "VERB", "VBD", "ROOT", 1), (".", ".", "PUNCT", ".", "punct", 1)],
        [("He", "he", "PRON", "PRP", "nsubj", 1), ("waved", "wave", "VERB", "VBD", "ROOT", 1), (".", ".", "PUNCT", ".", "punct", 1)],
        [("She", "she", "PRON", "PRP", "nsubj", 1), ("frowned", "frown", "VERB", "VBD", "ROOT", 1), (".", ".", "PUNCT", ".", "punct", 1)],
        [("She", "she", "PRON", "PRP", "nsubj", 1), ("sighed", "sigh", "VERB", "VBD", "ROOT", 1), (".", ".", "PUNCT", ".", "punct", 1)],
        [("I", "I", "PRON", "PRP", "nsubj", 1), ("laughed", "laugh", "VERB", "VBD", "ROOT", 1), (".", ".", "PUNCT", ".", "punct", 1)],
        [("Holmes", "Holmes", "PROPN", "NNP", "nsubj", 5), (",", ",", "PUNCT", ",", "punct", 5), ("the", "the", "DET", "DT", "det", 3),
         ("detective", "detective", "NOUN", "NN", "appos", 0), (",", ",", "PUNCT", ",", "punct", 5),
         ("waited", "wait", "VERB", "VBD", "ROOT", 5), (".", ".", "PUNCT", ".", "punct", 5)],
        [("It", "it", "PRON", "PRP", "nsubj", 1), ("rained", "rain", "VERB", "VBD", "ROOT", 1), (".", ".", "PUNCT", ".", "punct", 1)],
    ],
    para_starts={0, 1, 7},
    entities=[(0, 0, 0, "PRON", "PER", 0), (0, 3, 3, "PROP", "PER", 1), (1, 1, 1, "PRON", "PER", 0), (1, 7, 7, "PROP", "PER", 1),
              (2, 0, 0, "PRON", "PER", 1), (3, 0, 0, "PRON", "PER", 1), (4, 0, 0, "PRON", "PER", 2), (5, 0, 0, "PRON", "PER", 2),
              (6, 0, 0, "PRON", "PER", 0), (7, 0, 0, "PROP", "PER", 1), (7, 2, 3, "NOM", "PER", 3), (8, 0, 0, "PRON", "VAR", 4)],
    supersenses=[(0, 1, 1, "verb.social")],
    quotes=[(1, 0, 5, (1, 7, 7), 1)],
    genders={0: "he/him/his", 1: "he/him/his", 2: "she/her"},
)

# Kept for the tests of the main book
TEXT = MAIN.text
SENT_START = MAIN.sent_start
N_TOKENS = MAIN.n_tokens
ords = MAIN.ords


def write_book(out_dir: Path, name="fix", with_book=True, book: Book = MAIN) -> Path:
    """Write the BookNLP output files and the original text; return the text path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    text_path = out_dir.parent / f"{name}.txt"
    text_path.write_text(book.text, encoding="utf-8")
    header = ("paragraph_ID\tsentence_ID\ttoken_ID_within_sentence\ttoken_ID_within_document\tword\tlemma\tbyte_onset\t"
              "byte_offset\tPOS_tag\tfine_POS_tag\tdependency_relation\tsyntactic_head_ID\tevent")
    lines, cursor, doc, para = [header], 0, 0, -1
    rows = []
    for s, sent in enumerate(book.sentences):
        if s in book.para_starts:
            para += 1
        for i, (w, lemma, pos, tag, dep, head) in enumerate(sent):
            start = book.text.index(w, cursor)
            cursor = start + len(w)
            event = "EVENT" if pos == "VERB" else "O"
            rows.append((para, s, i, doc, w, pos, tag, lemma, dep, book.sent_start[s] + head, start))
            lines.append("\t".join(map(str, (para, s, i, doc, w, lemma, start, cursor, pos, tag, dep, book.sent_start[s] + head, event))))
            doc += 1
    (out_dir / f"{name}.tokens").write_text("\n".join(lines) + "\n", encoding="utf-8")
    ent = ["COREF\tstart_token\tend_token\tprop\tcat\ttext"]
    for s, a, b, prop, cat, g in book.entities:
        x, y = book.ords(s, a, b)
        ent.append(f"{g}\t{x}\t{y}\t{prop}\t{cat}\t{book.joined(x, y)}")
    (out_dir / f"{name}.entities").write_text("\n".join(ent) + "\n", encoding="utf-8")
    ss = ["start_token\tend_token\tsupersense_category\ttext"]
    for s, a, b, cat in book.supersenses:
        x, y = book.ords(s, a, b)
        ss.append(f"{x}\t{y}\t{cat}\t{book.joined(x, y)}")
    (out_dir / f"{name}.supersense").write_text("\n".join(ss) + "\n", encoding="utf-8")
    q = ["quote_start\tquote_end\tmention_start\tmention_end\tmention_phrase\tchar_id\tquote"]
    for s, a, b, mention, g in book.quotes:
        x, y = book.ords(s, a, b)
        if mention is None:   # BookNLP found no speaker mention for this quote
            m1 = m2 = mphrase = "None"
        else:
            m1, m2 = book.ords(*mention)
            mphrase = book.joined(m1, m2)
        q.append(f"{x}\t{y}\t{m1}\t{m2}\t{mphrase}\t{g}\t{book.joined(x, y)}")
    (out_dir / f"{name}.quotes").write_text("\n".join(q) + "\n", encoding="utf-8")
    if with_book:
        get_syntax, Tok, _ = bookfile.load()
        toks = [Tok(p, s, i, d, w, pos, tag, lemma, dep, head, None, start) for p, s, i, d, w, pos, tag, lemma, dep, head, start in rows]
        ents = [((*book.ords(s, a, b), f"{prop}_{cat}", book.joined(*book.ords(s, a, b))), g) for s, a, b, prop, cat, g in book.entities]
        gender = {}
        for g, argmax in book.genders.items():
            inf = {"he/him/his": 0.1, "she/her": 0.1, "they/them/their": 0.1}
            inf[argmax] = 0.8
            gender[g] = {"inference": inf, "argmax": argmax, "max": 0.8, "total": 5.0 + g}
        data = get_syntax(toks, [e for e, _ in ents], [g for _, g in ents], gender)
        (out_dir / f"{name}.book").write_text(json.dumps(data), encoding="utf-8")
        (out_dir / f"{name}.book.html").write_text("<html>original report</html>", encoding="utf-8")
    return text_path


class BookCase(unittest.TestCase):
    """Each test gets a fresh working copy of the fixture book, opened as `self.st`."""
    with_book = True
    book = MAIN

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bne-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.out = self.tmp / "output" / "fix"
        self.text_path = write_book(self.out, with_book=self.with_book, book=self.book)
        self.db_path = self.tmp / "fix.sqlite"
        create_project(self.db_path, self.out, self.text_path)
        self.open_store()

    def open_store(self):
        if getattr(self, "st", None):
            self.st.db.close()
        # a model that doesn't exist: re-tagging reports "spaCy couldn't load" instead of depending on the machine
        self.st = Store(self.db_path, spacy_model="no-such-model")
        self.addCleanup(self.st.db.close)
        return self.st

    # ---- helpers
    def uid_at(self, sent, first, last=None, table="entities"):
        a, b = self.book.ords(sent, first, last)
        r = self.st.db.execute(
            f"SELECT x.uid FROM {table} x JOIN tokens s ON s.uid=x.tok_start JOIN tokens e ON e.uid=x.tok_end "
            "WHERE s.ord=? AND e.ord=?", (a, b)).fetchone()
        self.assertIsNotNone(r, f"no {table} at sentence {sent} words {first}-{last}")
        return r[0]

    def ent(self, uid):
        return self.st._row("entities", uid)

    def group_uids(self, cid):
        return [r[0] for r in self.st.db.execute("SELECT uid FROM entities WHERE coref=? ORDER BY uid", (cid,))]

    def snapshot(self):
        """Everything the export writes, as plain data."""
        out = {}
        for t in ("tokens", "entities", "supersenses", "quotes", "groups", "flags"):
            out[t] = [dict(r) for r in self.st.db.execute(f"SELECT * FROM {t} ORDER BY uid")]
        return out
