"""Synthetic BookNLP output of any size, for tests of scale (not real language, but real file formats and realistic shape).

A book is a stream of sentences, each either narration ("<someone> <verb> <someone> [in <place>].") or a quote, tagged
("…," said <someone>.) or untagged. Characters are drawn from a cast with a Zipf-like popularity, so there are a few big
groups and a long tail of small ones, as in BookNLP's output for a novel; mentions are names, pronouns or descriptions.

    from tests.synthetic import write_book
    write_book(Path("out"), "big", sentences=20000)          # out/big/big.tokens … and out/big.txt

or from the command line: `python -m tests.synthetic OUT_DIR SENTENCES [SEED] [BOOK_ID]`.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from luna.core import bookfile  # noqa: E402

FIRST = ["John", "Mary", "Sherlock", "James", "Alice", "Henry", "Anna", "Peter", "Lucy", "Robert", "Emma", "George",
         "Helen", "Arthur", "Clara", "Edward", "Jane", "Thomas", "Rose", "William", "Ruth", "Frank", "Nora", "Hugh"]
LAST = ["Holmes", "Watson", "Hudson", "Lestrade", "Moriarty", "Baskerville", "Stapleton", "Barrymore", "Mortimer",
        "Openshaw", "Adler", "Morstan", "Sholto", "Jones", "Brown", "Smith", "Taylor", "Wilson", "Moore", "Clark",
        "Walker", "Hall", "Allen", "Young", "King", "Wright", "Scott", "Green", "Baker", "Adams", "Nelson", "Hill"]
SYLLABLES = ["ba", "ro", "ken", "mar", "li", "ton", "wor", "ley", "sen", "dan", "fil", "gor", "hal", "jen", "kel", "mon",
             "nar", "pel", "quin", "ris"]
MALE_TITLES, FEMALE_TITLES = ["Mr.", "Dr.", "Sir"], ["Mrs.", "Miss", "Lady"]
VERBS = {"see": "saw", "meet": "met", "call": "called", "follow": "followed", "tell": "told", "ask": "asked",
         "help": "helped", "watch": "watched", "visit": "visited", "leave": "left", "greet": "greeted", "thank": "thanked"}
NOUNS = ["man", "woman", "doctor", "landlady", "inspector", "stranger", "gentleman", "servant", "clerk", "child"]
PLACES = ["Baker Street", "London", "the moor", "the hall", "Devon", "the station", "the garden"]
WORDS = ("indeed certainly really remember never always think believe matter business night morning house room door "
         "window letter paper story case clue strange curious dreadful terrible pleasant simple plain evident likely").split()


def _cast(n_groups: int, seed: int, distinct: bool):
    """The characters: gender, a full name, a short name (the surname) and a description noun. With `distinct`, everyone
    after the first 40 gets a made-up name, so a long cast doesn't run out of real-sounding ones."""
    rnd = random.Random(seed)
    cast = []
    for g in range(n_groups):
        male = rnd.random() < 0.6
        first, last = rnd.choice(FIRST), rnd.choice(LAST)
        if distinct and g > 40:
            last = "".join(rnd.choice(SYLLABLES) for _ in range(rnd.randint(2, 3))).capitalize()
            first = "".join(rnd.choice(SYLLABLES) for _ in range(2)).capitalize()
        title = rnd.choice(MALE_TITLES if male else FEMALE_TITLES)
        cast.append({"male": male, "name": [title, last] if rnd.random() < 0.5 else [first, last], "short": [last],
                     "noun": rnd.choice(NOUNS)})
    return cast


class _Builder:
    """Collects tokens, mentions and quotes sentence by sentence."""

    def __init__(self):
        """Nothing yet: tokens are (word, lemma, POS, fine POS, dependency, head position, paragraph, sentence)."""
        self.tokens, self.ents, self.quotes, self.supers = [], [], [], []
        self.para = self.sent = -1

    def sentence(self, toks):
        """Add one sentence given as (word, lemma, POS, fine POS, dependency, head index within the sentence); returns the
        position of its first token."""
        self.sent += 1
        base = len(self.tokens)
        for w, lemma, pos, tag, dep, head in toks:
            self.tokens.append((w, lemma, pos, tag, dep, base + head, self.para, self.sent))
        return base


def _mention(rnd, grp):
    """How the character is referred to this time: (words, mention type)."""
    r = rnd.random()
    if r < 0.40:
        return (grp["name"] if rnd.random() < 0.5 else grp["short"]), "PROP"
    if r < 0.85:
        return ["he" if grp["male"] else "she"], "PRON"
    return ["the", grp["noun"]], "NOM"


