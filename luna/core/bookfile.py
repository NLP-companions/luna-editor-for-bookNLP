"""Rebuild BookNLP's .book and .book.html from the corrected working copy.

The character summary in .book comes from BookNLP's own `get_syntax`, taken
from the BookNLP installed in this environment. If that can't be imported
(for example because torch fails to load), a bundled verbatim copy of the
same function is used instead; the export says which one ran.

The .book.html report is written by code inside BookNLP's main pipeline
rather than a separate function, so it is ported here unchanged, apart from
using the names you set in the editor.

BookNLP is MIT-licensed: Copyright (c) 2021 David Bamman.
"""
from __future__ import annotations

from collections import Counter
from html import escape

_impl = None


class Token:
    """Same fields as booknlp.common.pipelines.Token."""

    def __init__(self, paragraph_id, sentence_id, index_within_sentence_idx, token_id, text, pos, fine_pos, lemma,
                 deprel, dephead, ner, startByte):
        """Set the fields BookNLP's get_syntax reads; the end offset follows from the text length."""
        self.text = text
        self.paragraph_id = paragraph_id
        self.sentence_id = sentence_id
        self.index_within_sentence_idx = index_within_sentence_idx
        self.token_id = token_id
        self.lemma = lemma
        self.pos = pos
        self.fine_pos = fine_pos
        self.deprel = deprel
        self.dephead = dephead
        self.ner = ner
        self.startByte = startByte
        self.endByte = startByte + len(text)
        self.inQuote = False
        self.event = "O"


def load():
    """Return (get_syntax, Token class, description of the code used)."""
    global _impl
    if _impl is None:
        try:
            from booknlp.english.english_booknlp import EnglishBookNLP
            from booknlp.common.pipelines import Token as BToken
            try:
                from importlib.metadata import version
                v = version("booknlp")
            except Exception:
                v = None
            _impl = (lambda *a: EnglishBookNLP.get_syntax(None, *a), BToken,
                     f"the installed BookNLP {v}" if v else "the installed BookNLP")
        except Exception as e:  # BookNLP or one of its dependencies won't import
            _impl = (lambda *a: _get_syntax(None, *a), Token,
                     f"the bundled copy of BookNLP's code, because the installed BookNLP couldn't be imported ({type(e).__name__}: {e})")
    return _impl


def combine_g(parts):
    """Combine BookNLP referential-gender entries, weighting each by its total."""
    parts = [(g, n) for g, n in parts if g]
    if not parts:
        return None
    if len(parts) == 1:
        return dict(parts[0][0])
    weights = [(g, g.get("total") or n) for g, n in parts]
    tw = sum(w for _, w in weights) or 1
    keys = []
    for g, _ in weights:
        for k in g.get("inference", {}):
            if k not in keys:
                keys.append(k)
    inf = {k: sum(g.get("inference", {}).get(k, 0) * w for g, w in weights) / tw for k in keys}
    argmax = max(inf, key=lambda k: inf[k]) if inf else None
    return {"inference": inf, "argmax": argmax, "max": inf.get(argmax, 0) if argmax else 0,
            "total": sum(g.get("total") or 0 for g, _ in parts)}


def manual_g(pronoun, base):
    """Referential-gender data for a group whose pronouns you set by hand: all weight on that pronoun."""
    keys = list((base or {}).get("inference", {}).keys()) or ["he/him/his", "she/her", "they/them/their"]
    if pronoun not in keys:
        keys.append(pronoun)
    inf = {k: (1.0 if k == pronoun else 0.0) for k in keys}
    return {"inference": inf, "argmax": pronoun, "max": 1.0, "total": (base or {}).get("total", 0)}


