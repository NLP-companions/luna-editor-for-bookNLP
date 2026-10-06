"""Rule-based helpers for coreference: the narrator rule, checks for impossible links and the pronoun pass.

Mixed into Store (next to CorefTools and QuoteTools).

* Narrator rule: first-person pronouns outside quotes belong to the narrator. The book has one narrator
  (suggested, then confirmed), and passages can have another narrator or none.
* Checks: links that can't be right, found without a model:
    - a pronoun whose gender or number doesn't fit its group (she in a he/him group, it in a PER group, …);
    - groups whose pronouns are split between he and she (probably two people merged);
    - a reflexive (himself, herself, …) in another group than the subject of its verb;
    - an object pronoun (him, her, …) in the same group as the subject of its verb;
    - two mentions in apposition ("Lestrade, the Scotland Yard man") in different groups.
* Pronoun pass: every pronoun in reading order, skipping the ones you choose (settled by the rules,
  only one candidate nearby, reviewed sentences, confirmed earlier, …).
* Quote pass: the same, but every quote in reading order, reviewing or setting its speaker (skipping
  reviewed sentences, confirmed earlier, set by hand, a checked speaker, or ones that already have a speaker).
"""
from __future__ import annotations

import bisect
import json
import time
from collections import Counter, defaultdict

from luna.core.errors import EditError

PRON_CLASS = {
    "he": "m", "him": "m", "his": "m", "himself": "m",
    "she": "f", "her": "f", "hers": "f", "herself": "f",
    "it": "n", "its": "n", "itself": "n",
    "they": "p", "them": "p", "their": "p", "theirs": "p", "themselves": "p", "themself": "p",
    "i": "1", "me": "1", "my": "1", "mine": "1", "myself": "1",
    "we": "1p", "us": "1p", "our": "1p", "ours": "1p", "ourselves": "1p",
    "you": "2", "your": "2", "yours": "2", "yourself": "2", "yourselves": "2", "thou": "2", "thee": "2", "thy": "2",
}
REFLEXIVE = {"himself", "herself", "itself", "themselves", "themself", "myself", "ourselves", "yourself",
             "yourselves", "oneself", "thyself"}
# object pronouns and the reflexive they'd have to be if the verb's subject were the same person
OBJECT_FORMS = {"him": "himself", "her": "herself", "them": "themselves", "me": "myself", "us": "ourselves", "it": "itself"}
CLASS_WORDS = {"m": "he/him", "f": "she/her", "n": "it", "p": "they/them", "1": "I/me", "1p": "we/us", "2": "you"}
PASS_INCLUDE = {"he_she": ("m", "f"), "they": ("p",), "it": ("n",), "first": ("1", "1p"), "you": ("2",)}
PASS_SKIP = ("rules", "one", "reviewed", "confirmed", "manual", "checked")
# how a pass moves on: 'auto' after Keep/a candidate (as before), or 'manual' (stays put; you press Next yourself)
PASS_ADVANCE = ("auto", "manual")
PASS_PRESETS = {
    "edit": {"include": ["he_she", "they"], "skip": ["rules", "one", "confirmed", "manual"]},
    "check": {"include": ["he_she", "they"], "skip": ["reviewed"]},
}
NON_PERSON_CATS = {"LOC", "FAC", "GPE", "ORG"}
SUBJ = ("nsubj", "nsubjpass")
PASS_WINDOW = 3  # sentences before a pronoun that count as "nearby" for the only-one-candidate rule


def pronoun_group_class(pron: str | None):
    """The class of a group's pronoun setting ("he/him/his" → m), or None for others."""
    if not pron:
        return None
    first = str(pron).split("/")[0].strip().lower()
    return {"he": "m", "she": "f", "they": "p", "it": "n"}.get(first)