def _mention_tokens(words, kind, dep, verb_pos, offset):
    """Tokens (word, lemma, POS, fine POS, dependency, head position in the sentence) of a mention that starts at position
    `offset` of its sentence: its last word is the head word (`dep` on the verb at `verb_pos`), the others hang on it. A
    sentence-initial pronoun or description is capitalised."""
    last = offset + len(words) - 1
    out = []
    for i, w in enumerate(words):
        head_word = i == len(words) - 1
        if kind == "PROP":
            pos, tag, d = "PROPN", "NNP", dep if head_word else "compound"
        elif kind == "PRON":
            pos, tag, d = "PRON", "PRP", dep
        else:
            pos, tag, d = ("DET", "DT", "det") if i == 0 else ("NOUN", "NN", dep)
        if offset == 0 and i == 0 and kind != "PROP":
            w = w.capitalize()
        out.append((w, w.lower(), pos, tag, d, verb_pos if head_word else last))
    return out


def generate(sentences: int, seed: int = 1, groups: int = 400, zipf: float = 0.9, distinct_names: bool = False):
    """Build the book: returns the `_Builder` and the cast."""
    rnd = random.Random(seed)
    cast = _cast(groups, seed, distinct_names)
    weights = [1 / (i + 1) ** zipf for i in range(groups)]
    place_ids = list(range(groups, groups + len(PLACES)))
    b = _Builder()
    in_para, subject = 0, 0
    for _ in range(sentences):
        if in_para == 0:
            b.para += 1
        in_para = (in_para + 1) % rnd.randint(2, 5)
        g1 = subject if rnd.random() < 0.4 else rnd.choices(range(groups), weights)[0]
        subject = g1
        if rnd.random() < 0.55:
            _narration(b, rnd, cast, weights, g1, place_ids)
        else:
            _quote(b, rnd, cast, g1)
    return b, cast


def _narration(b, rnd, cast, weights, g1, place_ids):
    """"<g1> <verb> <g2> [in <place>]." with the subject and object as mentions and the verb as a supersense."""
    g2 = rnd.choices(range(len(cast)), weights)[0]
    if g2 == g1:
        g2 = (g2 + 1) % len(cast)
    m1, p1 = _mention(rnd, cast[g1])
    m2, p2 = _mention(rnd, cast[g2])
    verb = rnd.choice(list(VERBS))
    vpos = len(m1)
    toks = _mention_tokens(m1, p1, "nsubj", vpos, 0)
    toks.append((VERBS[verb], verb, "VERB", "VBD", "ROOT", vpos))
    o_start = len(toks)
    toks += _mention_tokens(m2, p2, "dobj", vpos, o_start)
    place = None
    if rnd.random() < 0.3:
        pl = rnd.randrange(len(PLACES))
        words = PLACES[pl].split()
        toks.append(("in", "in", "ADP", "IN", "prep", vpos))
        pstart = len(toks)
        for i, w in enumerate(words):
            last = i == len(words) - 1
            toks.append((w, w.lower(), "PROPN", "NNP", "pobj" if last else "compound", pstart - 1 if last else pstart + len(words) - 1))
        place = (pstart, pstart + len(words) - 1, place_ids[pl])
    toks.append((".", ".", "PUNCT", ".", "punct", vpos))
    base = b.sentence(toks)
    b.ents.append((base, base + len(m1) - 1, p1, "PER", g1, " ".join(t[0] for t in toks[:len(m1)])))
    b.ents.append((base + o_start, base + o_start + len(m2) - 1, p2, "PER", g2, " ".join(m2)))
    if place:
        a, z, pid = place
        b.ents.append((base + a, base + z, "PROP", "LOC", pid, " ".join(t[0] for t in toks[a:z + 1])))
    b.supers.append((base + vpos, base + vpos, "verb.social", toks[vpos][0]))


def _quote(b, rnd, cast, g1):
    """A quote of 3-14 words, tagged ('…," said <g1>.') seven times in ten; some contain "I", a first-person mention."""
    body = [rnd.choice(WORDS) for _ in range(rnd.randint(3, 14))]
    first_person = []
    if rnd.random() < 0.25:
        k = rnd.randrange(len(body))
        body[k] = "I"
        first_person.append(k)
    tagged = rnd.random() < 0.7
    toks = [('"', '"', "PUNCT", "``", "punct", 0)]
    toks += [(w, w.lower(), "PRON" if w == "I" else "NOUN", "PRP" if w == "I" else "NN", "dep", 0) for w in body]
    toks += [(",", ",", "PUNCT", ",", "punct", 0), ('"', '"', "PUNCT", "''", "punct", 0)]
    q_end = len(toks) - 1
    mention = None
    if tagged:
        m, kind = _mention(rnd, cast[g1])
        vpos = len(toks)
        toks.append(("said", "say", "VERB", "VBD", "ROOT", vpos))
        s_start = len(toks)
        toks += _mention_tokens(m, kind, "nsubj", vpos, s_start)
        toks.append((".", ".", "PUNCT", ".", "punct", vpos))
        toks[:q_end + 1] = [(*t[:5], vpos) for t in toks[:q_end + 1]]
        mention = (s_start, s_start + len(m) - 1, kind, " ".join(m))
    else:
        toks.append((".", ".", "PUNCT", ".", "punct", 0))
    base = b.sentence(toks)
    text = " ".join(t[0] for t in toks[:q_end + 1])
    if mention:
        a, z, kind, phrase = mention
        b.ents.append((base + a, base + z, kind, "PER", g1, phrase))
        b.quotes.append((base, base + q_end, base + a, base + z, phrase, g1, text))
    else:
        b.quotes.append((base, base + q_end, None, None, None, g1 if rnd.random() < 0.8 else None, text))
    for k in first_person:
        b.ents.append((base + 1 + k, base + 1 + k, "PRON", "PER", g1, "I"))