def book_html(chardata, tokens, entities, assignments, quotes, renamed):
    """Port of the .book.html writer in BookNLP's english_booknlp.py.

    quotes: list of (start, end, speaker_id or None). renamed: {coref: name} for groups you named.
    """
    names = {}
    for idx, (start, end, cat, text) in enumerate(entities):
        coref = assignments[idx]
        if coref not in names:
            names[coref] = Counter()
        ner_prop = cat.split("_")[0]
        if ner_prop == "PROP":
            names[coref][text.lower()] += 10
        elif ner_prop == "NOM":
            names[coref][text.lower()] += 1
        else:
            names[coref][text.lower()] += .001

    def name_of(c):
        """The display name of a coreference group as HTML: your name for it (escaped, it is whatever you typed), else
        BookNLP's most frequent name (proper names weigh most; written as BookNLP writes it)."""
        return escape(renamed[c]) if renamed.get(c) else names[c].most_common(1)[0][0]

    out = []
    out.append("<html>")
    out.append("""<head>
		  <meta charset="UTF-8">
		</head>""")
    out.append("<h2>Named characters</h2>\n")
    for character in chardata["characters"]:
        char_id = character["id"]
        proper_names = character["mentions"]["proper"]
        if len(proper_names) > 0 or char_id == 0:  # 0=narrator
            proper_name_list = "/".join(["%s (%s)" % (name["n"], name["c"]) for name in proper_names])
            common_names = character["mentions"]["common"]
            common_name_list = "/".join(["%s (%s)" % (name["n"], name["c"]) for name in common_names])
            char_count = character["count"]
            if char_id == 0:
                if len(proper_name_list) == 0:
                    proper_name_list = "[NARRATOR]"
                else:
                    proper_name_list += "/[NARRATOR]"
            label = "<b>%s</b>: " % escape(renamed[char_id]) if char_id in renamed else ""
            out.append("%s %s%s %s <br />\n" % (char_count, label, proper_name_list, common_name_list))

    out.append("<p>\n")
    out.append("<h2>Major entities (proper, common)</h2>")
    # BookNLP's six types; VAR (set by hand in the editor) gets its own section only when used,
    # so a book without VAR gives exactly BookNLP's report.
    cats = ["FAC", "GPE", "LOC", "PER", "ORG", "VEH"]
    if any(cat.split("_", 1)[1] == "VAR" for _, _, cat, _ in entities):
        cats.append("VAR")
    major_places = {}
    for prop in ["PROP", "NOM"]:
        major_places[prop] = {}
        for cat in cats:
            major_places[prop][cat] = {}
    for idx, (start, end, cat, text) in enumerate(entities):
        coref = assignments[idx]
        ner_prop = cat.split("_")[0]
        ner_type = cat.split("_")[1]
        if ner_prop != "PRON" and ner_prop in major_places and ner_type in major_places[ner_prop]:
            if coref not in major_places[ner_prop][ner_type]:
                major_places[ner_prop][ner_type][coref] = Counter()
            major_places[ner_prop][ner_type][coref][text] += 1
    max_entities_to_display = 10
    for cat in cats:
        out.append("<h3>%s</h3>" % cat)
        for prop in ["PROP", "NOM"]:
            freqs = {}
            for coref in major_places[prop][cat]:
                freqs[coref] = sum(major_places[prop][cat][coref].values())
            sorted_freqs = sorted(freqs.items(), key=lambda x: x[1], reverse=True)
            for k, v in sorted_freqs[:max_entities_to_display]:
                ent_names = []
                for name, count in major_places[prop][cat][k].most_common():
                    ent_names.append("%s" % (name))
                out.append("%s %s <br />" % (v, '/'.join(ent_names)))
            out.append("<p>")

    out.append("<h2>Text</h2>\n")
    beforeToks = [""] * len(tokens)
    afterToks = [""] * len(tokens)
    lastP = None
    for idx, (start, end, cat, text) in enumerate(entities):
        coref = assignments[idx]
        name = name_of(coref)
        beforeToks[start] += "<font color=\"#D0D0D0\">[</font>"
        afterToks[end] = "<font color=\"#D0D0D0\">]</font><font color=\"#FF00FF\"><sub>%s-%s</sub></font>" % (coref, name) + afterToks[end]
    for start, end, speaker_id in quotes:
        if speaker_id is not None and speaker_id in names:
            name = name_of(speaker_id)
        else:
            speaker_id = "None"
            name = "None"
        beforeToks[start] += "<font color=\"#666699\">"
        afterToks[end] += "</font><sub>[%s-%s]</sub>" % (speaker_id, name)
    for idx in range(len(tokens)):
        if tokens[idx].paragraph_id != lastP:
            out.append("<p />")
        out.append("%s%s%s " % (beforeToks[idx], escape(tokens[idx].text), afterToks[idx]))
        lastP = tokens[idx].paragraph_id
    out.append("</html>")
    return "".join(out)


# --- bundled verbatim copy of EnglishBookNLP.get_syntax (booknlp/english/english_booknlp.py) ---
# What it does: for every coreference group (character) with enough mentions, it walks the dependency parse of each
# mention and collects what the character does (agent: verbs it is the subject of), what is done to it (patient),
# what it has (possessions) and how it is described (modifiers), plus its names, mention counts and gender evidence.
# The result is the content of BOOK.book. It is kept verbatim (tabs and all) so it can be compared with BookNLP's.