class RuleTools:
    """Rule-based coreference helpers mixed into Store. All results are computed from the current data and cached until
    the next edit; the only stored state is in `meta` (narrator, dismissed checks, pronoun-pass settings) and the
    `confirmed` table (mentions you kept with Enter in the pass; not part of undo or export).
    """
    # ------------------------------------------------------------ shared
    def _in_quote_mask(self):
        """(the quote covering each token or None, all quotes), cached."""
        return self._memo("inquote", None, self._build_in_quote_mask)

    def _build_in_quote_mask(self):
        """For each token position, the quote that covers it (or None), and the list of quotes."""
        quotes = self._quote_rows()
        inq = [None] * self.n_tokens
        for q in quotes:
            for i in range(q["s"], min(q["e"], self.n_tokens - 1) + 1):
                inq[i] = q
        return inq, quotes

    def _kids(self):
        """Dependents of each token (position → list of positions), cached."""
        return self._memo("kids", None, self._build_kids)

    def _build_kids(self):
        """Invert the head column: for every token, which tokens depend on it."""
        T = self._tok_arrays()
        kids = defaultdict(list)
        for i, h in enumerate(T["head"]):
            if h != i:
                kids[h].append(i)
        return kids

    def _mention_at_token(self):
        """For each token, the smallest entity mention that contains it (cached)."""
        return self._memo("mention_at", None, self._build_mention_at_token)

    def _build_mention_at_token(self):
        """Map each token position to the smallest mention containing it."""
        best = {}
        for r in self._ents()[0]:
            span = r["e"] - r["s"]
            for i in range(r["s"], r["e"] + 1):
                o = best.get(i)
                if o is None or span < o["e"] - o["s"]:
                    best[i] = r
        return best

    def _group_genders(self):
        """What each group's pronouns should be, and where that comes from (cached)."""
        return self._memo("genders", None, self._build_group_genders)

    def _build_group_genders(self):
        """For each group: the pronoun class it should have and why ('set by you', 'BookNLP', 'n of its m pronouns').
        
        Order: pronouns you set; else its own pronouns when at least 3 in 4 agree; a group with at least two he and two she is
        'mixed'; else BookNLP's value; else its only pronoun.
        """
        cl = self.clusters()
        manual = {r["uid"]: r["pronoun"] for r in self.db.execute("SELECT uid, pronoun FROM groups WHERE pronoun IS NOT NULL")}
        counts = defaultdict(Counter)
        for r in self._ents()[0]:
            if r["prop"] == "PRON":
                k = PRON_CLASS.get(r["text"].lower())
                if k in ("m", "f", "n", "p"):
                    counts[r["coref"]][k] += 1
        bp = self._book_pronouns()
        out = {}
        for cid, cinfo in cl.items():
            cnt = counts.get(cid, Counter())
            info = {"g": None, "mixed": False, "source": None, "counts": dict(cnt)}
            if cid in manual and manual[cid]:
                info.update(g=pronoun_group_class(manual[cid]), source="set by you")
            else:
                gendered = {k: v for k, v in cnt.items() if k in ("m", "f")}
                total = sum(cnt.values())
                top = cnt.most_common(2)
                if total >= 2 and top[0][1] / total >= 0.75:
                    info.update(g=top[0][0], source=f"{top[0][1]} of its {total} pronouns")
                elif len(gendered) == 2 and min(gendered.values()) >= 2:
                    info.update(mixed=True)
                elif bp.get(cid):
                    info.update(g=pronoun_group_class(bp[cid]), source="BookNLP")
                elif total >= 1 and len(cnt) == 1:
                    info.update(g=top[0][0], source=f"its only pronoun{'s' if total > 1 else ''}")
            out[cid] = info
        return out

    def _subject_of(self, T, kids, v, climb=True):
        """The subject token of verb v, climbing through xcomp/conj ("wanted to hurt himself")."""
        for _ in range(3):
            subj = [k for k in kids.get(v, []) if T["dep"][k] in SUBJ]
            if subj:
                return subj[0], v
            h = T["head"][v]
            if climb and T["dep"][v] in ("xcomp", "conj") and h != v:
                v = h
                continue
            return None, v
        return None, v

    # ------------------------------------------------------------ narrator rule
    def _narr(self):
        """The narrator setting: {cid: the narrator's group or None, passages: [{a, b, cid}]}."""
        n = dict(self.meta.get("narrator") or {})
        n.setdefault("cid", None)
        n.setdefault("passages", [])
        return n

    def _narrator_ranges(self):
        """(lo, hi, cid) per passage, in token positions; later passages win where they overlap."""
        out = []
        for p in self._narr()["passages"]:
            a, b = self.ord_of(p["a"]), self.ord_of(p["b"])
            if a is None or b is None:
                continue
            out.append((min(a, b), max(a, b), p.get("cid")))
        return out

    def _narrator_at(self, ord_, ranges=None, default=None):
        """Who narrates at a position: the last passage covering it, else the book's narrator (`default`)."""
        ranges = self._narrator_ranges() if ranges is None else ranges
        who = default
        for lo, hi, cid in ranges:
            if lo <= ord_ <= hi:
                who = cid
        return who

    def _first_person_outside(self):
        """First-person pronouns (I, me, my…) that are not inside a quote."""
        inq, _ = self._in_quote_mask()
        return [r for r in self._ents()[0] if r["prop"] == "PRON" and PRON_CLASS.get(r["text"].lower()) == "1"
                and inq[r["s"]] is None]

    def narrator_info(self):
        """The narrator panel: the chosen narrator, the groups with most first-person pronouns outside quotes, and the passages."""
        with self.lock:
            cl = self.clusters()
            fp = self._first_person_outside()
            counts = Counter(r["coref"] for r in fp)
            n = self._narr()
            cid = n["cid"] if n["cid"] in cl else None
            cands = [{"id": g, "name": cl[g]["name"], "count": cl[g]["count"], "first_person": k,
                      "named": self._group_has_name(g)} for g, k in counts.most_common(6) if g in cl]
            passages = []
            for i, p in enumerate(n["passages"]):
                a, b = self.ord_of(p["a"]), self.ord_of(p["b"])
                if a is None or b is None:
                    continue
                lo, hi = min(a, b), max(a, b)
                passages.append({"i": i, "s": lo, "e": hi, "cid": p.get("cid"), "name": self.cluster_name(p.get("cid")) if p.get("cid") is not None else None,
                                 "start": self.words_between(lo, min(hi, lo + 8)), "sent": self.sentence_of(lo),
                                 "sent_end": self.sentence_of(hi)})
            return {"cid": cid, "name": cl[cid]["name"] if cid is not None else None,
                    "named": self._group_has_name(cid) if cid is not None else None,
                    "suggested": cands[0]["id"] if cands else None, "candidates": cands, "total": len(fp),
                    "passages": passages, "lost": n["cid"] is not None and cid is None}

    def _group_has_name(self, cid):
        """Does the group have a proper name (a PROP mention, or one you gave it)?"""
        if cid is None:
            return False
        rows = self._ents()[2].get(cid, [])
        return any(r["prop"] == "PROP" for r in rows) or bool(self.clusters().get(cid, {}).get("named"))

    def set_narrator(self, cid):
        """Choose the book's narrator group (None = no narrator)."""
        with self.lock:
            n = self._narr()
            if cid is not None and int(cid) not in self.clusters():
                raise EditError("That group has no mentions")
            n["cid"] = None if cid is None else int(cid)
            self._set_meta("narrator", n)
            return self.narrator_info()

    def add_narrator_passage(self, a, b, cid):
        """Give the passage a..b its own narrator (another group, or none), for letters, diaries or a client's story."""
        with self.lock:
            lo, hi = sorted((int(a), int(b)))
            if lo < 0 or hi >= self.n_tokens:
                raise EditError("That passage is outside the book")
            if cid is not None and int(cid) not in self.clusters():
                raise EditError("That group has no mentions")
            n = self._narr()
            n["passages"].append({"a": self.uid_of_ord(lo), "b": self.uid_of_ord(hi), "cid": None if cid is None else int(cid)})
            self._set_meta("narrator", n)
            return self.narrator_info()

    def remove_narrator_passage(self, i):
        """Remove one narrator passage."""
        with self.lock:
            n = self._narr()
            if not 0 <= int(i) < len(n["passages"]):
                raise EditError("That passage is gone")
            n["passages"].pop(int(i))
            self._set_meta("narrator", n)
            return self.narrator_info()

    def _repoint_narrator(self, target, sources):
        """After a merge, a narrator that was merged away is the merged group."""
        n = self.meta.get("narrator")
        if not n:
            return
        src = set(sources)
        changed = False
        if n.get("cid") in src:
            n["cid"], changed = target, True
        for p in n.get("passages", []):
            if p.get("cid") in src:
                p["cid"], changed = target, True
        if changed:
            self._set_meta("narrator", n)

    def narrator_rule(self, apply=False, only=None, exclude=None, limit=400):
        """Narrator rule: first-person pronouns outside quotes belong to the narrator (book-wide, or the passage's).
        
        Without `apply`, lists what would move (`only`/`exclude` pick items); with it, moves them in one undoable step.
        """
        with self.lock:
            cl = self.clusters()
            n = self._narr()
            book = n["cid"] if n["cid"] in cl else None
            ranges = self._narrator_ranges()
            moves, no_narrator = [], 0
            for r in self._first_person_outside():
                who = self._narrator_at(r["s"], ranges, book)
                if who is None:
                    no_narrator += 1
                    continue
                if who not in cl:
                    continue
                if r["coref"] != who:
                    moves.append((r, who))
            if only is not None:
                keep = {int(u) for u in only}
                moves = [(r, w) for r, w in moves if r["uid"] in keep]
            if exclude:
                drop = {int(u) for u in exclude}
                moves = [(r, w) for r, w in moves if r["uid"] not in drop]
            if apply:
                if not moves:
                    raise EditError("Nothing to change")
                with self.batch(f"Narrator rule: moved {len(moves)} first-person pronoun{'s' if len(moves) != 1 else ''} "
                                f"outside quotes to the narrator"):
                    for r, who in moves:
                        before = self._row("entities", r["uid"])
                        if before and before["coref"] != who:
                            self.update("entities", r["uid"], {"coref": who})
                            self._sync_quotes_for_entity(before, {"coref": who})
                return {"history": self.history(1), "moved": len(moves)}
            items = []
            for r, who in moves[:limit]:
                ca, cb = max(0, r["s"] - 10), min(self.n_tokens - 1, r["e"] + 10)
                items.append({"uid": r["uid"], "s": r["s"], "e": r["e"], "text": r["text"], "from": r["coref"],
                              "from_name": self.cluster_name(r["coref"]), "to": who, "to_name": self.cluster_name(who),
                              "context": self.words_between(ca, cb), "context_start": ca})
            return {"n": len(moves), "items": items, "narrator": book, "no_narrator": no_narrator,
                    "set": book is not None or any(c is not None for _, _, c in ranges)}

    # ------------------------------------------------------------ checks for impossible links
    CHECK_KINDS = ("mismatch", "mixed", "reflexive", "objsubj", "appos")

    def _all_checks(self):
        """Every suspicious link found in the book, cached."""
        return self._memo("checks", None, self._build_all_checks)

    def _build_all_checks(self):
        """Find links that can't be right, from pronouns and the parse alone:
         1. mismatch/mixed: a pronoun whose class doesn't fit its group, or a group split between he and she;
         2. reflexive: 'himself' in another group than the subject of its verb;
         3. objsubj: 'him' as the object of a verb whose subject is in the same group;
         4. appos: two descriptions side by side (apposition) in different groups.
        """
        T = self._tok_arrays()
        kids = self._kids()
        mat = self._mention_at_token()
        cl = self.clusters()
        gg = self._group_genders()
        ents = self._ents()[0]
        out = []

        # 1. pronoun doesn't fit its group; groups split between he and she
        for cid, info in gg.items():
            c_ = cl.get(cid)
            if c_ and info["mixed"] and not c_["hidden"]:
                cnt = info["counts"]
                out.append({"kind": "mixed", "key": f"mixed:{cid}", "group": cid, "s": self._ents()[2][cid][0]["s"],
                            "counts": {CLASS_WORDS[k]: v for k, v in sorted(cnt.items(), key=lambda kv: -kv[1])},
                            "why": f"Referred to as he {cnt.get('m', 0)} times and as she {cnt.get('f', 0)} times: probably two people in one group"})
        for r in ents:
            if r["prop"] != "PRON":
                continue
            k = PRON_CLASS.get(r["text"].lower())
            if k not in ("m", "f", "n", "p"):
                continue
            c_ = cl.get(r["coref"])
            if not c_ or c_["hidden"]:
                continue
            cat, info = c_["cat"], gg[r["coref"]]
            why = None
            if cat == "PER":
                if k == "n":
                    why = f"“{r['text']}” in a PER group"
                elif info["g"] and info["g"] != k and not info["mixed"]:
                    why = f"“{r['text']}” in a group referred to as {CLASS_WORDS[info['g']]} ({info['source']})"
            elif cat in NON_PERSON_CATS and k in ("m", "f"):
                why = f"“{r['text']}” in a {cat} group"
            elif cat == "VEH" and k == "m":
                why = f"“{r['text']}” in a VEH group"
            if why:
                out.append({"kind": "mismatch", "key": f"mismatch:{r['uid']}", "uid": r["uid"], "s": r["s"], "e": r["e"],
                            "text": r["text"], "group": r["coref"], "cls": k, "why": why})

        # 2. reflexives and their subjects; 3. object pronouns and their subjects
        for r in ents:
            if r["s"] != r["e"] or r["prop"] != "PRON":
                continue
            w = r["text"].lower()
            i = r["s"]
            if w in REFLEXIVE:
                h = T["head"][i]
                ante_tok, verb = None, None
                if T["dep"][i] in ("appos", "npadvmod") and T["pos"][h] in ("PROPN", "NOUN", "PRON") and h != i:
                    ante_tok, verb = h, None  # "Holmes himself"
                else:
                    v = h
                    for _ in range(4):
                        if T["pos"][v] in ("VERB", "AUX") or T["head"][v] == v:
                            break
                        v = T["head"][v]
                    ante_tok, verb = self._subject_of(T, kids, v)
                    if ante_tok is not None and T["word"][ante_tok].lower() in ("who", "that", "which") and T["dep"][verb] == "relcl":
                        ante_tok = T["head"][verb]
                if ante_tok is None:
                    continue
                ante = mat.get(ante_tok)
                if not ante or ante["uid"] == r["uid"] or ante["coref"] == r["coref"]:
                    continue
                ka = PRON_CLASS.get(ante["text"].lower()) if ante["prop"] == "PRON" else None
                kr = PRON_CLASS.get(w)
                if ka and kr and ka != kr and not (ka == "1p" and kr == "1"):
                    continue  # the parse is probably wrong ("I" and "himself")
                vw = f" ({T['word'][verb]})" if verb is not None else ""
                out.append({"kind": "reflexive", "key": f"reflexive:{r['uid']}", "uid": r["uid"], "s": r["s"], "e": r["e"],
                            "text": r["text"], "group": r["coref"], "cls": kr,
                            "other": {"uid": ante["uid"], "s": ante["s"], "e": ante["e"], "text": ante["text"], "group": ante["coref"]},
                            "why": f"“{r['text']}” refers back to “{ante['text']}”{vw}, which is in another group"})
            elif w in OBJECT_FORMS and T["dep"][i] in ("dobj", "dative", "iobj"):
                v = T["head"][i]
                subj, _ = self._subject_of(T, kids, v, climb=False)
                if subj is None:
                    continue
                ante = mat.get(subj)
                if not ante or ante["uid"] == r["uid"] or ante["coref"] != r["coref"]:
                    continue
                out.append({"kind": "objsubj", "key": f"objsubj:{r['uid']}", "uid": r["uid"], "s": r["s"], "e": r["e"],
                            "text": r["text"], "group": r["coref"], "cls": PRON_CLASS.get(w),
                            "other": {"uid": ante["uid"], "s": ante["s"], "e": ante["e"], "text": ante["text"], "group": ante["coref"]},
                            "why": f"“{r['text']}” is the object of “{T['word'][v]}” and “{ante['text']}” its subject, "
                                   f"so they can’t be the same person (that would be “{OBJECT_FORMS[w]}”)"})

        # 4. appositions in different groups
        for i, dep in enumerate(T["dep"]):
            if dep != "appos":
                continue
            h = T["head"][i]
            a, b = mat.get(h), mat.get(i)
            if not a or not b or a["uid"] == b["uid"] or a["coref"] == b["coref"]:
                continue
            if (a["s"] <= b["s"] and b["e"] <= a["e"]) or (b["s"] <= a["s"] and a["e"] <= b["e"]):
                continue
            if a["cat"] != b["cat"] or "PRON" in (a["prop"], b["prop"]) or (a["prop"] == "PROP" and b["prop"] == "PROP"):
                continue
            first, second = (a, b) if a["s"] < b["s"] else (b, a)
            out.append({"kind": "appos", "key": f"appos:{second['uid']}", "uid": second["uid"], "s": second["s"], "e": second["e"],
                        "text": second["text"], "group": second["coref"],
                        "other": {"uid": first["uid"], "s": first["s"], "e": first["e"], "text": first["text"], "group": first["coref"]},
                        "why": f"“{second['text']}” stands beside “{first['text']}” as another name for it (apposition), but they are in different groups"})
        out.sort(key=lambda x: (x.get("s") or 0, x["kind"]))
        return out

    def checks(self, kinds=None, offset=0, limit=1):
        """One page of the checks still open (those you kept as 'it's right' are hidden), with counts per kind."""
        with self.lock:
            ok = set(self.meta.get("check_ok", []))
            items = [x for x in self._all_checks() if x["key"] not in ok]
            counts = Counter(x["kind"] for x in items)
            if kinds:
                want = set(kinds)
                items = [x for x in items if x["kind"] in want]
            total = len(items)
            page = [self._check_detail(x) for x in items[offset:offset + limit]]
            return {"total": total, "offset": offset, "items": page, "counts": {k: counts.get(k, 0) for k in self.CHECK_KINDS},
                    "dismissed": len(ok)}

    def _check_detail(self, x):
        """A check with what the review card needs: both groups, context, and candidate groups to move to."""
        cl = self.clusters()
        gg = self._group_genders()
        d = dict(x)
        g = cl.get(x["group"])
        d["group_name"] = g["name"] if g else f"#{x['group']}"
        d["group_count"] = g["count"] if g else 0
        d["group_pron"] = CLASS_WORDS.get(gg.get(x["group"], {}).get("g")) if x["group"] in gg else None
        if x.get("other"):
            o = cl.get(x["other"]["group"])
            d["other"] = dict(x["other"], group_name=o["name"] if o else f"#{x['other']['group']}", group_count=o["count"] if o else 0)
        if x["kind"] == "mixed":
            rows = self._ents()[2].get(x["group"], [])
            prons = [r for r in rows if r["prop"] == "PRON" and PRON_CLASS.get(r["text"].lower()) in ("m", "f")]
            cnt = Counter(PRON_CLASS[r["text"].lower()] for r in prons)
            minority = "f" if cnt.get("f", 0) <= cnt.get("m", 0) else "m"
            d["minority"] = CLASS_WORDS[minority]
            d["minority_uids"] = [r["uid"] for r in prons if PRON_CLASS[r["text"].lower()] == minority]
            d["samples"] = [{"uid": r["uid"], "s": r["s"], "e": r["e"], "text": r["text"], "ctx": self._ctx(r, 10)}
                            for r in prons if PRON_CLASS[r["text"].lower()] == minority][:4]
            d["s"] = d["samples"][0]["s"] if d["samples"] else (rows[0]["s"] if rows else 0)
            d["candidates"] = self._gender_candidates(d["s"], d["s"], minority, exclude=[x["group"]])
            return d
        a = min(x["s"], x["other"]["s"]) if x.get("other") else x["s"]
        b = max(x["e"], x["other"]["e"]) if x.get("other") else x["e"]
        ca, cb = max(0, a - 14), min(self.n_tokens - 1, b + 14)
        d["ctx"] = {"words": self.words_between(ca, cb), "start": ca}
        d["sent"] = self.sentence_of(x["s"])
        exclude = [x["group"]] + ([x["other"]["group"]] if x["kind"] == "objsubj" else [])
        d["candidates"] = self._gender_candidates(x["s"], x["e"], x.get("cls"), exclude=exclude)
        return d

    def _gender_candidates(self, a, b, cls, exclude=(), limit=6):
        """Groups mentioned nearby, those whose pronouns fit first."""
        gg = self._group_genders()
        near = [n for n in self.nearby_groups(a, b, window=300, limit=16, exclude=list(exclude)) if not n["hidden"]]
        cl = self.clusters()

        def fit(n):
            """Sort key: 0 for a candidate whose pronouns fit, 1 unknown, 2 doesn't fit."""
            g = gg.get(n["id"], {}).get("g")
            if cls in ("m", "f", "p", "n") and g:
                return 0 if g == cls else 2
            if cls in ("m", "f") and cl[n["id"]]["cat"] != "PER":
                return 2
            return 1
        near.sort(key=lambda n: (fit(n), n["distance"]))
        for n in near:
            n["fits"] = fit(n) == 0
            n["reason"] = f"{'Referred to as ' + CLASS_WORDS[gg[n['id']]['g']] + ', ' if gg.get(n['id'], {}).get('g') else ''}mentioned {n['distance']} words {n['side']}"
        return near[:limit]

    def dismiss_check(self, key):
        """Remember that a check was reviewed and is fine ('K')."""
        with self.lock:
            ok = list(self.meta.get("check_ok", []))
            if key not in ok:
                ok.append(key)
                self._set_meta("check_ok", ok)
            return {"ok": True}

    # ------------------------------------------------------------ shared by the pronoun pass and the quote pass
    @staticmethod
    def _pass_index(starts, ord_, direction):
        """Which item of a pass list (given by its start positions) comes next, before, or at `ord_`: an index, which may fall
        outside the list (before the first or after the last item)."""
        if direction == "next":
            return bisect.bisect_right(starts, ord_)
        if direction == "here":
            return bisect.bisect_left(starts, ord_)
        return bisect.bisect_left(starts, ord_) - 1

    def _reviewed_sentences(self):
        """The numbers of the sentences marked as reviewed."""
        return {self.sentence_of(r[0]) for r in self.db.execute(
            "SELECT ord FROM tokens WHERE reviewed=1 AND (sent_start=1 OR ord=0)")}

    def _hand_edited(self, tbl, field):
        """The uids of rows of a table whose `field` was set by hand."""
        return {r["uid"] for r in self.db.execute(f"SELECT uid, manual FROM {tbl} WHERE manual LIKE ?", (f"%{field}%",))
                if field in json.loads(r["manual"] or "[]")}

    # ------------------------------------------------------------ pronoun pass
    def pass_settings(self):
        """The pronoun pass settings (which pronouns to show, what to skip, how to advance) and which preset they match."""
        s = dict(self.meta.get("ppass") or {})
        base = PASS_PRESETS["edit"]
        s.setdefault("include", list(base["include"]))
        s.setdefault("skip", list(base["skip"]))
        s.setdefault("advance", "auto")
        s["preset"] = next((k for k, v in PASS_PRESETS.items() if sorted(v["include"]) == sorted(s["include"])
                            and sorted(v["skip"]) == sorted(s["skip"])), None)
        return s

    def set_pass_settings(self, include=None, skip=None, preset=None, advance=None):
        """Save the pass settings, from a preset ('edit', 'check') or from explicit include/skip lists; `advance`
        ('auto': Keep/a candidate also goes on to the next one, as before; 'manual': they stay put, go on with
        'Next') is independent of the preset and of include/skip, and carries over when either of those change."""
        with self.lock:
            cur = self.pass_settings()
            if preset:
                if preset not in PASS_PRESETS:
                    raise EditError("Unknown preset")
                s = {"include": list(PASS_PRESETS[preset]["include"]), "skip": list(PASS_PRESETS[preset]["skip"])}
            else:
                s = {"include": [x for x in (include if include is not None else cur["include"]) if x in PASS_INCLUDE],
                     "skip": [x for x in (skip if skip is not None else cur["skip"]) if x in PASS_SKIP]}
            if not s["include"]:
                raise EditError("Choose at least one kind of pronoun")
            s["advance"] = advance if advance is not None else cur["advance"]
            if s["advance"] not in PASS_ADVANCE:
                raise EditError("Unknown way to advance")
            self._set_meta("ppass", s)
            return self.pass_settings()

    def confirm_mentions(self, uids):
        """Remember that these mentions were kept in their group in the pass (they are skipped later while they stay there)."""
        with self.lock:
            now = time.time()
            rows = []
            for u in uids:
                r = self._row("entities", int(u))
                if r:
                    rows.append((r["uid"], r["coref"], now))
            self.db.executemany("INSERT OR REPLACE INTO confirmed(uid, coref, ts) VALUES (?,?,?)", rows)
            self.db.commit()
            return {"confirmed": len(rows)}

    def _pass_list(self):
        """Every pronoun the pass shows, in reading order, and the counts of those skipped (cached)."""
        st = self.pass_settings()
        conf_ver = self.db.execute("SELECT COUNT(*), COALESCE(MAX(ts), 0) FROM confirmed").fetchone()
        key = (tuple(st["include"]), tuple(st["skip"]), tuple(conf_ver), repr(self.meta.get("narrator")))
        return self._memo("pass", key, lambda: self._build_pass_list(st))

    def _build_pass_list(self, st):
        """The pronouns the pass will show, in reading order, and how many were skipped and why.
        
        A pronoun is skipped when it is: confirmed earlier and still in that group; moved by hand; in a reviewed sentence; in a
        checked group; settled by the quote or narrator rule; or the only matching candidate group in the last three
        sentences ('one'). Which are skipped depends on the settings.
        """
        want = {k for inc in st["include"] for k in PASS_INCLUDE[inc]}
        skip = set(st["skip"])
        cl = self.clusters()
        gg = self._group_genders()
        ents, starts, _ = self._ents()
        inq, _ = self._in_quote_mask()
        n = self._narr()
        book_narr = n["cid"] if n["cid"] in cl else None
        ranges = self._narrator_ranges()
        confirmed = {r[0]: r[1] for r in self.db.execute("SELECT uid, coref FROM confirmed")}
        rev_sents = self._reviewed_sentences()
        manual = self._hand_edited("entities", "coref")
        shown, skipped, total = [], Counter(), 0
        for r in ents:
            if r["prop"] != "PRON" or r["s"] != r["e"]:
                continue
            k = PRON_CLASS.get(r["text"].lower())
            if k not in want:
                continue
            total += 1
            reason = None
            if "confirmed" in skip and confirmed.get(r["uid"]) == r["coref"]:
                reason = "confirmed"
            elif "manual" in skip and r["uid"] in manual:
                reason = "manual"
            elif "reviewed" in skip and self.sentence_of(r["s"]) in rev_sents:
                reason = "reviewed"
            elif "checked" in skip and cl.get(r["coref"], {}).get("checked"):
                reason = "checked"
            elif "rules" in skip and k == "1":
                q = inq[r["s"]]
                if q is not None and q["char_id"] is not None:
                    reason = "rules"
                elif q is None and self._narrator_at(r["s"], ranges, book_narr) is not None:
                    reason = "rules"
            if reason is None and "one" in skip and k in ("m", "f") and gg.get(r["coref"], {}).get("g") == k:
                s0 = self.sentence_bounds(max(0, self.sentence_of(r["s"]) - PASS_WINDOW))[0]
                i = bisect.bisect_left(starts, s0)
                cands = set()
                unknown = False
                while i < len(ents) and ents[i]["s"] < r["s"]:
                    o = ents[i]
                    i += 1
                    oc = cl.get(o["coref"])
                    if not oc or oc["hidden"] or oc["cat"] != "PER":
                        continue
                    if o["prop"] == "PRON" and PRON_CLASS.get(o["text"].lower()) in ("1", "1p", "2"):
                        continue
                    g = gg.get(o["coref"], {}).get("g")
                    if g == k:
                        cands.add(o["coref"])
                    elif g is None:
                        unknown = True
                if cands == {r["coref"]} and not unknown:
                    reason = "one"
            if reason:
                skipped[reason] += 1
            else:
                shown.append(r)
        return shown, [r["s"] for r in shown], skipped, total

    def pass_step(self, ord_, direction="next"):
        """The next, previous or current pronoun of the pass from a position, with counts and the settings."""
        with self.lock:
            shown, starts, skipped, total = self._pass_list()
            i = self._pass_index(starts, int(ord_), direction)
            item = self._pass_item(shown[i], i + 1) if 0 <= i < len(shown) else None
            return {"item": item, "remaining": len(shown) - i if item else 0,
                    "shown": len(shown), "total": total, "skipped": dict(skipped), "settings": self.pass_settings()}

    def _pass_item(self, r, index=None):
        """One pronoun as the pass bar shows it: group, whether its pronouns fit, quote/speaker and candidate groups."""
        cl = self.clusters()
        gg = self._group_genders()
        k = PRON_CLASS.get(r["text"].lower())
        inq, _ = self._in_quote_mask()
        q = inq[r["s"]]
        return {"uid": r["uid"], "s": r["s"], "e": r["e"], "text": r["text"], "group": r["coref"],
                "group_name": cl[r["coref"]]["name"] if r["coref"] in cl else f"#{r['coref']}",
                "group_pron": CLASS_WORDS.get(gg.get(r["coref"], {}).get("g")),
                "fits": gg.get(r["coref"], {}).get("g") in (None, k), "sent": self.sentence_of(r["s"]),
                "in_quote": q is not None, "speaker": self.cluster_name(q["char_id"]) if q is not None and q["char_id"] is not None else None,
                "candidates": self._gender_candidates(r["s"], r["e"], k, exclude=[r["coref"]]), "index": index}

    def pass_item(self, uid):
        """One mention described as the pass shows it (for going back to one already passed)."""
        with self.lock:
            rows = self._ent_rows("WHERE x.uid=?", (int(uid),))
            if not rows:
                raise EditError("That mention no longer exists")
            shown, starts, skipped, total = self._pass_list()
            i = next((n for n, r in enumerate(shown) if r["uid"] == int(uid)), None)
            return {"item": self._pass_item(rows[0], i + 1 if i is not None else None),
                    "remaining": len(shown) - i if i is not None else None, "shown": len(shown), "total": total,
                    "skipped": dict(skipped), "settings": self.pass_settings()}

    # ------------------------------------------------------------ quote pass
    QPASS_SKIP = ("reviewed", "confirmed", "manual", "checked", "has_speaker")
    QPASS_PRESETS = {
        "edit": {"skip": ["confirmed", "manual"]},
        "check": {"skip": ["reviewed"]},
    }

    def qpass_settings(self):
        """The quote pass settings (what to skip, how to advance) and which preset they match."""
        s = dict(self.meta.get("qpass") or {})
        s.setdefault("skip", list(self.QPASS_PRESETS["edit"]["skip"]))
        s.setdefault("advance", "auto")
        s["preset"] = next((k for k, v in self.QPASS_PRESETS.items() if sorted(v["skip"]) == sorted(s["skip"])), None)
        return s

    def set_qpass_settings(self, skip=None, preset=None, advance=None):
        """Save the quote pass settings, from a preset ('edit', 'check') or an explicit skip list; `advance` ('auto':
        Keep/a candidate also goes on to the next one, as before; 'manual': they stay put, go on with 'Next') is
        independent of the preset and of skip, and carries over when either of those change."""
        with self.lock:
            cur = self.qpass_settings()
            if preset:
                if preset not in self.QPASS_PRESETS:
                    raise EditError("Unknown preset")
                s = {"skip": list(self.QPASS_PRESETS[preset]["skip"])}
            else:
                s = {"skip": [x for x in (skip if skip is not None else cur["skip"]) if x in self.QPASS_SKIP]}
            s["advance"] = advance if advance is not None else cur["advance"]
            if s["advance"] not in PASS_ADVANCE:
                raise EditError("Unknown way to advance")
            self._set_meta("qpass", s)
            return self.qpass_settings()

    def confirm_quotes(self, uids):
        """Remember that these quotes' speakers were kept in the quote pass (skipped later while unchanged)."""
        with self.lock:
            now = time.time()
            rows = []
            for u in uids:
                r = self._row("quotes", int(u))
                if r:
                    rows.append((r["uid"], r["char_id"], now))
            self.db.executemany("INSERT OR REPLACE INTO confirmed_quotes(uid, char_id, ts) VALUES (?,?,?)", rows)
            self.db.commit()
            return {"confirmed": len(rows)}

    def _qpass_list(self):
        """Every quote the pass shows, in reading order, and the counts of those skipped (cached)."""
        st = self.qpass_settings()
        conf_ver = self.db.execute("SELECT COUNT(*), COALESCE(MAX(ts), 0) FROM confirmed_quotes").fetchone()
        key = (tuple(st["skip"]), tuple(conf_ver))
        return self._memo("qpass", key, lambda: self._build_qpass_list(st))

    def _build_qpass_list(self, st):
        """The quotes the pass will show, in reading order, and how many were skipped and why.

        A quote is skipped when it is: confirmed earlier and its speaker hasn't changed since; its speaker was set by
        hand; in a reviewed sentence; its speaker's group is checked; or (only when asked) it already has a speaker.
        """
        skip = set(st["skip"])
        cl = self.clusters()
        quotes = self._quote_rows()
        confirmed = {r[0]: r[1] for r in self.db.execute("SELECT uid, char_id FROM confirmed_quotes")}
        rev_sents = self._reviewed_sentences()
        manual = self._hand_edited("quotes", "char_id")
        shown, skipped, total = [], Counter(), 0
        for q in quotes:
            total += 1
            reason = None
            if "confirmed" in skip and q["uid"] in confirmed and confirmed[q["uid"]] == q["char_id"]:
                reason = "confirmed"
            elif "manual" in skip and q["uid"] in manual:
                reason = "manual"
            elif "reviewed" in skip and self.sentence_of(q["s"]) in rev_sents:
                reason = "reviewed"
            elif "checked" in skip and q["char_id"] is not None and cl.get(q["char_id"], {}).get("checked"):
                reason = "checked"
            elif "has_speaker" in skip and q["char_id"] is not None:
                reason = "has_speaker"
            if reason:
                skipped[reason] += 1
            else:
                shown.append(q)
        return shown, [q["s"] for q in shown], skipped, total

    def qpass_step(self, ord_, direction="next"):
        """The next, previous or current quote of the pass from a position, with counts and the settings."""
        with self.lock:
            shown, starts, skipped, total = self._qpass_list()
            i = self._pass_index(starts, int(ord_), direction)
            item = self._qpass_item(shown[i], i + 1) if 0 <= i < len(shown) else None
            return {"item": item, "remaining": len(shown) - i if item else 0,
                    "shown": len(shown), "total": total, "skipped": dict(skipped), "settings": self.qpass_settings()}

    def _qpass_item(self, q, index=None):
        """One quote as the pass bar shows it: its speaker and candidate groups nearby."""
        return {"uid": q["uid"], "s": q["s"], "e": q["e"], "text": q["text"], "speaker": q["char_id"],
                "speaker_name": self.cluster_name(q["char_id"]) if q["char_id"] is not None else None,
                "sent": self.sentence_of(q["s"]),
                "candidates": self.nearby_groups(q["s"], q["e"], exclude=[q["char_id"]] if q["char_id"] is not None else []),
                "index": index}

    def qpass_item(self, uid):
        """One quote described as the pass shows it (for going back to one already passed)."""
        with self.lock:
            row = self._row("quotes", int(uid))
            if not row:
                raise EditError("That quote no longer exists")
            q = next(x for x in self._quote_rows() if x["uid"] == int(uid))
            shown, starts, skipped, total = self._qpass_list()
            i = next((n for n, x in enumerate(shown) if x["uid"] == int(uid)), None)
            return {"item": self._qpass_item(q, i + 1 if i is not None else None),
                    "remaining": len(shown) - i if i is not None else None, "shown": len(shown), "total": total,
                    "skipped": dict(skipped), "settings": self.qpass_settings()}