def write_book(out: Path, book_id: str = "big", sentences: int = 2000, seed: int = 1, groups: int = 400, zipf: float = 0.9,
               distinct_names: bool = False, with_book: bool = True) -> Path:
    """Write the BookNLP output files of a synthetic book to `out/<book_id>/` and its text to `out/<book_id>.txt` (returned).

    `with_book` also makes the `.book` file (BookNLP's `get_syntax` over the whole book: a few seconds for a long one)."""
    b, cast = generate(sentences, seed, groups, zipf, distinct_names)
    d = Path(out) / book_id
    d.mkdir(parents=True, exist_ok=True)
    pieces, offsets, cursor, prev_para = [], [], 0, None
    for w, *_rest, para, _sent in b.tokens:
        if prev_para is not None:
            sep = "\n\n" if para != prev_para else " "
            pieces.append(sep)
            cursor += len(sep)
        offsets.append((cursor, cursor + len(w)))
        pieces.append(w)
        cursor += len(w)
        prev_para = para
    text_path = Path(out) / f"{book_id}.txt"
    text_path.write_text("".join(pieces) + "\n", encoding="utf-8")
    lines, within, prev_sent = ["paragraph_ID\tsentence_ID\ttoken_ID_within_sentence\ttoken_ID_within_document\tword\tlemma\t"
                                "byte_onset\tbyte_offset\tPOS_tag\tfine_POS_tag\tdependency_relation\tsyntactic_head_ID\tevent"], 0, None
    for i, ((w, lemma, pos, tag, dep, head, para, sent), (a, z)) in enumerate(zip(b.tokens, offsets)):
        within = 0 if sent != prev_sent else within + 1
        prev_sent = sent
        lines.append("\t".join(map(str, (para, sent, within, i, w, lemma, a, z, pos, tag, dep, head, "EVENT" if pos == "VERB" else "O"))))
    (d / f"{book_id}.tokens").write_text("\n".join(lines) + "\n", encoding="utf-8")
    ents, seen = [], set()
    for e in sorted(b.ents):
        if (e[0], e[1]) not in seen:                       # one mention per span
            seen.add((e[0], e[1]))
            ents.append(e)
    (d / f"{book_id}.entities").write_text("COREF\tstart_token\tend_token\tprop\tcat\ttext\n" + "\n".join(
        f"{g}\t{a}\t{z}\t{prop}\t{cat}\t{t}" for a, z, prop, cat, g, t in ents) + "\n", encoding="utf-8")
    (d / f"{book_id}.supersense").write_text("start_token\tend_token\tsupersense_category\ttext\n" + "\n".join(
        f"{a}\t{z}\t{c}\t{t}" for a, z, c, t in b.supers) + "\n", encoding="utf-8")
    (d / f"{book_id}.quotes").write_text("quote_start\tquote_end\tmention_start\tmention_end\tmention_phrase\tchar_id\tquote\n" + "\n".join(
        f"{a}\t{z}\t{ms}\t{me}\t{mp}\t{c}\t{t}" for a, z, ms, me, mp, c, t in b.quotes) + "\n", encoding="utf-8")
    if with_book:
        get_syntax, Tok, _ = bookfile.load()
        toks = [Tok(para, sent, 0, i, w, pos, tag, lemma, dep, head, None, offsets[i][0])
                for i, (w, lemma, pos, tag, dep, head, para, sent) in enumerate(b.tokens)]
        gender = {}
        for g, grp in enumerate(cast):
            arg = "he/him/his" if grp["male"] else "she/her"
            gender[g] = {"inference": {"he/him/his": 0.1, "she/her": 0.1, "they/them/their": 0.1, arg: 0.8}, "argmax": arg,
                         "max": 0.8, "total": 5.0}
        data = get_syntax(toks, [(a, z, f"{p}_{c}", t) for a, z, p, c, g, t in ents], [g for *_, g, _ in ents], gender)
        (d / f"{book_id}.book").write_text(json.dumps(data), encoding="utf-8")
        (d / f"{book_id}.book.html").write_text("<html>original report</html>", encoding="utf-8")
    return text_path


if __name__ == "__main__":
    out, n = Path(sys.argv[1]), int(sys.argv[2])
    write_book(out, sys.argv[4] if len(sys.argv) > 4 else "big", n, int(sys.argv[3]) if len(sys.argv) > 3 else 1)
    print(f"wrote {n} sentences to {out}")