def _get_syntax(self, tokens, entities, assignments, genders):

	def check_conj(tok, tokens):
		if tok.deprel == "conj" and tok.dephead != tok.token_id:
			# print("found conj", tok.text)
			return tokens[tok.dephead]
		return tok

	def get_head_in_range(start, end, tokens):
		for i in range(start, end+1):
			if tokens[i].dephead < start or tokens[i].dephead > end:
				return tokens[i]
		return None

	agents={}
	patients={}
	poss={}
	mods={}
	prop_mentions={}
	pron_mentions={}
	nom_mentions={}
	keys=Counter()


	toks_by_children={}
	for tok in tokens:
		if tok.dephead not in toks_by_children:
			toks_by_children[tok.dephead]={}
		toks_by_children[tok.dephead][tok]=1

	for idx, (start_token, end_token, cat, phrase) in enumerate(entities):
		ner_prop=cat.split("_")[0]
		ner_type=cat.split("_")[1]

		if ner_type != "PER":
			continue

		coref=assignments[idx]

		keys[coref]+=1
		if coref not in agents:
			agents[coref]=[]
			patients[coref]=[]
			poss[coref]=[]
			mods[coref]=[]
			prop_mentions[coref]=Counter()
			pron_mentions[coref]=Counter()
			nom_mentions[coref]=Counter()

		if ner_prop == "PROP":
			prop_mentions[coref][phrase]+=1
		elif ner_prop == "PRON":
			pron_mentions[coref][phrase]+=1
		elif ner_prop == "NOM":
			nom_mentions[coref][phrase]+=1


		tok=get_head_in_range(start_token, end_token, tokens)
		if tok is not None:

			tok=check_conj(tok, tokens)
			head=tokens[tok.dephead]

			# nsubj
			# mod
			if tok.deprel == "nsubj" and head.lemma == "be":
				for sibling in toks_by_children[head.token_id]:

					# "he was strong and happy", where happy -> conj -> strong -> attr/acomp -> be
					sibling_id=sibling.token_id
					sibling_tok=tokens[sibling_id]
					if (sibling_tok.deprel == "attr" or sibling_tok.deprel == "acomp") and (sibling_tok.pos == "NOUN" or sibling_tok.pos == "ADJ"):
						mods[coref].append({"w":sibling_tok.text, "i":sibling_tok.token_id})

						if sibling.token_id in toks_by_children:
							for grandsibling in toks_by_children[sibling.token_id]:
								grandsibling_id=grandsibling.token_id
								grandsibling_tok=tokens[grandsibling_id]

								if grandsibling_tok.deprel == "conj" and (grandsibling_tok.pos == "NOUN" or grandsibling_tok.pos == "ADJ"):
									mods[coref].append({"w":grandsibling_tok.text, "i":grandsibling_tok.token_id})



			# ("Bill and Ted ran" conj captured by check_conj above)
			elif tok.deprel == "nsubj" and head.pos == ("VERB"):
				agents[coref].append({"w":head.text, "i":head.token_id})

			# "Bill ducked and ran", where ran -> conj -> ducked
				for sibling in toks_by_children[head.token_id]:
					sibling_id=sibling.token_id
					sibling_tok=tokens[sibling_id]
					if sibling_tok.deprel == "conj" and sibling_tok.pos == "VERB":
						agents[coref].append({"w":sibling_tok.text, "i":sibling_tok.token_id})
			
			# "Jack was hit by John and William" conj captured by check_conj above
			elif tok.deprel == "pobj" and head.deprel == "agent":
				# not root
				if head.dephead != head.token_id:
					grandparent=tokens[head.dephead]
					if grandparent.pos.startswith("V"):
						agents[coref].append({"w":grandparent.text, "i":grandparent.token_id})


			# patient ("He loved Bill and Ted" conj captured by check_conj above)
			elif (tok.deprel == "dobj" or tok.deprel == "nsubjpass") and head.pos == "VERB":
				patients[coref].append({"w":head.text, "i":head.token_id})


			# poss

			elif tok.deprel == "poss":
				poss[coref].append({"w":head.text, "i":head.token_id})

				# "her house and car", where car -> conj -> house
				for sibling in toks_by_children[head.token_id]:
					sibling_id=sibling.token_id
					sibling_tok=tokens[sibling_id]
					if sibling_tok.deprel == "conj":
						poss[coref].append({"w":sibling_tok.text, "i":sibling_tok.token_id})
				

	data={}
	data["characters"]=[]

	for coref, total_count in keys.most_common():

		# must observe a character at least *twice*

		if total_count > 1:
			chardata={}
			chardata["agent"]=agents[coref]
			chardata["patient"]=patients[coref]
			chardata["mod"]=mods[coref]
			chardata["poss"]=poss[coref]
			chardata["id"]=coref
			if coref in genders:
				chardata["g"]=genders[coref]
			else:
				chardata["g"]=None
			chardata["count"]=total_count

			mentions={}

			pnames=[]
			for k,v in prop_mentions[coref].most_common():
				pnames.append({"c":v, "n":k})
			mentions["proper"]=pnames

			nnames=[]
			for k,v in nom_mentions[coref].most_common():
				nnames.append({"c":v, "n":k})
			mentions["common"]=nnames

			prnames=[]
			for k,v in pron_mentions[coref].most_common():
				prnames.append({"c":v, "n":k})
			mentions["pronoun"]=prnames

			chardata["mentions"]=mentions

			
			data["characters"].append(chardata)
		
	return data
